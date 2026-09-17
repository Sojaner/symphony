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
from .routing import Assessment, fallback_snapshot, route_for, resolve_tier
from .store import StateStore


CONTROLS = {"agents", "bypass", "disable", "enable", "help", "reassess", "start", "status", "stop"}
ROLES = {"assessor", "consultant", "lead", "worker"}
HIGH_EFFORTS = {"high", "xhigh", "max", "ultra"}


def handle(payload: dict, environ: Mapping[str, str] = os.environ) -> HookResult:
    provider = str(environ.get("SYMPHONY_PROVIDER") or detect_provider(payload))
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
        state, delegation_actions = _prepare_delegation(state, source, provider)
        actions += delegation_actions
    elif source.kind in {"subagent_started", "subagent_stopped"}:
        state, observed_actions = _observe_delegation(state, source)
        if provider == "claude" and source.kind == "subagent_stopped":
            state = _defer_parent_actions(state, observed_actions)
        else:
            actions += observed_actions
    elif source.kind == "post_tool_use" and provider == "claude":
        state, parent_actions = _consume_parent_actions(state)
        actions += parent_actions
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
    current = next(
        (item for item in state.active_run.delegations if item.identity == str(identity)),
        None,
    )
    pending: Mapping[str, object] = {}
    if source.kind == "subagent_started" and current is None:
        state, pending = _consume_pending_delegation(state, source.payload)
    terminal = source.kind == "subagent_stopped"
    status = str(source.payload.get("status") or ("completed" if terminal else "working"))
    role = str(pending.get("role") or _observed_role(source.payload) or (current.role if current else "worker"))
    if role == "lead" and source.kind == "subagent_started":
        owner_generation = state.active_run.owner_generation
        if (
            state.active_run.status in {"interrupted", "recovering"}
            and state.active_run.lead_identity
            and state.active_run.lead_identity != str(identity)
        ):
            owner_generation += 1
        state, actions = reduce(
            state,
            _derived(
                source,
                "lead_started",
                {"identity": str(identity), "owner_generation": owner_generation},
                "lead",
            ),
        )
    else:
        actions = ()
    update = {
        "identity": str(identity),
        "role": role,
        "objective": str(
            pending.get("objective")
            or source.payload.get("task")
            or source.payload.get("objective")
            or ""
        ),
        "state": status,
    }
    child_metadata = source.payload.get("_symphony_child_metadata", ())
    model = pending.get("model") or (
        source.payload.get("model")
        if "model" in child_metadata or not current or not current.requested_tier
        else current.requested_tier
    )
    effort = pending.get("effort") or (
        source.payload.get("model_reasoning_effort")
        if "model_reasoning_effort" in child_metadata or not current or not current.requested_effort
        else current.requested_effort
    )
    if model:
        update["requested_tier"] = str(model)
    if effort:
        update["requested_effort"] = str(effort)
    tokens = source.payload.get("tokens")
    if isinstance(tokens, int) and not isinstance(tokens, bool):
        update["tokens"] = tokens
    duration = source.payload.get("duration_seconds")
    if isinstance(duration, (int, float)) and not isinstance(duration, bool):
        update["duration_seconds"] = duration
    state, delegation_actions = reduce(state, _derived(source, "delegation_updated", update, "delegation"))
    actions += delegation_actions
    if role == "assessor" and terminal and state.active_run:
        observed_assessor = next(
            (
                item
                for item in state.active_run.delegations
                if item.identity == str(identity)
            ),
            None,
        )
        if not observed_assessor or observed_assessor.requested_effort not in HIGH_EFFORTS:
            actions += (
                Action(
                    "inject_context",
                    {
                        "text": "The assessor result was not produced at high effort or above. Retry the assessment with an explicitly strong/high assessor before selecting a lead."
                    },
                ),
            )
        else:
            assessment = _assessment_marker(source.payload.get("last_assistant_message", ""))
            if assessment is None:
                actions += (
                    Action(
                        "inject_context",
                        {
                            "text": "The assessor finished without a valid SYMPHONY_ASSESSMENT line. Retry the assessment before selecting a lead."
                        },
                    ),
                )
            else:
                state, assessment_actions = _accept_assessment(
                    state,
                    source,
                    str(source.payload.get("provider") or "codex"),
                    {},
                    assessment,
                )
                actions += assessment_actions
    if role == "consultant" and terminal and state.active_run:
        decisions = _decision_markers(source.payload.get("last_assistant_message", ""))
        state = _set_invalid_consultant(state, str(identity), not decisions)
        if not decisions:
            actions += (
                Action(
                    "inject_context",
                    {
                        "text": "The consultant result is not actionable until every decision has a valid SYMPHONY_DECISION size/complexity line. Retry that consultant before completing the lead."
                    },
                ),
            )
    if role == "lead" and terminal and state.active_run:
        successful = status.lower() in {"completed", "done", "success", "succeeded"}
        assessment = state.active_run.assessment
        if successful and not (assessment.get("size") and assessment.get("complexity")):
            actions += (
                Action(
                    "inject_context",
                    {
                        "text": "Lead completion is waiting for an accepted assessment. Reconcile the assessor result before retrying the lead."
                    },
                ),
            )
            return state, actions
        route = assessment.get("route", {})
        required_model = route.get("lead_model") if isinstance(route, Mapping) else ""
        required_effort = route.get("lead_effort") if isinstance(route, Mapping) else ""
        observed_lead = next(
            (
                item
                for item in state.active_run.delegations
                if item.identity == str(identity)
            ),
            None,
        )
        if (
            successful
            and (required_model or required_effort)
            and observed_lead
            and (
                (required_model and observed_lead.requested_tier != required_model)
                or (required_effort and observed_lead.requested_effort != required_effort)
            )
        ):
            state, recovery_actions = reduce(
                state,
                _derived(
                    source,
                    "lead_failed",
                    {"identity": str(identity)},
                    "lead-route-recovery",
                ),
            )
            actions += recovery_actions
            actions += (
                Action(
                    "inject_context",
                    {
                        "text": f"Lead completion is waiting for the matrix-selected {required_model}/{required_effort}. Replace or retry the lead with the recorded route."
                    },
                ),
            )
            return state, actions
        invalid_consultants = state.active_run.assessment.get("_invalid_consultants", ())
        if successful and invalid_consultants:
            actions += (
                Action(
                    "inject_context",
                    {
                        "text": "Lead completion is waiting for classified consultant results: "
                        + ", ".join(map(str, invalid_consultants))
                    },
                ),
            )
            return state, actions
        completion_kind = "lead_completed" if successful else "lead_failed"
        outcome = {"status": status}
        message = str(source.payload.get("last_assistant_message") or "").strip()
        if message:
            outcome["message"] = message
        state, completion_actions = reduce(
            state,
            _derived(
                source,
                completion_kind,
                {
                    "identity": str(identity),
                    "owner_generation": state.active_run.owner_generation,
                    "outcome": outcome,
                },
                "lead-completion",
            ),
        )
        actions += completion_actions
    return state, actions


