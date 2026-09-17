import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from plugins.symphony.symphony.runtime import handle
from plugins.symphony.symphony.store import StateStore

CAPTURED = Path(__file__).resolve().parent / "fixtures" / "captured"

# Events the runtime handles but no capture covers yet. Listing them keeps the
# gap visible: a shape nobody has observed is a shape nobody should rely on.
UNCAPTURED = {
    "claude": ("PreToolUse", "PostToolUse", "SubagentStart", "SubagentStop"),
    "codex": ("SessionStart", "UserPromptSubmit", "SubagentStart", "SubagentStop", "Stop"),
}


def captures(provider):
    directory = CAPTURED / provider
    if not directory.is_dir():
        return {}
    return {path.stem: json.loads(path.read_text()) for path in sorted(directory.glob("*.json"))}


class CapturedPayloadTests(unittest.TestCase):
    """Hold the runtime to shapes a real host actually sent.

    The 1.0.0 suite passed against invented payloads while the plugin was
    broken on both hosts. These fixtures were recorded from installed sessions,
    so a field the code reads that a host never sends shows up here.
    """

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.environ = {
            "SYMPHONY_STATE_DIR": str(self.root / "state"),
            "SYMPHONY_PROFILE": "opus",
        }

    def tearDown(self):
        self.temp.cleanup()

    def test_a_capture_exists_for_at_least_one_provider(self):
        self.assertTrue(captures("claude"), "no captured Claude payloads to check against")

    def test_the_stop_payload_really_carries_the_retry_flag(self):
        # The stop circuit breaker exists because a blocked stop is re-fired
        # with this flag. If a host stopped sending it, the breaker would never
        # engage and the livelock would return silently.
        stop = captures("claude").get("Stop")
        self.assertIsNotNone(stop, "no captured Claude Stop payload")
        self.assertIn("stop_hook_active", stop)
        self.assertIsInstance(stop["stop_hook_active"], bool)

    def test_session_start_still_reports_no_live_agents(self):
        # Recovery keys on the session precisely because no host lists live
        # agents. If one ever does, this fails and the design should be revisited.
        start = captures("claude").get("SessionStart")
        self.assertIsNotNone(start, "no captured Claude SessionStart payload")
        self.assertNotIn("active_agent_ids", start)
        self.assertNotIn("active_ids", start)

    def test_no_capture_carries_a_session_model(self):
        # Entitlement cannot be read off the payload, which is why it is probed.
        for name, payload in captures("claude").items():
            with self.subTest(event=name):
                self.assertNotIn("model", payload)

    def test_the_runtime_accepts_every_captured_payload(self):
        for name, payload in captures("claude").items():
            with self.subTest(event=name):
                result = handle({**payload, "cwd": str(self.project)}, self.environ)
                if result.stdout:
                    json.loads(result.stdout)

    def test_a_captured_prompt_still_reaches_the_control_parser(self):
        prompt = captures("claude").get("UserPromptSubmit")
        self.assertIsNotNone(prompt)
        payload = {**prompt, "cwd": str(self.project), "prompt": "/symphony:enable"}
        handle(payload, self.environ)
        self.assertTrue(StateStore(self.root / "state").load(self.project).enabled)

    def test_uncaptured_events_are_declared_rather_than_assumed(self):
        for provider, events in UNCAPTURED.items():
            recorded = set(captures(provider))
            for event in events:
                with self.subTest(provider=provider, event=event):
                    self.assertNotIn(
                        event,
                        recorded,
                        f"{provider}/{event} is captured now; remove it from UNCAPTURED",
                    )


if __name__ == "__main__":
    unittest.main()
