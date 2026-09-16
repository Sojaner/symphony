"""Fast, offline hook runtime for Symphony's canonical lifecycle."""

from dataclasses import replace
import json
import os
from pathlib import Path
import sys
from typing import Mapping

from . import HOOK_SCHEMA_VERSION, PLUGIN_VERSION
from .adapters import HookResult, detect_provider, event_from_payload, render
from .model import Action, Delegation, Event, ProjectState
from .reducer import reduce
from .store import StateStore


CONTROLS = {"agents", "bypass", "disable", "enable", "help", "reassess", "start", "status", "stop"}


def handle(payload: dict, environ: Mapping[str, str] = os.environ) -> HookResult:
    provider = detect_provider(payload)
    project = Path(payload.get("cwd") or os.getcwd()).resolve()
    store = StateStore(Path(environ.get("SYMPHONY_STATE_DIR", Path.home() / ".symphony" / "state")))
    state = store.load(project)
    source = event_from_payload(provider, payload)
    actions: tuple[Action, ...] = ()

    if source.kind in {"session_heartbeat", "user_prompt"}:
        heartbeat = Event(
            source.event_id + ":heartbeat",
            "session_heartbeat",
            source.observed_at,
            {
                "provider": provider,
                "session_id": payload.get("session_id"),
                "plugin_version": PLUGIN_VERSION,
                "hook_schema_version": HOOK_SCHEMA_VERSION,
            },
        )
        state, heartbeat_actions = reduce(state, heartbeat)
        actions += heartbeat_actions

    if source.kind == "user_prompt":
        state, prompt_actions = _handle_prompt(state, source, provider)
        actions += prompt_actions
    elif source.kind == "session_heartbeat":
        if state.active_run:
            actions += (Action("inject_context", {"text": _recovery_guidance(state)}),)
    elif source.kind in {"subagent_started", "subagent_stopped", "post_tool_use"}:
        state, observed_actions = _observe_delegation(state, source)
        actions += observed_actions
    elif source.kind in {"stop_requested", "interrupt"}:
        state, lifecycle_actions = reduce(state, source)
        actions += lifecycle_actions

    store.save(project, state)
    return render(provider, _render_actions(actions, state, provider))


def _handle_prompt(state: ProjectState, source: Event, provider: str) -> tuple[ProjectState, tuple[Action, ...]]:
    prompt = str(source.payload.get("prompt") or "").strip()
    control = _parse_control(prompt)
    if control is None:
        if not state.enabled:
            return state, ()
        return reduce(state, _derived(source, "task_received", {"task": prompt}))

    name, argument = control
    if name == "help":
        return state, (Action("inject_context", {"text": _help(provider)}),)
    if name == "status":
        return state, (Action("inject_context", {"text": _status(state, False)}),)
    if name == "agents":
        return state, (Action("inject_context", {"text": _status(state, argument == "--all")}),)
    if name == "enable":
        next_state, actions = reduce(state, _derived(source, "enable"))
        if argument:
            next_state, more = reduce(
                next_state,
                _derived(source, "task_received", {"task": argument}, suffix="enabled-task"),
            )
            actions += more
        return next_state, actions
    if name == "start":
        if not argument:
            return state, (Action("inject_context", {"text": "Symphony start requires a task."}),)
        return reduce(state, _derived(source, "task_received", {"task": argument, "one_shot": True}))
    if name == "bypass":
        if not argument:
            return state, (Action("inject_context", {"text": "Symphony bypass requires a task."}),)
        return reduce(state, _derived(source, "bypass", {"task": argument}))
    if name == "disable":
        return reduce(state, _derived(source, "disable"))
    if name == "reassess":
        return reduce(state, _derived(source, "reassess", {"reason": argument or "explicit request"}))
    if name == "stop":
        kind = "force_stop" if argument == "--force" else "stop_requested"
        return reduce(state, _derived(source, kind))
    return state, (Action("inject_context", {"text": f"Unknown Symphony control: {name}. Use {_native_help(provider)}."}),)


def _parse_control(prompt: str) -> tuple[str, str] | None:
    marker = "$symphony:symphony"
    if marker in prompt:
        before, after = prompt.split(marker, 1)
        tail = after.strip()
        if not tail:
            return "help", ""
        name, _, argument = tail.partition(" ")
        if name in CONTROLS:
            return name, argument.strip()
        if not argument and not before.strip():
            return name, ""
        task = " ".join(part for part in (before.strip(), tail) if part).strip()
        if task.lower().startswith("use "):
            task = task[4:].strip()
        if task.lower().startswith("and "):
            task = task[4:].strip()
        return "start", task
    prefix = "SYMPHONY_CONTROL:"
    lines = prompt.splitlines()
    arguments = next(
        (line.split(":", 1)[1].strip() for line in lines if line.strip().startswith("ARGUMENTS:")),
        "",
    )
    for line in lines:
        if line.strip().startswith(prefix):
            value = line.strip()[len(prefix) :].strip()
            name, _, argument = value.partition(" ")
            argument = argument.strip() or arguments
            return (name, argument) if name in CONTROLS else (name or "unknown", argument)
    return None


def _derived(source: Event, kind: str, payload: dict | None = None, suffix: str = "control") -> Event:
    return Event(f"{source.event_id}:{suffix}:{kind}", kind, source.observed_at, payload or {})


