"""A run is held to its tier, and only a real weakening asks the user.

The 1.0 design pinned a run to the concrete model its assessment resolved to
and blocked every later spawn that did not match. That turns a model leaving
the map into a run nobody can ever advance. These tests hold the replacement
to its two halves: the tier is what a run is pinned to, and the stored model
survives only to answer whether re-resolving that tier made the work weaker.
"""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from plugins.symphony.symphony.runtime import handle

MARKER = json.dumps(
    {
        "size": "small",
        "complexity": "simple",
        "risk": "normal",
        "rationale": "bounded task",
        "topology": "direct",
    }
)


class RouteDriftTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        root = Path(self.temp.name)
        self.project = root / "project"
        self.project.mkdir()
        self.state_root = root / "state"

    def tearDown(self):
        self.temp.cleanup()

    def env(self, profile: str) -> dict:
        return {"SYMPHONY_STATE_DIR": str(self.state_root), "SYMPHONY_PROFILE": profile}

    def payload(self, session: str, event: str = "UserPromptSubmit") -> dict:
        return {
            "session_id": session,
            "cwd": str(self.project),
            "hook_event_name": event,
            "prompt": "",
            "turn_id": "turn-1",
            "model": "codex-model",
        }

    def output(self, result) -> dict:
        return json.loads(result.stdout) if result.stdout else {}

    def start(self, profile: str, session: str) -> None:
        handle({**self.payload(session, "SessionStart")}, self.env(profile))

    def spawn(self, profile, session, role, model, effort, marker=""):
        body = f"SYMPHONY_ROLE: {role}\n"
        if marker:
            body += f"SYMPHONY_ROUTE: {marker}\n"
        return handle(
            {
                **self.payload(session, "PreToolUse"),
                "tool_name": "spawn_agent",
                "tool_input": {
                    "message": body + "Ship it",
                    "model": model,
                    "reasoning_effort": effort,
                },
            },
            self.env(profile),
        )

    def proceed(self, profile: str, session: str) -> None:
        handle(
            {**self.payload(session), "prompt": "$symphony:symphony proceed"}, self.env(profile)
        )

    def accept_under(self, profile: str, session: str, model: str, effort: str) -> None:
        """Open a run and let its lead spawn record the accepted route.

        A weaker entitlement trips the tier clamp before drift is even in play,
        so that consent is given first. This isolates what these tests are about.
        """
        self.start(profile, session)
        self.spawn(profile, session, "assessor", "gpt-6-astra", "high")
        if profile != "full":
            self.proceed(profile, session)
        result = self.spawn(profile, session, "lead", model, effort, MARKER)
        self.assertNotEqual(
            self.output(result).get("decision"), "block", self.output(result).get("reason")
        )

    def test_standing_assessment_that_weakened_waits_for_the_user(self):
        self.accept_under("full", "session-1", "gpt-6-sol", "medium")

        # A later session on a weaker entitlement: the priced route is gone.
        self.start("base", "session-2")
        output = self.output(self.spawn("base", "session-2", "lead", "gpt-5.5", "medium", MARKER))

        self.assertEqual(output["decision"], "block")
        self.assertIn("no longer available", output["reason"])
        self.assertIn("gpt-6-sol", output["reason"])
        self.assertIn("proceed", output["reason"])

    def test_proceed_accepts_the_weaker_route_and_the_spawn_goes_through(self):
        self.accept_under("full", "session-1", "gpt-6-sol", "medium")
        self.start("base", "session-2")
        blocked = self.spawn("base", "session-2", "lead", "gpt-5.5", "medium", MARKER)
        self.assertEqual(self.output(blocked)["decision"], "block")

        self.proceed("base", "session-2")
        output = self.output(self.spawn("base", "session-2", "lead", "gpt-5.5", "medium", MARKER))

        self.assertNotEqual(output.get("decision"), "block", output.get("reason"))

    def test_a_fresh_assessment_never_gates(self):
        """Nothing was priced before, so nothing was taken away."""
        self.start("base", "session-1")
        self.spawn("base", "session-1", "assessor", "gpt-5.5", "high")
        self.proceed("base", "session-1")
        output = self.output(
            self.spawn("base", "session-1", "lead", "gpt-5.5", "medium", MARKER)
        )

        self.assertNotEqual(output.get("decision"), "block", output.get("reason"))

    def test_an_entitlement_upgrade_discloses_instead_of_gating(self):
        """Moving to a better map is not a degradation and needs no consent."""
        self.accept_under("base", "session-1", "gpt-5.5", "medium")

        self.start("full", "session-2")
        output = self.output(
            self.spawn("full", "session-2", "lead", "gpt-6-sol", "medium", MARKER)
        )

        self.assertNotEqual(output.get("decision"), "block", output.get("reason"))

    def test_the_tier_not_the_stored_model_is_what_a_spawn_must_match(self):
        """The 1.0 deadlock: a stored model that no longer exists.

        Under the old rule the stored model was the required model, so once it
        left the map every lead spawn was refused and the run was stuck. The
        run must now be advanceable by spawning what the tier resolves to today.
        """
        self.accept_under("full", "session-1", "gpt-6-sol", "medium")
        self.start("base", "session-2")
        self.proceed("base", "session-2")

        stale = self.output(self.spawn("base", "session-2", "lead", "gpt-5.6-sol", "medium", MARKER))
        current = self.output(self.spawn("base", "session-2", "lead", "gpt-5.5", "medium", MARKER))

        self.assertEqual(stale["decision"], "block")
        self.assertNotEqual(current.get("decision"), "block", current.get("reason"))


if __name__ == "__main__":
    unittest.main()
