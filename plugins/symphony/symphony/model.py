"""Provider-neutral Symphony lifecycle records."""

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class Event:
    event_id: str
    kind: str
    observed_at: str
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Action:
    kind: str
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Delegation:
    identity: str
    role: str
    objective: str
    state: str
    requested_tier: str
    requested_effort: str
    updated_at: str = ""
    tokens: int | None = None
    duration_seconds: float | None = None


@dataclass(frozen=True)
class RunState:
    run_id: str
    task: str
    status: str = "active"
    owner_generation: int = 1
    lead_identity: str | None = None
    assessment: Mapping[str, Any] = field(default_factory=dict)
    delegations: tuple[Delegation, ...] = ()
    outcome: Mapping[str, Any] | None = None
    started_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class CapabilitySnapshot:
    provider: str
    available_models: tuple[str, ...]
    supported_efforts: Mapping[str, tuple[str, ...]]
    tiers: Mapping[str, str]
    source: str
    provider_version: str | None
    refreshed_at: str


@dataclass(frozen=True)
class MemoryStatus:
    enabled: bool = False
    reason: str = "not_probed"
    indexed_at: str | None = None


@dataclass(frozen=True)
class ProjectState:
    schema_version: int = 1
    enabled: bool = False
    configuration: Mapping[str, Any] = field(default_factory=dict)
    activation: Mapping[str, Any] = field(default_factory=dict)
    capabilities: tuple[CapabilitySnapshot, ...] = ()
    active_run: RunState | None = None
    recent_runs: tuple[RunState, ...] = ()
    event_history: tuple[Event, ...] = ()
    needs_reassessment: bool = False
    memory: MemoryStatus = field(default_factory=MemoryStatus)
    capability_suggestions: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
