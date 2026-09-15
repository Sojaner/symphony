import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PLUGIN_ROOT / "scripts" / "symphony_hook.py"


def load_hook_module():
    spec = importlib.util.spec_from_file_location("symphony_hook", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SymphonyHookTests(unittest.TestCase):
    def setUp(self):
        self.hook = load_hook_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name) / "project"
        self.data = Path(self.tmp.name) / "data"
        self.project.mkdir()
        self.data.mkdir()

    def event(self, name, **values):
        payload = {
            "hook_event_name": name,
            "session_id": values.pop("session_id", "session-1"),
            "cwd": str(self.project),
        }
        payload.update(values)
        return payload

    def state(self):
        return self.hook.read_project_state(self.data, str(self.project))

    def test_legacy_runs_normalize_memory_before_child_and_final_checkpoints(self):
        for checkpoint_event in (None, "SubagentStop", "Stop"):
            with self.subTest(checkpoint_event=checkpoint_event):
                self.hook.handle_event(
                    self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: start\nSYMPHONY_TASK: legacy"),
                    self.data, now=1_000,
                )
                state = self.state()
                run = state["active_run"]
                run.pop("memory")
                run.pop("agent_records")
                state.pop("run_history")
                self.hook.write_project_state(self.data, state)
                memory = self.state()["active_run"]["memory"]
                self.assertEqual({
                    "enabled": False, "checkpoint_at": None,
                    "current": ".symphony/memory/current.md",
                    "history": f".symphony/memory/history/{run['id']}.md",
                }, memory)
                marker = f"<!-- SYMPHONY_MEMORY_CHECKPOINT:{run['id']}:codebase-memory-mcp -->"
                if checkpoint_event:
                    current = self.project / memory["current"]
                    current.parent.mkdir(parents=True, exist_ok=True)
                    current.write_text("# Legacy recovery checkpoint\n", encoding="utf-8")
                if checkpoint_event == "SubagentStop":
                    self.hook.handle_event(self.event("SubagentStart", agent_id="legacy-lead"), self.data)
                    self.hook.handle_event(
                        self.event("SubagentStop", agent_id="legacy-lead", last_assistant_message=marker),
                        self.data, now=1_001,
                    )
                    self.assertEqual(1_001, self.state()["active_run"]["memory"]["checkpoint_at"])
                result = self.hook.handle_event(
                    self.event("Stop", last_assistant_message=(
                        f"SYMPHONY_MODE:small\n{run['receipt']}\n" + (marker if checkpoint_event else "")
                    )), self.data, stop_wait_seconds=0,
                )
                self.assertFalse(result.block)
                self.assertIsNone(self.state()["active_run"])

    def test_malformed_memory_state_fails_closed_and_force_stop_recovers(self):
        for memory in (None, [], {"enabled": "false"}, {"enabled": True, "checkpoint_at": []}):
            with self.subTest(memory=memory):
                self.hook.handle_event(self.event("UserPromptSubmit", prompt="/symphony:start task"), self.data)
                state = self.state()
                state["active_run"]["memory"] = memory
                self.hook.write_project_state(self.data, state)
                blocked = self.hook.handle_event(self.event("Stop"), self.data, stop_wait_seconds=0)
                self.assertTrue(blocked.block)
                self.assertIn("corrupt", blocked.reason)
                self.hook.handle_event(self.event("UserPromptSubmit", prompt="/symphony:stop --force"), self.data)
                self.assertFalse(self.state()["corrupt"])
                self.assertIsNone(self.state()["active_run"])

    def test_terminal_agent_retains_metadata_without_worker_content(self):
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: start\nSYMPHONY_TASK: task"),
            self.data, now=1_000,
        )
        self.hook.handle_event(
            self.event("SubagentStart", agent_id="worker-1", agent_type="test-writer",
                       model="gpt-6-astra", reasoning_effort="high", prompt="private worker prompt"),
            self.data, now=1_001,
        )
        self.assertEqual(["worker-1"], self.state()["active_run"]["agents"])
        self.hook.handle_event(
            self.event("SubagentStop", agent_id="worker-1",
                       last_assistant_message="private worker output", transcript="private transcript"),
            self.data, now=1_002,
        )
        run = self.state()["active_run"]
        self.assertEqual([], run["agents"])
        self.assertEqual({
            "id": "worker-1", "status": "terminal", "role": "test-writer",
            "model": "gpt-6-astra", "effort": "high", "started_at": 1_001, "stopped_at": 1_002,
        }, run["agent_records"]["worker-1"])
        self.assertNotIn("private", json.dumps(self.state()))

    def test_late_child_stop_updates_its_archived_run_without_changing_new_run(self):
        self.hook.handle_event(self.event("UserPromptSubmit", prompt="/symphony:start old task"), self.data)
        self.hook.handle_event(self.event("SubagentStart", agent_id="old-worker"), self.data)
        self.hook.handle_event(self.event("UserPromptSubmit", prompt="/symphony:stop --force"), self.data)
        self.hook.handle_event(self.event("UserPromptSubmit", prompt="/symphony:start new task"), self.data)
        self.hook.handle_event(self.event("SubagentStart", agent_id="new-worker"), self.data)
        before = self.state()["active_run"]
        result = self.hook.handle_event(
            self.event("SubagentStop", agent_id="old-worker", model="gpt-6-astra",
                       last_assistant_message=(f"SYMPHONY_MODE:large\n{before['receipt']}\n"
                           f"SYMPHONY_MEMORY_CHECKPOINT:{before['id']}:codebase-memory-mcp")),
            self.data, now=2_000,
        )
        self.assertEqual(before, self.state()["active_run"])
        self.assertEqual("", self.hook.format_output(self.event("SubagentStop"), result))
        record = self.state()["run_history"][0]["agent_records"][0]
        self.assertEqual(("terminal", 2_000, "gpt-6-astra"),
                         (record["status"], record["stopped_at"], record["model"]))
        path = self.hook.project_state_path(self.data, str(self.project))
        unchanged = path.read_bytes()
        self.hook.handle_event(
            self.event("SubagentStop", agent_id="unknown-worker", last_assistant_message="SYMPHONY_MODE:large"),
            self.data,
        )
        self.assertEqual(unchanged, path.read_bytes())
        self.hook.handle_event(self.event("UserPromptSubmit", prompt="/symphony:stop --force"), self.data)
        self.hook.handle_event(self.event("SubagentStop", agent_id="new-worker"), self.data)
        self.assertEqual("terminal", self.state()["run_history"][-1]["agent_records"][0]["status"])

    def test_raw_commands_control_lifecycle_without_becoming_work(self):
        for command in ("help", "status", "agents", "agents --all", "stop", "disable", "start"):
            with self.subTest(command=command):
                self.hook.handle_event(self.event("UserPromptSubmit", prompt=f"/symphony:{command}"), self.data)
                self.assertIsNone(self.state()["active_run"])
        self.hook.handle_event(self.event("UserPromptSubmit", prompt="/symphony:enable"), self.data)
        self.assertTrue(self.state()["enabled"])
        for command in ("help", "status", "agents", "agents --all", "start"):
            self.hook.handle_event(self.event("UserPromptSubmit", prompt=f"/symphony:{command}"), self.data)
            self.assertIsNone(self.state()["active_run"])
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="/symphony:start first line\nsecond line"), self.data,
        )
        run = self.state()["active_run"]
        self.assertEqual("first line\nsecond line", run["objective"])
        self.hook.handle_event(self.event("SubagentStart", agent_id="raw-worker"), self.data)
        path = self.hook.project_state_path(self.data, str(self.project))
        before = path.read_bytes()
        listing = self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="/symphony:agents"), self.data,
        )
        self.assertIn("raw-worker", listing.context)
        self.assertEqual(before, path.read_bytes())
        inspected = self.hook.handle_event(
            self.event("Stop", last_assistant_message=listing.context.splitlines()[-1]),
            self.data, stop_wait_seconds=0,
        )
        self.assertFalse(inspected.block)
        self.assertEqual(before, path.read_bytes())
        self.hook.handle_event(self.event("UserPromptSubmit", prompt="/symphony:stop --force"), self.data)
        self.assertIsNone(self.state()["active_run"])
        listing = self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="/symphony:agents --all"), self.data,
        )
        self.assertIn(run["id"], listing.context)
        self.assertIn("raw-worker", listing.context)
        self.hook.handle_event(self.event("UserPromptSubmit", prompt="/symphony:enable next task"), self.data)
        self.assertEqual("next task", self.state()["active_run"]["objective"])
        self.hook.handle_event(self.event("UserPromptSubmit", prompt="/symphony:disable"), self.data)
        self.assertFalse(self.state()["enabled"])
        self.assertEqual("stopping", self.state()["active_run"]["status"])
        self.hook.handle_event(self.event("Stop"), self.data, stop_wait_seconds=0)
        for prompt in ("Discuss /symphony:enable", "/symphony:enable-other", "`/symphony:enable`"):
            self.hook.handle_event(self.event("UserPromptSubmit", prompt=prompt), self.data)
            self.assertFalse(self.state()["enabled"])
            self.assertIsNone(self.state()["active_run"])

    def test_shipped_command_templates_handle_empty_and_multiline_arguments(self):
        enable = (PLUGIN_ROOT / "commands" / "enable.md").read_text(encoding="utf-8")
        for argument in ("", "$ARGUMENTS", "\n"):
            self.hook.handle_event(
                self.event("UserPromptSubmit", prompt=enable.replace("$ARGUMENTS", argument)), self.data,
            )
            self.assertTrue(self.state()["enabled"])
            self.assertIsNone(self.state()["active_run"])
        task = "first line\nsecond line\n\nlast line"
        for command in ("enable", "start"):
            template = (PLUGIN_ROOT / "commands" / f"{command}.md").read_text(encoding="utf-8")
            self.hook.handle_event(
                self.event("UserPromptSubmit", prompt=template.replace("$ARGUMENTS", task)), self.data,
            )
            self.assertEqual(task, self.state()["active_run"]["objective"])
            stop = (PLUGIN_ROOT / "commands" / "stop.md").read_text(encoding="utf-8")
            self.hook.handle_event(
                self.event("UserPromptSubmit", prompt=stop.replace("$ARGUMENTS", "\n--force\n")), self.data,
            )
            self.assertIsNone(self.state()["active_run"])
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: start\nSYMPHONY_TASK: " + task),
            self.data,
        )
        self.assertEqual(task, self.state()["active_run"]["objective"])

    def test_agents_all_includes_completed_runs_without_changing_state(self):
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: enable\nSYMPHONY_TASK: task"),
            self.data, now=1_000,
        )
        run = self.state()["active_run"]
        self.hook.handle_event(
            self.event("SubagentStart", agent_id="worker-1", agent_type="test-writer",
                       model="gpt-6-astra", reasoning_effort="high"),
            self.data, now=1_001,
        )
        self.hook.handle_event(self.event("SubagentStop", agent_id="worker-1"), self.data, now=1_002)
        result = self.hook.handle_event(
            self.event("Stop", last_assistant_message=f"SYMPHONY_MODE:small\n{run['receipt']}"),
            self.data, now=1_003, stop_wait_seconds=0,
        )
        self.assertFalse(result.block)
        state_path = self.hook.project_state_path(self.data, str(self.project))
        before = state_path.read_bytes()
        current = self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: agents"), self.data,
        )
        historical = self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: agents\nSYMPHONY_ARGS: --all"),
            self.data,
        )
        self.assertIn("No active Symphony run", current.context)
        self.assertNotIn("worker-1", current.context)
        self.assertIn(run["id"], historical.context)
        self.assertIn("worker-1", historical.context)
        self.assertIn("gpt-6-astra", historical.context)
        self.assertEqual(before, state_path.read_bytes())
        archived = self.state()["run_history"][0]
        self.assertEqual("completed", archived["status"])
        self.assertEqual("small", archived["mode"])
        self.assertEqual(1_000, archived["created_at"])
        self.assertEqual(1_003, archived["completed_at"])

    def test_agents_missing_metadata_is_explicit_and_listing_is_read_only(self):
        state_path = self.hook.project_state_path(self.data, str(self.project))
        result = self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: agents"), self.data,
        )
        self.assertIn("No active Symphony run", result.context)
        self.assertFalse(state_path.exists())
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: start\nSYMPHONY_TASK: task"),
            self.data,
        )
        self.hook.handle_event(self.event("SubagentStart", agent_id="worker-1"), self.data)
        before = state_path.read_bytes()
        result = self.hook.handle_event(
            self.event("UserPromptSubmit", session_id="session-2", prompt="SYMPHONY_CONTROL: agents"),
            self.data,
        )
        self.assertIn("worker-1", result.context)
        self.assertEqual(3, result.context.count("not exposed by host"))
        self.assertEqual(before, state_path.read_bytes())

    def test_agents_support_legacy_state_without_rewriting_it(self):
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: start\nSYMPHONY_TASK: legacy task"),
            self.data,
        )
        legacy = self.state()
        legacy.pop("run_history")
        legacy["active_run"].pop("agent_records")
        legacy["active_run"]["agents"] = ["legacy-worker"]
        self.hook.write_project_state(self.data, legacy)
        path = self.hook.project_state_path(self.data, str(self.project))
        before = path.read_bytes()
        current = self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: agents"), self.data,
        )
        self.assertIn("legacy-worker", current.context)
        self.assertEqual(3, current.context.count("not exposed by host"))
        self.assertEqual(before, path.read_bytes())
        self.hook.handle_event(self.event("SubagentStop", agent_id="legacy-worker"), self.data)
        record = self.state()["active_run"]["agent_records"]["legacy-worker"]
        self.assertEqual("terminal", record["status"])
        self.assertIsNone(record["started_at"])

    def test_all_stop_paths_retain_history_and_all_includes_active_run(self):
        run_ids = []
        for index, control in enumerate(("stop", "disable", "stop\nSYMPHONY_ARGS: --force")):
            self.hook.handle_event(
                self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: start\nSYMPHONY_TASK: task"),
                self.data,
            )
            run_ids.append(self.state()["active_run"]["id"])
            self.hook.handle_event(
                self.event("SubagentStart", agent_id=f"worker-{index}", effort="medium"), self.data,
            )
            if index < 2:
                self.hook.handle_event(self.event("SubagentStop", agent_id=f"worker-{index}"), self.data)
            self.hook.handle_event(
                self.event("UserPromptSubmit", prompt=f"SYMPHONY_CONTROL: {control}"), self.data,
            )
            self.hook.handle_event(self.event("Stop"), self.data, stop_wait_seconds=0)
        history = self.state()["run_history"]
        self.assertEqual(["stopped", "stopped", "force-stopped"], [run["status"] for run in history])
        self.assertEqual("active", history[-1]["agent_records"][0]["status"])
        self.assertEqual("medium", history[-1]["agent_records"][0]["effort"])
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: start\nSYMPHONY_TASK: new task"),
            self.data,
        )
        run_ids.append(self.state()["active_run"]["id"])
        self.hook.handle_event(self.event("SubagentStart", agent_id="current-worker"), self.data)
        before = self.state()
        historical = self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: agents\nSYMPHONY_ARGS: --all"),
            self.data,
        )
        for run_id in run_ids:
            self.assertIn(run_id, historical.context)
        self.assertIn("current-worker", historical.context)
        self.assertEqual(before, self.state())

    def test_agents_does_not_quarantine_corrupt_state(self):
        path = self.hook.project_state_path(self.data, str(self.project))
        path.parent.mkdir(parents=True)
        path.write_text("{broken", encoding="utf-8")
        result = self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: agents"), self.data,
        )
        self.assertIn("corrupt", result.context.lower())
        stopped = self.hook.handle_event(
            self.event("Stop", last_assistant_message=result.context.splitlines()[-1]),
            self.data, stop_wait_seconds=0,
        )
        self.assertFalse(stopped.block)
        self.assertEqual("{broken", path.read_text(encoding="utf-8"))
        self.assertFalse(list(path.parent.glob("*.corrupt.*")))

    def test_inspection_response_stop_preserves_active_and_stopping_runs(self):
        for stopping in (False, True):
            for args in ("", "--all"):
                with self.subTest(stopping=stopping, args=args):
                    self.hook.handle_event(
                        self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: start\nSYMPHONY_TASK: task"),
                        self.data,
                    )
                    run = self.state()["active_run"]
                    if not stopping:
                        self.hook.handle_event(self.event("SubagentStart", agent_id="worker-1"), self.data)
                    if stopping:
                        self.hook.handle_event(
                            self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: stop"), self.data,
                        )
                    path = self.hook.project_state_path(self.data, str(self.project))
                    before = path.read_bytes()
                    listing = self.hook.handle_event(
                        self.event("UserPromptSubmit", prompt=f"SYMPHONY_CONTROL: agents\nSYMPHONY_ARGS: {args}"),
                        self.data,
                    )
                    receipt = listing.context.splitlines()[-1]
                    self.assertNotIn(run["id"], receipt)
                    pending_path = self.hook.inspection_path(self.data, str(self.project), "session-1")
                    self.assertTrue(pending_path.exists())
                    self.assertEqual({"nonce", "run_id", "session_id", "turn_id"},
                                     set(json.loads(pending_path.read_text(encoding="utf-8"))))
                    result = self.hook.handle_event(
                        self.event("Stop", last_assistant_message=f"Inspection report.\n{receipt}",
                                   background_tasks=[{"id": "background-1", "status": "running"}]),
                        self.data, stop_wait_seconds=0,
                    )
                    self.assertFalse(result.block)
                    self.assertEqual(before, path.read_bytes())
                    self.assertFalse(pending_path.exists())
                    normal = self.hook.handle_event(
                        self.event("Stop", last_assistant_message="ordinary response"),
                        self.data, stop_wait_seconds=0,
                    )
                    self.assertEqual(not stopping, normal.block)
                    self.hook.handle_event(
                        self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: stop\nSYMPHONY_ARGS: --force"),
                        self.data,
                    )

    def test_inspection_requires_issued_nonce_and_rejects_replay_and_other_identity(self):
        for attack in ("unissued", "consumed", "ordinary-prompt", "other-session", "other-turn", "corrupt-pending", "old-run"):
            with self.subTest(attack=attack):
                self.hook.handle_event(
                    self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: start\nSYMPHONY_TASK: task"),
                    self.data,
                )
                run = self.state()["active_run"]
                receipt = f"<!-- SYMPHONY_AGENTS_INSPECTED:{'a' * 32} -->"
                if attack != "unissued":
                    listing = self.hook.handle_event(
                        self.event("UserPromptSubmit", turn_id="inspection-turn",
                                   prompt="SYMPHONY_CONTROL: agents"), self.data,
                    )
                    receipt = listing.context.splitlines()[-1]
                if attack == "consumed":
                    first = self.hook.handle_event(
                        self.event("Stop", turn_id="inspection-turn", last_assistant_message=receipt),
                        self.data, stop_wait_seconds=0,
                    )
                    self.assertFalse(first.block)
                if attack == "ordinary-prompt":
                    self.hook.handle_event(self.event("UserPromptSubmit", prompt="continue work"), self.data)
                    pending_path = self.hook.inspection_path(self.data, str(self.project), "session-1")
                    self.assertFalse(pending_path.exists())
                if attack == "corrupt-pending":
                    pending_path = self.hook.inspection_path(self.data, str(self.project), "session-1")
                    pending_path.write_text("{broken", encoding="utf-8")
                if attack == "old-run":
                    self.hook.handle_event(
                        self.event("UserPromptSubmit", session_id="session-2", prompt="/symphony:stop --force"),
                        self.data,
                    )
                    self.hook.handle_event(
                        self.event("UserPromptSubmit", session_id="session-2", prompt="/symphony:start next task"),
                        self.data,
                    )
                    run = self.state()["active_run"]
                result = self.hook.handle_event(
                    self.event("Stop", last_assistant_message=receipt,
                               session_id="session-2" if attack == "other-session" else "session-1",
                               turn_id="another-turn" if attack == "other-turn" else "inspection-turn"),
                    self.data, stop_wait_seconds=0,
                )
                self.assertTrue(result.block)
                self.assertEqual(run, self.state()["active_run"])
                self.hook.handle_event(
                    self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: stop\nSYMPHONY_ARGS: --force"),
                    self.data,
                )

    def test_inspection_authorizations_are_independent_per_session(self):
        self.hook.handle_event(self.event("UserPromptSubmit", prompt="/symphony:start task"), self.data)
        path = self.hook.project_state_path(self.data, str(self.project))
        before = path.read_bytes()
        receipts = {}
        for session in ("session-A", "session-B"):
            listing = self.hook.handle_event(
                self.event("UserPromptSubmit", session_id=session, turn_id="inspect", prompt="/symphony:agents"),
                self.data,
            )
            receipts[session] = listing.context.splitlines()[-1]
        for session, receipt in receipts.items():
            result = self.hook.handle_event(
                self.event("Stop", session_id=session, turn_id="inspect", last_assistant_message=receipt),
                self.data, stop_wait_seconds=0,
            )
            self.assertFalse(result.block)
        self.assertEqual(before, path.read_bytes())
        for session in receipts:
            listing = self.hook.handle_event(
                self.event("UserPromptSubmit", session_id=session, prompt="/symphony:agents"), self.data,
            )
            receipts[session] = listing.context.splitlines()[-1]
        self.hook.handle_event(
            self.event("UserPromptSubmit", session_id="session-A", prompt="continue"), self.data,
        )
        for session, receipt in receipts.items():
            result = self.hook.handle_event(
                self.event("Stop", session_id=session, last_assistant_message=receipt),
                self.data, stop_wait_seconds=0,
            )
            self.assertEqual(session == "session-A", result.block)
        self.assertEqual(before, path.read_bytes())

    def test_malformed_agent_ledgers_allow_listing_and_force_stop(self):
        for malformed in (None, "broken", 42, [None, "bad", {}], {"worker-1": None}):
            with self.subTest(malformed=malformed):
                self.hook.handle_event(
                    self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: start\nSYMPHONY_TASK: task"),
                    self.data,
                )
                state = self.state()
                state["active_run"]["agents"] = ["worker-1"]
                state["active_run"]["agent_records"] = malformed
                state["run_history"] = malformed
                self.hook.write_project_state(self.data, state)
                path = self.hook.project_state_path(self.data, str(self.project))
                before = path.read_bytes()
                result = self.hook.handle_event(
                    self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: agents\nSYMPHONY_ARGS: --all"),
                    self.data,
                )
                self.assertIn("worker-1", result.context)
                self.assertEqual(before, path.read_bytes())
                self.hook.handle_event(
                    self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: stop\nSYMPHONY_ARGS: --force"),
                    self.data,
                )
                self.assertIsNone(self.state()["active_run"])
                self.assertEqual("force-stopped", self.state()["run_history"][-1]["status"])

    def test_mixed_history_preserves_valid_agents_and_ignores_malformed_records(self):
        state = self.state()
        state["run_history"] = [None, {}, {
            "id": "old-run", "status": "completed", "agent_records": [None, {}, {
                "id": "old-worker", "status": "terminal", "model": "gpt-6-astra",
                "role": "reviewer", "effort": "high", "started_at": 1, "stopped_at": 2,
            }],
        }, {"id": "null-records", "status": "stopped", "agent_records": None}]
        self.hook.write_project_state(self.data, state)
        path = self.hook.project_state_path(self.data, str(self.project))
        before = path.read_bytes()
        result = self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: agents\nSYMPHONY_ARGS: --all"), self.data,
        )
        self.assertIn("old-worker", result.context)
        self.assertIn("gpt-6-astra", result.context)
        self.assertIn("null-records", result.context)
        self.assertEqual(before, path.read_bytes())

    def test_inspection_receipt_must_match_run_and_end_response(self):
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: start\nSYMPHONY_TASK: task"), self.data,
        )
        run = self.state()["active_run"]
        listing = self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: agents"), self.data,
        )
        receipt = listing.context.splitlines()[-1]
        for message in ("<!-- SYMPHONY_AGENTS_INSPECTED:none -->",
                        receipt + "\nNow doing project work."):
            result = self.hook.handle_event(
                self.event("Stop", last_assistant_message=message), self.data, stop_wait_seconds=0,
            )
            self.assertTrue(result.block)
        self.assertEqual(run, self.state()["active_run"])

    def test_stop_event_merges_exposed_metadata_without_erasing_known_values(self):
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: start\nSYMPHONY_TASK: task"), self.data,
        )
        self.hook.handle_event(self.event("SubagentStart", agent_id="worker-1"), self.data)
        self.hook.handle_event(
            self.event("SubagentStop", agent_id="worker-1", agent_type="reviewer",
                       model="gpt-6-astra", reasoning_effort="high"), self.data,
        )
        record = self.state()["active_run"]["agent_records"]["worker-1"]
        self.assertEqual(("reviewer", "gpt-6-astra", "high"),
                         (record["role"], record["model"], record["effort"]))
        self.hook.handle_event(
            self.event("SubagentStop", agent_id="worker-1", agent_type=None,
                       model="not exposed by host", reasoning_effort=""), self.data,
        )
        record = self.state()["active_run"]["agent_records"]["worker-1"]
        self.assertEqual(("reviewer", "gpt-6-astra", "high"),
                         (record["role"], record["model"], record["effort"]))

    def test_new_run_exposes_memory_candidates_without_creating_documents(self):
        result = self.hook.handle_event(
            self.event(
                "UserPromptSubmit",
                prompt="SYMPHONY_CONTROL: start\nSYMPHONY_TASK: retain project facts",
            ),
            self.data,
        )

        run = self.state()["active_run"]
        self.assertEqual(
            {
                "enabled": False,
                "current": ".symphony/memory/current.md",
                "history": f".symphony/memory/history/{run['id']}.md",
                "checkpoint_at": None,
            },
            run["memory"],
        )
        self.assertIn(str(self.project / run["memory"]["current"]), result.context)
        self.assertIn("codebase-memory-mcp", result.context)
        self.assertFalse((self.project / ".symphony").exists())

    def test_memory_marker_activates_only_for_the_current_run(self):
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: start\nSYMPHONY_TASK: task"),
            self.data,
            now=1_000,
        )
        run = self.state()["active_run"]
        self.hook.handle_event(self.event("SubagentStart", agent_id="lead-1"), self.data)
        other_run_id = "a" * 16 if run["id"] != "a" * 16 else "b" * 16

        self.hook.handle_event(
            self.event(
                "SubagentStop",
                agent_id="lead-1",
                last_assistant_message=(
                    f"<!-- SYMPHONY_MEMORY_CHECKPOINT:{other_run_id}:codebase-memory-mcp -->"
                ),
            ),
            self.data,
            now=1_001,
        )
        self.assertFalse(self.state()["active_run"]["memory"]["enabled"])

        self.hook.handle_event(
            self.event(
                "SubagentStop",
                agent_id="lead-1",
                last_assistant_message=(
                    f"<!-- SYMPHONY_MEMORY_CHECKPOINT:{run['id']}:codebase-memory-mcp -->"
                ),
            ),
            self.data,
            now=1_002,
        )
        memory = self.state()["active_run"]["memory"]
        self.assertTrue(memory["enabled"])
        self.assertEqual(1_002, memory["checkpoint_at"])

        result = self.hook.handle_event(
            self.event(
                "Stop",
                last_assistant_message=(
                    f"<!-- SYMPHONY_MODE:small -->\n"
                    f"<!-- SYMPHONY_MEMORY_CHECKPOINT:{other_run_id}:codebase-memory-mcp -->\n"
                    f"<!-- {run['receipt']} -->"
                ),
            ),
            self.data,
            now=1_003,
            stop_wait_seconds=0,
        )
        self.assertTrue(result.block)
        self.assertIn("matching checkpoint receipt", result.reason)

    def test_activated_memory_can_degrade_and_finish_from_compact_recovery(self):
        for boundary, missing in (("SubagentStop", "mcp"), ("Stop", "index"),
                                  ("Stop", "current"), ("SubagentStop", "history")):
            with self.subTest(boundary=boundary, missing=missing):
                self.hook.handle_event(self.event("UserPromptSubmit", prompt="/symphony:start task"), self.data)
                run = self.state()["active_run"]
                for field in ("current", "history"):
                    document = self.project / run["memory"][field]
                    document.parent.mkdir(parents=True, exist_ok=True)
                    document.write_text("# Verified checkpoint\n", encoding="utf-8")
                self.hook.handle_event(self.event("SubagentStart", agent_id=run["id"]), self.data)
                self.hook.handle_event(
                    self.event("SubagentStop", agent_id=run["id"], last_assistant_message=(
                        f"SYMPHONY_MEMORY_CHECKPOINT:{run['id']}:codebase-memory-mcp"
                    )), self.data, now=1_234,
                )
                recovery = self.hook.handle_event(self.event("SessionStart", source="compact"), self.data)
                self.assertIn("enabled=true", recovery.context)
                self.assertIn("checkpoint_at=1234", recovery.context)
                marker = f"SYMPHONY_MEMORY_UNAVAILABLE:{run['id']}:codebase-memory-mcp"
                self.assertIn(marker, recovery.context)
                if missing in ("current", "history"):
                    (self.project / run["memory"][missing]).unlink()
                result = self.hook.handle_event(
                    self.event(boundary, agent_id=run["id"], last_assistant_message=f"{missing} unavailable\n<!-- {marker} -->"),
                    self.data, now=1_235, stop_wait_seconds=0,
                )
                memory = self.state()["active_run"]["memory"]
                self.assertFalse(memory["enabled"])
                self.assertEqual(1_234, memory["checkpoint_at"])
                recovery = self.hook.handle_event(self.event("SessionStart", source="resume"), self.data)
                self.assertIn("enabled=false", recovery.context)
                self.assertIn("checkpoint_at=1234", recovery.context)
                completed = self.hook.handle_event(
                    self.event("Stop", last_assistant_message=f"SYMPHONY_MODE:small\n{run['receipt']}"),
                    self.data, stop_wait_seconds=0,
                )
                self.assertFalse(completed.block)
                self.assertIsNone(self.state()["active_run"])

    def test_unavailable_memory_receipt_is_run_bound_and_preserves_checkpoint_guard(self):
        self.hook.handle_event(self.event("UserPromptSubmit", prompt="/symphony:start task"), self.data)
        run = self.state()["active_run"]
        for field in ("current", "history"):
            document = self.project / run["memory"][field]
            document.parent.mkdir(parents=True, exist_ok=True)
            document.write_text("# Verified checkpoint\n", encoding="utf-8")
        checkpoint = f"SYMPHONY_MEMORY_CHECKPOINT:{run['id']}:codebase-memory-mcp"
        self.hook.handle_event(
            self.event("Stop", last_assistant_message=checkpoint), self.data, now=1_234, stop_wait_seconds=0,
        )
        unavailable = f"SYMPHONY_MEMORY_UNAVAILABLE:{run['id']}:codebase-memory-mcp"
        other_id = "a" * 16 if run["id"] != "a" * 16 else "b" * 16
        for marker in ("", f"NOT_{unavailable}", f"{unavailable}-other", unavailable.replace(run["id"], other_id)):
            result = self.hook.handle_event(
                self.event("Stop", last_assistant_message=f"SYMPHONY_MODE:small\n{run['receipt']}\n{marker}"),
                self.data, stop_wait_seconds=0,
            )
            self.assertTrue(result.block)
            self.assertIn("matching checkpoint receipt", result.reason)
            self.assertTrue(self.state()["active_run"]["memory"]["enabled"])
            self.assertEqual(1_234, self.state()["active_run"]["memory"]["checkpoint_at"])
        # An observed loss takes precedence over an earlier checkpoint copied into the same response.
        result = self.hook.handle_event(
            self.event("Stop", last_assistant_message=(
                f"SYMPHONY_MODE:small\n{run['receipt']}\n{checkpoint}\n{unavailable}"
            )), self.data, stop_wait_seconds=0,
        )
        self.assertFalse(result.block)
        self.assertIsNone(self.state()["active_run"])

    def test_memory_marker_requires_exact_token_boundaries(self):
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: start\nSYMPHONY_TASK: task"),
            self.data,
            now=1_000,
        )
        run = self.state()["active_run"]
        self.hook.handle_event(self.event("SubagentStart", agent_id="lead-1"), self.data)
        marker = f"SYMPHONY_MEMORY_CHECKPOINT:{run['id']}:codebase-memory-mcp"

        for malformed in (f"NOT_{marker}", f"{marker}-other"):
            self.hook.handle_event(
                self.event("SubagentStop", agent_id="lead-1", last_assistant_message=malformed),
                self.data,
                now=1_001,
            )
            self.assertFalse(self.state()["active_run"]["memory"]["enabled"])

        self.hook.handle_event(
            self.event(
                "SubagentStop",
                agent_id="lead-1",
                last_assistant_message=f"Completed work <!-- {marker} --> in surrounding prose.",
            ),
            self.data,
            now=1_002,
        )
        self.assertTrue(self.state()["active_run"]["memory"]["enabled"])

    def test_background_task_stop_block_persists_memory_checkpoint(self):
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: start\nSYMPHONY_TASK: task"),
            self.data,
            now=1_000,
        )
        run = self.state()["active_run"]
        marker = f"SYMPHONY_MEMORY_CHECKPOINT:{run['id']}:codebase-memory-mcp"
        message = f"<!-- SYMPHONY_MODE:small -->\n<!-- {marker} -->\n<!-- {run['receipt']} -->"

        blocked = self.hook.handle_event(
            self.event(
                "Stop",
                last_assistant_message=message,
                background_tasks=[{"id": "shell-1", "status": "running"}],
            ),
            self.data,
            now=1_001,
            stop_wait_seconds=0,
        )
        self.assertTrue(blocked.block)
        self.assertIn("background tasks", blocked.reason)
        self.assertTrue(self.state()["active_run"]["memory"]["enabled"])

        final = self.hook.handle_event(
            self.event("Stop", last_assistant_message=f"<!-- SYMPHONY_MODE:small -->\n<!-- {run['receipt']} -->"),
            self.data,
            now=1_002,
            stop_wait_seconds=0,
        )
        self.assertTrue(final.block)
        self.assertIn("matching checkpoint receipt", final.reason)

    def test_tracked_agent_stop_block_persists_memory_checkpoint(self):
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: start\nSYMPHONY_TASK: task"),
            self.data,
            now=1_000,
        )
        run = self.state()["active_run"]
        marker = f"SYMPHONY_MEMORY_CHECKPOINT:{run['id']}:codebase-memory-mcp"
        message = f"<!-- SYMPHONY_MODE:small -->\n<!-- {marker} -->\n<!-- {run['receipt']} -->"
        self.hook.handle_event(
            self.event("SubagentStart", agent_id="worker-1", agent_type="worker"),
            self.data,
            now=1_001,
        )

        blocked = self.hook.handle_event(
            self.event("Stop", last_assistant_message=message),
            self.data,
            now=1_002,
            stop_wait_seconds=0,
        )
        self.assertTrue(blocked.block)
        self.assertIn("worker-1", blocked.reason)
        self.assertTrue(self.state()["active_run"]["memory"]["enabled"])

        self.hook.handle_event(
            self.event("SubagentStop", agent_id="worker-1", last_assistant_message="finished"),
            self.data,
            now=1_003,
        )
        final = self.hook.handle_event(
            self.event("Stop", last_assistant_message=f"<!-- SYMPHONY_MODE:small -->\n<!-- {run['receipt']} -->"),
            self.data,
            now=1_004,
            stop_wait_seconds=0,
        )
        self.assertTrue(final.block)
        self.assertIn("matching checkpoint receipt", final.reason)

    def test_active_memory_blocks_completion_when_current_document_is_missing(self):
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: start\nSYMPHONY_TASK: task"),
            self.data,
            now=1_000,
        )
        run = self.state()["active_run"]
        marker = f"SYMPHONY_MEMORY_CHECKPOINT:{run['id']}:codebase-memory-mcp"

        result = self.hook.handle_event(
            self.event(
                "Stop",
                last_assistant_message=(
                    f"<!-- SYMPHONY_MODE:small -->\n<!-- {marker} -->\n<!-- {run['receipt']} -->"
                ),
            ),
            self.data,
            now=1_003,
            stop_wait_seconds=0,
        )

        self.assertTrue(result.block)
        self.assertIn("current.md", result.reason)
        self.assertTrue(self.state()["active_run"]["memory"]["enabled"])

    def test_active_memory_completes_with_fresh_nonempty_current_document(self):
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: start\nSYMPHONY_TASK: task"),
            self.data,
        )
        run = self.state()["active_run"]
        current = self.project / run["memory"]["current"]
        current.parent.mkdir(parents=True)
        current.write_text("# Symphony Current Memory\n\n## Run\nactive\n", encoding="utf-8")
        marker = f"SYMPHONY_MEMORY_CHECKPOINT:{run['id']}:codebase-memory-mcp"

        result = self.hook.handle_event(
            self.event(
                "Stop",
                last_assistant_message=(
                    f"<!-- SYMPHONY_MODE:small -->\n<!-- {marker} -->\n<!-- {run['receipt']} -->"
                ),
            ),
            self.data,
            stop_wait_seconds=0,
        )

        self.assertFalse(result.block)
        self.assertIsNone(self.state()["active_run"])

    def test_active_memory_blocks_completion_when_current_document_is_stale(self):
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: start\nSYMPHONY_TASK: task"),
            self.data,
            now=1_000,
        )
        run = self.state()["active_run"]
        current = self.project / run["memory"]["current"]
        current.parent.mkdir(parents=True)
        current.write_text("# Symphony Current Memory\n", encoding="utf-8")
        os.utime(current, (1, 1))
        marker = f"SYMPHONY_MEMORY_CHECKPOINT:{run['id']}:codebase-memory-mcp"

        result = self.hook.handle_event(
            self.event(
                "Stop",
                last_assistant_message=(
                    f"<!-- SYMPHONY_MODE:small -->\n<!-- {marker} -->\n<!-- {run['receipt']} -->"
                ),
            ),
            self.data,
            now=1_003,
            stop_wait_seconds=0,
        )

        self.assertTrue(result.block)
        self.assertIn("stale", result.reason)

    def test_enable_persists_without_starting_when_task_is_empty(self):
        result = self.hook.handle_event(
            self.event(
                "UserPromptSubmit",
                prompt="SYMPHONY_CONTROL: enable\nSYMPHONY_TASK:",
            ),
            self.data,
        )

        self.assertTrue(self.state()["enabled"])
        self.assertIsNone(self.state()["active_run"])
        self.assertIn("enabled", result.context.lower())

    def test_help_and_status_do_not_create_project_state(self):
        state_path = self.hook.project_state_path(self.data, str(self.project))

        help_result = self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: help"),
            self.data,
        )
        status_result = self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: status"),
            self.data,
        )

        self.assertEqual("", help_result.context)
        self.assertIn("Active run: none", status_result.context)
        self.assertFalse(state_path.exists())

    def test_unexpanded_argument_placeholder_is_empty(self):
        self.hook.handle_event(
            self.event(
                "UserPromptSubmit",
                prompt="SYMPHONY_CONTROL: enable\nSYMPHONY_TASK: $ARGUMENTS",
            ),
            self.data,
        )

        self.assertTrue(self.state()["enabled"])
        self.assertIsNone(self.state()["active_run"])

    def test_enabled_project_automatically_arms_next_prompt(self):
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: enable\nSYMPHONY_TASK:"),
            self.data,
        )

        result = self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="Implement the parser"),
            self.data,
        )

        run = self.state()["active_run"]
        self.assertIsNotNone(run)
        self.assertEqual("session-1", run["owner_session_id"])
        self.assertIn("Invoke the installed Symphony skill first", result.context)
        self.assertIn("strong execution lead", result.context)
        self.assertIn(run["receipt"], result.context)

    def test_start_is_one_off_and_does_not_enable_project(self):
        result = self.hook.handle_event(
            self.event(
                "UserPromptSubmit",
                prompt="SYMPHONY_CONTROL: start\nSYMPHONY_TASK: fix the parser",
            ),
            self.data,
        )

        state = self.state()
        self.assertFalse(state["enabled"])
        self.assertIsNotNone(state["active_run"])
        self.assertIn("fix the parser", result.context)

    def test_duplicate_session_recovers_existing_run(self):
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: start\nSYMPHONY_TASK: task"),
            self.data,
        )

        result = self.hook.handle_event(
            self.event("UserPromptSubmit", session_id="session-2", prompt="continue"),
            self.data,
        )

        self.assertEqual("session-1", self.state()["active_run"]["owner_session_id"])
        self.assertIn("Recovery required", result.context)

    def test_resume_and_compaction_reinject_active_run(self):
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: start\nSYMPHONY_TASK: task"),
            self.data,
        )

        for source in ("resume", "compact"):
            result = self.hook.handle_event(
                self.event("SessionStart", source=source),
                self.data,
            )
            self.assertIn("Recover Symphony run", result.context)

    def test_subagent_lifecycle_blocks_stop_until_receipt(self):
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: start\nSYMPHONY_TASK: task"),
            self.data,
        )
        run = self.state()["active_run"]
        self.hook.handle_event(
            self.event("SubagentStart", agent_id="worker-1", agent_type="worker"),
            self.data,
        )

        blocked = self.hook.handle_event(
            self.event("Stop", last_assistant_message="done", stop_hook_active=False),
            self.data,
            stop_wait_seconds=0,
        )
        self.assertTrue(blocked.block)
        self.assertIn("worker-1", blocked.reason)

        self.hook.handle_event(
            self.event(
                "SubagentStop",
                agent_id="worker-1",
                agent_type="worker",
                last_assistant_message="<!-- SYMPHONY_MODE: medium -->",
            ),
            self.data,
        )
        self.assertEqual("medium", self.state()["active_run"]["mode"])
        missing_receipt = self.hook.handle_event(
            self.event("Stop", last_assistant_message="done", stop_hook_active=True),
            self.data,
            stop_wait_seconds=0,
        )
        self.assertTrue(missing_receipt.block)

        complete = self.hook.handle_event(
            self.event(
                "Stop",
                last_assistant_message=f"<!-- SYMPHONY_MODE: medium -->\n{run['receipt']}",
            ),
            self.data,
            stop_wait_seconds=0,
        )
        self.assertFalse(complete.block)
        self.assertIsNone(self.state()["active_run"])
        self.assertFalse((self.project / ".symphony").exists())

    def test_claude_background_task_blocks_stop(self):
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: start\nSYMPHONY_TASK: task"),
            self.data,
        )

        result = self.hook.handle_event(
            self.event(
                "Stop",
                last_assistant_message="done",
                background_tasks=[{"id": "shell-1", "status": "running"}],
            ),
            self.data,
            stop_wait_seconds=0,
        )

        self.assertTrue(result.block)
        self.assertIn("background tasks", result.reason)

    def test_completion_without_any_mode_is_blocked(self):
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: start\nSYMPHONY_TASK: dry run"),
            self.data,
        )
        run = self.state()["active_run"]

        result = self.hook.handle_event(
            self.event("Stop", last_assistant_message=run["receipt"]),
            self.data,
            stop_wait_seconds=0,
        )

        self.assertTrue(result.block)
        self.assertIn("mode", result.reason)
        self.assertIsNotNone(self.state()["active_run"])

    def test_unrelated_background_task_does_not_block_without_a_run(self):
        result = self.hook.handle_event(
            self.event(
                "Stop",
                last_assistant_message="done",
                background_tasks=[{"id": "shell-1", "status": "running"}],
            ),
            self.data,
            stop_wait_seconds=0,
        )

        self.assertFalse(result.block)

    def test_stop_preserves_enablement_and_disable_removes_it(self):
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: enable\nSYMPHONY_TASK: task"),
            self.data,
        )
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: stop"),
            self.data,
        )
        stopped = self.hook.handle_event(
            self.event("Stop", last_assistant_message="stopped"),
            self.data,
            stop_wait_seconds=0,
        )
        self.assertFalse(stopped.block)
        self.assertTrue(self.state()["enabled"])

        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="next task"),
            self.data,
        )
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: disable"),
            self.data,
        )
        self.hook.handle_event(
            self.event("Stop", last_assistant_message="disabled"),
            self.data,
            stop_wait_seconds=0,
        )
        state = self.state()
        self.assertFalse(state["enabled"])
        self.assertIsNone(state["active_run"])

    def test_force_stop_clears_run_even_with_tracked_agent(self):
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: start\nSYMPHONY_TASK: task"),
            self.data,
        )
        self.hook.handle_event(
            self.event("SubagentStart", agent_id="worker-1", agent_type="worker"),
            self.data,
        )

        result = self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: stop\nSYMPHONY_ARGS: --force"),
            self.data,
        )

        self.assertIsNone(self.state()["active_run"])
        self.assertIn("may still be running", result.context)

    def test_corrupt_state_is_quarantined_and_blocks_stop(self):
        state_path = self.hook.project_state_path(self.data, str(self.project))
        state_path.parent.mkdir(parents=True)
        state_path.write_text("{broken", encoding="utf-8")

        state = self.state()
        self.assertTrue(state["corrupt"])
        self.assertTrue(list(state_path.parent.glob(state_path.name + ".corrupt.*")))

        result = self.hook.handle_event(
            self.event("Stop", last_assistant_message="done"),
            self.data,
            stop_wait_seconds=0,
        )
        self.assertTrue(result.block)
        self.assertIn("corrupt", result.reason.lower())

        recovered = self.hook.handle_event(
            self.event(
                "UserPromptSubmit",
                prompt="SYMPHONY_CONTROL: stop\nSYMPHONY_ARGS: --force",
            ),
            self.data,
        )
        self.assertIn("force-stopped", recovered.context)
        self.assertFalse(self.state()["corrupt"])

        started = self.hook.handle_event(
            self.event(
                "UserPromptSubmit",
                prompt="SYMPHONY_CONTROL: start\nSYMPHONY_TASK: recovered task",
            ),
            self.data,
        )
        self.assertIn("strong execution lead", started.context)
        self.assertIsNotNone(self.state()["active_run"])

    def test_assessment_state_normalizes_legacy_runs(self):
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="/symphony:start legacy task"), self.data,
        )
        state = self.state()
        state.pop("assessment", None)
        for field in ("mode_revision", "assessment_due", "mode_history"):
            state["active_run"].pop(field, None)
        self.hook.write_project_state(self.data, state)

        state = self.state()
        self.assertEqual({
            "profile": None,
            "source": None,
            "revision": 0,
            "reason": None,
            "assessed_at": None,
        }, state.get("assessment"))
        self.assertEqual(0, state["active_run"].get("mode_revision"))
        self.assertTrue(state["active_run"].get("assessment_due"))
        self.assertEqual([], state["active_run"].get("mode_history"))

    def test_assess_commands_persist_clear_and_request_profiles(self):
        command_path = PLUGIN_ROOT / "commands" / "assess.md"
        self.assertTrue(command_path.is_file())
        template = command_path.read_text(encoding="utf-8")

        for source in ("raw", "template"):
            with self.subTest(source=source):
                self.data = Path(self.tmp.name) / f"data-{source}"
                self.data.mkdir()
                self.hook.handle_event(
                    self.event("UserPromptSubmit", prompt="/symphony:start task"), self.data, now=1_000,
                )

                def assess(argument, now):
                    prompt = (
                        f"/symphony:assess{f' {argument}' if argument else ''}"
                        if source == "raw" else template.replace("$ARGUMENTS", argument)
                    )
                    return self.hook.handle_event(
                        self.event("UserPromptSubmit", prompt=prompt), self.data, now=now,
                    )

                assess("large", 1_001)
                state = self.state()
                self.assertEqual("large", state["assessment"]["profile"])
                self.assertEqual("manual", state["assessment"]["source"])
                self.assertEqual(1, state["assessment"]["revision"])
                self.assertIsInstance(state["assessment"]["reason"], str)
                self.assertEqual(1_001, state["assessment"]["assessed_at"])
                self.assertTrue(state["active_run"]["assessment_due"])

                state = self.state()
                state["active_run"]["assessment_due"] = False
                self.hook.write_project_state(self.data, state, now=1_002)
                assess("", 1_003)
                state = self.state()
                self.assertEqual("large", state["assessment"]["profile"])
                self.assertEqual("manual", state["assessment"]["source"])
                self.assertEqual(1, state["assessment"]["revision"])
                self.assertEqual(1_001, state["assessment"]["assessed_at"])
                self.assertTrue(state["active_run"]["assessment_due"])

                assess("auto", 1_004)
                state = self.state()
                self.assertEqual({
                    "profile": None,
                    "source": None,
                    "revision": 2,
                    "reason": None,
                    "assessed_at": None,
                }, state["assessment"])
                self.assertTrue(state["active_run"]["assessment_due"])

                state["active_run"]["assessment_due"] = False
                self.hook.write_project_state(self.data, state, now=1_005)
                before = self.state()
                result = assess("invalid", 1_006)
                self.assertIn("Usage", result.context)
                self.assertEqual(before, self.state())

                self.hook.handle_event(
                    self.event("UserPromptSubmit", prompt="/symphony:stop --force"), self.data,
                )

    def test_assessment_receipts_are_authorized_and_versioned(self):
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="/symphony:start task"), self.data, now=1_000,
        )
        state = self.state()
        run = state["active_run"]
        run["id"] = "0123456789abcdef"
        run["receipt"] = "SYMPHONY_RUN_COMPLETE:0123456789abcdef"
        self.hook.write_project_state(self.data, state, now=1_000)
        receipt = (
            "SYMPHONY_ASSESSMENT:0123456789abcdef:large:medium\n"
            "SYMPHONY_ASSESSMENT_REASON:Long-running repository with two independent work units"
        )
        self.hook.handle_event(
            self.event("SubagentStart", agent_id="assessor", agent_type="symphony_assessor"),
            self.data, now=1_001,
        )
        self.hook.handle_event(
            self.event("SubagentStop", agent_id="assessor", last_assistant_message=receipt),
            self.data, now=1_002,
        )
        state = self.state()
        run = state["active_run"]
        self.assertEqual(("large", "automatic", 1), (
            state["assessment"]["profile"], state["assessment"]["source"], state["assessment"]["revision"],
        ))
        self.assertEqual(("medium", 1, False), (run["mode"], run["mode_revision"], run["assessment_due"]))
        self.assertEqual([{
            "mode": "medium", "profile": "large", "source": "automatic",
            "reason": "Long-running repository with two independent work units",
            "revision": 1, "assessed_at": 1_002,
        }], run["mode_history"])

        before = json.dumps(state, sort_keys=True)
        for message, agent_id in (
            (receipt.replace("0123456789abcdef", "fedcba9876543210", 1), "assessor"),
            (receipt.replace(":large:medium", ":invalid:medium"), "assessor"),
            (receipt, "unowned"),
        ):
            self.assertFalse(self.hook._record_assessment_receipt(state, run, message, 1_003, agent_id))
            self.assertEqual(before, json.dumps(state, sort_keys=True))

        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="/symphony:assess small"), self.data, now=1_004,
        )
        state = self.state()
        run = state["active_run"]
        self.assertTrue(self.hook._record_assessment_receipt(state, run, receipt, 1_005, "assessor"))
        self.assertEqual(("small", "manual", 2), (
            state["assessment"]["profile"], state["assessment"]["source"], state["assessment"]["revision"],
        ))
        self.assertEqual((2, 2), (run["mode_revision"], len(run["mode_history"])))
        self.assertLessEqual(len(state["assessment"]["reason"]), 500)
        self.hook.write_project_state(self.data, state, now=1_005)

        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="/symphony:assess auto"), self.data, now=1_006,
        )
        state = self.state()
        run = state["active_run"]
        changed = receipt.replace(":large:medium", ":medium:large").replace(
            "Long-running repository with two independent work units", "x" * 600,
        ) + "\nnot retained"
        self.assertTrue(self.hook._record_assessment_receipt(state, run, changed, 1_007, "assessor"))
        self.assertEqual(("medium", "automatic", 4), (
            state["assessment"]["profile"], state["assessment"]["source"], state["assessment"]["revision"],
        ))
        self.assertEqual(("large", 3), (run["mode"], run["mode_revision"]))
        self.assertEqual("x" * 500, state["assessment"]["reason"])
        self.assertEqual("x" * 500, run["mode_history"][-1]["reason"])

    def test_reassessment_triggers_at_material_boundaries(self):
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="/symphony:start task"), self.data, now=1_000,
        )
        self.assertTrue(self.state()["active_run"]["assessment_due"])
        self.hook.handle_event(
            self.event("SubagentStart", agent_id="assessor", agent_type="symphony_assessor"), self.data,
        )
        run = self.state()["active_run"]
        receipt = f"SYMPHONY_ASSESSMENT:{run['id']}:small:small\nSYMPHONY_ASSESSMENT_REASON:Routine task"

        def clear_due(now):
            state = self.state()
            self.assertTrue(self.hook._record_assessment_receipt(
                state, state["active_run"], receipt, now, "assessor",
            ))
            self.hook.write_project_state(self.data, state, now=now)

        clear_due(1_001)
        self.hook.handle_event(self.event("UserPromptSubmit", prompt="continue with the parser"), self.data, now=1_002)
        self.assertTrue(self.state()["active_run"]["assessment_due"])
        clear_due(1_003)
        self.hook.handle_event(self.event("UserPromptSubmit", prompt="/symphony:status"), self.data, now=1_004)
        self.assertFalse(self.state()["active_run"]["assessment_due"])
        self.hook.handle_event(self.event("SessionStart", source="compact"), self.data, now=1_005)
        self.assertTrue(self.state()["active_run"]["assessment_due"])
        clear_due(1_006)
        self.hook.handle_event(self.event("Interrupt"), self.data, now=1_007)
        self.assertTrue(self.state()["active_run"]["assessment_due"])

        self.hook.handle_event(
            self.event("SubagentStart", agent_id="lead", agent_type="symphony_lead"), self.data, now=1_008,
        )
        self.hook.handle_event(
            self.event("SubagentStart", agent_id="worker-a", agent_type="worker"), self.data, now=1_009,
        )
        self.hook.handle_event(
            self.event("SubagentStart", agent_id="worker-b", agent_type="worker"), self.data, now=1_010,
        )
        clear_due(1_011)
        self.hook.handle_event(self.event("SubagentStop", agent_id="worker-a"), self.data, now=1_012)
        self.assertFalse(self.state()["active_run"]["assessment_due"])
        self.hook.handle_event(self.event("SubagentStop", agent_id="worker-b"), self.data, now=1_013)
        self.assertTrue(self.state()["active_run"]["assessment_due"])
        clear_due(1_014)
        self.hook.handle_event(self.event("SubagentStop", agent_id="assessor"), self.data, now=1_015)
        self.assertFalse(self.state()["active_run"]["assessment_due"])

    def test_suggestion_receipt_sets_thirty_day_cooldown(self):
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: start\nSYMPHONY_TASK: task"),
            self.data,
            now=1_000_000,
        )
        run = self.state()["active_run"]

        self.hook.handle_event(
            self.event(
                "Stop",
                last_assistant_message=(
                    f"<!-- SYMPHONY_MODE: small -->\n{run['receipt']}\nSYMPHONY_SUGGESTED:context7\n"
                    "SYMPHONY_SUGGESTED:codebase-memory"
                ),
            ),
            self.data,
            now=1_000_000,
            stop_wait_seconds=0,
        )

        state = self.state()
        self.assertEqual(["context7"], list(state["suggestions"]))
        self.assertFalse(self.hook.can_suggest(state, "context7", now=1_000_001))
        self.assertTrue(
            self.hook.can_suggest(
                state,
                "context7",
                now=1_000_000 + self.hook.SUGGESTION_COOLDOWN_SECONDS,
            )
        )


