import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from plugins.symphony.symphony.model import Delegation, ProjectState, RunState
from plugins.symphony.symphony.runtime import compact_delegations, format_delegation, handle
from plugins.symphony.symphony.store import StateStore


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.state_root = self.root / "state"
        self.environ = {"SYMPHONY_STATE_DIR": str(self.state_root)}

    def tearDown(self):
        self.temp.cleanup()

    def payload(self, prompt: str, provider: str = "codex") -> dict:
        result = {
            "session_id": f"{provider}-session",
            "cwd": str(self.project),
            "hook_event_name": "UserPromptSubmit",
            "prompt": prompt,
        }
        if provider == "codex":
            result.update({"turn_id": "turn-1", "model": "codex-model"})
        return result

    def output(self, result) -> dict:
        return json.loads(result.stdout) if result.stdout else {}

    def context(self, result) -> str:
        return self.output(result).get("hookSpecificOutput", {}).get("additionalContext", "")

    def test_enable_persists_and_next_task_requests_bounded_assessment(self):
        enabled = handle(self.payload("$symphony:symphony enable"), self.environ)
        self.assertIn("enabled", self.context(enabled).lower())

        task = handle(self.payload("Implement the feature"), self.environ)

        self.assertIn("assess", self.context(task).lower())
        state = StateStore(self.state_root).load(self.project)
        self.assertTrue(state.enabled)
        self.assertIsNotNone(state.active_run)
        self.assertEqual(state.active_run.task, "Implement the feature")

    def test_one_shot_start_does_not_enable_project(self):
        result = handle(self.payload("$symphony:symphony start Check the release"), self.environ)
        state = StateStore(self.state_root).load(self.project)
        self.assertFalse(state.enabled)
        self.assertEqual(state.active_run.task, "Check the release")
        self.assertIn("assess", self.context(result).lower())

    def test_bypass_does_not_mutate_enablement_or_active_run(self):
        store = StateStore(self.state_root)
        store.save(self.project, ProjectState(enabled=True))
        result = handle(self.payload("$symphony:symphony bypass Explain this file"), self.environ)
        state = store.load(self.project)
        self.assertTrue(state.enabled)
        self.assertIsNone(state.active_run)
        self.assertIn("outside symphony", self.context(result).lower())

    def test_status_records_current_hook_heartbeat_as_guarded(self):
        result = handle(self.payload("$symphony:symphony status"), self.environ)
        state = StateStore(self.state_root).load(self.project)
        self.assertEqual(state.activation["codex"]["state"], "guarded")
        self.assertEqual(state.activation["codex"]["session_id"], "codex-session")
        self.assertIn("guarded", self.context(result).lower())
        self.assertNotIn("unarmed", self.context(result).lower())

    def test_claude_marker_uses_the_same_control_contract(self):
        result = handle(self.payload("SYMPHONY_CONTROL: enable", "claude"), self.environ)
        self.assertIn("enabled", self.context(result).lower())
        self.assertTrue(StateStore(self.state_root).load(self.project).enabled)

    def test_claude_command_arguments_start_a_one_shot_run(self):
        prompt = "SYMPHONY_CONTROL: start\nARGUMENTS: Check the release"
        result = handle(self.payload(prompt, "claude"), self.environ)
        state = StateStore(self.state_root).load(self.project)
        self.assertEqual(state.active_run.task, "Check the release")
        self.assertIn("assess", self.context(result).lower())

    def test_help_is_inert_and_uses_provider_native_syntax(self):
        result = handle(self.payload("$symphony:symphony help"), self.environ)
        text = self.context(result)
        self.assertIn("$symphony:symphony", text)
        self.assertNotIn("/symphony:start", text)
        self.assertIsNone(StateStore(self.state_root).load(self.project).active_run)

    def test_stop_blocks_when_host_observed_delegation_is_active(self):
        delegation = Delegation("w1", "worker", "work", "working", "balanced", "medium")
        run = RunState("run-1", "task", delegations=(delegation,))
        StateStore(self.state_root).save(self.project, ProjectState(enabled=True, active_run=run))
        payload = self.payload("")
        payload["hook_event_name"] = "Stop"

        result = handle(payload, self.environ)
        replay = handle(payload, self.environ)

        self.assertEqual(self.output(result)["decision"], "block")
        self.assertIn("w1", self.output(result)["reason"])
        self.assertEqual(self.output(replay)["decision"], "block")

    def test_host_observed_lead_completion_allows_normal_stop(self):
        handle(self.payload("$symphony:symphony start Ship it"), self.environ)
        started = self.payload("")
        started.update(
            {
                "hook_event_name": "SubagentStart",
                "agent_id": "lead-1",
                "agent_type": "symphony_lead_gpt_5_medium",
                "model": "gpt-5",
                "model_reasoning_effort": "medium",
            }
        )
        handle(started, self.environ)
        stopped = {**started, "hook_event_name": "SubagentStop", "status": "completed"}
        handle(stopped, self.environ)

        stop = self.payload("")
        stop["hook_event_name"] = "Stop"
        result = handle(stop, self.environ)
        state = StateStore(self.state_root).load(self.project)

        self.assertEqual(result.stdout, "")
        self.assertIsNone(state.active_run)
        self.assertEqual(state.recent_runs[-1].status, "completed")

    def test_compact_delegations_prioritizes_failed_then_active_then_recent(self):
        delegations = (
            Delegation("done-old", "worker", "", "completed", "economy", "low", "2026-01-01"),
            Delegation("wait", "worker", "", "waiting", "economy", "low", "2026-01-06"),
            Delegation("done-new", "worker", "", "completed", "economy", "low", "2026-01-05"),
            Delegation("working", "worker", "", "working", "economy", "low", "2026-01-04"),
            Delegation("failed", "worker", "", "failed", "economy", "low", "2026-01-03"),
            Delegation("done-mid", "worker", "", "completed", "economy", "low", "2026-01-02"),
        )
        state = ProjectState(active_run=RunState("run", "task", delegations=delegations))

        rows = compact_delegations(state)

        self.assertEqual(len(rows), 5)
        self.assertEqual([row.identity for row in rows[:3]], ["failed", "working", "wait"])
        self.assertNotIn("done-old", [row.identity for row in rows])

    def test_missing_metrics_are_omitted(self):
        row = Delegation("w1", "worker", "work", "working", "balanced", "medium")
        rendered = format_delegation(row)
        self.assertNotIn("token", rendered.lower())
        self.assertNotIn("duration", rendered.lower())
        self.assertNotIn("not exposed", rendered.lower())

    def test_delegation_label_includes_observed_model_and_effort(self):
        row = Delegation("lead-1", "lead", "work", "working", "gpt-5", "high")
        self.assertIn("lead [gpt-5/high]", format_delegation(row))

    def test_malformed_control_is_inert(self):
        result = handle(self.payload("$symphony:symphony nonsense"), self.environ)
        self.assertIn("unknown symphony control", self.context(result).lower())
        self.assertIsNone(StateStore(self.state_root).load(self.project).active_run)


if __name__ == "__main__":
    unittest.main()
