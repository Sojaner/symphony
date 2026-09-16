"""Pure lifecycle transitions for provider-neutral Symphony events."""

from collections import deque
from collections.abc import Callable
from dataclasses import replace

from .model import Action, Delegation, Event, ProjectState, RunState


_ACTIVE_STATES = {"active", "created", "pending", "running", "waiting", "working"}
_EVENT_HISTORY_LIMIT = 200


def _active_identities(run: RunState) -> list[str]:
    return [item.identity for item in run.delegations if item.state in _ACTIVE_STATES]


def _archive(state: ProjectState, run: RunState, status: str, observed_at: str) -> ProjectState:
    archived = replace(run, status=status, updated_at=observed_at)
    return replace(state, active_run=None, recent_runs=(*state.recent_runs, archived)[-20:])


def _heartbeat(state: ProjectState, event: Event):
    provider = event.payload.get("provider")
    if not provider:
        return state, ()
    activation = dict(state.activation)
    facts = {
        "state": "guarded",
        "session_id": event.payload.get("session_id"),
        "plugin_version": event.payload.get("plugin_version"),
        "plugin_root": event.payload.get("plugin_root"),
        "hook_schema_version": event.payload.get("hook_schema_version"),
        "observed_at": event.observed_at,
    }
    activation[provider] = {key: value for key, value in facts.items() if value is not None}
    return replace(state, activation=activation), ()


def _enable(state: ProjectState, event: Event):
    if state.enabled:
        return state, ()
    return replace(state, enabled=True, needs_reassessment=True), (Action("project_enabled"),)


def _disable(state: ProjectState, event: Event):
    actions: list[Action] = []
    next_state = state
    if state.active_run:
        active = _active_identities(state.active_run)
        if active:
            actions.append(Action("stop_delegations", {"active": active}))
            stopping = replace(state.active_run, status="stopping", updated_at=event.observed_at)
            next_state = replace(state, active_run=stopping)
        else:
            actions.append(Action("archive_run", {"run_id": state.active_run.run_id}))
            next_state = _archive(state, state.active_run, "disabled", event.observed_at)
    if next_state.enabled:
        actions.append(Action("project_disabled"))
    return replace(next_state, enabled=False), tuple(actions)


def _bypass(state: ProjectState, event: Event):
    task = event.payload.get("task")
    return state, (Action("execute_bypass", {"task": task}),)


def _task_received(state: ProjectState, event: Event):
    if not state.enabled and not event.payload.get("one_shot", False):
        return state, ()
    if state.active_run:
        return state, (Action("run_already_active", {"run_id": state.active_run.run_id}),)
    run_id = str(event.payload.get("run_id") or event.event_id)
    run = RunState(
        run_id=run_id,
        task=str(event.payload.get("task") or ""),
        status="assessing",
        started_at=event.observed_at,
        updated_at=event.observed_at,
    )
    return replace(state, active_run=run), (Action("request_assessment", {"run_id": run_id}),)


def _assessment_requested(state: ProjectState, event: Event):
    if not state.active_run:
        return state, ()
    run = replace(state.active_run, status="assessing", updated_at=event.observed_at)
    return replace(state, active_run=run), (Action("spawn_assessor", {"run_id": run.run_id}),)


def _assessment_accepted(state: ProjectState, event: Event):
    if not state.active_run:
        return state, ()
    run = replace(
        state.active_run,
        status="assessed",
        assessment=dict(event.payload),
        updated_at=event.observed_at,
    )
    return (
        replace(state, active_run=run, needs_reassessment=False),
        (Action("route_run", {"run_id": run.run_id}),),
    )


def _lead_started(state: ProjectState, event: Event):
    run = state.active_run
    identity = event.payload.get("identity")
    if not run or not identity:
        return state, ()

    requested_generation = int(event.payload.get("owner_generation", run.owner_generation))
    replacing = identity != run.lead_identity and (
        run.lead_identity is not None or run.status in {"interrupted", "recovering"}
    )
    safe = run.status in {"interrupted", "recovering"} or bool(event.payload.get("safe_boundary"))
    if replacing and (not safe or requested_generation != run.owner_generation + 1):
        return state, (Action("reject_lead_replacement", {"identity": identity}),)
    if not replacing and requested_generation != run.owner_generation:
        return state, (Action("ignore_stale_owner", {"identity": identity}),)

    run = replace(
        run,
        lead_identity=str(identity),
        owner_generation=requested_generation,
        status="active",
        updated_at=event.observed_at,
    )
    return replace(state, active_run=run), ()


def _delegation_updated(state: ProjectState, event: Event):
    run = state.active_run
    identity = event.payload.get("identity")
    if not run or not identity:
        return state, ()
    current = next((item for item in run.delegations if item.identity == identity), None)
    item = Delegation(
        identity=str(identity),
        role=str(event.payload.get("role") or (current.role if current else "worker")),
        objective=str(event.payload.get("objective") or (current.objective if current else "")),
        state=str(event.payload.get("state") or (current.state if current else "pending")),
        requested_tier=str(event.payload.get("requested_tier") or (current.requested_tier if current else "")),
        requested_effort=str(
            event.payload.get("requested_effort") or (current.requested_effort if current else "")
        ),
        updated_at=event.observed_at,
        tokens=event.payload.get("tokens", current.tokens if current else None),
        duration_seconds=event.payload.get(
            "duration_seconds", current.duration_seconds if current else None
        ),
    )
    delegations = tuple(existing for existing in run.delegations if existing.identity != identity) + (item,)
    updated = replace(run, delegations=delegations, updated_at=event.observed_at)
    if updated.status == "stopping" and not _active_identities(updated):
        return _archive(state, updated, "disabled", event.observed_at), (
            Action("archive_run", {"run_id": updated.run_id}),
        )
    return replace(state, active_run=updated), ()


