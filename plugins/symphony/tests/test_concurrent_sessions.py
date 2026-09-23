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
from plugins.symphony.symphony.routing import profiles_for, snapshot_for

FULL_SIMPLE = snapshot_for("codex", "full").matrix["small/simple"]
BASE_SIMPLE = snapshot_for("codex", "base").matrix["small/simple"]
CODEX_STRONGEST = profiles_for("codex")[0]["tiers"]["strongest"]

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
        # A populated payload, so a branch renders real values rather than the
        # literal word None, and so the catch-all is distinguishable from a
        # real branch. Asserting only "something came back" proves nothing:
        # the catch-all guarantees that for every kind, invented ones included.
        payload = {
            "identity": "agent-1", "session_id": "session-1", "run_id": "run-1",
            "active": ("agent-2",), "unreachable": (), "unreconciled": ("agent-3",),
            "reason": "outcome_missing", "task": "ship it", "owner_generation": 2,
        }
        unhandled, leaky = [], []
        for kind in sorted(emitted - set(runtime_module.INTERNAL_ACTIONS)):
            produced = runtime_module._render_actions(
                (Action(kind, payload),), ProjectState(), "codex", ""
            )
            if not produced:
                unhandled.append(kind)
                continue
            text = produced[0].payload.get("text") or produced[0].payload.get("reason") or ""
            if "has no message for" in text:
                unhandled.append(kind)
            if "None" in text:
                leaky.append(kind)
        self.assertEqual(
            [], unhandled,
            f"emitted actions with no real message, so the host never learns of them: {unhandled}",
        )
        self.assertEqual([], leaky, f"these render a literal None into their text: {leaky}")

    def test_the_catch_all_is_what_makes_an_unknown_action_visible(self):
        """Guards the guard: an invented kind must still surface loudly."""
        produced = runtime_module._render_actions(
            (Action("totally_made_up_kind", {}),), ProjectState(), "codex", ""
        )
        self.assertTrue(produced)
        self.assertIn("has no message for", produced[0].payload["text"])


class PacketCompletenessTests(unittest.TestCase):
    """A lead spawned without the conversation only gets what the packet says."""

    def test_guidance_tells_the_root_to_relay_every_part_of_the_task(self):
        guidance = runtime_module._assessment_guidance("fix a, b, c, d and e", "codex")
        self.assertIn("in full", guidance)
        self.assertIn("every part", guidance)
        self.assertIn("fix a, b, c, d and e", guidance)


