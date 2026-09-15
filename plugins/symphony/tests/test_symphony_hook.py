import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


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

    def start_role(self, agent_id, role, agent_type="general-purpose", now=1_000):
        self.hook.handle_event(
            self.event("SubagentStart", agent_id=agent_id, agent_type=agent_type), self.data, now=now,
        )
        run = self.state()["active_run"]
        self.hook.handle_event(self.event("Stop", last_assistant_message=(
            f"SYMPHONY_REGISTER:{run['id']}:{role}:{agent_id}"
        )), self.data, now=now, stop_wait_seconds=0)

    def set_current_assessment(self, mode="small"):
        state = self.state()
        run = state["active_run"]
        run.update({
            "mode": mode,
            "mode_revision": max(1, run["mode_revision"]),
            "assessment_due": False,
            "strong_assessment_required": False,
        })
        run["mode_history"] = run["mode_history"] or [{"mode": mode}]
        self.hook.write_project_state(self.data, state)

    def test_worker_mode_marker_and_conflicting_completion_cannot_change_accepted_mode(self):
        self.hook.handle_event(self.event("UserPromptSubmit", prompt="/symphony:start task"), self.data)
        run = self.state()["active_run"]
        self.set_current_assessment("medium")
        receipt = f"SYMPHONY_ASSESSMENT:{run['id']}:large:medium\nSYMPHONY_ASSESSMENT_REASON:Bounded task"
        self.hook.handle_event(self.event("SubagentStart", agent_id="worker"), self.data)
        self.hook.handle_event(self.event("SubagentStop", agent_id="worker", last_assistant_message=(
            "SYMPHONY_MODE:large\n" + receipt.replace(":large:medium", ":large:large")
        )), self.data)
        current = self.state()["active_run"]
        self.assertEqual(("medium", 1, "medium"), (
            current["mode"], current["mode_revision"], current["mode_history"][-1]["mode"],
        ))
        for marker in ("SYMPHONY_MODE:large", "SYMPHONY_MODE:medium\nSYMPHONY_MODE:small"):
            result = self.hook.handle_event(self.event("Stop", last_assistant_message=(
                marker + "\n" + run["receipt"]
            )), self.data, stop_wait_seconds=0)
            self.assertTrue(result.block)
            self.assertEqual("medium", self.state()["active_run"]["mode"])

    def test_terminal_assessor_replay_and_fresh_assessment_invalidate_old_authority(self):
        self.hook.handle_event(self.event("UserPromptSubmit", prompt="/symphony:start task"), self.data)
        self.start_role("assessor", "assessor")
        run = self.state()["active_run"]
        receipt = f"SYMPHONY_ASSESSMENT:{run['id']}:large:medium\nSYMPHONY_ASSESSMENT_REASON:Bounded task"
        self.hook.handle_event(self.event("SubagentStop", agent_id="assessor", last_assistant_message=receipt), self.data)
        self.assertEqual(1, self.state()["active_run"]["mode_revision"])
        state = self.state()
        state["active_run"]["agent_records"]["assessor"]["usage"] = {
            "final_request_total_tokens": 12,
            "source": "claude-post-tool-use",
            "scope": "final-agent-request",
        }
        self.hook.write_project_state(self.data, state)
        before = self.state()["assessment"]
        self.hook.handle_event(self.event("SubagentStop", agent_id="assessor", last_assistant_message=receipt), self.data)
        self.assertEqual(1, self.state()["active_run"]["mode_revision"])
        self.assertEqual(before, self.state()["assessment"])
        self.hook.handle_event(self.event("SubagentStart", agent_id="assessor", agent_type="general-purpose"), self.data)
        resumed = self.state()["active_run"]
        self.assertEqual("active", resumed["agent_records"]["assessor"]["status"])
        self.assertEqual("symphony_assessor", resumed["agent_records"]["assessor"]["role"])
        self.assertEqual(1_000, resumed["agent_records"]["assessor"]["started_at"])
        self.assertEqual(12, resumed["agent_records"]["assessor"]["usage"]["final_request_total_tokens"])
        self.assertIn("assessor", resumed["agents"])
        blocked = self.hook.handle_event(
            self.event("Stop", last_assistant_message=resumed["receipt"]),
            self.data, stop_wait_seconds=0,
        )
        self.assertTrue(blocked.block)
        self.hook.handle_event(self.event("UserPromptSubmit", prompt="/symphony:assess"), self.data)
        self.assertIsNone(self.state()["active_run"]["assessor_agent_id"])
        self.hook.handle_event(self.event("SubagentStop", agent_id="assessor", last_assistant_message=receipt), self.data)
        run = self.state()["active_run"]
        self.assertEqual((1, True, True), (run["mode_revision"], run["assessment_due"], run["strong_assessment_required"]))
        self.start_role("fresh", "assessor", agent_type="symphony:assessor")
        self.hook.handle_event(self.event("UserPromptSubmit", prompt="/symphony:assess"), self.data)
        self.hook.handle_event(self.event("SubagentStop", agent_id="fresh", last_assistant_message=receipt), self.data)
        self.assertTrue(self.state()["active_run"]["strong_assessment_required"])

    def test_roles_require_owner_registration_for_real_host_types(self):
        self.hook.handle_event(self.event("UserPromptSubmit", prompt="/symphony:start task"), self.data)
        run = self.state()["active_run"]
        registration = f"SYMPHONY_REGISTER:{run['id']}:assessor:spoof"
        receipt = f"SYMPHONY_ASSESSMENT:{run['id']}:large:medium\nSYMPHONY_ASSESSMENT_REASON:Spoofed role"
        self.hook.handle_event(self.event("SubagentStart", agent_id="spoof", agent_type="symphony_assessor"), self.data)
        self.assertIsNone(self.state()["active_run"]["assessor_agent_id"])
        for session_id, agent_id in (("foreign", None), ("session-1", "spoof"), (None, None)):
            self.hook.handle_event(self.event("Stop", session_id=session_id, agent_id=agent_id,
                last_assistant_message=registration), self.data, stop_wait_seconds=0)
            self.assertIsNone(self.state()["active_run"]["assessor_agent_id"])
        self.hook.handle_event(self.event("SubagentStop", agent_id="spoof", last_assistant_message=registration + "\n" + receipt), self.data)
        self.assertEqual(0, self.state()["active_run"]["mode_revision"])
        for invalid in (registration.replace(run["id"], "0" * 16), registration.replace(":spoof", ":unobserved")):
            self.hook.handle_event(self.event("Stop", last_assistant_message=invalid), self.data, stop_wait_seconds=0)
            self.assertIsNone(self.state()["active_run"]["assessor_agent_id"])
        self.start_role("assessor", "assessor", "general-purpose")
        self.assertEqual("assessor", self.state()["active_run"]["assessor_agent_id"])
        self.hook.handle_event(self.event("Stop", last_assistant_message=(
            f"SYMPHONY_REGISTER:{run['id']}:lead:assessor"
        )), self.data, stop_wait_seconds=0)
        self.assertIsNone(self.state()["active_run"]["lead_agent_id"])
        self.hook.handle_event(self.event("SubagentStop", agent_id="assessor", last_assistant_message=receipt), self.data)
        self.assertEqual(1, self.state()["active_run"]["mode_revision"])
        self.start_role("lead-a", "lead", "symphony:executor")
        self.start_role("lead-b", "lead", "general-purpose")
        self.assertEqual("lead-a", self.state()["active_run"]["lead_agent_id"])
        self.hook.handle_event(self.event("SubagentStop", agent_id="lead-a"), self.data)
        self.hook.handle_event(self.event("Stop", last_assistant_message=(
            f"SYMPHONY_REGISTER:{run['id']}:lead:lead-b"
        )), self.data, stop_wait_seconds=0)
        self.assertEqual("lead-b", self.state()["active_run"]["lead_agent_id"])
        self.hook.handle_event(self.event("Stop", last_assistant_message=receipt), self.data, stop_wait_seconds=0)
        self.hook.handle_event(self.event("SubagentStop", agent_id="lead-b", agent_type="general-purpose"), self.data)
        self.assertFalse(self.state()["active_run"]["assessment_due"])
        self.assertEqual("symphony_lead", self.state()["active_run"]["agent_records"]["lead-b"]["role"])

    def test_synchronous_agent_roles_and_assessment_register_on_root_relay(self):
        self.hook.handle_event(self.event("UserPromptSubmit", prompt="/symphony:start task"), self.data)
        run = self.state()["active_run"]
        receipt = f"SYMPHONY_ASSESSMENT:{run['id']}:large:medium\nSYMPHONY_ASSESSMENT_REASON:Bounded task"
        for role, host_type in (("assessor", "general-purpose"), ("lead", "symphony:executor")):
            self.hook.handle_event(self.event("SubagentStart", agent_id=role, agent_type=host_type), self.data)
            self.hook.handle_event(self.event("SubagentStop", agent_id=role, agent_type=host_type,
                                             last_assistant_message=receipt), self.data)
            result = self.hook.handle_event(self.event("Stop", last_assistant_message=(
                f"SYMPHONY_REGISTER:{run['id']}:{role}:{role}\n" + receipt
            )), self.data, stop_wait_seconds=0)
            self.assertIn("Symphony registered roles", result.reason)
            self.assertIn("Symphony accepted assessment", result.reason)
            self.assertEqual(role, self.state()["active_run"][f"{role}_agent_id"])
            self.assertEqual("medium", self.state()["active_run"]["mode"])
            self.assertFalse(self.state()["active_run"]["assessment_due"])
        self.start_role("new-assessor", "assessor")
        self.assertTrue(self.state()["active_run"]["strong_assessment_required"])
        self.hook.handle_event(self.event("SubagentStop", agent_id="new-assessor",
            last_assistant_message=receipt.replace(":large:medium", ":large:large")), self.data)
        self.assertEqual("large", self.state()["active_run"]["mode"])

    def test_status_reports_assessment_fields_without_lifecycle_writes(self):
        self.hook.handle_event(self.event("UserPromptSubmit", prompt="/symphony:assess large"), self.data)
        for active in (False, True):
            if active:
                self.hook.handle_event(self.event("UserPromptSubmit", prompt="/symphony:start task"), self.data)
            path = self.hook.project_state_path(self.data, str(self.project))
            before = path.read_bytes()
            result = self.hook.handle_event(self.event("UserPromptSubmit", prompt="/symphony:status"), self.data)
            for field in ("Project profile: large", "profile source: manual", "assessment revision: 1",
                          "mode revision: " + ("0" if active else "none"),
                          "reassessment due: " + ("true" if active else "no active run")):
                self.assertIn(field, result.context)
            self.assertEqual(before, path.read_bytes())
            self.hook.handle_event(self.event("Stop", last_assistant_message=result.context.splitlines()[-1]),
                                   self.data, stop_wait_seconds=0)
            self.assertEqual(before, path.read_bytes())

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
                self.set_current_assessment()
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

    def test_every_terminal_control_gets_a_single_use_stop_receipt_in_every_run_state(self):
        controls = {
            "help": "/symphony:help",
            "status": "/symphony:status",
            "agents": "/symphony:agents",
            "agents-all": "/symphony:agents --all",
            "enable-empty": "/symphony:enable",
            "disable": "/symphony:disable",
            "stop": "/symphony:stop",
            "force-stop": "/symphony:stop --force",
            "start-empty": "/symphony:start",
            "assess": "/symphony:assess",
            "assess-profile": "/symphony:assess large",
            "assess-auto": "/symphony:assess auto",
            "assess-invalid": "/symphony:assess gigantic",
            "malformed-raw": "/symphony:not-a-command",
            "malformed-marker": "SYMPHONY_CONTROL: not-a-command\nSYMPHONY_TASK: ignored",
        }
        for state_name in ("no-run", "starting", "active", "stopping"):
            for control_name, prompt in controls.items():
                with self.subTest(state=state_name, control=control_name):
                    data = Path(self.tmp.name) / f"control-{state_name}-{control_name}"
                    data.mkdir()
                    if state_name != "no-run":
                        self.hook.handle_event(
                            self.event("UserPromptSubmit", prompt="/symphony:start task"), data,
                        )
                        if state_name == "active":
                            self.hook.handle_event(
                                self.event("SubagentStart", agent_id="worker"), data,
                            )
                        elif state_name == "stopping":
                            state = self.hook.read_project_state(data, str(self.project))
                            state["active_run"]["status"] = "stopping"
                            self.hook.write_project_state(data, state)

                    response = self.hook.handle_event(
                        self.event("UserPromptSubmit", turn_id="control-turn", prompt=prompt), data,
                    )
                    receipt = response.context.splitlines()[-1]
                    self.assertRegex(
                        receipt, r"^<!-- SYMPHONY_CONTROL_HANDLED:[a-f0-9]{32} -->$",
                    )
                    after_control = self.hook.read_project_state(data, str(self.project))
                    hook_time = mock.Mock(wraps=self.hook.time)
                    hook_time.sleep.side_effect = AssertionError("control entered Stop wait")
                    with mock.patch.object(self.hook, "time", hook_time):
                        stopped = self.hook.handle_event(
                            self.event(
                                "Stop", turn_id="control-turn", last_assistant_message=receipt,
                            ),
                            data, stop_wait_seconds=55,
                        )
                    self.assertFalse(stopped.block)
                    self.assertEqual(
                        after_control, self.hook.read_project_state(data, str(self.project)),
                    )

    def test_empty_start_is_terminal_usage_without_work_instructions_for_active_runs(self):
        prompts = (
            "/symphony:start",
            "SYMPHONY_CONTROL: start\nSYMPHONY_TASK:",
        )
        forbidden = (
            "Delegating:", "Reassessment is due", "read-only symphony_assessor",
            "execution lead", "Continue Symphony run", "reconcile tracked agents",
        )
        for state_name in ("starting", "active", "stopping"):
            for index, prompt in enumerate(prompts):
                with self.subTest(state=state_name, prompt=prompt):
                    data = Path(self.tmp.name) / f"empty-start-{state_name}-{index}"
                    data.mkdir()
                    self.hook.handle_event(
                        self.event("UserPromptSubmit", prompt="/symphony:start task"), data,
                    )
                    if state_name == "active":
                        self.hook.handle_event(
                            self.event("SubagentStart", agent_id="worker"), data,
                        )
                    elif state_name == "stopping":
                        state = self.hook.read_project_state(data, str(self.project))
                        state["active_run"]["status"] = "stopping"
                        self.hook.write_project_state(data, state)
                    before = self.hook.read_project_state(data, str(self.project))

                    response = self.hook.handle_event(
                        self.event("UserPromptSubmit", prompt=prompt), data,
                    )

                    self.assertIn("Usage: /symphony:start [--dry-run] <task>", response.context)
                    self.assertIn("SYMPHONY_CONTROL_HANDLED", response.context)
                    for text in forbidden:
                        self.assertNotIn(text, response.context)
                    self.assertEqual(before, self.hook.read_project_state(data, str(self.project)))

    def test_control_receipt_is_consumed_and_next_prompt_invalidates_it(self):
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="/symphony:start task"), self.data,
        )
        first = self.hook.handle_event(
            self.event("UserPromptSubmit", turn_id="first", prompt="/symphony:status"), self.data,
        ).context.splitlines()[-1]
        accepted = self.hook.handle_event(
            self.event("Stop", turn_id="first", last_assistant_message=first),
            self.data, stop_wait_seconds=0,
        )
        replayed = self.hook.handle_event(
            self.event("Stop", turn_id="first", last_assistant_message=first),
            self.data, stop_wait_seconds=0,
        )
        self.assertFalse(accepted.block)
        self.assertTrue(replayed.block)

        second = self.hook.handle_event(
            self.event("UserPromptSubmit", turn_id="second", prompt="/symphony:help"), self.data,
        ).context.splitlines()[-1]
        self.hook.handle_event(
            self.event("UserPromptSubmit", turn_id="project", prompt="continue"), self.data,
        )
        invalidated = self.hook.handle_event(
            self.event("Stop", turn_id="second", last_assistant_message=second),
            self.data, stop_wait_seconds=0,
        )
        self.assertTrue(invalidated.block)

    def test_malformed_control_arguments_are_terminal_and_side_effect_free(self):
        prompts = (
            "/symphony:help extra", "/symphony:status extra", "/symphony:agents --bogus",
            "/symphony:disable extra", "/symphony:stop --bogus",
            "SYMPHONY_CONTROL: stop\nSYMPHONY_ARGS: --force extra",
        )
        for state_name in ("no-run", "starting", "active", "stopping"):
            for index, prompt in enumerate(prompts):
                with self.subTest(state=state_name, prompt=prompt):
                    data = Path(self.tmp.name) / f"malformed-{state_name}-{index}"
                    data.mkdir()
                    if state_name != "no-run":
                        self.hook.handle_event(
                            self.event("UserPromptSubmit", prompt="/symphony:start task"), data,
                        )
                    if state_name == "active":
                        self.hook.handle_event(
                            self.event("SubagentStart", agent_id="worker"), data,
                        )
                    elif state_name == "stopping":
                        state = self.hook.read_project_state(data, str(self.project))
                        state["active_run"]["status"] = "stopping"
                        self.hook.write_project_state(data, state)
                    before = self.hook.read_project_state(data, str(self.project))

                    response = self.hook.handle_event(
                        self.event("UserPromptSubmit", prompt=prompt), data,
                    )

                    self.assertIn("Invalid Symphony control", response.context)
                    self.assertIn("SYMPHONY_CONTROL_HANDLED", response.context)
                    self.assertEqual(before, self.hook.read_project_state(data, str(self.project)))

    def test_foreign_controls_are_inspection_only_for_a_live_run(self):
        mutating_controls = (
            "/symphony:enable", "/symphony:disable", "/symphony:stop",
            "/symphony:stop --force", "/symphony:assess", "/symphony:assess large",
            "/symphony:assess auto",
        )
        for index, prompt in enumerate(mutating_controls):
            with self.subTest(prompt=prompt):
                data = Path(self.tmp.name) / f"foreign-control-{index}"
                data.mkdir()
                self.hook.handle_event(
                    self.event("UserPromptSubmit", prompt="/symphony:start task"), data,
                )
                before = self.hook.read_project_state(data, str(self.project))
                response = self.hook.handle_event(
                    self.event(
                        "UserPromptSubmit", session_id="foreign", turn_id="foreign-control",
                        prompt=prompt,
                    ),
                    data,
                )
                self.assertEqual(before, self.hook.read_project_state(data, str(self.project)))
                self.assertIn("inspection-only", response.context)
                receipt = response.context.splitlines()[-1]
                stopped = self.hook.handle_event(
                    self.event(
                        "Stop", session_id="foreign", turn_id="foreign-control",
                        last_assistant_message=receipt,
                    ),
                    data, stop_wait_seconds=0,
                )
                self.assertFalse(stopped.block)
                self.assertEqual(before, self.hook.read_project_state(data, str(self.project)))

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
        self.set_current_assessment()
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
        self.assertEqual(11, result.context.count("not exposed by host"))
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
        self.assertEqual(11, current.context.count("not exposed by host"))
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
                receipt = f"<!-- SYMPHONY_CONTROL_HANDLED:{'a' * 32} -->"
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
                        self.event("UserPromptSubmit", prompt="/symphony:stop --force"),
                        self.data,
                    )
                    self.hook.handle_event(
                        self.event("UserPromptSubmit", prompt="/symphony:start next task"),
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

    def test_post_tool_use_records_owned_final_request_usage(self):
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="/symphony:start task"), self.data, now=1_000,
        )
        self.hook.handle_event(self.event("SubagentStart", agent_id="worker-1"), self.data, now=1_001)
        self.hook.handle_event(
            self.event(
                "PostToolUse",
                tool_name="Agent",
                tool_response={
                    "agentId": "worker-1",
                    "totalTokens": 120,
                    "totalDurationMs": 3400,
                    "totalToolUseCount": 4,
                    "usage": {
                        "inputTokens": 50,
                        "outputTokens": 40,
                        "cacheCreationInputTokens": 20,
                        "cacheReadInputTokens": 10,
                    },
                },
            ),
            self.data,
            now=1_002,
        )
        usage = self.state()["active_run"]["agent_records"]["worker-1"]["usage"]
        self.assertEqual({
            "final_request_total_tokens": 120,
            "final_request_input_tokens": 50,
            "final_request_output_tokens": 40,
            "final_request_cache_creation_tokens": 20,
            "final_request_cache_read_tokens": 10,
            "duration_ms": 3400,
            "tool_uses": 4,
            "observed_at": 1_002,
            "source": "claude-post-tool-use",
            "scope": "final-agent-request",
        }, usage)

        self.hook.handle_event(
            self.event(
                "PostToolUse", tool_name="Agent",
                tool_response={"agentId": "worker-1", "usage": {"outputTokens": 45}},
            ), self.data, now=1_003,
        )
        usage = self.state()["active_run"]["agent_records"]["worker-1"]["usage"]
        self.assertEqual(120, usage["final_request_total_tokens"])
        self.assertEqual(45, usage["final_request_output_tokens"])
        self.assertEqual(1_003, usage["observed_at"])

        self.hook.handle_event(self.event("SubagentStop", agent_id="worker-1"), self.data, now=1_004)
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="/symphony:stop --force"), self.data, now=1_005,
        )
        self.hook.handle_event(
            self.event(
                "PostToolUse", tool_name="Agent",
                tool_response={"agentId": "worker-1", "totalTokens": 125},
            ), self.data, now=1_006,
        )
        usage = self.state()["run_history"][-1]["agent_records"][0]["usage"]
        self.assertEqual(125, usage["final_request_total_tokens"])
        self.assertEqual("claude-post-tool-use", usage["source"])

    def test_usage_ingestion_rejects_invalid_unowned_and_background_data(self):
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="/symphony:start task"), self.data, now=1_000,
        )
        self.hook.handle_event(self.event("SubagentStart", agent_id="worker-1"), self.data, now=1_001)
        before = self.state()
        invalid_responses = (
            {"agentId": "worker-1", "status": "async_launched", "totalTokens": 1},
            {"agentId": "worker-1", "totalTokens": True},
            {"agentId": "worker-1", "totalTokens": -1},
            {"agentId": "worker-1", "totalTokens": "1"},
            {"agentId": "worker-1", "usage": []},
            {"agentId": "unknown", "totalTokens": 1},
        )
        for response in invalid_responses:
            with self.subTest(response=response):
                self.hook.handle_event(
                    self.event("PostToolUse", tool_name="Agent", tool_response=response), self.data,
                )
                self.assertEqual(before, self.state())

        self.hook.handle_event(
            self.event("PostToolUse", tool_name="Agent", tool_response={
                "agentId": "worker-1", "usage": {"inputTokens": 2},
            }), self.data,
        )
        self.assertEqual(
            2, self.state()["active_run"]["agent_records"]["worker-1"]["usage"]
            ["final_request_input_tokens"],
        )
        self.hook.handle_event(
            self.event("PostToolUse", tool_name="Agent", tool_response={
                "agentId": "worker-1", "totalTokens": 2, "totalDurationMs": 3,
                "totalToolUseCount": 4, "usage": [],
            }), self.data,
        )
        usage = self.state()["active_run"]["agent_records"]["worker-1"]["usage"]
        self.assertEqual((2, 3, 4), (
            usage["final_request_total_tokens"], usage["duration_ms"], usage["tool_uses"],
        ))

        state = self.state()
        state["run_history"] = [{
            "id": "old-run", "status": "completed", "agent_records": [{
                "id": "worker-1", "status": "terminal", "role": "worker",
                "model": "not exposed by host", "effort": "not exposed by host",
            }],
        }]
        self.hook.write_project_state(self.data, state, now=1_002)
        before = self.state()
        self.hook.handle_event(
            self.event("PostToolUse", tool_name="Agent", tool_response={
                "agentId": "worker-1", "totalTokens": 3,
            }), self.data,
        )
        self.assertEqual(before, self.state())
        self.assertIsNone(self.hook._usage_record({
            "tool_response": {"agentId": "worker-1", "async_launched": True, "totalTokens": 1},
        }, 1_003))

    def test_agent_and_status_usage_are_honest_and_read_only(self):
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="/symphony:start task"), self.data, now=1_000,
        )
        self.hook.handle_event(self.event("SubagentStart", agent_id="known"), self.data)
        self.hook.handle_event(self.event("SubagentStart", agent_id="unknown"), self.data)
        self.hook.handle_event(
            self.event("PostToolUse", tool_name="Agent", tool_response={
                "agentId": "known", "totalTokens": 120, "usage": {"inputTokens": 50},
            }), self.data,
        )
        state_path = self.hook.project_state_path(self.data, str(self.project))
        before = state_path.read_bytes()
        listing = self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="/symphony:agents"), self.data,
        )
        self.assertEqual(before, state_path.read_bytes())
        for heading in (
            "Final-request total tokens", "Final-request input tokens",
            "Final-request output tokens", "Final-request cache creation tokens",
            "Final-request cache read tokens", "Run duration", "Run tool uses",
            "Usage source/token scope",
        ):
            self.assertIn(heading, listing.context)
        self.assertIn("120", listing.context)
        self.assertIn("not exposed by host", listing.context)
        for line in (line for line in listing.context.splitlines() if line.startswith("|")):
            self.assertEqual(14, len(line.split("|")[1:-1]))

        self.hook.handle_event(self.event("UserPromptSubmit", prompt="/symphony:stop --force"), self.data)
        historical = self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="/symphony:agents --all"), self.data,
        )
        self.assertIn("known", historical.context)
        self.assertIn("120", historical.context)
        for line in (line for line in historical.context.splitlines() if line.startswith("|")):
            self.assertEqual(14, len(line.split("|")[1:-1]))
        status = self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="/symphony:status"), self.data,
        )
        self.assertIn("Observed final-request tokens (partial): 120", status.context)
        self.assertIn("agents lacking final-request totals: 1", status.context)

    def test_status_inspection_is_read_only_and_one_shot_for_active_and_stopping_runs(self):
        for stopping in (False, True):
            with self.subTest(stopping=stopping):
                self.hook.handle_event(
                    self.event("UserPromptSubmit", prompt="/symphony:start task"), self.data,
                )
                if stopping:
                    self.hook.handle_event(
                        self.event("UserPromptSubmit", prompt="/symphony:stop"), self.data,
                    )
                path = self.hook.project_state_path(self.data, str(self.project))
                before = path.read_bytes()
                status = self.hook.handle_event(
                    self.event("UserPromptSubmit", turn_id="status-turn", prompt="/symphony:status"),
                    self.data,
                )
                receipt = status.context.splitlines()[-1]
                self.assertTrue(receipt.startswith("<!-- SYMPHONY_CONTROL_HANDLED:"))
                self.assertEqual(before, path.read_bytes())
                result = self.hook.handle_event(
                    self.event("Stop", turn_id="status-turn", last_assistant_message=receipt),
                    self.data, stop_wait_seconds=0,
                )
                self.assertFalse(result.block)
                self.assertEqual(before, path.read_bytes())
                self.hook.handle_event(
                    self.event("UserPromptSubmit", prompt="/symphony:stop --force"), self.data,
                )

    def test_new_run_keeps_memory_candidates_out_of_thin_root_bootstrap(self):
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
        self.assertNotIn(str(self.project / run["memory"]["current"]), result.context)
        self.assertNotIn("codebase-memory-mcp", result.context)
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
        self.set_current_assessment()

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
                self.assertNotIn("enabled=true", recovery.context)
                self.assertNotIn("checkpoint_at=1234", recovery.context)
                marker = f"SYMPHONY_MEMORY_UNAVAILABLE:{run['id']}:codebase-memory-mcp"
                self.assertNotIn(marker, recovery.context)
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
                self.assertNotIn("enabled=false", recovery.context)
                self.assertNotIn("checkpoint_at=1234", recovery.context)
                self.set_current_assessment()
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
        self.set_current_assessment()
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
        self.set_current_assessment()

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
        self.set_current_assessment()
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
        self.set_current_assessment()

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
        self.set_current_assessment()

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
        self.set_current_assessment()

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

        self.assertTrue(help_result.context.endswith(" -->"))
        self.assertIn("SYMPHONY_CONTROL_HANDLED", help_result.context)
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
        self.assertIn("thin root/session keeper", result.context)
        self.assertIn("read-only symphony_assessor", result.context)
        self.assertIn("separate mode-appropriate execution lead", result.context)
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

    def test_bootstrap_limits_root_to_exact_lifecycle_relay_contract(self):
        result = self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="/symphony:start fix the parser"), self.data,
        )

        state = self.state()
        run = state["active_run"]
        for required in (
            f"Run id: {run['id']}",
            "Objective: fix the parser",
            "Project profile: automatic",
            "Delegating: <role> — <bounded objective> — <model>/<effort> — <reason>",
            "Waiting: <role or wave> — <bounded in-progress fact>",
            "Completed: <agent id/role> — <status> — tokens <value or not exposed by host> — duration <value or not exposed by host>",
            f"SYMPHONY_REGISTER:{run['id']}:assessor:<agent-id>",
            f"SYMPHONY_ASSESSMENT:{run['id']}:<project-profile>:<run-mode>",
            run["receipt"],
        ):
            self.assertIn(required, result.context)
        for forbidden in (
            "repository", "worktree", "capability", "codebase-memory", "MCP", "Memory",
            "verification", "Verify", "skills and tools", "current.md", "history.md",
        ):
            self.assertNotIn(forbidden, result.context)

    def test_normal_completion_requires_an_accepted_current_assessment(self):
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="/symphony:start task"), self.data,
        )
        run = self.state()["active_run"]

        missing = self.hook.handle_event(
            self.event("Stop", last_assistant_message=(
                f"<!-- SYMPHONY_MODE:small -->\n{run['receipt']}"
            )), self.data, stop_wait_seconds=0,
        )

        self.assertTrue(missing.block)
        self.assertIn("accepted current assessment", missing.reason)
        self.assertIsNotNone(self.state()["active_run"])

        receipt = (
            f"SYMPHONY_ASSESSMENT:{run['id']}:small:small\n"
            "SYMPHONY_ASSESSMENT_REASON:Bounded task"
        )
        self.start_role("assessor", "assessor")
        self.hook.handle_event(
            self.event("SubagentStop", agent_id="assessor", last_assistant_message=receipt), self.data,
        )
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="continue"), self.data,
        )
        stale = self.hook.handle_event(
            self.event("Stop", last_assistant_message=(
                f"<!-- SYMPHONY_MODE:small -->\n{run['receipt']}"
            )), self.data, stop_wait_seconds=0,
        )

        self.assertTrue(stale.block)
        self.assertIn("accepted current assessment", stale.reason)
        self.assertIsNotNone(self.state()["active_run"])

    def test_strong_owner_relay_requires_terminal_registered_assessor(self):
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="/symphony:start task"), self.data,
        )
        run = self.state()["active_run"]
        receipt = (
            f"SYMPHONY_ASSESSMENT:{run['id']}:medium:medium\n"
            "SYMPHONY_ASSESSMENT_REASON:Bounded task"
        )
        self.start_role("assessor", "assessor")

        blocked = self.hook.handle_event(
            self.event("Stop", last_assistant_message=receipt), self.data, stop_wait_seconds=0,
        )

        self.assertTrue(blocked.block)
        self.assertEqual((0, True, True), (
            self.state()["active_run"]["mode_revision"],
            self.state()["active_run"]["assessment_due"],
            self.state()["active_run"]["strong_assessment_required"],
        ))

        self.hook.handle_event(
            self.event("SubagentStop", agent_id="assessor"), self.data,
        )
        relayed = self.hook.handle_event(
            self.event("Stop", last_assistant_message=receipt), self.data, stop_wait_seconds=0,
        )
        self.assertTrue(relayed.block)
        self.assertIn("Symphony accepted assessment", relayed.reason)
        self.assertEqual((1, False, False), (
            self.state()["active_run"]["mode_revision"],
            self.state()["active_run"]["assessment_due"],
            self.state()["active_run"]["strong_assessment_required"],
        ))

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
        self.assertIn("inspection-only", result.context)

    def test_resume_and_compaction_reinject_active_run(self):
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: start\nSYMPHONY_TASK: task"),
            self.data,
        )
        state = self.state()
        state["active_run"]["mode"] = "medium"
        state["active_run"]["mode_revision"] = 1
        state["active_run"]["assessment_due"] = False
        state["active_run"]["strong_assessment_required"] = False
        self.hook.write_project_state(self.data, state)

        for source in ("resume", "compact"):
            state = self.state()
            state["active_run"]["assessment_due"] = False
            state["active_run"]["strong_assessment_required"] = False
            self.hook.write_project_state(self.data, state)
            result = self.hook.handle_event(
                self.event("SessionStart", source=source),
                self.data,
            )
            self.assertIn("Recover Symphony run", result.context)
            self.assertIn("fresh separate mode-appropriate execution lead", result.context)

    def test_recovery_requires_accepted_assessment_before_skipping_assessor(self):
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="/symphony:start task"), self.data,
        )
        state = self.state()
        state["active_run"].update({"mode": "medium", "mode_revision": 0, "assessment_due": False})
        self.hook.write_project_state(self.data, state)

        legacy = self.hook.handle_event(self.event("SessionStart", source="resume"), self.data)
        self.assertIn("read-only symphony_assessor", legacy.context)

        state = self.state()
        state["active_run"].update({
            "mode": "medium", "mode_revision": 1, "assessment_due": False,
            "strong_assessment_required": False,
        })
        self.hook.write_project_state(self.data, state)
        accepted = self.hook.handle_event(self.event("SessionStart", source="resume"), self.data)
        self.assertIn("fresh separate mode-appropriate execution lead", accepted.context)

    def test_ordinary_reassessment_is_led_then_root_relayed(self):
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="/symphony:start task"), self.data, now=1_000,
        )
        run = self.state()["active_run"]
        receipt = (
            f"SYMPHONY_ASSESSMENT:{run['id']}:medium:medium\n"
            "SYMPHONY_ASSESSMENT_REASON:Same work remains bounded"
        )
        self.start_role("assessor", "assessor")
        self.hook.handle_event(
            self.event("SubagentStop", agent_id="assessor", last_assistant_message=receipt), self.data,
        )
        self.start_role("lead", "lead")

        due = self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="continue the parser"), self.data, now=1_001,
        )
        self.assertIn("Reassessment is due", due.context)
        self.assertIn("current execution lead", due.context)
        self.assertIn("root must relay", due.context)
        self.assertTrue(self.state()["active_run"]["assessment_due"])

        self.hook.handle_event(
            self.event("SubagentStop", agent_id="lead", last_assistant_message=receipt), self.data,
        )
        self.assertTrue(self.state()["active_run"]["assessment_due"])

        changed = receipt.replace(":medium:medium", ":medium:large")
        self.hook.handle_event(
            self.event("Stop", last_assistant_message=changed), self.data, now=1_002,
            stop_wait_seconds=0,
        )
        self.assertEqual(("medium", True, False), (
            self.state()["active_run"]["mode"],
            self.state()["active_run"]["assessment_due"],
            self.state()["active_run"]["strong_assessment_required"],
        ))

        self.hook.handle_event(
            self.event("Stop", last_assistant_message=receipt), self.data, now=1_003,
            stop_wait_seconds=0,
        )
        self.assertFalse(self.state()["active_run"]["assessment_due"])

    def test_ordinary_owner_relay_requires_terminal_registered_lead(self):
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="/symphony:start task"), self.data,
        )
        run = self.state()["active_run"]
        self.set_current_assessment("medium")
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="continue"), self.data,
        )
        receipt = (
            f"SYMPHONY_ASSESSMENT:{run['id']}:medium:medium\n"
            "SYMPHONY_ASSESSMENT_REASON:Same work remains bounded"
        )

        result = self.hook.handle_event(
            self.event("Stop", last_assistant_message=receipt), self.data, stop_wait_seconds=0,
        )

        self.assertTrue(result.block)
        self.assertEqual(("medium", 1, True, False), (
            self.state()["active_run"]["mode"],
            self.state()["active_run"]["mode_revision"],
            self.state()["active_run"]["assessment_due"],
            self.state()["active_run"]["strong_assessment_required"],
        ))

    def test_assess_context_survives_reassessment_helper(self):
        result = self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="/symphony:assess"), self.data,
        )
        self.assertIn("Symphony assessment requested", result.context)

    def test_explicit_assessment_stays_strong_until_assessor_receipt(self):
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="/symphony:start task"), self.data,
        )
        run = self.state()["active_run"]
        receipt = (
            f"SYMPHONY_ASSESSMENT:{run['id']}:medium:medium\n"
            "SYMPHONY_ASSESSMENT_REASON:Initial evidence is sufficient"
        )
        self.start_role("assessor-a", "assessor")
        self.hook.handle_event(
            self.event("SubagentStop", agent_id="assessor-a", last_assistant_message=receipt), self.data,
        )
        self.hook.handle_event(self.event("UserPromptSubmit", prompt="/symphony:assess"), self.data)
        self.assertEqual((True, True), (
            self.state()["active_run"]["assessment_due"],
            self.state()["active_run"]["strong_assessment_required"],
        ))

        continued = self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="continue after explicit reassessment"), self.data,
        )
        self.assertIn("read-only symphony_assessor", continued.context)
        self.assertTrue(self.state()["active_run"]["strong_assessment_required"])
        self.start_role("assessor-b", "assessor")
        self.hook.handle_event(
            self.event("SubagentStop", agent_id="assessor-b", last_assistant_message=receipt), self.data,
        )
        self.assertEqual((False, False), (
            self.state()["active_run"]["assessment_due"],
            self.state()["active_run"]["strong_assessment_required"],
        ))

    def test_accepted_interrupt_and_resume_require_only_ordinary_reassessment(self):
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="/symphony:start task"), self.data,
        )
        run = self.state()["active_run"]
        receipt = (
            f"SYMPHONY_ASSESSMENT:{run['id']}:medium:medium\n"
            "SYMPHONY_ASSESSMENT_REASON:Initial evidence is sufficient"
        )
        self.start_role("assessor", "assessor")
        self.hook.handle_event(
            self.event("SubagentStop", agent_id="assessor", last_assistant_message=receipt), self.data,
        )
        self.hook.handle_event(self.event("Interrupt"), self.data)
        self.assertEqual((True, False), (
            self.state()["active_run"]["assessment_due"],
            self.state()["active_run"]["strong_assessment_required"],
        ))
        recovery = self.hook.handle_event(self.event("SessionStart", source="resume"), self.data)
        self.assertIn("fresh separate mode-appropriate execution lead", recovery.context)
        self.assertNotIn("read-only symphony_assessor", recovery.context)

    def test_only_explicit_dry_run_bypasses_assessment_completion(self):
        for index, (prompt, expected_dry_run, objective, completes) in enumerate((
            ("/symphony:start --dry-run validate routing", True, "validate routing", True),
            ("SYMPHONY_CONTROL: start\nSYMPHONY_TASK: --dry-run validate markers",
             True, "validate markers", True),
            ("/symphony:start validate dry-run behavior", False,
             "validate dry-run behavior", False),
        )):
            with self.subTest(prompt=prompt):
                data = Path(self.tmp.name) / f"dry-run-{index}"
                data.mkdir()
                started = self.hook.handle_event(
                    self.event("UserPromptSubmit", prompt=prompt), data,
                )
                run = self.hook.read_project_state(data, str(self.project))["active_run"]
                self.assertEqual(expected_dry_run, run["dry_run"])
                self.assertEqual(objective, run["objective"])
                if expected_dry_run:
                    self.assertIn("Dry run: true", started.context)
                    self.assertIn("Do not spawn agents or write project files", started.context)
                    self.assertNotIn("spawn one", started.context)

                result = self.hook.handle_event(
                    self.event("Stop", last_assistant_message=(
                        f"<!-- SYMPHONY_MODE:medium -->\n{run['receipt']}"
                    )),
                    data, stop_wait_seconds=0,
                )
                self.assertEqual(completes, not result.block)
                self.assertEqual(
                    completes,
                    self.hook.read_project_state(data, str(self.project))["active_run"] is None,
                )

    def test_dry_run_state_must_be_an_explicit_boolean(self):
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="/symphony:start task"), self.data,
        )
        state = self.state()
        state["active_run"]["dry_run"] = "true"
        self.hook.write_project_state(self.data, state)

        self.assertTrue(self.state()["corrupt"])

    def test_interrupted_run_transfers_once_after_passive_foreign_startup_and_control(self):
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="/symphony:start task"), self.data,
            now=1_000,
        )
        self.set_current_assessment("medium")
        before_start = self.state()

        foreign_start = self.hook.handle_event(
            self.event("SessionStart", session_id="session-2", source="resume"),
            self.data, now=1_001,
        )
        self.assertEqual(before_start, self.state())
        self.assertIn("inspection-only", foreign_start.context)

        self.hook.handle_event(
            self.event("Interrupt", session_id="session-2"), self.data, now=1_002,
        )
        self.assertEqual(before_start, self.state())

        self.hook.handle_event(self.event("Interrupt"), self.data, now=1_003)
        interrupted = self.state()
        self.assertEqual(1_003, interrupted["active_run"]["interrupted_at"])
        self.assertTrue(interrupted["active_run"]["interruption_recovery_eligible"])

        control = self.hook.handle_event(
            self.event(
                "UserPromptSubmit", session_id="session-2", turn_id="status",
                prompt="/symphony:status",
            ),
            self.data, now=1_004,
        )
        after_control = self.state()
        self.assertEqual("session-1", after_control["active_run"]["owner_session_id"])
        stopped = self.hook.handle_event(
            self.event(
                "Stop", session_id="session-2", turn_id="status",
                last_assistant_message=control.context.splitlines()[-1],
            ),
            self.data, now=1_005, stop_wait_seconds=0,
        )
        self.assertFalse(stopped.block)
        self.assertEqual(after_control, self.state())

        recovered = self.hook.handle_event(
            self.event("UserPromptSubmit", session_id="session-2", prompt="continue"),
            self.data, now=1_006,
        )
        run = self.state()["active_run"]
        self.assertEqual("session-2", run["owner_session_id"])
        self.assertEqual("session-1", run["previous_owner_session_id"])
        self.assertEqual(1_006, run["ownership_transferred_at"])
        self.assertFalse(run["interruption_recovery_eligible"])
        self.assertIn("Recover Symphony run", recovered.context)

        transferred = self.state()
        self.hook.handle_event(
            self.event("Interrupt", session_id="session-1"), self.data, now=1_007,
        )
        self.assertEqual(transferred, self.state())
        competitor = self.hook.handle_event(
            self.event("UserPromptSubmit", session_id="session-3", prompt="continue"),
            self.data, now=1_008,
        )
        self.assertEqual(transferred, self.state())
        self.assertIn("inspection-only", competitor.context)

    def test_interrupted_run_with_a_live_agent_cannot_transfer_until_agent_is_terminal(self):
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="/symphony:start task"), self.data,
            now=1_000,
        )
        self.hook.handle_event(
            self.event("SubagentStart", agent_id="worker"), self.data, now=1_001,
        )
        self.hook.handle_event(self.event("Interrupt"), self.data, now=1_002)
        interrupted = self.state()

        for event, now in (("SessionStart", 1_003), ("UserPromptSubmit", 1_004)):
            payload = self.event(event, session_id="session-2", source="resume")
            if event == "UserPromptSubmit":
                payload["prompt"] = "continue"
            response = self.hook.handle_event(payload, self.data, now=now)
            self.assertEqual(interrupted, self.state())
            self.assertIn("inspection-only", response.context)
            self.assertIn("worker", response.context)

        self.hook.handle_event(
            self.event("SubagentStop", agent_id="worker"), self.data, now=1_005,
        )
        self.hook.handle_event(
            self.event("UserPromptSubmit", session_id="session-2", prompt="continue"),
            self.data, now=1_006,
        )
        run = self.state()["active_run"]
        self.assertEqual("session-2", run["owner_session_id"])
        self.assertFalse(run["interruption_recovery_eligible"])

    def test_legacy_interrupted_timestamp_does_not_grant_takeover_eligibility(self):
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="/symphony:start task"), self.data,
            now=1_000,
        )
        state = self.state()
        state["active_run"]["interrupted_at"] = 1_001
        state["active_run"].pop("interruption_recovery_eligible")
        self.hook.write_project_state(self.data, state, now=1_001)

        upgraded = self.state()
        self.assertFalse(upgraded["active_run"]["interruption_recovery_eligible"])
        response = self.hook.handle_event(
            self.event("UserPromptSubmit", session_id="foreign", prompt="continue"),
            self.data, now=1_002,
        )

        run = self.state()["active_run"]
        self.assertEqual("session-1", run["owner_session_id"])
        self.assertIsNone(run["previous_owner_session_id"])
        self.assertIsNone(run["ownership_transferred_at"])
        self.assertIn("inspection-only", response.context)

    def test_unknown_session_identity_cannot_interrupt_or_recover_a_run(self):
        for index, session_id in enumerate((None, "", "unknown")):
            with self.subTest(session_id=session_id):
                data = Path(self.tmp.name) / f"unknown-owner-{index}"
                data.mkdir()
                self.hook.handle_event(
                    self.event("UserPromptSubmit", prompt="/symphony:start task"), data,
                )
                state = self.hook.read_project_state(data, str(self.project))
                state["active_run"]["owner_session_id"] = session_id
                self.hook.write_project_state(data, state)
                before = self.hook.read_project_state(data, str(self.project))

                interrupted = self.hook.handle_event(
                    self.event("Interrupt", session_id=session_id), data, now=2_000,
                )
                recovered = self.hook.handle_event(
                    self.event("SessionStart", session_id=session_id, source="resume"),
                    data, now=2_001,
                )

                self.assertEqual("", interrupted.context)
                self.assertEqual(before, self.hook.read_project_state(data, str(self.project)))
                self.assertIn("inspection-only", recovered.context)

    def test_malformed_strong_assessment_requirement_is_quarantined(self):
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="/symphony:start task"), self.data,
        )
        state = self.state()
        state["active_run"]["strong_assessment_required"] = "yes"
        self.hook.write_project_state(self.data, state)
        self.assertTrue(self.state()["corrupt"])

    def test_replacement_lead_updates_wave_exclusion_without_worker_takeover(self):
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="/symphony:start task"), self.data,
        )
        self.start_role("lead-a", "lead")
        self.hook.handle_event(self.event("SubagentStop", agent_id="lead-a"), self.data)
        state = self.state()
        state["active_run"]["assessment_due"] = False
        self.hook.write_project_state(self.data, state)

        self.start_role("lead-b", "lead")
        self.assertEqual("lead-b", self.state()["active_run"]["lead_agent_id"])
        self.hook.handle_event(self.event("SubagentStop", agent_id="lead-b"), self.data)
        self.assertFalse(self.state()["active_run"]["assessment_due"])

        self.hook.handle_event(
            self.event("SubagentStart", agent_id="worker", agent_type="worker"), self.data,
        )
        self.assertEqual("lead-b", self.state()["active_run"]["lead_agent_id"])
        self.hook.handle_event(self.event("SubagentStop", agent_id="worker"), self.data)
        self.assertTrue(self.state()["active_run"]["assessment_due"])

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
        self.assertIsNone(self.state()["active_run"]["mode"])
        self.set_current_assessment("medium")
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
        self.assertIn("mode-appropriate execution lead", result.reason)
        self.assertNotIn("strongest/high lead", result.reason)
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
        self.assertIn("read-only symphony_assessor", started.context)
        self.assertIn("separate mode-appropriate execution lead", started.context)
        self.assertIsNotNone(self.state()["active_run"])

    def test_assessment_state_normalizes_legacy_runs(self):
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="/symphony:start legacy task"), self.data,
        )
        state = self.state()
        state.pop("assessment", None)
        for field in ("mode_revision", "assessment_due", "strong_assessment_required", "mode_history"):
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
        self.assertTrue(state["active_run"].get("strong_assessment_required"))
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
        self.start_role("assessor", "assessor", now=1_001)
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

        no_lifecycle = Path(self.tmp.name) / "hooks-without-children.json"
        no_lifecycle.write_text(json.dumps({"hooks": {"Stop": []}}), encoding="utf-8")
        self.hook.HOOK_DECLARATION_PATH = no_lifecycle
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="/symphony:assess small"), self.data, now=1_004,
        )
        state = self.state()
        run = state["active_run"]
        self.assertTrue(self.hook._record_assessment_receipt(state, run, receipt, 1_005))
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
        self.assertTrue(self.hook._record_assessment_receipt(state, run, changed, 1_007))
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
        self.start_role("assessor", "assessor")
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

        self.start_role("lead", "lead", now=1_008)
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

    def test_foreign_stop_cannot_relay_assessment_receipt(self):
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="/symphony:start task"), self.data, now=1_000,
        )
        run = self.state()["active_run"]
        receipt = (
            f"SYMPHONY_ASSESSMENT:{run['id']}:large:medium\n"
            "SYMPHONY_ASSESSMENT_REASON:Foreign session must not update this run"
        )

        self.hook.handle_event(
            self.event("Stop", session_id="foreign-session", last_assistant_message=receipt),
            self.data, now=1_001, stop_wait_seconds=0,
        )

        state = self.state()
        self.assertEqual({
            "profile": None, "source": None, "revision": 0, "reason": None, "assessed_at": None,
        }, state["assessment"])
        self.assertEqual((None, 0, True, []), (
            state["active_run"]["mode"], state["active_run"]["mode_revision"],
            state["active_run"]["assessment_due"], state["active_run"]["mode_history"],
        ))

    def test_stop_assessment_relays_require_known_owner_identity(self):
        for owner_session_id, session_id in ((None, None), ("", ""), ("unknown", "unknown")):
            with self.subTest(owner_session_id=owner_session_id, session_id=session_id):
                data = Path(self.tmp.name) / f"data-{owner_session_id!r}"
                data.mkdir()
                self.hook.handle_event(
                    self.event("UserPromptSubmit", session_id=owner_session_id, prompt="/symphony:start task"),
                    data, now=1_000,
                )
                run = self.hook.read_project_state(data, str(self.project))["active_run"]
                receipt = (
                    f"SYMPHONY_ASSESSMENT:{run['id']}:large:medium\n"
                    "SYMPHONY_ASSESSMENT_REASON:Unidentified root relay"
                )
                self.hook.handle_event(
                    self.event("Stop", session_id=session_id, last_assistant_message=receipt),
                    data, now=1_001, stop_wait_seconds=0,
                )
                state = self.hook.read_project_state(data, str(self.project))
                self.assertEqual((None, 0, True), (
                    state["assessment"]["profile"], state["active_run"]["mode_revision"],
                    state["active_run"]["assessment_due"],
                ))

        no_lifecycle = Path(self.tmp.name) / "hooks-without-children.json"
        no_lifecycle.write_text(json.dumps({"hooks": {"Stop": []}}), encoding="utf-8")
        self.hook.HOOK_DECLARATION_PATH = no_lifecycle
        self.hook.handle_event(
            self.event("UserPromptSubmit", session_id="known-owner", prompt="/symphony:start task"),
            self.data, now=1_010,
        )
        run = self.state()["active_run"]
        receipt = (
            f"SYMPHONY_ASSESSMENT:{run['id']}:large:medium\n"
            "SYMPHONY_ASSESSMENT_REASON:Known owner relay"
        )
        self.hook.handle_event(
            self.event("Stop", session_id="known-owner", last_assistant_message=receipt),
            self.data, now=1_011, stop_wait_seconds=0,
        )
        self.assertEqual(("large", 1, False), (
            self.state()["assessment"]["profile"], self.state()["active_run"]["mode_revision"],
            self.state()["active_run"]["assessment_due"],
        ))

    def test_replayed_worker_stop_does_not_retrigger_reassessment(self):
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="/symphony:start task"), self.data, now=1_000,
        )
        self.start_role("lead", "lead")
        self.start_role("assessor", "assessor")
        self.hook.handle_event(
            self.event("SubagentStart", agent_id="worker", agent_type="worker"), self.data,
        )
        state = self.state()
        run = state["active_run"]
        receipt = f"SYMPHONY_ASSESSMENT:{run['id']}:small:small\nSYMPHONY_ASSESSMENT_REASON:Routine task"
        self.assertTrue(self.hook._record_assessment_receipt(state, run, receipt, 1_001, "assessor"))
        self.hook.write_project_state(self.data, state, now=1_001)

        self.hook.handle_event(self.event("SubagentStop", agent_id="worker"), self.data, now=1_002)
        self.assertTrue(self.state()["active_run"]["assessment_due"])
        state = self.state()
        self.assertTrue(self.hook._record_assessment_receipt(
            state, state["active_run"], receipt, 1_003, "assessor",
        ))
        self.hook.write_project_state(self.data, state, now=1_003)

        self.hook.handle_event(self.event("SubagentStop", agent_id="worker"), self.data, now=1_004)
        self.assertFalse(self.state()["active_run"]["assessment_due"])

    def test_suggestion_receipt_sets_thirty_day_cooldown(self):
        self.hook.handle_event(
            self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: start\nSYMPHONY_TASK: task"),
            self.data,
            now=1_000_000,
        )
        run = self.state()["active_run"]
        self.set_current_assessment()

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
    def test_skill_separates_assessment_execution_and_exposes_delegations(self):
        skill = (PLUGIN_ROOT / "skills" / "symphony" / "SKILL.md").read_text(encoding="utf-8")
        required = (
            "symphony_assessor",
            "exactly one concise assessment result",
            "must not implement",
            "separate execution lead",
            "SYMPHONY_ASSESSMENT:<run-id>:<project-profile>:<run-mode>",
            "SYMPHONY_ASSESSMENT_REASON:<single bounded line>",
            "current run's root must relay",
            "current execution lead cheaply reassesses",
            "proposed mode change or unresolved high-risk ambiguity requires a new strong assessor",
            "ordinary reassessment due",
            "strong assessment required",
            "Delegating: <role> — <bounded objective> — <model>/<effort> — <reason>",
            "Waiting: <role or wave> — <bounded in-progress fact>",
            "Completed: <agent id/role> — <status> — tokens <value or not exposed by host> — duration <value or not exposed by host>",
            "`Waiting:` may report only observed lifecycle state",
            "fresh execution lead from bounded lifecycle/document memory",
        )
        for text in required:
            self.assertIn(text, skill)
        self.assertNotIn(
            "Spawn exactly one `symphony_lead` with no inherited turns, using the strongest available "
            "general reasoning model at `high`.", skill,
        )
        self.assertNotIn("medium-shaped planned route", skill)

        prompt = (PLUGIN_ROOT / "evals" / "match-proceeds" / "prompt.md").read_text(
            encoding="utf-8"
        )
        for text in (
            "medium-shaped",
            "must not implement",
            "Delegating: symphony_assessor",
            "Completed: symphony_assessor",
            "Delegating: symphony_lead",
            "Completed: symphony_lead",
        ):
            self.assertIn(text, prompt)
        grader = (PLUGIN_ROOT / "evals" / "match-proceeds" / "graders" / "delegation-visibility.md").read_text(
            encoding="utf-8"
        )
        pattern = re.compile(
            re.search(r"^pattern: '(.*)'$", grader, re.MULTILINE).group(1), re.IGNORECASE | re.DOTALL,
        )
        passing = """Delegating: symphony_assessor — classify task — claude-opus-5/high — bounded read-only assessment
Completed: symphony_assessor — planned — tokens not exposed by host — duration not exposed by host
Delegating: symphony_lead — implement task — claude-sonnet-5/medium — medium execution
Completed: symphony_lead — planned — tokens not exposed by host — duration not exposed by host
<!-- SYMPHONY_MODE:medium -->"""
        adversarial = """Delegating: symphony_assessor — classify task — claude-opus-5/medium — assessment
Completed: symphony_assessor — planned
Delegating: symphony_lead — implement task — claude-opus-5/high — execution
Completed: symphony_lead — planned
<!-- SYMPHONY_MODE:medium -->"""
        implementation_adversarial = passing.replace("bounded read-only assessment", "implement the task")
        self.assertIsNotNone(re.search(pattern, passing))
        self.assertIsNone(re.search(pattern, adversarial))
        self.assertIsNone(re.search(pattern, implementation_adversarial))
        lines = passing.splitlines()
        for report in (
            "\n".join([lines[1], lines[0], *lines[2:]]),
            passing + "\nDelegating: symphony_lead — implement task — claude-opus-5/high — execution",
            passing + "\n<!-- SYMPHONY_MODE:large -->",
            passing.replace("bounded read-only assessment", "read-only assessment then implement the task"),
        ):
            self.assertIsNone(re.search(pattern, report), report)

    def test_assessor_and_lead_own_discovery_routing_memory_and_verification(self):
        skill = (PLUGIN_ROOT / "skills" / "symphony" / "SKILL.md").read_text(encoding="utf-8")
        routing = (PLUGIN_ROOT / "skills" / "symphony" / "references" / "capability-routing.md").read_text(
            encoding="utf-8"
        )
        for required in (
            "The root does not inspect the project, inventory capabilities, choose document memory, or run verification.",
            "The assessor owns initial discovery, capability routing, memory choice, and the verification strategy.",
            "The execution lead owns implementation and authoritative verification.",
        ):
            self.assertIn(required, skill)
        self.assertIn(
            "The assessor or execution lead performs this grounding; the root only relays its bounded result.",
            routing,
        )
        self.assertNotIn("legacy/dry-run marker fallback", skill)
        self.assertNotIn("SYMPHONY_AGENTS_INSPECTED", skill)
        self.assertIn("SYMPHONY_CONTROL_HANDLED", skill)
        self.assertIn("/symphony:start [--dry-run] <task>", skill)
        self.assertIn("Only a run persisted with `dry_run=true` may bypass accepted assessment", skill)

    @unittest.skipUnless(shutil.which("node"), "Hosted eval regex checks require Node")
    def test_eval_graders_compile_in_host_javascript_runtime(self):
        graders = []
        for path in (PLUGIN_ROOT / "evals").rglob("*.md"):
            text = path.read_text(encoding="utf-8")
            pattern = re.search(r"^pattern: '(.*)'$", text, re.MULTILINE)
            if pattern:
                flags = re.search(r"^flags: (.*)$", text, re.MULTILINE)
                graders.append([str(path), pattern.group(1), flags.group(1) if flags else ""])
        result = subprocess.run(["node", "-e", """
const fs = require('fs');
for (const [path, pattern, flags] of JSON.parse(fs.readFileSync(0, 'utf8'))) {
  try { new RegExp(pattern, flags); }
  catch (error) { throw new Error(path + ': ' + error.message); }
}
"""], input=json.dumps(graders), capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_documentation_and_manifests_describe_assessment_release(self):
        readme = (PLUGIN_ROOT.parents[1] / "README.md").read_text(encoding="utf-8")
        help_text = (PLUGIN_ROOT / "commands" / "help.md").read_text(encoding="utf-8")
        for text in (readme, help_text):
            self.assertIn(".symphony/memory/current.md", text)
            self.assertIn("codebase-memory-mcp", text)
            self.assertIn(
                "Manually ignoring `.symphony/memory/` disables indexed history until the ignore policy changes.",
                text,
            )
            for required in (
                "/symphony:assess [small|medium|large|auto]",
                "/symphony:assess large",
                "project profile",
                "per-run execution mode",
                "long-running large-profile project may still have a small task",
                "owner prompt, final worker wave, interrupt, or resume",
                "separate read-only assessor",
                "bounded, read-only assessor",
                "returns exactly one concise assessment result",
                "does not implement",
                "Delegating:",
                "Completed:",
                "authoritative host observations only",
                "not exposed by host",
                "Claude synchronous Agent usage may be exposed",
                "background Agent usage and Codex usage remain `not exposed by host`",
                "No hard token or cost budget is promised.",
            ):
                self.assertIn(required, text)
        versions = {
            json.loads((PLUGIN_ROOT / relative).read_text(encoding="utf-8"))["version"]
            for relative in (".claude-plugin/plugin.json", ".codex-plugin/plugin.json")
        }
        self.assertEqual({"0.15.0"}, versions)
        self.assertEqual({
            "name": "symphony",
            "interface": {"displayName": "Symphony"},
            "plugins": [{
                "name": "symphony",
                "source": {"source": "local", "path": "./plugins/symphony"},
                "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
                "category": "Productivity",
            }],
        }, json.loads((PLUGIN_ROOT.parents[1] / ".agents/plugins/marketplace.json").read_text(encoding="utf-8")))
        self.assertEqual({
            "name": "symphony",
            "owner": {"name": "Sojaner"},
            "plugins": [{
                "name": "symphony",
                "source": "./plugins/symphony",
                "description": "Multi-model orchestration that gives the right task to the right model: steadier progress, less overthinking, lower cost.",
                "category": "productivity",
            }],
        }, json.loads((PLUGIN_ROOT.parents[1] / ".claude-plugin/marketplace.json").read_text(encoding="utf-8")))

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
            "The execution lead is the only writer",
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
