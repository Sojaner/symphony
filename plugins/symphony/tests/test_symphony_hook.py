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

        self.hook.handle_event(
            self.event(
                "SubagentStop",
                agent_id="lead-1",
                last_assistant_message=(
                    "<!-- SYMPHONY_MEMORY_CHECKPOINT:not-this-run:codebase-memory-mcp -->"
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