class GovernanceLabelTests(unittest.TestCase):
    """A one-shot run and a governed one look identical until they diverge.

    A user who ran `start` believes the project is enabled, works for hours,
    and never sees that every prompt after the first was ungoverned.
    """

    def setUp(self):
        self.temp = TemporaryDirectory()
        root = Path(self.temp.name)
        self.project = root / "project"
        self.project.mkdir()
        self.environ = {"SYMPHONY_STATE_DIR": str(root / "state"), "SYMPHONY_PROFILE": "full"}

    def tearDown(self):
        self.temp.cleanup()

    def payload(self, prompt):
        return {"session_id": "s1", "cwd": str(self.project), "hook_event_name": "UserPromptSubmit",
                "prompt": prompt, "turn_id": "t", "model": "m"}

    def text(self, result):
        out = json.loads(result.stdout) if result.stdout else {}
        return out.get("hookSpecificOutput", {}).get("additionalContext", "")

    def open_lead(self):
        handle({**self.payload(""), "hook_event_name": "SessionStart"}, self.environ)
        for role, model, effort in (("assessor", CODEX_STRONGEST, "high"), ("lead", FULL_SIMPLE["model"], FULL_SIMPLE["effort"])):
            body = f"SYMPHONY_ROLE: {role}\n" + (f"SYMPHONY_ROUTE: {MARKER}\n" if role == "lead" else "")
            handle({**self.payload(""), "hook_event_name": "PreToolUse", "tool_name": "spawn_agent",
                    "tool_input": {"message": body + "Ship it", "model": model,
                                   "reasoning_effort": effort}}, self.environ)
        handle({**self.payload(""), "hook_event_name": "SubagentStart", "agent_id": "lead-1",
                "agent_type": "symphony_lead_x_medium", "model": FULL_SIMPLE["model"],
                "model_reasoning_effort": FULL_SIMPLE["effort"]}, self.environ)

    def test_a_one_shot_run_labels_its_lead_transactional(self):
        handle(self.payload("$symphony:symphony start Ship it"), self.environ)
        self.open_lead()

        status = self.text(handle(self.payload("$symphony:symphony status"), self.environ))

        self.assertIn("[transactional]", status)
        self.assertNotIn("[enabled]", status)

    def test_an_enabled_project_labels_its_lead_enabled(self):
        handle(self.payload("$symphony:symphony enable"), self.environ)
        self.open_lead()

        status = self.text(handle(self.payload("$symphony:symphony status"), self.environ))

        self.assertIn("[enabled]", status)
        self.assertNotIn("[transactional]", status)

    def test_a_transactional_run_says_the_next_prompt_is_ungoverned(self):
        handle(self.payload("$symphony:symphony start Ship it"), self.environ)
        self.open_lead()

        status = self.text(handle(self.payload("$symphony:symphony status"), self.environ))

        self.assertIn("ungoverned", status.lower())
        self.assertIn("enable", status.lower())

    def test_a_live_run_speaks_even_when_the_project_is_not_enabled(self):
        """Silence here let the root do the work beside a lead it never saw."""
        handle(self.payload("$symphony:symphony start Ship it"), self.environ)
        self.open_lead()

        guidance = self.text(handle(self.payload("keep going on the other thing"), self.environ))

        self.assertTrue(guidance, "a live run produced no guidance at all")
        self.assertIn("remains active", guidance)

    def test_the_label_reaches_the_guidance_a_live_run_injects(self):
        handle(self.payload("$symphony:symphony start Ship it"), self.environ)
        self.open_lead()

        guidance = self.text(handle(self.payload("keep going on the other thing"), self.environ))

        self.assertIn("[transactional]", guidance)


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

    def spawn_in(self, session, env, role, model, effort, marker=""):
        body = f"SYMPHONY_ROLE: {role}\n" + (f"SYMPHONY_ROUTE: {marker}\n" if marker else "")
        return handle(self.payload(session, "PreToolUse", tool_name="spawn_agent",
                                   tool_input={"message": body + "Ship it", "model": model,
                                               "reasoning_effort": effort}), env)

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
        self.spawn(session, "assessor", CODEX_STRONGEST, "high")
        self.start_agent(session, "assessor-1", "assessor", CODEX_STRONGEST, "high")
        self.spawn(session, "lead", FULL_SIMPLE["model"], FULL_SIMPLE["effort"], MARKER)
        self.start_agent(session, "lead-1", "lead", FULL_SIMPLE["model"], FULL_SIMPLE["effort"])

    def state(self):
        path = next(self.state_root.glob("*.json"))
        return json.loads(path.read_text())

    # ---- the renderer must speak ------------------------------------------
    def test_a_second_lead_is_told_it_is_not_the_lead(self):
        self.run_with_live_lead()
        spoken = self.text(self.start_agent("root-a", "lead-2", "lead", FULL_SIMPLE["model"], FULL_SIMPLE["effort"]))
        self.assertTrue(spoken, "Symphony rejected a second lead and said nothing")
        self.assertIn("lead", spoken.lower())

    def test_a_stale_completion_is_not_silently_swallowed(self):
        self.run_with_live_lead()
        self.start_agent("root-a", "lead-2", "lead", FULL_SIMPLE["model"], FULL_SIMPLE["effort"])
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

    def test_consent_still_holds_at_the_gate_after_a_stranger_heartbeats(self):
        """The gate runs on a spawn, which fires no heartbeat of its own.

        Carrying consent correctly is useless if the check that blocks the
        spawn reads a different field, which is what it did.
        """
        env = {**self.environ, "SYMPHONY_PROFILE": "base"}
        handle(self.payload("root-a", "SessionStart"), env)
        self.spawn_in("root-a", env, "assessor", BASE_SIMPLE["model"], "high")
        blocked = self.text(self.spawn_in("root-a", env, "lead", BASE_SIMPLE["model"], BASE_SIMPLE["effort"], MARKER))
        self.assertIn("proceed", blocked)

        handle({**self.payload("root-a"), "prompt": "$symphony:symphony proceed"}, env)
        handle(self.payload("stranger", "SessionStart"), env)

        after = self.text(self.spawn_in("root-a", env, "lead", BASE_SIMPLE["model"], BASE_SIMPLE["effort"], MARKER))
        self.assertNotIn(
            "proceed", after, "a stranger's heartbeat undid the clamp this session accepted"
        )

    def test_adopting_a_stale_run_stamps_the_new_owner(self):
        """Otherwise the next session adopts it all over again."""
        self.run_with_live_lead("root-a")
        path = next(self.state_root.glob("*.json"))
        document = json.loads(path.read_text())
        document["active_run"]["owner_seen_at"] = "2020-01-01T00:00:00+00:00"
        path.write_text(json.dumps(document))

        handle(self.payload("later-c", "SessionStart"), self.environ)
        self.assertEqual("later-c", self.state()["active_run"]["session_id"])

        spoken = self.text(handle(self.payload("fourth-d", "SessionStart"), self.environ))
        self.assertEqual(
            "later-c", self.state()["active_run"]["session_id"],
            "the run was adopted twice in a row",
        )
        self.assertIn("owned by session", spoken)

    def test_a_refused_session_is_not_also_told_to_reconcile(self):
        self.run_with_live_lead("root-a")
        spoken = self.text(handle(self.payload("other-b", "SessionStart"), self.environ))

        self.assertIn("will not take it over", spoken)
        self.assertNotIn("Reconcile observed agents", spoken)
        self.assertIn("--force", spoken)

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
