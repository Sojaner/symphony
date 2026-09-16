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
from .routing import Assessment, route_for, resolve_tier
from .store import StateStore


CONTROLS = {"agents", "bypass", "disable", "enable", "help", "reassess", "start", "status", "stop"}


def handle(payload: dict, environ: Mapping[str, str] = os.environ) -> HookResult:
    provider = detect_provider(payload)
    project = Path(payload.get("cwd") or os.getcwd()).resolve()
    legacy_roots = tuple(
        Path(environ[name])
        for name in ("PLUGIN_DATA", "CLAUDE_PLUGIN_DATA")
        if environ.get(name)
    )
    store = StateStore(
        Path(environ.get("SYMPHONY_STATE_DIR", Path.home() / ".symphony" / "state")),
        legacy_roots,
    )
    source = event_from_payload(provider, payload)

    def transition(state: ProjectState) -> tuple[ProjectState, tuple[Action, ...]]:
        next_state, actions = _transition(state, source, provider, payload, environ)
        return next_state, _render_actions(actions, next_state, provider)

    actions = store.update(project, transition)
    return render(provider, actions, str(payload.get("hook_event_name") or "UserPromptSubmit"))


def _transition(
    state: ProjectState,
    source: Event,
    provider: str,
    payload: Mapping[str, object],
    environ: Mapping[str, str],
) -> tuple[ProjectState, tuple[Action, ...]]:
    actions: tuple[Action, ...] = ()

    if source.kind in {"session_heartbeat", "user_prompt"}:
        heartbeat = Event(
            source.event_id + ":" + source.observed_at + ":heartbeat",
            "session_heartbeat",
            source.observed_at,
            {
                "provider": provider,
                "session_id": payload.get("session_id"),
                "plugin_version": PLUGIN_VERSION,
                "plugin_root": environ.get(
                    "SYMPHONY_PLUGIN_ROOT", str(Path(__file__).resolve().parents[1])
                ),
                "hook_schema_version": HOOK_SCHEMA_VERSION,
            },
        )
        state, heartbeat_actions = reduce(state, heartbeat)
        actions += heartbeat_actions

    if source.kind == "user_prompt":
        state, prompt_actions = _handle_prompt(state, source, provider)
        actions += prompt_actions
    elif source.kind == "session_heartbeat":
        active_ids = payload.get("active_agent_ids", payload.get("active_ids"))
        if state.active_run and isinstance(active_ids, list):
            state, resume_actions = reduce(
                state,
                _derived(source, "resume_reconciled", {"active_ids": active_ids}, "resume"),
            )
            actions += resume_actions
        if state.active_run:
            actions += (Action("inject_context", {"text": _recovery_guidance(state)}),)
    elif source.kind == "pre_tool_use":
        state, route_actions = _register_assessment(state, source, provider)
        actions += route_actions
    elif source.kind in {"subagent_started", "subagent_stopped", "post_tool_use"}:
        state, observed_actions = _observe_delegation(state, source)
        actions += observed_actions
    elif source.kind in {"stop_requested", "interrupt"}:
        state, lifecycle_actions = reduce(state, source)
        actions += lifecycle_actions

    return state, actions


def _handle_prompt(state: ProjectState, source: Event, provider: str) -> tuple[ProjectState, tuple[Action, ...]]:
    prompt = str(source.payload.get("prompt") or "").strip()
    control = _parse_control(prompt)
    if control is None:
        if not state.enabled:
            return state, ()
        return reduce(state, _task_event(state, source, {"task": prompt}))

    name, argument = control
    if name == "help":
        return state, (Action("inject_context", {"text": _help(provider)}),)
    if name == "status":
        return state, (Action("inject_context", {"text": _status(state, False, provider)}),)
    if name == "agents":
        return state, (Action("inject_context", {"text": _status(state, argument == "--all", provider)}),)
    if name == "enable":
        next_state, actions = reduce(state, _derived(source, "enable"))
        if argument:
            next_state, more = reduce(
                next_state,
                _task_event(next_state, source, {"task": argument}, suffix="enabled-task"),
            )
            actions += more
        return next_state, actions
    if name == "start":
        if not argument:
            return state, (Action("inject_context", {"text": "Symphony start requires a task."}),)
        return reduce(state, _task_event(state, source, {"task": argument, "one_shot": True}))
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
        next_state, actions = reduce(state, _derived(source, kind))
        return next_state, _prompt_stop_actions(actions)
    return state, (Action("inject_context", {"text": f"Unknown Symphony control: {name}. Use {_native_help(provider)}."}),)


