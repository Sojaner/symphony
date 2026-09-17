"""Translate provider hook payloads to and from Symphony's core model."""

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
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
    if provider == "codex" and name in {"SubagentStart", "SubagentStop"}:
        child_metadata = _codex_subagent_metadata(payload)
        canonical.update(child_metadata)
        canonical["_symphony_child_metadata"] = tuple(child_metadata)
    canonical["provider"] = provider
    raw = json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str)
    return Event(
        event_id=hashlib.sha256(raw.encode()).hexdigest(),
        kind=kind,
        observed_at=datetime.now(UTC).isoformat(),
        payload=canonical,
    )


def _codex_subagent_metadata(payload: dict[str, Any]) -> dict[str, str]:
    transcript = payload.get("agent_transcript_path") or payload.get("transcript_path")
    if not transcript:
        return {}
    found: dict[str, str] = {}
    try:
        with Path(str(transcript)).open(encoding="utf-8") as handle:
            for index, line in enumerate(handle):
                if index >= 32:
                    break
                record = json.loads(line)
                record_payload = record.get("payload", {})
                if record.get("type") == "session_meta":
                    spawn = (
                        record_payload.get("source", {})
                        .get("subagent", {})
                        .get("thread_spawn", {})
                    )
                    agent_path = record_payload.get("agent_path") or spawn.get("agent_path")
                    if agent_path:
                        found["task_name"] = str(agent_path).rsplit("/", 1)[-1]
                elif record.get("type") == "turn_context":
                    if record_payload.get("model"):
                        found["model"] = str(record_payload["model"])
                    if record_payload.get("effort"):
                        found["model_reasoning_effort"] = str(record_payload["effort"])
                if "task_name" in found and "model_reasoning_effort" in found:
                    break
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return found
    return found


def render(
    provider: str,
    actions: tuple[Action, ...],
    hook_event_name: str = "UserPromptSubmit",
) -> HookResult:
    context = "\n".join(
        str(action.payload.get("text", ""))
        for action in actions
        if action.kind == "inject_context" and action.payload.get("text")
    )
    block = next((action for action in actions if action.kind in {"block_stop", "block_tool"}), None)
    if block:
        reason = block.payload.get("reason") or _active_reason(block.payload.get("active", ()))
        if block.kind == "block_tool" and provider == "claude":
            return HookResult(
                json.dumps(
                    {
                        "hookSpecificOutput": {
                            "hookEventName": hook_event_name,
                            "permissionDecision": "deny",
                            "permissionDecisionReason": reason,
                        }
                    }
                )
            )
        return HookResult(json.dumps({"decision": "block", "reason": reason}))
    if context:
        return HookResult(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": hook_event_name,
                        "additionalContext": context,
                    }
                }
            )
        )
    return HookResult()


def _active_reason(active: Any) -> str:
    identities = ", ".join(map(str, active))
    return f"Symphony is still tracking active work: {identities}." if identities else "Symphony work remains active."
