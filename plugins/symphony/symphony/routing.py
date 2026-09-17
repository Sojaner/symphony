"""Fixed task routing with provider-specific capability resolution."""

from dataclasses import dataclass, replace
from functools import lru_cache
import json
from pathlib import Path

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

PROFILES_PATH = Path(__file__).resolve().parent.parent / "profiles.json"


@lru_cache(maxsize=1)
def _profiles() -> dict:
    """The shipped tier-to-model profiles, maintained at release time."""
    return json.loads(PROFILES_PATH.read_text(encoding="utf-8"))["providers"]


def profiles_for(provider: str) -> tuple[dict, ...]:
    """Every shipped profile for a provider, best first, floor last."""
    return tuple(_profiles()[provider]["profiles"])


def snapshot_for(provider: str, profile_id: str | None = None) -> CapabilitySnapshot:
    """The capability snapshot for one entitlement profile.

    With no profile named, the last profile applies: it is the conservative
    floor, so an account whose entitlement could not be probed is never routed
    to a model it may not be able to run.
    """
    profiles = profiles_for(provider)
    profile = next(
        (item for item in profiles if item["id"] == profile_id),
        profiles[-1],
    )
    tiers = dict(profile["tiers"])
    efforts = {model: tuple(levels) for model, levels in profile["efforts"].items()}
    return CapabilitySnapshot(
        provider=provider,
        available_models=tuple(dict.fromkeys(tiers.values())),
        supported_efforts=efforts,
        tiers=tiers,
        source=f"profile:{profile['id']}",
        provider_version=None,
        refreshed_at=_profiles_generated_at(),
    )


def _profiles_generated_at() -> str:
    return json.loads(PROFILES_PATH.read_text(encoding="utf-8")).get("generated_at", "")


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


def resolve_tier(route: Route, snapshot: CapabilitySnapshot) -> dict[str, object]:
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
    return {
        "lead_tier": route.lead_tier,
        "lead_effort": effort,
        "execution": route.execution,
        "consultation": route.consultation,
        "independent_review": route.independent_review,
        "lead_model": model,
        "degraded": (
            not model
            or model != snapshot.tiers.get(route.lead_tier)
            or effort != route.lead_effort
        ),
    }


def _supported_effort(requested: str, supported: tuple[str, ...]) -> str:
    if requested in supported:
        return requested
    if not supported:
        return requested
    target = EFFORTS.index(requested) if requested in EFFORTS else len(EFFORTS)
    lower = [effort for effort in supported if effort in EFFORTS and EFFORTS.index(effort) <= target]
    return max(lower, key=EFFORTS.index) if lower else min(supported, key=EFFORTS.index)