class HookDeclarationTests(unittest.TestCase):
    def test_documentation_and_manifests_describe_memory_release(self):
        readme = (PLUGIN_ROOT.parents[1] / "README.md").read_text(encoding="utf-8")
        help_text = (PLUGIN_ROOT / "commands" / "help.md").read_text(encoding="utf-8")
        for text in (readme, help_text):
            self.assertIn(".symphony/memory/current.md", text)
            self.assertIn("codebase-memory-mcp", text)
            self.assertIn(
                "Manually ignoring `.symphony/memory/` disables indexed history until the ignore policy changes.",
                text,
            )
        versions = {
            json.loads((PLUGIN_ROOT / relative).read_text(encoding="utf-8"))["version"]
            for relative in (".claude-plugin/plugin.json", ".codex-plugin/plugin.json")
        }
        self.assertEqual({"0.14.0"}, versions)

    def test_agent_inspection_command_and_live_host_contract(self):
        command = (PLUGIN_ROOT / "commands" / "agents.md").read_text(encoding="utf-8")
        self.assertIn("SYMPHONY_CONTROL: agents", command)
        self.assertIn("SYMPHONY_ARGS: $ARGUMENTS", command)
        self.assertIn("live agent-listing tool", command)
        self.assertIn("not exposed by host", command)
        for relative in ("commands/help.md", "skills/symphony/SKILL.md"):
            content = (PLUGIN_ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("/symphony:agents [--all]", content)

    def test_skill_defines_mcp_gated_document_memory(self):
        skill = (PLUGIN_ROOT / "skills" / "symphony" / "SKILL.md").read_text(encoding="utf-8")
        required = (
            "Extended document memory",
            ".symphony/memory/current.md",
            ".symphony/memory/history/<run-id>.md",
            "SYMPHONY_MEMORY_CHECKPOINT:<run-id>:codebase-memory-mcp",
            "The strong lead is the only writer",
            "## Objective and acceptance criteria",
            "## Verification evidence",
            "check_index_coverage",
            "after selecting or changing mode",
            "after a material decision or discovery",
            "before dispatching a worker wave",
            "immediately before successful or graceful completion",
            "for every successful run",
            "When extended memory is active, also relay",
            "root relays it in the root-final response",
            "`current.md` is compact and bounded",
            "timestamp, run id, mode, reason for the checkpoint, changed facts, decisions, evidence, and next action",
            "short search terms",
            "relevant history headings",
            "qualified code symbols",
            "graph generation",
            "evidence paths",
        )
        for text in required:
            self.assertIn(text, skill)

    def test_hook_declarations_and_manifest_are_wired(self):
        for relative in ("hooks/hooks.json", "hooks/codex.json"):
            declaration = json.loads((PLUGIN_ROOT / relative).read_text(encoding="utf-8"))
            commands = [
                hook["command"]
                for matchers in declaration["hooks"].values()
                for matcher in matchers
                for hook in matcher["hooks"]
            ]
            self.assertTrue(commands)
            self.assertTrue(
                all(
                    '${CLAUDE_PLUGIN_ROOT}/scripts/symphony_hook.py' in command
                    for command in commands
                )
            )

        manifest = json.loads(
            (PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        self.assertEqual("./hooks/codex.json", manifest["hooks"])

    def test_ci_runs_lifecycle_tests(self):
        workflow = (PLUGIN_ROOT.parents[1] / ".github" / "workflows" / "plugin-eval.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("python3 -m unittest discover -s plugins/symphony/tests -v", workflow)

    def test_subagent_start_gets_context_and_terminal_stop_emits_no_continuation(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            data = Path(temporary) / "data"
            project.mkdir()
            data.mkdir()
            env = os.environ.copy()
            env["PLUGIN_DATA"] = str(data)
            env["SYMPHONY_STOP_WAIT_SECONDS"] = "0"

            def invoke(payload):
                completed = subprocess.run(
                    [sys.executable, str(SCRIPT_PATH)],
                    input=json.dumps(payload),
                    capture_output=True,
                    text=True,
                    env=env,
                    check=True,
                )
                return json.loads(completed.stdout) if completed.stdout else None

            invoke(
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "session-1",
                    "cwd": str(project),
                    "prompt": "/symphony:start task",
                }
            )
            started = invoke(
                {
                    "hook_event_name": "SubagentStart",
                    "session_id": "session-1",
                    "cwd": str(project),
                    "agent_id": "worker-1",
                    "agent_type": "worker",
                }
            )
            stopped = invoke(
                {
                    "hook_event_name": "SubagentStop",
                    "session_id": "session-1",
                    "cwd": str(project),
                    "agent_id": "worker-1",
                    "agent_type": "worker",
                    "last_assistant_message": "finished",
                }
            )

            self.assertEqual("SubagentStart", started["hookSpecificOutput"]["hookEventName"])
            self.assertIn("capability receipt", started["hookSpecificOutput"]["additionalContext"])
            self.assertIsNone(stopped)
            state = load_hook_module().read_project_state(data, str(project))
            self.assertEqual("terminal", state["active_run"]["agent_records"]["worker-1"]["status"])
            self.assertEqual([], state["active_run"]["agents"])


if __name__ == "__main__":
    unittest.main()