def _observed_role(payload: Mapping[str, object]) -> str:
    label = " ".join(
        str(payload.get(key) or "") for key in ("agent_type", "role", "task_name")
    )
    for role in ("assessor", "consultant", "lead", "worker"):
        if role in label.lower():
            return role
    return ""


def _prepare_delegation(
    state: ProjectState, source: Event, provider: str
) -> tuple[ProjectState, tuple[Action, ...]]:
    if not state.active_run:
        return state, ()
    tool_name = str(source.payload.get("tool_name") or source.payload.get("tool") or "").lower()
    if "agent" not in tool_name:
        return state, ()
    values = source.payload.get("tool_input") or source.payload.get("input") or {}
    role = _marker_value(values, "SYMPHONY_ROLE:")
    if role not in ROLES:
        return state, (_block_tool("Add exactly one SYMPHONY_ROLE: assessor|lead|worker|consultant line, then retry the spawn."),)
    model, effort = _requested_model_effort(values, provider, role)
    if not model or not effort:
        if provider == "claude":
            reason = (
                f"Use a Symphony agent type named symphony-{role}-<model>-<effort>; "
                "generic Claude agents cannot pin effort."
            )
        else:
            reason = f"Spawn the Symphony {role} with an explicit model and effort, then retry."
        return state, (_block_tool(reason),)
    if provider == "claude":
        override = str(values.get("model") or "").strip() if isinstance(values, dict) else ""
        if override and override != model:
            return state, (
                _block_tool(
                    f"Remove the Claude model override or use `{model}` so the packaged Symphony role remains observable."
                ),
            )
    if role == "assessor" and effort not in HIGH_EFFORTS:
        return state, (_block_tool("The Symphony assessor requires a strong model at high effort or above."),)

    actions: tuple[Action, ...] = ()
    if role == "lead":
        assessment = _route_marker(values)
        if assessment is None:
            return state, (_block_tool("Add a valid SYMPHONY_ROUTE JSON line to the lead packet, then retry."),)
        recorded = state.active_run.assessment
        if recorded.get("size") and recorded.get("complexity"):
            if (
                assessment.size != recorded.get("size")
                or assessment.complexity != recorded.get("complexity")
            ):
                return state, (
                    _block_tool("Use the accepted Symphony size/complexity route for this lead."),
                )
            route = route_for(
                Assessment(
                    str(recorded["size"]),
                    str(recorded["complexity"]),
                    str(recorded.get("risk", "normal")),
                    str(recorded.get("rationale", "")),
                    str(recorded.get("topology", "")),
                )
            )
        else:
            route = route_for(assessment)
        snapshot = next(
            (item for item in reversed(state.capabilities) if item.provider == provider),
            fallback_snapshot(provider),
        )
        recorded_route = recorded.get("route", {})
        required_model = (
            str(recorded_route.get("lead_model") or "")
            if isinstance(recorded_route, Mapping)
            else ""
        )
        required_effort = (
            str(recorded_route.get("lead_effort") or "")
            if isinstance(recorded_route, Mapping)
            else ""
        )
        if not required_model or not required_effort:
            resolved = resolve_tier(route, snapshot)
            required_model = required_model or resolved.lead_model
            required_effort = required_effort or resolved.lead_effort
        if model != required_model or effort != required_effort:
            return state, (
                _block_tool(
                    f"Spawn the selected lead as {required_model} at {required_effort} effort, then retry."
                ),
            )
        if not (recorded.get("size") and recorded.get("complexity")):
            state, actions = _accept_assessment(state, source, provider, values, assessment)
    elif role in {"worker", "consultant"} and not state.active_run.lead_identity:
        return state, (_block_tool(f"Register the selected lead before spawning a Symphony {role}."),)
    if role == "consultant" and _decision_marker(values) is None:
        return state, (_block_tool("Add SYMPHONY_DECISION JSON with decision-local size and complexity, then retry."),)

    objective = _tool_objective(values)
    state = _queue_pending_delegation(state, role, objective, model, effort)
    return state, actions


