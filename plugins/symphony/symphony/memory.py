"""Bounded, provider-neutral project context helpers."""

from dataclasses import replace
from datetime import datetime
from pathlib import Path
import re
from typing import Mapping

from .model import CapabilitySnapshot, MemoryStatus, ProjectState
from .routing import snapshot_is_stale


ALLOWED_CONTEXT_SECTIONS = (
    "current_goals",
    "settled_decisions_and_sources",
    "constraints_and_safety_boundaries",
    "stable_architecture_facts",
    "verified_outcomes",
    "unresolved_risks",
    "concise_continuation_state",
)

_SECRET_PATTERNS = (
    re.compile(
        r"(?i)\b(?:password|passwd|secret|api[_-]?key|access[_-]?token|refresh[_-]?token)\b\s*[:=]\s*\S+"
    ),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----"),
)


def context_update_path(project: str | Path, status: MemoryStatus = MemoryStatus()) -> Path | None:
    """Return the sole context target after a recorded healthy index probe."""
    if not (status.enabled and status.reason == "healthy" and status.indexed_at):
        return None
    return Path(project) / ".symphony" / "context.md"


def validated_context_sections(sections: Mapping[str, str]) -> dict[str, str]:
    """Copy a curated update or reject content unsafe for persistent memory."""
    unknown = set(sections) - set(ALLOWED_CONTEXT_SECTIONS)
    if unknown:
        raise ValueError(f"unsupported context sections: {', '.join(sorted(unknown))}")
    for content in sections.values():
        if any(pattern.search(content) for pattern in _SECRET_PATTERNS):
            raise ValueError("context updates must not contain secrets")
    return dict(sections)


def capability_refresh_due(snapshot: CapabilitySnapshot, now: datetime | None = None) -> bool:
    return snapshot_is_stale(snapshot, now)


def record_missing_capability_suggestion(
    state: ProjectState,
    capability: str,
    version: str,
) -> tuple[ProjectState, bool]:
    """Record one suggestion per missing capability and Symphony version."""
    seen = state.capability_suggestions.get(capability, ())
    if version in seen:
        return state, False
    suggestions = dict(state.capability_suggestions)
    suggestions[capability] = (*seen, version)
    return replace(state, capability_suggestions=suggestions), True
