"""Fixed task routing with provider-specific capability resolution."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from .model import CapabilitySnapshot


TIERS = ("economy", "balanced", "capable", "strongest")
EFFORTS = ("low", "medium", "high", "xhigh", "max", "ultra")


@dataclass(frozen=True)
class Assessment:
    size: str
    complexity: str
    risk: str = "normal"
    rationale: str = ""
    topology: str = ""


@dataclass(frozen=True)
class Route:
    lead_tier: str
    lead_effort: str
    execution: str
    consultation: str
    independent_review: bool = False


@dataclass(frozen=True)
class ResolvedRoute(Route):
    lead_model: str = ""
    degraded: bool = False


MATRIX = {
    ("small", "simple"): Route("capable", "medium", "direct", "none"),
    ("small", "mixed"): Route("capable", "high", "direct", "optional"),
    ("small", "complex"): Route("strongest", "high", "direct", "independent-check", True),
    ("medium", "simple"): Route("balanced", "medium", "mixed", "none"),
    ("medium", "mixed"): Route("balanced", "high", "mixed", "optional"),
    ("medium", "complex"): Route("capable", "high", "mixed", "reserved"),
    ("large", "simple"): Route("economy", "low", "delegated", "none"),
    ("large", "mixed"): Route("economy", "medium", "delegated", "reserved"),
    ("large", "complex"): Route("economy", "medium", "delegated", "strongest"),
}

_FALLBACK_MODELS = {
    "codex": {
        "economy": "gpt-5.6-luna",
        "balanced": "gpt-5.6-terra",
        "capable": "gpt-5.6-sol",
        "strongest": "gpt-6-astra",
    },
    "claude": {
        "economy": "haiku",
        "balanced": "sonnet",
        "capable": "opus",
        "strongest": "opus",
    },
}


def route_for(assessment: Assessment) -> Route:
    """Return the literal matrix route, applying only risk safeguards."""
    try:
        route = MATRIX[(assessment.size, assessment.complexity)]
    except KeyError as error:
        raise ValueError(f"unsupported assessment: {assessment.size}/{assessment.complexity}") from error
    if assessment.risk == "high":
        effort = "medium" if route.lead_effort == "low" else route.lead_effort
        return replace(route, lead_effort=effort, independent_review=True)
    return route


def fallback_snapshot(provider: str) -> CapabilitySnapshot:
    """Return the shipped model map used until a provider capability refresh succeeds."""
    tiers = _FALLBACK_MODELS[provider]
    models = tuple(dict.fromkeys(tiers.values()))
    return CapabilitySnapshot(
        provider=provider,
        available_models=models,
        supported_efforts={model: ("low", "medium", "high") for model in models},
        tiers=tiers,
        source="shipped-fallback",
        provider_version=None,
        refreshed_at=datetime.now(UTC).isoformat(),
    )


def resolve_tier(route: Route, snapshot: CapabilitySnapshot) -> ResolvedRoute:
    """Resolve an abstract tier to the least capable declared matching model."""
    requested = TIERS.index(route.lead_tier)
    candidates = (
        snapshot.tiers[tier]
        for tier in TIERS[requested:]
        if tier in snapshot.tiers and snapshot.tiers[tier] in snapshot.available_models
    )
    model = next(candidates, snapshot.available_models[-1] if snapshot.available_models else "")
    supported = snapshot.supported_efforts.get(model, ())
    effort = _supported_effort(route.lead_effort, supported)
    return ResolvedRoute(
        route.lead_tier,
        effort,
        route.execution,
        route.consultation,
        route.independent_review,
        model,
        not model or model != snapshot.tiers.get(route.lead_tier) or effort != route.lead_effort,
    )


def _supported_effort(requested: str, supported: tuple[str, ...]) -> str:
    if requested in supported:
        return requested
    if not supported:
        return requested
    target = EFFORTS.index(requested) if requested in EFFORTS else len(EFFORTS)
    lower = [effort for effort in supported if effort in EFFORTS and EFFORTS.index(effort) <= target]
    return max(lower, key=EFFORTS.index) if lower else min(supported, key=EFFORTS.index)


def snapshot_is_stale(
    snapshot: CapabilitySnapshot,
    now: datetime | None = None,
    max_age: timedelta = timedelta(hours=24),
) -> bool:
    refreshed = datetime.fromisoformat(snapshot.refreshed_at)
    if refreshed.tzinfo is None:
        refreshed = refreshed.replace(tzinfo=UTC)
    return (now or datetime.now(UTC)) - refreshed > max_age