def _lead_completed(state: ProjectState, event: Event):
    run = state.active_run
    if not run:
        return state, ()
    identity = event.payload.get("identity")
    generation = int(event.payload.get("owner_generation", run.owner_generation))
    if identity != run.lead_identity or generation != run.owner_generation:
        return state, (Action("ignore_stale_owner", {"identity": identity}),)

    outcome = event.payload.get("outcome")
    if outcome is None:
        return state, (Action("block_completion", {"reason": "outcome_missing"}),)
    active = [item for item in _active_identities(run) if item != identity]
    completed = replace(run, outcome=dict(outcome or {}), updated_at=event.observed_at)
    if active:
        completed = replace(completed, status="completing")
        return replace(state, active_run=completed), (Action("wait_for_delegations", {"active": active}),)
    next_state = _archive(state, completed, "completed", event.observed_at)
    return next_state, (Action("permit_completion", {"run_id": run.run_id}),)


def _lead_failed(state: ProjectState, event: Event):
    run = state.active_run
    if not run or event.payload.get("identity") != run.lead_identity:
        return state, ()
    recovering = replace(run, status="recovering", updated_at=event.observed_at)
    return replace(state, active_run=recovering), (
        Action("replace_lead", {"owner_generation": run.owner_generation + 1}),
    )


def _interrupt(state: ProjectState, event: Event):
    if not state.active_run:
        return state, ()
    run = replace(state.active_run, status="interrupted", updated_at=event.observed_at)
    return (
        replace(state, active_run=run),
        (Action("preserve_recovery_context", {"run_id": run.run_id}),),
    )


def _resume_reconciled(state: ProjectState, event: Event):
    run = state.active_run
    if not run:
        return state, ()
    active_ids = {str(identity) for identity in event.payload.get("active_ids", ())}
    delegations = tuple(
        item
        if item.state not in _ACTIVE_STATES or item.identity in active_ids
        else replace(item, state="interrupted", updated_at=event.observed_at)
        for item in run.delegations
    )
    if run.lead_identity and run.lead_identity in active_ids:
        resumed = replace(run, status="active", delegations=delegations, updated_at=event.observed_at)
        return replace(state, active_run=resumed), ()
    recovering = replace(run, status="recovering", delegations=delegations, updated_at=event.observed_at)
    return (
        replace(state, active_run=recovering),
        (Action("replace_lead", {"owner_generation": run.owner_generation + 1}),),
    )


def _reassess(state: ProjectState, event: Event):
    next_state = replace(state, needs_reassessment=True)
    if not state.active_run:
        return next_state, ()
    return next_state, (Action("request_assessment", {"run_id": state.active_run.run_id}),)


def _stop_requested(state: ProjectState, event: Event):
    run = state.active_run
    if not run:
        return state, (Action("permit_stop"),)
    active = _active_identities(run)
    if active:
        return state, (Action("block_stop", {"active": active}),)
    if run.outcome is None:
        return state, (Action("block_stop", {"reason": "lead_outcome_missing"}),)
    next_state = _archive(state, run, "completed", event.observed_at)
    return (
        next_state,
        (Action("archive_run", {"run_id": run.run_id}), Action("permit_stop")),
    )


def _force_stop(state: ProjectState, event: Event):
    if not state.active_run:
        return state, (Action("permit_stop"),)
    run = state.active_run
    actions: list[Action] = []
    active = _active_identities(run)
    if active:
        actions.append(Action("stop_delegations", {"active": active}))
    actions.extend((Action("archive_run", {"run_id": run.run_id}), Action("permit_stop")))
    return _archive(state, run, "force_stopped", event.observed_at), tuple(actions)


_Handler = Callable[[ProjectState, Event], tuple[ProjectState, tuple[Action, ...]]]
_HANDLERS: dict[str, _Handler] = {
    "session_heartbeat": _heartbeat,
    "enable": _enable,
    "disable": _disable,
    "bypass": _bypass,
    "task_received": _task_received,
    "assessment_requested": _assessment_requested,
    "assessment_accepted": _assessment_accepted,
    "lead_started": _lead_started,
    "delegation_updated": _delegation_updated,
    "lead_completed": _lead_completed,
    "lead_failed": _lead_failed,
    "interrupt": _interrupt,
    "resume_reconciled": _resume_reconciled,
    "reassess": _reassess,
    "stop_requested": _stop_requested,
    "force_stop": _force_stop,
}


def reduce(state: ProjectState, event: Event) -> tuple[ProjectState, tuple[Action, ...]]:
    """Apply one canonical event without side effects."""
    if any(record.event_id == event.event_id for record in state.event_history):
        if event.kind == "stop_requested":
            return _stop_requested(state, event)
        return state, ()
    handler = _HANDLERS.get(event.kind)
    if handler is None:
        return state, ()

    next_state, actions = handler(state, event)
    if next_state == state and not actions:
        return state, ()
    history = tuple(deque((*next_state.event_history, event), maxlen=_EVENT_HISTORY_LIMIT))
    return replace(next_state, event_history=history), actions
