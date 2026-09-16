"""Translate provider hook payloads to and from Symphony's core model."""

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
from typing import Any

from .model import Action, Event


EVENT_KINDS = {
    "SessionStart": "session_heartbeat",
    "UserPromptSubmit": "user_prompt",
    "PreToolUse": "pre_tool_use",
    "SubagentStart": "subagent_started",
    "SubagentStop": "subagent_stopped",
    "PostToolUse": "post_tool_use",
    "Stop": "stop_requested",
    "Interrupt": "interrupt",
}


@dataclass(frozen=True)
class HookResult:
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0


def detect_provider(payload: dict[str, Any]) -> str:
    explicit = payload.get("provider")
    if explicit in {"codex", "claude"}:
        return explicit
    if "turn_id" in payload or "model" in payload:
        return "codex"
    return "claude"


def event_from_payload(provider: str, payload: dict[str, Any]) -> Event:
    name = payload.get("hook_event_name", "")
    kind = EVENT_KINDS.get(name, "unknown")
    canonical = dict(payload)
    canonical["provider"] = provider
    raw = json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str)
    return Event(
        event_id=hashlib.sha256(raw.encode()).hexdigest(),
        kind=kind,
        observed_at=datetime.now(UTC).isoformat(),
        payload=canonical,
    )


def render(provider: str, actions: tuple[Action, ...]) -> HookResult:
    context = "\n".join(
        str(action.payload.get("text", ""))
        for action in actions
        if action.kind == "inject_context" and action.payload.get("text")
    )
    block = next((action for action in actions if action.kind == "block_stop"), None)
    if block:
        reason = block.payload.get("reason") or _active_reason(block.payload.get("active", ()))
        return HookResult(json.dumps({"decision": "block", "reason": reason}))
    if context:
        return HookResult(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "UserPromptSubmit",
                        "additionalContext": context,
                    }
                }
            )
        )
    return HookResult()


def _active_reason(active: Any) -> str:
    identities = ", ".join(map(str, active))
    return f"Symphony is still tracking active work: {identities}." if identities else "Symphony work remains active."