def _observe_delegation(state: ProjectState, source: Event) -> tuple[ProjectState, tuple[Action, ...]]:
    identity = source.payload.get("agent_id") or source.payload.get("subagent_id")
    if not identity or not state.active_run:
        return state, ()
    terminal = source.kind == "subagent_stopped"
    status = str(source.payload.get("status") or ("completed" if terminal else "working"))
    role = _observed_role(source.payload)
    if role == "lead" and source.kind == "subagent_started":
        state, actions = reduce(
            state,
            _derived(
                source,
                "lead_started",
                {"identity": str(identity), "owner_generation": state.active_run.owner_generation},
                "lead",
            ),
        )
    else:
        actions = ()
    payload = {
        "identity": str(identity),
        "role": role,
        "objective": str(source.payload.get("task") or source.payload.get("objective") or ""),
        "state": status,
        "requested_tier": str(source.payload.get("model") or "unknown"),
        "requested_effort": str(source.payload.get("model_reasoning_effort") or "unknown"),
    }
    for field in ("tokens", "duration_seconds"):
        if source.payload.get(field) is not None:
            payload[field] = source.payload[field]
    state, delegation_actions = reduce(state, _derived(source, "delegation_updated", payload, "delegation"))
    actions += delegation_actions
    if role == "lead" and terminal and state.active_run:
        state, completion_actions = reduce(
            state,
            _derived(
                source,
                "lead_completed",
                {
                    "identity": str(identity),
                    "owner_generation": state.active_run.owner_generation,
                    "outcome": {"status": status},
                },
                "lead-completion",
            ),
        )
        actions += completion_actions
    return state, actions


def _observed_role(payload: Mapping[str, object]) -> str:
    label = str(payload.get("agent_type") or payload.get("role") or payload.get("task_name") or "worker")
    for role in ("assessor", "consultant", "lead", "worker"):
        if role in label.lower():
            return role
    return label


def _render_actions(
    actions: tuple[Action, ...], state: ProjectState, provider: str
) -> tuple[Action, ...]:
    rendered: list[Action] = []
    for action in actions:
        if action.kind in {"inject_context", "block_stop"}:
            rendered.append(action)
        elif action.kind == "project_enabled":
            rendered.append(Action("inject_context", {"text": "Symphony is enabled for this project; hooks are guarded."}))
        elif action.kind == "project_disabled":
            rendered.append(Action("inject_context", {"text": "Symphony is disabled for future tasks in this project."}))
        elif action.kind == "request_assessment":
            task = state.active_run.task if state.active_run else "the task"
            rendered.append(Action("inject_context", {"text": _assessment_guidance(task)}))
        elif action.kind == "execute_bypass":
            rendered.append(
                Action(
                    "inject_context",
                    {"text": f"Execute outside Symphony without changing project state: {action.payload.get('task', '')}"},
                )
            )
        elif action.kind == "run_already_active":
            rendered.append(Action("inject_context", {"text": _recovery_guidance(state)}))
        elif action.kind == "preserve_recovery_context":
            rendered.append(Action("inject_context", {"text": "Symphony recorded the interruption for safe reconciliation on resume."}))
    return tuple(rendered)


def _assessment_guidance(task: str) -> str:
    return (
        "Symphony owns execution topology. Keep the root thin. Assess this bounded task with a strongest/high "
        "assessor, then select the lead mechanically from the nine-cell matrix; the assessor must not become the "
        f"lead. Task: {task}"
    )


def _recovery_guidance(state: ProjectState) -> str:
    run = state.active_run
    if not run:
        return "Symphony has no active run."
    return f"Symphony run {run.run_id} remains active. Reconcile observed agents, preserve ownership, and wait for the lead."


def compact_delegations(state: ProjectState, limit: int = 5) -> tuple[Delegation, ...]:
    if limit <= 0:
        return ()
    delegations = state.active_run.delegations if state.active_run else ()
    rank = {"failed": 0, "terminated": 0, "working": 1, "running": 1, "active": 1, "waiting": 2, "pending": 2}
    ordered = sorted(delegations, key=lambda item: (rank.get(item.state, 3), item.updated_at), reverse=False)
    active = [item for item in ordered if rank.get(item.state, 3) < 3]
    completed = sorted((item for item in ordered if rank.get(item.state, 3) == 3), key=lambda item: item.updated_at, reverse=True)
    return tuple((active + completed)[:limit])


def format_delegation(item: Delegation) -> str:
    result = f"- {item.state}: {item.role} [{item.requested_tier}/{item.requested_effort}] — {item.identity}"
    if item.objective:
        result += f" — {item.objective}"
    if item.tokens is not None:
        result += f" — tokens {item.tokens}"
    if item.duration_seconds is not None:
        result += f" — duration {item.duration_seconds:g}s"
    return result


def _status(state: ProjectState, include_history: bool) -> str:
    activation = next(iter(state.activation.values()), {})
    lines = [
        f"Symphony: {'enabled' if state.enabled else 'disabled'}",
        f"Hooks: {activation.get('state', 'pending verification')}",
    ]
    runs = ([state.active_run] if state.active_run else []) + (list(state.recent_runs) if include_history else [])
    if state.active_run:
        lines.append(f"Run: {state.active_run.run_id} ({state.active_run.status})")
    records = [item for run in runs if run for item in run.delegations]
    if include_history:
        visible = records
    else:
        visible = list(compact_delegations(state))
    lines.extend(format_delegation(item) for item in visible)
    return "\n".join(lines)


def _native_help(provider: str) -> str:
    return "/symphony:help" if provider == "claude" else "$symphony:symphony help"


def _help(provider: str) -> str:
    if provider == "claude":
        return "Symphony controls: /symphony:enable, :start, :bypass, :disable, :status, :agents, :reassess, :stop, :help."
    return "Symphony controls: $symphony:symphony enable|start|bypass|disable|status|agents|reassess|stop|help."


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        result = handle(payload, os.environ)
    except Exception as error:  # Hook failures must not block unrelated host work.
        sys.stderr.write(f"Symphony hook fault: {type(error).__name__}\n")
        return 0
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    return result.exit_code
