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
                    pending_path = path.with_suffix(".inspection.json")
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
        for attack in ("unissued", "consumed", "ordinary-prompt", "other-session", "other-turn"):
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
                    pending_path = self.hook.project_state_path(self.data, str(self.project)).with_suffix(".inspection.json")
                    self.assertFalse(pending_path.exists())
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

    def test_memory_marker_requires_exact_token_boundaries(self):
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: start\nSYMPHONY_TASK: task"),
            self.data,
            now=1_000,
        )
        run = self.state()["active_run"]
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

    def test_subagent_outputs_use_structured_hook_context(self):
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
                return json.loads(completed.stdout)

            invoke(
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "session-1",
                    "cwd": str(project),
                    "prompt": "SYMPHONY_CONTROL: start\nSYMPHONY_TASK: task",
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
            self.assertEqual("SubagentStop", stopped["hookSpecificOutput"]["hookEventName"])
            self.assertIn("integrate", stopped["hookSpecificOutput"]["additionalContext"])


if __name__ == "__main__":
    unittest.main()
