import json
import unittest
from pathlib import Path

from plugins.symphony.symphony.adapters import detect_provider, event_from_payload, render
from plugins.symphony.symphony.model import Action


FIXTURES = Path(__file__).parent / "fixtures"


def fixture(provider: str, name: str) -> dict:
    return json.loads((FIXTURES / provider / f"{name}.json").read_text(encoding="utf-8"))


class AdapterContractTests(unittest.TestCase):
    def test_detects_provider_from_native_payload(self):
        self.assertEqual(detect_provider(fixture("codex", "user_prompt")), "codex")
        self.assertEqual(detect_provider(fixture("claude", "user_prompt")), "claude")

    def test_user_prompt_normalizes_to_canonical_event(self):
        event = event_from_payload("codex", fixture("codex", "user_prompt"))
        self.assertEqual(event.kind, "user_prompt")
        self.assertEqual(event.payload["session_id"], "codex-session")
        self.assertEqual(event.payload["prompt"], "Implement the feature")
        self.assertEqual(event.payload["provider"], "codex")

    def test_event_id_is_stable_for_replayed_payload(self):
        payload = fixture("codex", "user_prompt")
        self.assertEqual(
            event_from_payload("codex", payload).event_id,
            event_from_payload("codex", payload).event_id,
        )

    def test_codex_context_uses_hook_specific_output(self):
        result = render("codex", (Action("inject_context", {"text": "Assess this task."}),))
        body = json.loads(result.stdout)
        self.assertEqual(body["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
        self.assertEqual(body["hookSpecificOutput"]["additionalContext"], "Assess this task.")
        self.assertEqual(result.exit_code, 0)

    def test_codex_stop_block_uses_continuation_decision(self):
        result = render("codex", (Action("block_stop", {"reason": "Worker remains active."}),))
        self.assertEqual(json.loads(result.stdout), {"decision": "block", "reason": "Worker remains active."})

    def test_claude_context_uses_additional_context(self):
        result = render(
            "claude",
            (Action("inject_context", {"text": "Recover this task."}),),
            "SessionStart",
        )
        output = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(output["hookEventName"], "SessionStart")
        self.assertEqual(output["additionalContext"], "Recover this task.")

    def test_registered_lifecycle_events_normalize(self):
        expected = {
            "SessionStart": "session_heartbeat",
            "UserPromptSubmit": "user_prompt",
            "PreToolUse": "pre_tool_use",
            "SubagentStart": "subagent_started",
            "SubagentStop": "subagent_stopped",
            "PostToolUse": "post_tool_use",
            "Stop": "stop_requested",
            "Interrupt": "interrupt",
        }
        for hook_name, kind in expected.items():
            with self.subTest(hook_name=hook_name):
                event = event_from_payload("codex", {"hook_event_name": hook_name})
                self.assertEqual(event.kind, kind)

    def test_malformed_payload_is_a_nonblocking_fault(self):
        event = event_from_payload("codex", {"hook_event_name": "Unknown"})
        self.assertEqual(event.kind, "unknown")


if __name__ == "__main__":
    unittest.main()
