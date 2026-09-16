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
        self.assertEqual(state.activation["codex"]["plugin_version"], "1.0.0")
        self.assertTrue(state.activation["codex"]["plugin_root"].endswith("plugins/symphony"))
        self.assertIn("guarded", self.context(result).lower())
        self.assertNotIn("unarmed", self.context(result).lower())

    def test_claude_marker_uses_the_same_control_contract(self):
        result = handle(self.payload("SYMPHONY_CONTROL: enable", "claude"), self.environ)
        self.assertIn("enabled", self.context(result).lower())
        self.assertTrue(StateStore(self.state_root).load(self.project).enabled)

    def test_raw_claude_slash_command_is_applied_before_skill_expansion(self):
        result = handle(self.payload("/symphony:enable", "claude"), self.environ)

        state = StateStore(self.state_root).load(self.project)
        self.assertTrue(state.enabled)
        self.assertIsNone(state.active_run)
        self.assertIn("enabled", self.context(result).lower())

    def test_raw_claude_start_preserves_the_full_task(self):
        handle(self.payload("/symphony:start Check the release safely", "claude"), self.environ)

        state = StateStore(self.state_root).load(self.project)
        self.assertEqual(state.active_run.task, "Check the release safely")

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

    def test_failed_lead_stays_recoverable_and_stop_remains_guarded(self):
        handle(self.payload("$symphony:symphony start Ship it"), self.environ)
        started = self.payload("")
        started.update(
            {
                "hook_event_name": "SubagentStart",
                "agent_id": "lead-1",
                "agent_type": "symphony_lead",
            }
        )
        handle(started, self.environ)
        handle({**started, "hook_event_name": "SubagentStop", "status": "failed"}, self.environ)

        state = StateStore(self.state_root).load(self.project)
        self.assertEqual(state.active_run.status, "recovering")
        self.assertEqual(state.active_run.delegations[-1].state, "failed")
        stop = {**self.payload(""), "hook_event_name": "Stop"}
        self.assertEqual(self.output(handle(stop, self.environ))["decision"], "block")

    def test_invalid_optional_metrics_are_ignored_without_losing_guard_state(self):
        handle(self.payload("$symphony:symphony start Ship it"), self.environ)
        started = {
            **self.payload(""),
            "hook_event_name": "SubagentStart",
            "agent_id": "worker-1",
            "agent_type": "worker",
            "tokens": "not-an-int",
            "duration_seconds": False,
        }
        handle(started, self.environ)

        state = StateStore(self.state_root).load(self.project)
        self.assertIsNotNone(state.active_run)
        self.assertIsNone(state.active_run.delegations[-1].tokens)
        stop = {**self.payload(""), "hook_event_name": "Stop"}
        self.assertEqual(self.output(handle(stop, self.environ))["decision"], "block")

    def test_disable_preserves_active_run_until_observed_agents_stop(self):
        handle(self.payload("$symphony:symphony enable Ship it"), self.environ)
        started = {
            **self.payload(""),
            "hook_event_name": "SubagentStart",
            "agent_id": "worker-1",
            "agent_type": "worker",
        }
        handle(started, self.environ)

        disabled = handle(self.payload("$symphony:symphony disable"), self.environ)
        stopping = StateStore(self.state_root).load(self.project)
        self.assertFalse(stopping.enabled)
        self.assertEqual(stopping.active_run.status, "stopping")
        self.assertIn("worker-1", self.context(disabled))

        handle({**started, "hook_event_name": "SubagentStop", "status": "completed"}, self.environ)
        finished = StateStore(self.state_root).load(self.project)
        self.assertIsNone(finished.active_run)
        self.assertEqual(finished.recent_runs[-1].status, "disabled")

    def test_session_start_reconciles_only_when_host_reports_active_ids(self):
        delegation = Delegation("lead-1", "lead", "work", "working", "", "")
        run = RunState("run-1", "task", status="interrupted", lead_identity="lead-1", delegations=(delegation,))
        StateStore(self.state_root).save(self.project, ProjectState(enabled=True, active_run=run))

        unknown = {**self.payload(""), "hook_event_name": "SessionStart"}
        unknown_result = handle(unknown, self.environ)
        self.assertEqual(
            self.output(unknown_result)["hookSpecificOutput"]["hookEventName"],
            "SessionStart",
        )
        self.assertEqual(StateStore(self.state_root).load(self.project).active_run.status, "interrupted")

        handle({**unknown, "active_agent_ids": []}, self.environ)
        self.assertEqual(StateStore(self.state_root).load(self.project).active_run.status, "recovering")

    def test_pre_tool_route_marker_persists_assessment_and_status(self):
        handle(self.payload("$symphony:symphony start Ship it"), self.environ)
        marker = json.dumps(
            {
                "size": "medium",
                "complexity": "mixed",
                "risk": "normal",
                "rationale": "bounded work",
                "topology": "mixed",
            }
        )
        hook = {
            **self.payload(""),
            "hook_event_name": "PreToolUse",
            "tool_name": "spawn_agent",
            "tool_input": {"message": f"SYMPHONY_ROUTE: {marker}\nShip it"},
        }
        handle(hook, self.environ)
        lead = {
            **self.payload(""),
            "hook_event_name": "SubagentStart",
            "agent_id": "lead-1",
            "agent_type": "lead",
        }
        handle(lead, self.environ)

        status = self.context(handle(self.payload("$symphony:symphony status"), self.environ))
        self.assertIn("Assessment: medium/mixed", status)
        self.assertIn("Topology: mixed", status)
        self.assertIn("Lead route: balanced/high", status)
        self.assertIn("Lead: lead-1", status)

    def test_legacy_provider_data_is_imported_once(self):
        legacy_root = self.root / "plugin-data"
        from plugins.symphony.symphony.store import legacy_project_key

        legacy = legacy_root / "projects" / f"{legacy_project_key(self.project)}.json"
        legacy.parent.mkdir(parents=True)
        legacy.write_text(json.dumps({"schema_version": 0, "enabled": True, "configuration": {"x": 1}}))
        environ = {**self.environ, "PLUGIN_DATA": str(legacy_root)}

        handle(self.payload("$symphony:symphony status"), environ)

        state = StateStore(self.state_root).load(self.project)
        self.assertTrue(state.enabled)
        self.assertEqual(state.configuration, {"x": 1})
        self.assertTrue(list(legacy.parent.glob(legacy.name + ".pre-1.0-*")))

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

    def test_single_word_codex_task_starts_a_one_shot_run(self):
        result = handle(self.payload("$symphony:symphony summarize"), self.environ)
        self.assertIn("assess", self.context(result).lower())
        self.assertEqual(StateStore(self.state_root).load(self.project).active_run.task, "summarize")

    def test_identical_claude_task_can_run_again_after_completion(self):
        prompt = "SYMPHONY_CONTROL: start\nARGUMENTS: Repeat me"
        handle(self.payload(prompt, "claude"), self.environ)
        store = StateStore(self.state_root)
        state = store.load(self.project)
        store.save(
            self.project,
            ProjectState(
                enabled=state.enabled,
                activation=state.activation,
                recent_runs=(state.active_run,),
                event_history=state.event_history,
            ),
        )

        handle(self.payload(prompt, "claude"), self.environ)

        self.assertEqual(store.load(self.project).active_run.task, "Repeat me")


if __name__ == "__main__":
    unittest.main()