def _parse_control(prompt: str) -> tuple[str, str] | None:
    if prompt.startswith("/symphony:"):
        first_line = prompt.splitlines()[0]
        command, _, argument = first_line.partition(" ")
        name = command.removeprefix("/symphony:").strip()
        return (name, argument.strip()) if name in CONTROLS else (name or "unknown", argument.strip())
    marker = "$symphony:symphony"
    if marker in prompt:
        before, after = prompt.split(marker, 1)
        tail = after.strip()
        if not tail:
            return "help", ""
        name, _, argument = tail.partition(" ")
        if name in CONTROLS:
            return name, argument.strip()
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


def _task_event(
    state: ProjectState,
    source: Event,
    payload: dict,
    suffix: str = "control",
) -> Event:
    candidate = _derived(source, "task_received", payload, suffix)
    if any(item.event_id == candidate.event_id for item in state.event_history):
        return replace(candidate, event_id=f"{candidate.event_id}:{source.observed_at}")
    return candidate


def _observe_delegation(state: ProjectState, source: Event) -> tuple[ProjectState, tuple[Action, ...]]:
    identity = source.payload.get("agent_id") or source.payload.get("subagent_id")
    if not identity or not state.active_run:
        return state, ()
    terminal = source.kind == "subagent_stopped"
    status = str(source.payload.get("status") or ("completed" if terminal else "working"))
    current = next(
        (item for item in state.active_run.delegations if item.identity == str(identity)),
        None,
    )
    role = _observed_role(source.payload) or (current.role if current else "worker")
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
    update = {
        "identity": str(identity),
        "role": role,
        "objective": str(source.payload.get("task") or source.payload.get("objective") or ""),
        "state": status,
    }
    if source.payload.get("model"):
        update["requested_tier"] = str(source.payload["model"])
    if source.payload.get("model_reasoning_effort"):
        update["requested_effort"] = str(source.payload["model_reasoning_effort"])
    tokens = source.payload.get("tokens")
    if isinstance(tokens, int) and not isinstance(tokens, bool):
        update["tokens"] = tokens
    duration = source.payload.get("duration_seconds")
    if isinstance(duration, (int, float)) and not isinstance(duration, bool):
        update["duration_seconds"] = duration
    state, delegation_actions = reduce(state, _derived(source, "delegation_updated", update, "delegation"))
    actions += delegation_actions
    if role == "lead" and terminal and state.active_run:
        completion_kind = "lead_completed" if status.lower() in {"completed", "done", "success", "succeeded"} else "lead_failed"
        state, completion_actions = reduce(
            state,
            _derived(
                source,
                completion_kind,
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
    label = str(payload.get("agent_type") or payload.get("role") or payload.get("task_name") or "")
    for role in ("assessor", "consultant", "lead", "worker"):
        if role in label.lower():
            return role
    return label


def _register_assessment(
    state: ProjectState, source: Event, provider: str
) -> tuple[ProjectState, tuple[Action, ...]]:
    if not state.active_run:
        return state, ()
    tool_name = str(source.payload.get("tool_name") or source.payload.get("tool") or "").lower()
    if "agent" not in tool_name:
        return state, ()
    marker = "SYMPHONY_ROUTE:"
    values = source.payload.get("tool_input") or source.payload.get("input") or {}
    texts = [str(values)]
    if isinstance(values, dict):
        texts = [str(value) for value in values.values() if isinstance(value, str)]
    line = next(
        (item.strip()[len(marker) :].strip() for text in texts for item in text.splitlines() if item.strip().startswith(marker)),
        "",
    )
    if not line:
        return state, ()
    try:
        raw = json.loads(line)
        assessment = Assessment(
            str(raw["size"]),
            str(raw["complexity"]),
            str(raw.get("risk", "normal")),
            str(raw.get("rationale", "")),
            str(raw.get("topology", "")),
        )
        route = route_for(assessment)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return state, (Action("inject_context", {"text": "Symphony rejected an invalid SYMPHONY_ROUTE marker."}),)

    route_data = {
        "lead_tier": route.lead_tier,
        "lead_effort": route.lead_effort,
        "execution": route.execution,
        "consultation": route.consultation,
        "independent_review": route.independent_review,
    }
    snapshot = next((item for item in reversed(state.capabilities) if item.provider == provider), None)
    if snapshot:
        resolved = resolve_tier(route, snapshot)
        route_data.update(
            {
                "lead_model": resolved.lead_model,
                "lead_effort": resolved.lead_effort,
                "degraded": resolved.degraded,
            }
        )
    elif isinstance(values, dict):
        if values.get("model"):
            route_data["lead_model"] = str(values["model"])
        if values.get("reasoning_effort") or values.get("model_reasoning_effort"):
            route_data["lead_effort"] = str(
                values.get("reasoning_effort") or values.get("model_reasoning_effort")
            )
    accepted = {
        "size": assessment.size,
        "complexity": assessment.complexity,
        "risk": assessment.risk,
        "rationale": assessment.rationale,
        "topology": assessment.topology or route.execution,
        "route": route_data,
    }
    return reduce(state, _derived(source, "assessment_accepted", accepted, "assessment"))


def _prompt_stop_actions(actions: tuple[Action, ...]) -> tuple[Action, ...]:
    converted = []
    for action in actions:
        if action.kind == "block_stop":
            active = ", ".join(map(str, action.payload.get("active", ())))
            reason = action.payload.get("reason") or f"active work remains: {active}"
            converted.append(Action("inject_context", {"text": f"Symphony stop is blocked: {reason}."}))
        elif action.kind == "permit_stop":
            converted.append(Action("inject_context", {"text": "Symphony has no active work to stop."}))
        else:
            converted.append(action)
    return tuple(converted)


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
        elif action.kind == "stop_delegations":
            identities = ", ".join(map(str, action.payload.get("active", ())))
            rendered.append(Action("inject_context", {"text": f"Stop these tracked Symphony agents, then let lifecycle hooks reconcile them: {identities}."}))
        elif action.kind == "replace_lead":
            rendered.append(Action("inject_context", {"text": "The observed lead is unavailable. Spawn one safe replacement at the recorded owner generation."}))
        elif action.kind == "route_run":
            rendered.append(Action("inject_context", {"text": "Symphony accepted the assessed route. Spawn only the selected lead and keep the root thin."}))
    return tuple(rendered)


def _assessment_guidance(task: str) -> str:
    return (
        "Symphony owns execution topology. Keep the root thin. Assess this bounded task with a strongest/high "
        "assessor, then select the lead mechanically from the nine-cell matrix; the assessor must not become the "
        "lead. Before spawning that lead, include one exact line in its task: "
        "SYMPHONY_ROUTE: {\"size\":\"small|medium|large\",\"complexity\":\"simple|mixed|complex\","
        "\"risk\":\"normal|high\",\"rationale\":\"...\",\"topology\":\"...\"}. "
        f"Task: {task}"
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
    label = item.role
    if item.requested_tier and item.requested_effort:
        label += f" [{item.requested_tier}/{item.requested_effort}]"
    elif item.requested_tier:
        label += f" [{item.requested_tier}]"
    elif item.requested_effort:
        label += f" [effort={item.requested_effort}]"
    result = f"- {item.state}: {label} — {item.identity}"
    if item.objective:
        result += f" — {item.objective}"
    if item.tokens is not None:
        result += f" — tokens {item.tokens}"
    if item.duration_seconds is not None:
        result += f" — duration {item.duration_seconds:g}s"
    return result


def _status(state: ProjectState, include_history: bool, provider: str = "") -> str:
    activation = state.activation.get(provider, {}) if provider else next(iter(state.activation.values()), {})
    lines = [
        f"Symphony: {'enabled' if state.enabled else 'disabled'}",
        f"Hooks: {activation.get('state', 'pending verification')}",
    ]
    runs = ([state.active_run] if state.active_run else []) + (list(state.recent_runs) if include_history else [])
    if state.active_run:
        lines.append(f"Run: {state.active_run.run_id} ({state.active_run.status})")
        assessment = state.active_run.assessment
        if assessment.get("size") and assessment.get("complexity"):
            lines.append(f"Assessment: {assessment['size']}/{assessment['complexity']}")
        route = assessment.get("route", {})
        if not isinstance(route, Mapping):
            route = {}
        topology = assessment.get("topology") or route.get("execution")
        if topology:
            lines.append(f"Topology: {topology}")
        model = route.get("lead_model") or route.get("lead_tier")
        effort = route.get("lead_effort")
        if model:
            lines.append(f"Lead route: {model}{f'/{effort}' if effort else ''}")
        if state.active_run.lead_identity:
            lines.append(f"Lead: {state.active_run.lead_identity}")
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
        return "Symphony controls: /symphony:enable, /symphony:start, /symphony:bypass, /symphony:disable, /symphony:status, /symphony:agents, /symphony:reassess, /symphony:stop, /symphony:help."
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
