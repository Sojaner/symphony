"""Atomic JSON persistence for Symphony project state."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .model import Action, CapabilitySnapshot, Delegation, Event, MemoryStatus, ProjectState, RunState

try:
    import fcntl
except ImportError:  # pragma: no cover - exercised on platforms without fcntl
    fcntl = None


SCHEMA_VERSION = 1
_LOCAL_LOCKS: dict[str, threading.Lock] = {}
_LOCAL_LOCKS_GUARD = threading.Lock()


def project_key(project: Path) -> str:
    canonical = os.path.normcase(str(Path(project).resolve()))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _event_to_dict(event: Event) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "kind": event.kind,
        "observed_at": event.observed_at,
        "payload": dict(event.payload),
    }


def _event_from_dict(value: Any) -> Event:
    value = _object(value, "event")
    return Event(
        event_id=_text(value.get("event_id"), "event.event_id"),
        kind=_text(value.get("kind"), "event.kind"),
        observed_at=_text(value.get("observed_at"), "event.observed_at"),
        payload=_object(value.get("payload", {}), "event.payload"),
    )


def _action_to_dict(action: Action) -> dict[str, Any]:
    return {"kind": action.kind, "payload": dict(action.payload)}


def _action_from_dict(value: Any) -> Action:
    value = _object(value, "action")
    return Action(
        kind=_text(value.get("kind"), "action.kind"),
        payload=_object(value.get("payload", {}), "action.payload"),
    )


def _delegation_to_dict(delegation: Delegation) -> dict[str, Any]:
    return {
        "identity": delegation.identity,
        "role": delegation.role,
        "objective": delegation.objective,
        "state": delegation.state,
        "requested_tier": delegation.requested_tier,
        "requested_effort": delegation.requested_effort,
        "updated_at": delegation.updated_at,
        "tokens": delegation.tokens,
        "duration_seconds": delegation.duration_seconds,
    }


def _delegation_from_dict(value: Any) -> Delegation:
    value = _object(value, "delegation")
    tokens = value.get("tokens")
    duration = value.get("duration_seconds")
    if tokens is not None and (not isinstance(tokens, int) or isinstance(tokens, bool)):
        raise ValueError("delegation.tokens must be an integer or null")
    if duration is not None and (not isinstance(duration, (int, float)) or isinstance(duration, bool)):
        raise ValueError("delegation.duration_seconds must be a number or null")
    return Delegation(
        identity=_text(value.get("identity"), "delegation.identity"),
        role=_text(value.get("role"), "delegation.role"),
        objective=_text(value.get("objective"), "delegation.objective"),
        state=_text(value.get("state"), "delegation.state"),
        requested_tier=_text(value.get("requested_tier"), "delegation.requested_tier"),
        requested_effort=_text(value.get("requested_effort"), "delegation.requested_effort"),
        updated_at=_text(value.get("updated_at", ""), "delegation.updated_at"),
        tokens=tokens,
        duration_seconds=duration,
    )


def _run_to_dict(run: RunState) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "task": run.task,
        "status": run.status,
        "owner_generation": run.owner_generation,
        "lead_identity": run.lead_identity,
        "assessment": dict(run.assessment),
        "delegations": [_delegation_to_dict(item) for item in run.delegations],
        "events": [_event_to_dict(item) for item in run.events],
        "pending_actions": [_action_to_dict(item) for item in run.pending_actions],
        "outcome": None if run.outcome is None else dict(run.outcome),
        "started_at": run.started_at,
        "updated_at": run.updated_at,
    }


def _run_from_dict(value: Any) -> RunState:
    value = _object(value, "run")
    generation = value.get("owner_generation", 1)
    if not isinstance(generation, int) or isinstance(generation, bool):
        raise ValueError("run.owner_generation must be an integer")
    lead_identity = value.get("lead_identity")
    if lead_identity is not None:
        lead_identity = _text(lead_identity, "run.lead_identity")
    outcome = value.get("outcome")
    if outcome is not None:
        outcome = _object(outcome, "run.outcome")
    return RunState(
        run_id=_text(value.get("run_id"), "run.run_id"),
        task=_text(value.get("task"), "run.task"),
        status=_text(value.get("status", "active"), "run.status"),
        owner_generation=generation,
        lead_identity=lead_identity,
        assessment=_object(value.get("assessment", {}), "run.assessment"),
        delegations=tuple(_delegation_from_dict(item) for item in _array(value.get("delegations", ()), "run.delegations")),
        events=tuple(_event_from_dict(item) for item in _array(value.get("events", ()), "run.events")),
        pending_actions=tuple(
            _action_from_dict(item) for item in _array(value.get("pending_actions", ()), "run.pending_actions")
        ),
        outcome=outcome,
        started_at=_text(value.get("started_at", ""), "run.started_at"),
        updated_at=_text(value.get("updated_at", ""), "run.updated_at"),
    )


def _capability_to_dict(snapshot: CapabilitySnapshot) -> dict[str, Any]:
    return {
        "provider": snapshot.provider,
        "available_models": list(snapshot.available_models),
        "supported_efforts": {model: list(efforts) for model, efforts in snapshot.supported_efforts.items()},
        "tiers": dict(snapshot.tiers),
        "source": snapshot.source,
        "provider_version": snapshot.provider_version,
        "refreshed_at": snapshot.refreshed_at,
    }


def _capability_from_dict(value: Any) -> CapabilitySnapshot:
    value = _object(value, "capability")
    provider_version = value.get("provider_version")
    if provider_version is not None:
        provider_version = _text(provider_version, "capability.provider_version")
    efforts = _object(value.get("supported_efforts"), "capability.supported_efforts")
    tiers = _object(value.get("tiers"), "capability.tiers")
    return CapabilitySnapshot(
        provider=_text(value.get("provider"), "capability.provider"),
        available_models=tuple(
            _text(item, "capability.available_models item")
            for item in _array(value.get("available_models"), "capability.available_models")
        ),
        supported_efforts={
            _text(model, "capability model"): tuple(
                _text(item, "capability effort") for item in _array(items, "capability efforts")
            )
            for model, items in efforts.items()
        },
        tiers={_text(tier, "capability tier"): _text(model, "capability tier model") for tier, model in tiers.items()},
        source=_text(value.get("source"), "capability.source"),
        provider_version=provider_version,
        refreshed_at=_text(value.get("refreshed_at"), "capability.refreshed_at"),
    )


def _state_to_dict(state: ProjectState) -> dict[str, Any]:
    if state.schema_version != SCHEMA_VERSION:
        raise ValueError(f"cannot save schema version {state.schema_version}")
    return {
        "schema_version": SCHEMA_VERSION,
        "enabled": state.enabled,
        "configuration": dict(state.configuration),
        "activation": dict(state.activation),
        "capabilities": [_capability_to_dict(item) for item in state.capabilities],
        "active_run": None if state.active_run is None else _run_to_dict(state.active_run),
        "recent_runs": [_run_to_dict(item) for item in state.recent_runs[-20:]],
        "event_history": [_event_to_dict(item) for item in state.event_history],
        "needs_reassessment": state.needs_reassessment,
        "memory": {
            "enabled": state.memory.enabled,
            "reason": state.memory.reason,
            "indexed_at": state.memory.indexed_at,
        },
        "capability_suggestions": {
            capability: list(versions) for capability, versions in state.capability_suggestions.items()
        },
    }


def _state_from_dict(value: Any) -> ProjectState:
    value = _object(value, "state")
    version = value.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ValueError(f"unsupported schema version {version!r}")
    enabled = value.get("enabled", False)
    needs_reassessment = value.get("needs_reassessment", False)
    if not isinstance(enabled, bool) or not isinstance(needs_reassessment, bool):
        raise ValueError("state flags must be booleans")
    active_run = value.get("active_run")
    memory = _object(value.get("memory", {}), "state.memory")
    memory_enabled = memory.get("enabled", False)
    if not isinstance(memory_enabled, bool):
        raise ValueError("state.memory.enabled must be a boolean")
    indexed_at = memory.get("indexed_at")
    if indexed_at is not None:
        indexed_at = _text(indexed_at, "state.memory.indexed_at")
    suggestions = _object(value.get("capability_suggestions", {}), "state.capability_suggestions")
    return ProjectState(
        enabled=enabled,
        configuration=_object(value.get("configuration", {}), "state.configuration"),
        activation=_object(value.get("activation", {}), "state.activation"),
        capabilities=tuple(
            _capability_from_dict(item) for item in _array(value.get("capabilities", ()), "state.capabilities")
        ),
        active_run=None if active_run is None else _run_from_dict(active_run),
        recent_runs=tuple(
            _run_from_dict(item) for item in _array(value.get("recent_runs", ()), "state.recent_runs")[-20:]
        ),
        event_history=tuple(
            _event_from_dict(item) for item in _array(value.get("event_history", ()), "state.event_history")
        ),
        needs_reassessment=needs_reassessment,
        memory=MemoryStatus(
            enabled=memory_enabled,
            reason=_text(memory.get("reason", "not_probed"), "state.memory.reason"),
            indexed_at=indexed_at,
        ),
        capability_suggestions={
            _text(capability, "state capability suggestion"): tuple(
                _text(version, "state capability suggestion version")
                for version in _array(versions, "state capability suggestion versions")
            )
            for capability, versions in suggestions.items()
        },
    )


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _array(value: Any, name: str) -> list[Any] | tuple[Any, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{name} must be an array")
    return value


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _local_lock(path: Path) -> threading.Lock:
    key = str(path)
    with _LOCAL_LOCKS_GUARD:
        # ponytail: fallback locks remain for process lifetime; replace with weak locks if Windows projects become unbounded.
        return _LOCAL_LOCKS.setdefault(key, threading.Lock())


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fcntl is None:  # pragma: no cover - exercised on platforms without fcntl
        with _local_lock(path):
            yield
        return

    with path.with_name(path.name + ".lock").open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


class StateStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, project: Path) -> Path:
        return self.root / f"{project_key(project)}.json"

    def load(self, project: Path) -> ProjectState:
        path = self._path(project)
        with _locked(path):
            if not path.exists():
                return ProjectState()
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                raw = _object(raw, "state")
                if raw.get("schema_version") == SCHEMA_VERSION:
                    return _state_from_dict(raw)
                return self._migrate(path, raw)
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                self._archive(path, "corrupt")
                rebuilt = ProjectState(needs_reassessment=True)
                self._write(path, rebuilt)
                return rebuilt

    def save(self, project: Path, state: ProjectState) -> None:
        path = self._path(project)
        with _locked(path):
            self._write(path, replace(state, recent_runs=state.recent_runs[-20:]))

    def _migrate(self, path: Path, raw: dict[str, Any]) -> ProjectState:
        enabled = raw.get("enabled", False)
        configuration = raw.get("configuration", {})
        if not isinstance(enabled, bool):
            raise ValueError("legacy enabled flag must be a boolean")
        configuration = _object(configuration, "legacy configuration")
        migrated = ProjectState(
            enabled=enabled,
            configuration=configuration,
            needs_reassessment=True,
        )
        self._archive(path, "pre-1.0")
        self._write(path, migrated)
        return migrated

    @staticmethod
    def _archive(path: Path, reason: str) -> None:
        os.replace(path, path.with_name(f"{path.name}.{reason}-{_timestamp()}"))

    @staticmethod
    def _write(path: Path, state: ProjectState) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(_state_to_dict(state), handle, sort_keys=True, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
