"""Two things Symphony decided correctly and then failed to act on.

A user reported leads multiplying and heartbeats doubling. The lifecycle
reducer turned out to be right every time: it kept one lead and refused the
rest. What it did not do was tell anyone, because the renderer silently drops
any action kind it has no branch for. Separately, any session heartbeat from a
second terminal in the same project was taken as proof the owning process had
died, which killed a live lead and rewrote the run's session to the stranger.
"""

import json
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from plugins.symphony.symphony import runtime as runtime_module
from plugins.symphony.symphony.model import Action, ProjectState
from plugins.symphony.symphony.runtime import handle

MARKER = json.dumps(
    {"size": "small", "complexity": "simple", "risk": "normal",
     "rationale": "bounded", "topology": "direct"}
)


class ActionCoverageTests(unittest.TestCase):
    """No action the reducer emits may vanish on the way to the host."""

    def test_every_emitted_action_is_rendered_or_declared_internal(self):
        root = Path(__file__).resolve().parents[1] / "symphony"
        emitted = set()
        for name in ("reducer.py", "runtime.py"):
            emitted |= set(re.findall(r'Action\(\s*"([a-z_]+)"', (root / name).read_text()))
        silent = []
        for kind in sorted(emitted - set(runtime_module.INTERNAL_ACTIONS)):
            produced = runtime_module._render_actions(
                (Action(kind, {}),), ProjectState(), "codex", ""
            )
            if not produced:
                silent.append(kind)
        self.assertEqual(
            [], silent,
            f"these actions are emitted and then silently dropped by the renderer: {silent}",
        )


class PacketCompletenessTests(unittest.TestCase):
    """A lead spawned without the conversation only gets what the packet says."""

    def test_guidance_tells_the_root_to_relay_every_part_of_the_task(self):
        guidance = runtime_module._assessment_guidance("fix a, b, c, d and e", "codex")
        self.assertIn("in full", guidance)
        self.assertIn("every part", guidance)
        self.assertIn("fix a, b, c, d and e", guidance)


class ConcurrentSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        root = Path(self.temp.name)
        self.project = root / "project"
        self.project.mkdir()
        self.state_root = root / "state"
        self.environ = {"SYMPHONY_STATE_DIR": str(self.state_root), "SYMPHONY_PROFILE": "full"}

    def tearDown(self):
        self.temp.cleanup()

    def payload(self, session, event="UserPromptSubmit", **extra):
        base = {
            "session_id": session, "cwd": str(self.project), "hook_event_name": event,
            "prompt": "", "turn_id": "turn-1", "model": "codex-model",
        }
        base.update(extra)
        return base

    def out(self, result):
        return json.loads(result.stdout) if result.stdout else {}

    def text(self, result):
        o = self.out(result)
        return o.get("hookSpecificOutput", {}).get("additionalContext", "") or o.get("reason", "")

    def spawn(self, session, role, model, effort, marker=""):
        body = f"SYMPHONY_ROLE: {role}\n" + (f"SYMPHONY_ROUTE: {marker}\n" if marker else "")
        return handle(self.payload(session, "PreToolUse", tool_name="spawn_agent",
                                   tool_input={"message": body + "Ship it", "model": model,
                                               "reasoning_effort": effort}), self.environ)

    def start_agent(self, session, agent_id, role, model, effort):
        return handle(self.payload(session, "SubagentStart", agent_id=agent_id,
                                   agent_type=f"symphony_{role}_{model.replace('.','_').replace('-','_')}_{effort}",
                                   model=model, model_reasoning_effort=effort), self.environ)

    def stop_agent(self, session, agent_id, message="done"):
        return handle(self.payload(session, "SubagentStop", agent_id=agent_id,
                                   last_assistant_message=message), self.environ)

    def run_with_live_lead(self, session="root-a"):
        handle(self.payload(session, "SessionStart"), self.environ)
        self.spawn(session, "assessor", "gpt-6-astra", "high")
        self.start_agent(session, "assessor-1", "assessor", "gpt-6-astra", "high")
        self.spawn(session, "lead", "gpt-5.6-sol", "medium", MARKER)
        self.start_agent(session, "lead-1", "lead", "gpt-5.6-sol", "medium")

    def state(self):
        path = next(self.state_root.glob("*.json"))
        return json.loads(path.read_text())

    # ---- the renderer must speak ------------------------------------------
    def test_a_second_lead_is_told_it_is_not_the_lead(self):
        self.run_with_live_lead()
        spoken = self.text(self.start_agent("root-a", "lead-2", "lead", "gpt-5.6-sol", "medium"))
        self.assertTrue(spoken, "Symphony rejected a second lead and said nothing")
        self.assertIn("lead", spoken.lower())

    def test_a_stale_completion_is_not_silently_swallowed(self):
        self.run_with_live_lead()
        self.start_agent("root-a", "lead-2", "lead", "gpt-5.6-sol", "medium")
        self.stop_agent("root-a", "lead-2")
        spoken = self.text(handle(self.payload("root-a"), self.environ))
        self.assertTrue(spoken, "a completion from a non-lead was ignored silently")

    # ---- a second terminal must not kill a live run -----------------------
    def test_a_concurrent_session_does_not_declare_a_live_lead_dead(self):
        self.run_with_live_lead("root-a")
        spoken = self.text(handle(self.payload("other-b", "SessionStart"), self.environ))

        run = self.state()["active_run"]
        self.assertEqual("root-a", run["session_id"], "a stranger took ownership of the run")
        self.assertNotIn("unavailable", spoken.lower())
        self.assertNotIn("replacement", spoken.lower())
        statuses = {a.get("status") for a in (run.get("agent_records") or {}).values()}
        self.assertNotIn("interrupted", statuses, "a live lead was declared dead")

    def test_a_concurrent_session_does_not_destroy_an_accepted_clamp(self):
        environ = {**self.environ, "SYMPHONY_PROFILE": "base"}
        handle(self.payload("root-a", "SessionStart"), environ)
        handle({**self.payload("root-a"), "prompt": "$symphony:symphony proceed"}, environ)
        handle(self.payload("other-b", "SessionStart"), environ)

        accepted = (self.state()["activation"]["codex"].get("accepted") or {})
        self.assertIn("root-a", accepted, "a stranger's heartbeat erased this session's consent")
        self.assertTrue(accepted["root-a"].get("profile"))

    def test_consent_recorded_before_it_was_keyed_by_session_survives(self):
        """An upgraded machine carries records with one unkeyed slot."""
        handle(self.payload("old-s", "SessionStart"), self.environ)
        path = next(self.state_root.glob("*.json"))
        document = json.loads(path.read_text())
        document["activation"]["codex"] = {
            "session_id": "old-s", "profile": "base", "accepted_profile": "base",
            "accepted_route": "gpt-5.5/medium", "plugin_version": "1.1.0",
        }
        path.write_text(json.dumps(document))

        handle(self.payload("old-s"), self.environ)
        handle(self.payload("stranger", "SessionStart"), self.environ)

        accepted = self.state()["activation"]["codex"].get("accepted") or {}
        self.assertEqual("base", accepted.get("old-s", {}).get("profile"))

    def test_a_naive_timestamp_from_an_older_state_file_does_not_crash(self):
        """Subtracting a naive stamp from an aware one raises TypeError."""
        self.run_with_live_lead("root-a")
        path = next(self.state_root.glob("*.json"))
        document = json.loads(path.read_text())
        document["active_run"]["owner_seen_at"] = "2020-01-01T00:00:00"
        path.write_text(json.dumps(document))

        handle(self.payload("later-c", "SessionStart"), self.environ)

        self.assertEqual("later-c", self.state()["active_run"]["session_id"])

    # ---- but real recovery must still work --------------------------------
    def test_a_run_whose_owner_has_gone_quiet_is_still_adopted(self):
        self.run_with_live_lead("root-a")
        path = next(self.state_root.glob("*.json"))
        document = json.loads(path.read_text())
        document["active_run"]["owner_seen_at"] = "2020-01-01T00:00:00+00:00"
        path.write_text(json.dumps(document))

        handle(self.payload("later-c", "SessionStart"), self.environ)

        run = self.state()["active_run"]
        self.assertEqual("later-c", run["session_id"], "a genuinely stale run was not recovered")


if __name__ == "__main__":
    unittest.main()