def _accept_assessment(
    state: ProjectState,
    source: Event,
    provider: str,
    values: object,
    assessment: Assessment,
) -> tuple[ProjectState, tuple[Action, ...]]:
    route = route_for(assessment)
    route_data = {
        "lead_tier": route.lead_tier,
        "lead_effort": route.lead_effort,
        "execution": route.execution,
        "consultation": route.consultation,
        "independent_review": route.independent_review,
    }
    snapshot = next(
        (item for item in reversed(state.capabilities) if item.provider == provider),
        fallback_snapshot(provider),
    )
    resolved = resolve_tier(route, snapshot)
    route_data.update(
        {
            "lead_model": resolved.lead_model,
            "lead_effort": resolved.lead_effort,
            "degraded": resolved.degraded,
        }
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


def _route_marker(values: object) -> Assessment | None:
    return _assessment_from_marker(values, "SYMPHONY_ROUTE:")


def _assessment_marker(values: object) -> Assessment | None:
    return _assessment_from_marker(values, "SYMPHONY_ASSESSMENT:")


def _assessment_from_marker(values: object, marker: str) -> Assessment | None:
    line = _marker_value(values, marker)
    if not line:
        return None
    try:
        raw = json.loads(line)
        assessment = Assessment(
            str(raw["size"]),
            str(raw["complexity"]),
            str(raw.get("risk", "normal")),
            str(raw.get("rationale", "")),
            str(raw.get("topology", "")),
        )
        route_for(assessment)
        return assessment
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _decision_marker(values: object) -> Mapping[str, object] | None:
    decisions = _decision_markers(values)
    return decisions[0] if decisions else None


def _decision_markers(values: object) -> tuple[Mapping[str, object], ...]:
    texts = [str(values)]
    if isinstance(values, dict):
        texts = [str(value) for value in values.values() if isinstance(value, str)]
    marker = "SYMPHONY_DECISION:"
    lines = [
        item.strip()[len(marker) :].strip()
        for text in texts
        for item in text.splitlines()
        if item.strip().startswith(marker)
    ]
    decisions = []
    for line in lines:
        try:
            raw = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            return ()
        if not isinstance(raw, dict):
            return ()
        if raw.get("size") not in {"small", "medium", "large"}:
            return ()
        if raw.get("complexity") not in {"simple", "mixed", "complex"}:
            return ()
        decisions.append(raw)
    return tuple(decisions)


def _marker_value(values: object, marker: str) -> str:
    texts = [str(values)]
    if isinstance(values, dict):
        texts = [str(value) for value in values.values() if isinstance(value, str)]
    return next(
        (
            item.strip()[len(marker) :].strip()
            for text in texts
            for item in text.splitlines()
            if item.strip().startswith(marker)
        ),
        "",
    )


def _requested_model_effort(values: object, provider: str, role: str) -> tuple[str, str]:
    if not isinstance(values, dict):
        return "", ""
    if provider == "claude":
        return _agent_label_model_effort(str(values.get("subagent_type") or ""), role)
    model = str(values.get("model") or "").strip()
    effort = str(
        values.get("reasoning_effort")
        or values.get("model_reasoning_effort")
        or values.get("effort")
        or ""
    ).strip()
    return model, effort


def _agent_label_model_effort(label: str, role: str) -> tuple[str, str]:
    agent_type = label.split(":")[-1]
    prefix = f"symphony-{role}-"
    if not agent_type.startswith(prefix):
        return "", ""
    setting = agent_type[len(prefix) :]
    model, separator, effort = setting.rpartition("-")
    return (
        (model, effort)
        if separator and effort in {"low", "medium", "high", "xhigh", "max"}
        else ("", "")
    )


def _tool_objective(values: object) -> str:
    if not isinstance(values, dict):
        return ""
    text = str(values.get("message") or values.get("prompt") or values.get("task") or "")
    return "\n".join(
        line for line in text.splitlines() if not line.strip().startswith("SYMPHONY_")
    ).strip()


def _block_tool(reason: str) -> Action:
    return Action("block_tool", {"reason": reason})


def _queue_pending_delegation(
    state: ProjectState,
    role: str,
    objective: str,
    model: str,
    effort: str,
) -> ProjectState:
    if not state.active_run:
        return state
    assessment = dict(state.active_run.assessment)
    pending = list(assessment.get("_pending_delegations", ()))
    pending.append(
        {
            "role": role,
            "objective": objective,
            "model": model,
            "effort": effort,
        }
    )
    # ponytail: bound unmatched host events; add ID correlation only if a provider exposes it.
    assessment["_pending_delegations"] = pending[-32:]
    return replace(state, active_run=replace(state.active_run, assessment=assessment))


def _consume_pending_delegation(
    state: ProjectState,
    payload: Mapping[str, object],
) -> tuple[ProjectState, Mapping[str, object]]:
    if not state.active_run:
        return state, {}
    assessment = dict(state.active_run.assessment)
    pending = list(assessment.get("_pending_delegations", ()))
    if not pending:
        return state, {}
    observed_role = _observed_role(payload)
    observed_model, observed_effort = _agent_label_model_effort(
        str(payload.get("agent_type") or ""), observed_role
    )
    matching = [
        index
        for index, item in enumerate(pending)
        if isinstance(item, Mapping)
        and item.get("role") == observed_role
        and (
            not observed_model
            or (
                item.get("model") == observed_model
                and item.get("effort") == observed_effort
            )
        )
    ]
    if matching:
        item = pending.pop(matching[0])
    elif len(pending) == 1 and not observed_role:
        item = pending.pop(0)
    else:
        return state, {}
    if pending:
        assessment["_pending_delegations"] = pending
    else:
        assessment.pop("_pending_delegations", None)
    state = replace(state, active_run=replace(state.active_run, assessment=assessment))
    return state, item if isinstance(item, Mapping) else {}


def _set_invalid_consultant(
    state: ProjectState, identity: str, invalid: bool
) -> ProjectState:
    if not state.active_run:
        return state
    assessment = dict(state.active_run.assessment)
    identities = set(map(str, assessment.get("_invalid_consultants", ())))
    if invalid:
        identities.add(identity)
    else:
        identities.discard(identity)
    if identities:
        assessment["_invalid_consultants"] = sorted(identities)
    else:
        assessment.pop("_invalid_consultants", None)
    return replace(state, active_run=replace(state.active_run, assessment=assessment))


def _defer_parent_actions(
    state: ProjectState, actions: tuple[Action, ...]
) -> ProjectState:
    if not state.active_run:
        return state
    visible = [
        {"kind": action.kind, "payload": dict(action.payload)}
        for action in actions
        if action.kind in {"inject_context", "replace_lead", "route_run"}
    ]
    if not visible:
        return state
    assessment = dict(state.active_run.assessment)
    pending = list(assessment.get("_pending_parent_actions", ()))
    assessment["_pending_parent_actions"] = (pending + visible)[-16:]
    return replace(state, active_run=replace(state.active_run, assessment=assessment))


def _consume_parent_actions(
    state: ProjectState,
) -> tuple[ProjectState, tuple[Action, ...]]:
    if not state.active_run:
        return state, ()
    assessment = dict(state.active_run.assessment)
    pending = assessment.pop("_pending_parent_actions", ())
    actions = tuple(
        Action(str(item.get("kind") or ""), dict(item.get("payload") or {}))
        for item in pending
        if isinstance(item, Mapping) and item.get("kind")
    )
    return (
        replace(state, active_run=replace(state.active_run, assessment=assessment)),
        actions,
    )


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
        if action.kind in {"inject_context", "block_stop", "block_tool"}:
            rendered.append(action)
        elif action.kind == "project_enabled":
            rendered.append(Action("inject_context", {"text": "Symphony is enabled for this project; hooks are guarded."}))
        elif action.kind == "project_disabled":
            rendered.append(Action("inject_context", {"text": "Symphony is disabled for future tasks in this project."}))
        elif action.kind == "request_assessment":
            task = state.active_run.task if state.active_run else "the task"
            rendered.append(Action("inject_context", {"text": _assessment_guidance(task, provider)}))
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


def _assessment_guidance(task: str, provider: str = "") -> str:
    codex = (
        "On Codex, use `fork_turns=\"none\"` for assessor and lead, name them "
        "`symphony_<role>_<model>_<effort>`, and require the assessor's final response to contain one exact "
        "`SYMPHONY_ASSESSMENT: {\"size\":\"...\",\"complexity\":\"...\",\"risk\":\"...\","
        "\"rationale\":\"...\",\"topology\":\"...\"}` line. "
        if provider == "codex"
        else ""
    )
    return (
        "Symphony owns execution topology. Keep the root thin. Spawn a strong/high assessor with explicit model "
        "and effort and put `SYMPHONY_ROLE: assessor` on its own line. Then select the lead mechanically from the "
        "nine-cell matrix; the assessor must not become the lead. Spawn the lead with explicit model and effort, "
        "put `SYMPHONY_ROLE: lead` on its own line, and include one exact line in its task: "
        "SYMPHONY_ROUTE: {\"size\":\"small|medium|large\",\"complexity\":\"simple|mixed|complex\","
        "\"risk\":\"normal|high\",\"rationale\":\"...\",\"topology\":\"...\"}. "
        "Every later worker or consultant spawn needs its matching SYMPHONY_ROLE line and explicit model/effort; "
        "consultants also need SYMPHONY_DECISION JSON with decision-local size and complexity. "
        f"{codex}Task: {task}"
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
