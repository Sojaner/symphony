import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from plugins.symphony.symphony.model import Delegation, Event, ProjectState, RunState
from plugins.symphony.symphony import runtime as runtime_module
from plugins.symphony.symphony.runtime import compact_delegations, format_delegation, handle
from plugins.symphony.symphony.store import StateStore


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.state_root = self.root / "state"
        # Pin the entitlement profile so tests never probe the host machine.
        self.environ = {
            "SYMPHONY_STATE_DIR": str(self.state_root),
            "SYMPHONY_PROFILE": "full",
        }
        self.claude_environ = {**self.environ, "SYMPHONY_PROFILE": "opus"}

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

    def flush(self, provider: str = "codex") -> str:
        """Collect guidance deferred from a subagent-stop result.

        Neither host accepts injected context on a stop result, so Symphony
        holds it until the next event that does accept one.
        """
        control = "/symphony:status" if provider == "claude" else "$symphony:symphony status"
        return self.context(handle(self.payload(control, provider), self.environ))

    def open_run(self, task: str = "Implement the feature", provider: str = "codex"):
        """Open a run the way the contract requires: by spawning the assessor.

        A prompt alone no longer opens a run, so every test that needs tracked
        work must put an assessor in front of the host.
        """
        environ = self.claude_environ if provider == "claude" else self.environ
        # A real session heartbeats before it spawns anything, and that is when
        # the entitlement profile is recorded.
        handle({**self.payload("", provider), "hook_event_name": "SessionStart"}, environ)
        hook = {
            **self.payload("", provider),
            "hook_event_name": "PreToolUse",
            "tool_name": "Agent" if provider == "claude" else "spawn_agent",
        }
        if provider == "claude":
            hook["tool_input"] = {
                "subagent_type": "symphony-assessor-opus-high",
                "prompt": f"SYMPHONY_ROLE: assessor\n{task}",
            }
        else:
            hook["tool_input"] = {
                "message": f"SYMPHONY_ROLE: assessor\n{task}",
                "model": "gpt-6-astra",
                "reasoning_effort": "high",
            }
        return handle(hook, environ)

    def test_enable_persists_and_next_task_requests_bounded_assessment(self):
        enabled = handle(self.payload("$symphony:symphony enable"), self.environ)
        self.assertIn("enabled", self.context(enabled).lower())

        task = handle(self.payload("Implement the feature"), self.environ)

        self.assertIn("assess", self.context(task).lower())
        state = StateStore(self.state_root).load(self.project)
        self.assertTrue(state.enabled)
        self.assertIsNone(state.active_run, "a prompt guides; only the assessor opens a run")

        self.open_run("Implement the feature")
        state = StateStore(self.state_root).load(self.project)
        self.assertIsNotNone(state.active_run)
        self.assertEqual(state.active_run.task, "Implement the feature")

    def test_one_shot_start_does_not_enable_project(self):
        result = handle(self.payload("$symphony:symphony start Check the release"), self.environ)
        self.assertIn("assess", self.context(result).lower())
        self.assertIn("Check the release", self.context(result))

        self.open_run("Check the release")
        state = StateStore(self.state_root).load(self.project)
        self.assertFalse(state.enabled)
        self.assertEqual(state.active_run.task, "Check the release")

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
        self.assertEqual(
            state.activation["codex"]["plugin_version"],
            __import__("plugins.symphony.symphony", fromlist=["x"]).PLUGIN_VERSION,
        )
        self.assertTrue(state.activation["codex"]["plugin_root"].endswith("plugins/symphony"))
        self.assertIn("guarded", self.context(result).lower())
        self.assertNotIn("unarmed", self.context(result).lower())

    def test_status_does_not_call_a_historical_heartbeat_current(self):
        handle(self.payload("$symphony:symphony status"), self.environ)
        state = StateStore(self.state_root).load(self.project)
        text = runtime_module._status(state, False, "codex", "new-session")
        self.assertIn("pending verification", text)
        self.assertIn("historical heartbeat", text.lower())

    def test_reconciliation_ignores_malformed_health_roster(self):
        run = RunState(
            "run-1", "task", session_id="old-session",
            delegations=(Delegation("agent-1", "worker", "task", "working", "tier", "medium"),),
        )
        state = ProjectState(active_run=run)
        source = Event("event", "session_heartbeat", "2026-09-22T12:00:00+00:00")
        next_state, actions = runtime_module._reconcile_session(
            state, source,
            {"session_id": "new-session", "active_agent_ids": [None]},
        )
        self.assertEqual(next_state, state)
        self.assertEqual(actions, ())

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
        result = handle(self.payload("/symphony:start Check the release safely", "claude"), self.environ)
        self.assertIn("Check the release safely", self.context(result))

        self.open_run("Check the release safely", "claude")
        state = StateStore(self.state_root).load(self.project)
        self.assertEqual(state.active_run.task, "Check the release safely")

    def test_claude_command_arguments_start_a_one_shot_run(self):
        prompt = "SYMPHONY_CONTROL: start\nARGUMENTS: Check the release"
        result = handle(self.payload(prompt, "claude"), self.environ)
        self.assertIn("assess", self.context(result).lower())

        self.open_run("Check the release", "claude")
        state = StateStore(self.state_root).load(self.project)
        self.assertEqual(state.active_run.task, "Check the release")

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
        self.open_run("Ship it")
        marker = json.dumps(
            {
                "size": "small",
                "complexity": "simple",
                "risk": "normal",
                "rationale": "bounded task",
                "topology": "direct",
            }
        )
        handle(
            {
                **self.payload(""),
                "hook_event_name": "PreToolUse",
                "tool_name": "spawn_agent",
                "tool_input": {
                    "message": f"SYMPHONY_ROLE: lead\nSYMPHONY_ROUTE: {marker}\nShip it",
                    "model": "gpt-5.6-sol",
                    "reasoning_effort": "medium",
                },
            },
            self.environ,
        )
        started = self.payload("")
        started.update(
            {
                "hook_event_name": "SubagentStart",
                "agent_id": "lead-1",
                "agent_type": "symphony_lead_gpt_5_6_sol_medium",
                "model": "gpt-5.6-sol",
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

    def test_codex_lead_completion_waits_for_accepted_assessment(self):
        self.open_run("Ship it")
        lead = {
            **self.payload(""),
            "hook_event_name": "SubagentStart",
            "agent_id": "lead-1",
            "agent_type": "symphony_lead_gpt_5_6_sol_high",
            "model": "gpt-5.6-sol",
            "model_reasoning_effort": "high",
        }
        handle(lead, self.environ)

        missing = handle(
            {
                **lead,
                "hook_event_name": "SubagentStop",
                "status": "completed",
                "last_assistant_message": "Done",
            },
            self.environ,
        )

        self.assertEqual(missing.stdout, "", "a stop result carries no injected context")
        self.assertIn("accepted assessment", self.flush().lower())
        self.assertIsNone(StateStore(self.state_root).load(self.project).active_run.outcome)

    def test_codex_lead_completion_waits_for_matrix_effort(self):
        self.open_run("Ship it")
        assessor = {
            **self.payload(""),
            "hook_event_name": "SubagentStart",
            "agent_id": "assessor-1",
            "agent_type": "symphony_assessor_gpt_6_high",
            "model_reasoning_effort": "high",
        }
        handle(assessor, self.environ)
        assessment = json.dumps(
            {
                "size": "small",
                "complexity": "simple",
                "risk": "normal",
                "rationale": "bounded task",
                "topology": "direct",
            }
        )
        handle(
            {
                **assessor,
                "hook_event_name": "SubagentStop",
                "status": "completed",
                "last_assistant_message": f"SYMPHONY_ASSESSMENT: {assessment}",
            },
            self.environ,
        )
        lead = {
            **self.payload(""),
            "hook_event_name": "SubagentStart",
            "agent_id": "lead-1",
            "agent_type": "symphony_lead_gpt_5_high",
            "model_reasoning_effort": "high",
        }
        handle(lead, self.environ)

        result = handle(
            {
                **lead,
                "hook_event_name": "SubagentStop",
                "status": "completed",
                "last_assistant_message": "Done",
            },
            self.environ,
        )

        self.assertEqual(result.stdout, "", "a stop result carries no injected context")
        self.assertIn("gpt-5.6-sol/medium", self.flush().lower())
        recovering = StateStore(self.state_root).load(self.project).active_run
        self.assertEqual(recovering.status, "recovering")
        replacement = {
            **self.payload(""),
            "hook_event_name": "SubagentStart",
            "agent_id": "lead-2",
            "agent_type": "symphony_lead_gpt_5_6_sol_medium",
            "model": "gpt-5.6-sol",
            "model_reasoning_effort": "medium",
        }
        handle(replacement, self.environ)
        replaced = StateStore(self.state_root).load(self.project).active_run
        self.assertEqual(replaced.lead_identity, "lead-2")
        self.assertEqual(replaced.owner_generation, 2)
        handle(
            {
                **replacement,
                "hook_event_name": "SubagentStop",
                "status": "completed",
                "last_assistant_message": "Done",
            },
            self.environ,
        )
        self.assertEqual(
            StateStore(self.state_root).load(self.project).recent_runs[-1].outcome["status"],
            "completed",
        )

    def test_codex_native_lifecycle_accepts_assessment_and_lead_outcome(self):
        self.open_run("Return OK")

        assessor_transcript = self.root / "assessor.jsonl"
        assessor_transcript.write_text(
            "\n".join(
                (
                    json.dumps(
                        {
                            "type": "session_meta",
                            "payload": {"agent_path": "/root/symphony_assessor_gpt_6_astra_high"},
                        }
                    ),
                    json.dumps(
                        {
                            "type": "turn_context",
                            "payload": {"model": "gpt-6-astra", "effort": "high"},
                        }
                    ),
                )
            ),
            encoding="utf-8",
        )
        assessor = {
            **self.payload(""),
            "hook_event_name": "SubagentStart",
            "agent_id": "assessor-1",
            "agent_type": "default",
            "transcript_path": str(assessor_transcript),
        }
        handle(assessor, self.environ)
        observed_assessor = StateStore(self.state_root).load(self.project).active_run.delegations[-1]
        self.assertEqual(observed_assessor.role, "assessor")
        self.assertEqual(observed_assessor.requested_effort, "high")
        assessment = json.dumps(
            {
                "size": "small",
                "complexity": "simple",
                "risk": "normal",
                "rationale": "bounded read-only response",
                "topology": "direct",
            }
        )
        handle(
            {
                **assessor,
                "hook_event_name": "SubagentStop",
                "transcript_path": "",
                "agent_transcript_path": str(assessor_transcript),
                "last_assistant_message": f"SYMPHONY_ASSESSMENT: {assessment}",
            },
            self.environ,
        )

        state = StateStore(self.state_root).load(self.project)
        self.assertEqual(state.active_run.assessment["size"], "small")
        self.assertEqual(state.active_run.assessment["complexity"], "simple")
        self.assertEqual(state.active_run.assessment["route"]["lead_model"], "gpt-5.6-sol")
        self.assertEqual(state.active_run.assessment["route"]["lead_effort"], "medium")

        lead_transcript = self.root / "lead.jsonl"
        lead_transcript.write_text(
            "\n".join(
                (
                    json.dumps(
                        {
                            "type": "session_meta",
                            "payload": {"agent_path": "/root/symphony_lead_gpt_5_6_sol_medium"},
                        }
                    ),
                    json.dumps(
                        {
                            "type": "turn_context",
                            "payload": {"model": "gpt-5.6-sol", "effort": "medium"},
                        }
                    ),
                )
            ),
            encoding="utf-8",
        )
        lead = {
            **self.payload(""),
            "hook_event_name": "SubagentStart",
            "agent_id": "lead-1",
            "agent_type": "default",
            "transcript_path": str(lead_transcript),
        }
        handle(lead, self.environ)
        handle(
            {
                **lead,
                "hook_event_name": "SubagentStop",
                "transcript_path": "",
                "agent_transcript_path": str(lead_transcript),
                "last_assistant_message": "OK",
            },
            self.environ,
        )

        stop = {**self.payload(""), "hook_event_name": "Stop"}
        self.assertEqual(handle(stop, self.environ).stdout, "")
        completed = StateStore(self.state_root).load(self.project).recent_runs[-1]
        self.assertEqual(completed.outcome["status"], "completed")
        self.assertNotIn("message", completed.outcome)

    def test_codex_low_effort_assessor_result_is_not_accepted(self):
        assessor = {
            **self.payload(""),
            "hook_event_name": "SubagentStart",
            "agent_id": "assessor-1",
            "agent_type": "symphony_assessor_gpt_5_low",
            "model_reasoning_effort": "low",
        }
        handle(assessor, self.environ)
        assessment = json.dumps(
            {
                "size": "small",
                "complexity": "simple",
                "risk": "normal",
                "rationale": "bounded task",
                "topology": "direct",
            }
        )

        result = handle(
            {
                **assessor,
                "hook_event_name": "SubagentStop",
                "status": "completed",
                "last_assistant_message": f"SYMPHONY_ASSESSMENT: {assessment}",
            },
            self.environ,
        )

        self.assertEqual(result.stdout, "", "a stop result carries no injected context")
        self.assertIn("high effort", self.flush().lower())
        self.assertNotIn("size", StateStore(self.state_root).load(self.project).active_run.assessment)

    def test_codex_stop_can_fill_missing_start_metadata_from_child_transcript(self):
        environ = {**self.environ, "SYMPHONY_PROVIDER": "codex"}
        self.open_run("Ship it")
        start = {
            "session_id": "codex-session",
            "cwd": str(self.project),
            "hook_event_name": "SubagentStart",
            "agent_id": "assessor-1",
            "agent_type": "default",
        }
        handle(start, environ)
        transcript = self.root / "late-assessor.jsonl"
        transcript.write_text(
            "\n".join(
                (
                    json.dumps(
                        {
                            "type": "session_meta",
                            "payload": {"agent_path": "/root/symphony_assessor_gpt_6_astra_high"},
                        }
                    ),
                    json.dumps(
                        {
                            "type": "turn_context",
                            "payload": {"model": "gpt-6-astra", "effort": "high"},
                        }
                    ),
                )
            ),
            encoding="utf-8",
        )
        assessment = json.dumps(
            {
                "size": "small",
                "complexity": "simple",
                "risk": "normal",
                "rationale": "bounded task",
                "topology": "direct",
            }
        )

        handle(
            {
                **start,
                "hook_event_name": "SubagentStop",
                "agent_transcript_path": str(transcript),
                "status": "completed",
                "last_assistant_message": f"SYMPHONY_ASSESSMENT: {assessment}",
            },
            environ,
        )

        state = StateStore(self.state_root).load(self.project)
        assessor = state.active_run.delegations[-1]
        self.assertEqual((assessor.requested_tier, assessor.requested_effort), ("gpt-6-astra", "high"))
        self.assertEqual(state.active_run.assessment["size"], "small")

    def test_failed_lead_stays_recoverable_and_stop_remains_guarded(self):
        self.open_run("Ship it")
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

    def test_unknown_payload_fields_do_not_disturb_the_guard(self):
        self.open_run("Ship it")
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
        self.assertEqual(state.active_run.delegations[-1].identity, "worker-1")
        stop = {**self.payload(""), "hook_event_name": "Stop"}
        self.assertEqual(self.output(handle(stop, self.environ))["decision"], "block")

    def test_disable_preserves_active_run_until_observed_agents_stop(self):
        handle(self.payload("$symphony:symphony enable Ship it"), self.environ)
        self.open_run("Ship it")
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
        self.open_run("Ship it")
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
            "tool_input": {
                "message": f"SYMPHONY_ROLE: lead\nSYMPHONY_ROUTE: {marker}\nShip it",
                "model": "gpt-5.6-terra",
                "reasoning_effort": "high",
            },
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
        self.assertIn("Lead route: gpt-5.6-terra/high", status)
        self.assertIn("Lead: lead-1", status)

    def test_pre_tool_use_denies_unclassified_agent_spawn(self):
        handle(self.payload("$symphony:symphony enable"), self.environ)
        hook = {
            **self.payload(""),
            "hook_event_name": "PreToolUse",
            "tool_name": "spawn_agent",
            "tool_input": {"message": "Inspect the repository"},
        }

        output = self.output(handle(hook, self.environ))

        self.assertEqual(output["decision"], "block")
        self.assertIn("SYMPHONY_ROLE", output["reason"])

    def test_prepared_assessor_role_survives_generic_host_label(self):
        handle(self.payload("$symphony:symphony start Ship it"), self.environ)
        prepared = {
            **self.payload(""),
            "hook_event_name": "PreToolUse",
            "tool_name": "spawn_agent",
            "tool_input": {
                "message": "SYMPHONY_ROLE: assessor\nAssess the task",
                "model": "gpt-strong",
                "reasoning_effort": "high",
            },
        }
        prepared_output = self.output(handle(prepared, self.environ))
        self.assertNotIn("decision", prepared_output)

        started = {
            **self.payload(""),
            "hook_event_name": "SubagentStart",
            "agent_id": "assessor-1",
            "agent_type": "general-purpose",
        }
        handle(started, self.environ)

        delegation = StateStore(self.state_root).load(self.project).active_run.delegations[-1]
        self.assertEqual(delegation.role, "assessor")
        self.assertEqual(delegation.requested_tier, "gpt-strong")
        self.assertEqual(delegation.requested_effort, "high")

    def test_prepared_lead_role_survives_generic_host_label(self):
        self.open_run("Ship it")
        handle(
            {
                **self.payload(""),
                "hook_event_name": "SubagentStart",
                "agent_id": "assessor-1",
                "agent_type": "symphony_assessor_gpt_6_astra_high",
            },
            self.environ,
        )
        marker = json.dumps(
            {
                "size": "large",
                "complexity": "complex",
                "risk": "normal",
                "rationale": "broad project work",
                "topology": "delegated",
            }
        )
        prepared = {
            **self.payload(""),
            "hook_event_name": "PreToolUse",
            "tool_name": "spawn_agent",
            "tool_input": {
                "message": f"SYMPHONY_ROLE: lead\nSYMPHONY_ROUTE: {marker}\nRun it",
                "model": "gpt-5.6-luna",
                "reasoning_effort": "medium",
            },
        }
        prepared_output = self.output(handle(prepared, self.environ))
        self.assertNotIn("decision", prepared_output)

        started = {
            **self.payload(""),
            "hook_event_name": "SubagentStart",
            "agent_id": "lead-1",
            "agent_type": "claude",
        }
        handle(started, self.environ)

        state = StateStore(self.state_root).load(self.project)
        self.assertEqual(state.active_run.lead_identity, "lead-1")
        self.assertEqual(state.active_run.delegations[-1].role, "lead")
        self.assertEqual(state.active_run.delegations[-1].requested_tier, "gpt-5.6-luna")
        self.assertEqual(state.active_run.delegations[-1].objective, "Run it")

    def test_lead_spawn_without_route_is_denied(self):
        self.open_run("Ship it")
        hook = {
            **self.payload(""),
            "hook_event_name": "PreToolUse",
            "tool_name": "spawn_agent",
            "tool_input": {
                "message": "SYMPHONY_ROLE: lead\nRun it",
                "model": "gpt-balanced",
                "reasoning_effort": "high",
            },
        }

        output = self.output(handle(hook, self.environ))

        self.assertEqual(output["decision"], "block")
        self.assertIn("SYMPHONY_ROUTE", output["reason"])

    def test_consultant_spawn_requires_decision_local_classification(self):
        self.open_run("Ship it")
        store = StateStore(self.state_root)
        state = store.load(self.project)
        store.save(
            self.project,
            ProjectState(
                enabled=state.enabled,
                activation=state.activation,
                active_run=RunState(
                    state.active_run.run_id,
                    state.active_run.task,
                    status="active",
                    lead_identity="lead-1",
                ),
            ),
        )
        hook = {
            **self.payload(""),
            "hook_event_name": "PreToolUse",
            "tool_name": "spawn_agent",
            "tool_input": {
                "message": "SYMPHONY_ROLE: consultant\nDecide the storage boundary",
                "model": "gpt-strong",
                "reasoning_effort": "high",
            },
        }

        output = self.output(handle(hook, self.environ))

        self.assertEqual(output["decision"], "block")
        self.assertIn("SYMPHONY_DECISION", output["reason"])

    def test_unclassified_consultant_result_blocks_lead_completion(self):
        self.open_run("Ship it")
        marker = json.dumps(
            {
                "size": "small",
                "complexity": "simple",
                "risk": "normal",
                "rationale": "bounded task",
                "topology": "direct",
            }
        )
        handle(
            {
                **self.payload(""),
                "hook_event_name": "PreToolUse",
                "tool_name": "spawn_agent",
                "tool_input": {
                    "message": f"SYMPHONY_ROLE: lead\nSYMPHONY_ROUTE: {marker}\nShip it",
                    "model": "gpt-5.6-sol",
                    "reasoning_effort": "medium",
                },
            },
            self.environ,
        )
        lead = {
            **self.payload(""),
            "hook_event_name": "SubagentStart",
            "agent_id": "lead-1",
            "agent_type": "symphony_lead_gpt_5_medium",
        }
        consultant = {
            **self.payload(""),
            "hook_event_name": "SubagentStart",
            "agent_id": "consultant-1",
            "agent_type": "symphony_consultant_gpt_6_high",
        }
        handle(lead, self.environ)
        handle(consultant, self.environ)

        missing = handle(
            {
                **consultant,
                "hook_event_name": "SubagentStop",
                "status": "completed",
                "last_assistant_message": "Use the simpler storage boundary.",
            },
            self.environ,
        )
        self.assertIn("SYMPHONY_DECISION", self.flush())
        handle(
            {
                **lead,
                "hook_event_name": "SubagentStop",
                "status": "completed",
                "last_assistant_message": "Done",
            },
            self.environ,
        )
        self.assertIsNone(StateStore(self.state_root).load(self.project).active_run.outcome)

        classified = "\n".join(
            (
                'SYMPHONY_DECISION: {"size":"small","complexity":"simple"}',
                'SYMPHONY_DECISION: {"size":"small","complexity":"mixed"}',
            )
        )
        handle(
            {
                **consultant,
                "hook_event_name": "SubagentStop",
                "status": "completed",
                "last_assistant_message": classified,
            },
            self.environ,
        )
        handle(
            {
                **lead,
                "hook_event_name": "SubagentStop",
                "status": "completed",
                "last_assistant_message": "Done",
            },
            self.environ,
        )
        self.assertEqual(
            StateStore(self.state_root).load(self.project).recent_runs[-1].outcome["status"],
            "completed",
        )

    def test_late_unclassified_consultant_blocks_deferred_stop(self):
        marker = json.dumps(
            {
                "size": "small",
                "complexity": "simple",
                "risk": "normal",
                "rationale": "bounded task",
                "topology": "direct",
            }
        )
        self.open_run("Ship it")
        handle(
            {
                **self.payload(""),
                "hook_event_name": "PreToolUse",
                "tool_name": "spawn_agent",
                "tool_input": {
                    "message": f"SYMPHONY_ROLE: lead\nSYMPHONY_ROUTE: {marker}\nShip it",
                    "model": "gpt-5.6-sol",
                    "reasoning_effort": "medium",
                },
            },
            self.environ,
        )
        lead = {
            **self.payload(""),
            "hook_event_name": "SubagentStart",
            "agent_id": "lead-1",
            "agent_type": "lead",
        }
        consultant = {
            **self.payload(""),
            "hook_event_name": "SubagentStart",
            "agent_id": "consultant-1",
            "agent_type": "consultant",
        }
        handle(lead, self.environ)
        handle(consultant, self.environ)
        handle(
            {
                **lead,
                "hook_event_name": "SubagentStop",
                "status": "completed",
                "last_assistant_message": "Done",
            },
            self.environ,
        )
        handle(
            {
                **consultant,
                "hook_event_name": "SubagentStop",
                "status": "completed",
                "last_assistant_message": "Use the simple option.",
            },
            self.environ,
        )

        blocked = handle({**self.payload(""), "hook_event_name": "Stop"}, self.environ)

        self.assertEqual(self.output(blocked)["decision"], "block")
        self.assertIn("consultant", self.output(blocked)["reason"].lower())
        self.assertIsNotNone(StateStore(self.state_root).load(self.project).active_run)

    def test_invalid_consultant_does_not_suppress_failed_lead_recovery(self):
        self.open_run("Ship it")
        lead = {
            **self.payload(""),
            "hook_event_name": "SubagentStart",
            "agent_id": "lead-1",
            "agent_type": "lead",
        }
        consultant = {
            **self.payload(""),
            "hook_event_name": "SubagentStart",
            "agent_id": "consultant-1",
            "agent_type": "consultant",
        }
        handle(lead, self.environ)
        handle(consultant, self.environ)
        handle(
            {
                **consultant,
                "hook_event_name": "SubagentStop",
                "status": "completed",
                "last_assistant_message": "Unclassified advice",
            },
            self.environ,
        )

        result = handle(
            {**lead, "hook_event_name": "SubagentStop", "status": "failed"},
            self.environ,
        )

        self.assertEqual(StateStore(self.state_root).load(self.project).active_run.status, "recovering")
        self.assertIn("replacement", self.flush().lower())

    def test_claude_pending_spawns_match_native_roles_out_of_order(self):
        StateStore(self.state_root).save(
            self.project,
            ProjectState(active_run=RunState("run-1", "task", lead_identity="lead-1")),
        )
        for role, agent_type, extra in (
            ("worker", "symphony:symphony-worker-haiku-low", ""),
            (
                "consultant",
                "symphony:symphony-consultant-opus-high",
                '\nSYMPHONY_DECISION: {"size":"small","complexity":"mixed"}',
            ),
        ):
            handle(
                {
                    **self.payload("", "claude"),
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Agent",
                    "tool_input": {
                        "prompt": f"SYMPHONY_ROLE: {role}{extra}\nDo it",
                        "subagent_type": agent_type,
                    },
                },
                self.environ,
            )
        consultant_started = {
            **self.payload("", "claude"),
            "hook_event_name": "SubagentStart",
            "agent_id": "consultant-1",
            "agent_type": "symphony:symphony-consultant-opus-high",
        }
        handle(consultant_started, self.environ)
        handle(consultant_started, self.environ)
        handle(
            {
                **self.payload("", "claude"),
                "hook_event_name": "SubagentStart",
                "agent_id": "worker-1",
                "agent_type": "symphony:symphony-worker-haiku-low",
            },
            self.environ,
        )

        roles = {
            item.identity: item.role
            for item in StateStore(self.state_root).load(self.project).active_run.delegations
        }
        self.assertEqual(roles, {"consultant-1": "consultant", "worker-1": "worker"})

    def test_claude_same_role_pending_spawns_match_native_model_and_effort(self):
        StateStore(self.state_root).save(
            self.project,
            ProjectState(active_run=RunState("run-1", "task", lead_identity="lead-1")),
        )
        for agent_type in (
            "symphony:symphony-worker-haiku-low",
            "symphony:symphony-worker-sonnet-high",
        ):
            handle(
                {
                    **self.payload("", "claude"),
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Agent",
                    "tool_input": {
                        "prompt": "SYMPHONY_ROLE: worker\nDo it",
                        "subagent_type": agent_type,
                    },
                },
                self.environ,
            )
        for identity, agent_type in (
            ("worker-sonnet", "symphony:symphony-worker-sonnet-high"),
            ("worker-haiku", "symphony:symphony-worker-haiku-low"),
        ):
            handle(
                {
                    **self.payload("", "claude"),
                    "hook_event_name": "SubagentStart",
                    "agent_id": identity,
                    "agent_type": agent_type,
                },
                self.environ,
            )

        delegations = {
            item.identity: (item.requested_tier, item.requested_effort)
            for item in StateStore(self.state_root).load(self.project).active_run.delegations
        }
        self.assertEqual(delegations["worker-sonnet"], ("sonnet", "high"))
        self.assertEqual(delegations["worker-haiku"], ("haiku", "low"))

    def test_claude_replacement_preparation_preserves_recovery_generation(self):
        marker = json.dumps(
            {
                "size": "small",
                "complexity": "simple",
                "risk": "normal",
                "rationale": "bounded task",
                "topology": "direct",
            }
        )
        run = RunState(
            "run-1",
            "task",
            status="recovering",
            lead_identity="lead-1",
            assessment={
                "size": "small",
                "complexity": "simple",
                "risk": "normal",
                "rationale": "bounded task",
                "topology": "direct",
                "route": {"lead_model": "sonnet", "lead_effort": "medium"},
                "_invalid_consultants": ["consultant-1"],
            },
        )
        StateStore(self.state_root).save(self.project, ProjectState(active_run=run))
        prepared = {
            **self.payload("", "claude"),
            "hook_event_name": "PreToolUse",
            "tool_name": "Agent",
            "tool_input": {
                "prompt": f"SYMPHONY_ROLE: lead\nSYMPHONY_ROUTE: {marker}\nRecover",
                "subagent_type": "symphony:symphony-lead-sonnet-medium",
            },
        }
        handle(prepared, self.environ)
        handle(
            {
                **self.payload("", "claude"),
                "hook_event_name": "SubagentStart",
                "agent_id": "lead-2",
                "agent_type": "symphony:symphony-lead-sonnet-medium",
            },
            self.environ,
        )

        replaced = StateStore(self.state_root).load(self.project).active_run
        self.assertEqual(replaced.status, "active")
        self.assertEqual(replaced.lead_identity, "lead-2")
        self.assertEqual(replaced.owner_generation, 2)
        self.assertEqual(replaced.assessment["_invalid_consultants"], ["consultant-1"])

    def test_claude_agent_spawn_is_denied_without_a_symphony_agent_type(self):
        handle(self.payload("/symphony:start Ship it", "claude"), self.environ)
        hook = {
            **self.payload("", "claude"),
            "hook_event_name": "PreToolUse",
            "tool_name": "Agent",
            "tool_input": {
                "prompt": "SYMPHONY_ROLE: assessor\nAssess the task",
                "model": "opus",
            },
        }

        output = self.output(handle(hook, self.environ))

        decision = output["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertIn("Symphony agent type", decision["permissionDecisionReason"])

    def test_claude_agent_model_override_cannot_change_packaged_role_model(self):
        handle(self.payload("/symphony:start Ship it", "claude"), self.environ)
        hook = {
            **self.payload("", "claude"),
            "hook_event_name": "PreToolUse",
            "tool_name": "Agent",
            "tool_input": {
                "prompt": "SYMPHONY_ROLE: assessor\nAssess the task",
                "subagent_type": "symphony:symphony-assessor-opus-high",
                "model": "haiku",
            },
        }

        decision = self.output(handle(hook, self.environ))["hookSpecificOutput"]

        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertIn("model override", decision["permissionDecisionReason"].lower())

    def test_claude_symphony_agent_type_supplies_model_and_effort(self):
        handle(self.payload("/symphony:start Ship it", "claude"), self.environ)
        prepared = {
            **self.payload("", "claude"),
            "hook_event_name": "PreToolUse",
            "tool_name": "Agent",
            "tool_input": {
                "prompt": "SYMPHONY_ROLE: assessor\nAssess the task",
                "subagent_type": "symphony:symphony-assessor-opus-high",
            },
        }
        self.assertEqual(handle(prepared, self.environ).stdout, "")

        started = {
            **self.payload("", "claude"),
            "hook_event_name": "SubagentStart",
            "agent_id": "assessor-1",
            "agent_type": "symphony:symphony-assessor-opus-high",
        }
        handle(started, self.environ)

        delegation = StateStore(self.state_root).load(self.project).active_run.delegations[-1]
        self.assertEqual(delegation.role, "assessor")
        self.assertEqual(delegation.requested_tier, "opus")
        self.assertEqual(delegation.requested_effort, "high")

    def test_claude_assessment_feedback_is_delivered_to_parent_post_tool_use(self):
        handle(self.payload("/symphony:start Ship it", "claude"), self.environ)
        prepared = {
            **self.payload("", "claude"),
            "hook_event_name": "PreToolUse",
            "tool_name": "Agent",
            "tool_input": {
                "prompt": "SYMPHONY_ROLE: assessor\nAssess the task",
                "subagent_type": "symphony:symphony-assessor-opus-high",
            },
        }
        handle(prepared, self.environ)
        started = {
            **self.payload("", "claude"),
            "hook_event_name": "SubagentStart",
            "agent_id": "assessor-1",
            "agent_type": "symphony:symphony-assessor-opus-high",
        }
        handle(started, self.environ)
        assessment = json.dumps(
            {
                "size": "small",
                "complexity": "simple",
                "risk": "normal",
                "rationale": "bounded task",
                "topology": "direct",
            }
        )

        stopped = handle(
            {
                **started,
                "hook_event_name": "SubagentStop",
                "status": "completed",
                "last_assistant_message": f"SYMPHONY_ASSESSMENT: {assessment}",
            },
            self.environ,
        )
        parent = handle(
            {
                **self.payload("", "claude"),
                "hook_event_name": "PostToolUse",
                "tool_name": "Agent",
            },
            self.environ,
        )

        self.assertEqual(stopped.stdout, "")
        self.assertIn("accepted", self.context(parent).lower())

    def test_legacy_provider_data_is_imported_once(self):
        legacy_root = self.root / "plugin-data"
        from plugins.symphony.symphony.store import legacy_project_keys

        legacy = legacy_root / "projects" / f"{legacy_project_keys(self.project)[0]}.json"
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
        self.assertIn("summarize", self.context(result))

        self.open_run("summarize")
        self.assertEqual(StateStore(self.state_root).load(self.project).active_run.task, "summarize")

    def test_identical_claude_task_can_run_again_after_completion(self):
        prompt = "SYMPHONY_CONTROL: start\nARGUMENTS: Repeat me"
        handle(self.payload(prompt, "claude"), self.environ)
        self.open_run("Repeat me", "claude")
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
        self.open_run("Repeat me", "claude")

        self.assertEqual(store.load(self.project).active_run.task, "Repeat me")


    def test_enabled_prompt_alone_cannot_hold_the_session(self):
        handle(self.payload("$symphony:symphony enable"), self.environ)
        handle(self.payload("Just answer this directly"), self.environ)

        stop = {**self.payload(""), "hook_event_name": "Stop"}
        result = handle(stop, self.environ)

        self.assertEqual(result.stdout, "", "a prompt that spawned nothing must not block Stop")
        self.assertIsNone(StateStore(self.state_root).load(self.project).active_run)

    def test_repeated_stop_releases_a_session_whose_child_never_reported(self):
        self.open_run("Ship it")
        handle(
            {
                **self.payload(""),
                "hook_event_name": "SubagentStart",
                "agent_id": "assessor-1",
                "agent_type": "symphony_assessor_gpt_6_astra_high",
            },
            self.environ,
        )
        stop = {**self.payload(""), "hook_event_name": "Stop"}

        blocked = self.output(handle(stop, self.environ))
        self.assertEqual(blocked["decision"], "block")
        self.assertIn("--force", blocked["reason"])
        self.assertIn("timeout", blocked["reason"])

        released = handle({**stop, "stop_hook_active": True}, self.environ)

        self.assertEqual(released.stdout, "", "abandonment must render an empty Stop response")
        state = StateStore(self.state_root).load(self.project)
        self.assertIsNone(state.active_run)
        self.assertEqual(state.recent_runs[-1].status, "abandoned")
        self.assertEqual(state.recent_runs[-1].unreconciled, ("assessor-1",))

    def test_resumed_session_reconciles_delegations_the_host_cannot_list(self):
        """A host that reports a resume is telling us the old process ended.

        A session id it has never seen is not the same claim: that is also what
        a second terminal in the same project looks like, and taking the run
        over on that evidence killed live leads.
        """
        self.open_run("Ship it")
        handle(
            {
                **self.payload(""),
                "hook_event_name": "SubagentStart",
                "agent_id": "worker-1",
                "agent_type": "symphony_worker_gpt_5_6_sol_medium",
            },
            self.environ,
        )

        resumed = {
            **self.payload(""),
            "session_id": "codex-session-2",
            "hook_event_name": "SessionStart",
            "source": "resume",
        }
        handle(resumed, self.environ)

        run = StateStore(self.state_root).load(self.project).active_run
        self.assertEqual(run.status, "recovering")
        self.assertEqual({item.state for item in run.delegations}, {"interrupted"})

    def test_version_reports_the_build_actually_running(self):
        """Installed and running differ until the host restarts, which is the
        whole reason to ask."""
        from plugins.symphony.symphony import PLUGIN_VERSION

        text = self.context(handle(self.payload("/symphony:version", "claude"), self.environ))

        self.assertIn(PLUGIN_VERSION, text)
        self.assertIn("running", text.lower())

    def test_version_names_the_directory_the_code_was_loaded_from(self):
        environ = {**self.environ, "SYMPHONY_PLUGIN_ROOT": "/cache/symphony/9.9.9"}
        text = self.context(handle(self.payload("/symphony:version", "claude"), environ))

        self.assertIn("/cache/symphony/9.9.9", text)

    def test_version_flags_a_newer_build_waiting_for_a_restart(self):
        """The trap we hit twice: `plugin update` reports a version the open
        session is not running."""
        cache = self.root / "cache" / "symphony"
        (cache / "1.1.1").mkdir(parents=True)
        (cache / "1.2.0").mkdir()
        environ = {**self.environ, "SYMPHONY_PLUGIN_ROOT": str(cache / "1.1.1")}

        text = self.context(handle(self.payload("$symphony:symphony version"), environ))

        self.assertIn("1.2.0", text)
        self.assertIn("restart", text.lower())

    def test_version_orders_builds_numerically_not_alphabetically(self):
        """0.10.1 is newer than 0.9.0, and a string sort says the opposite."""
        cache = self.root / "cache" / "symphony"
        (cache / "0.9.0").mkdir(parents=True)
        (cache / "0.10.1").mkdir()
        environ = {**self.environ, "SYMPHONY_PLUGIN_ROOT": str(cache / "0.9.0")}

        text = self.context(handle(self.payload("$symphony:symphony version"), environ))

        self.assertIn("0.10.1", text)

    def test_version_does_not_offer_a_restart_onto_an_older_build(self):
        """The same comparison in the other direction, where a string sort lies."""
        cache = self.root / "cache" / "symphony"
        (cache / "0.9.0").mkdir(parents=True)
        (cache / "0.10.1").mkdir()
        environ = {**self.environ, "SYMPHONY_PLUGIN_ROOT": str(cache / "0.10.1")}

        text = self.context(handle(self.payload("$symphony:symphony version"), environ))

        self.assertNotIn("restart", text.lower())

    def test_repeated_control_in_one_claude_session_is_not_swallowed(self):
        handle(self.payload("/symphony:enable", "claude"), self.environ)
        handle(self.payload("/symphony:disable", "claude"), self.environ)
        result = handle(self.payload("/symphony:enable", "claude"), self.environ)

        self.assertIn("enabled", self.context(result).lower())
        self.assertTrue(StateStore(self.state_root).load(self.project).enabled)

    def test_retried_consultant_clears_the_earlier_classification_block(self):
        self.open_run("Ship it")
        lead = {
            **self.payload(""),
            "hook_event_name": "SubagentStart",
            "agent_id": "lead-1",
            "agent_type": "symphony_lead_gpt_5_6_sol_medium",
            "model": "gpt-5.6-sol",
            "model_reasoning_effort": "medium",
        }
        handle(lead, self.environ)

        def consultant(identity, message):
            spawn = {
                **self.payload(""),
                "hook_event_name": "SubagentStart",
                "agent_id": identity,
                "agent_type": "symphony_consultant_gpt_6_astra_high",
                "task": "Pick the cache strategy",
            }
            handle(spawn, self.environ)
            handle(
                {
                    **spawn,
                    "hook_event_name": "SubagentStop",
                    "status": "completed",
                    "last_assistant_message": message,
                },
                self.environ,
            )

        consultant("consultant-1", "no classification here")
        blocked = StateStore(self.state_root).load(self.project)
        self.assertIn("consultant-1", blocked.active_run.assessment["_invalid_consultants"])

        decision = json.dumps({"size": "small", "complexity": "simple"})
        consultant("consultant-2", f"SYMPHONY_DECISION: {decision}")

        cleared = StateStore(self.state_root).load(self.project)
        self.assertNotIn("_invalid_consultants", cleared.active_run.assessment)

    def test_host_payload_prose_is_never_written_to_disk(self):
        secret = "ghp_examplevaluethatmustnotpersist"
        handle(self.payload(f"$symphony:symphony enable"), self.environ)
        handle(self.payload(f"Deploy using {secret} right now"), self.environ)
        self.open_run("Ship it")
        handle(
            {
                **self.payload(""),
                "hook_event_name": "SubagentStart",
                "agent_id": "lead-1",
                "agent_type": "symphony_lead_gpt_5_6_sol_medium",
            },
            self.environ,
        )
        handle(
            {
                **self.payload(""),
                "hook_event_name": "SubagentStop",
                "agent_id": "lead-1",
                "agent_type": "symphony_lead_gpt_5_6_sol_medium",
                "status": "completed",
                "last_assistant_message": f"the token is {secret}",
                "transcript_path": "/tmp/transcript.jsonl",
            },
            self.environ,
        )
        handle(
            {
                **self.payload(""),
                "hook_event_name": "Stop",
                "stop_hook_active": True,
                "last_assistant_message": f"and again {secret}",
            },
            self.environ,
        )

        written = "\n".join(
            path.read_text() for path in self.state_root.rglob("*.json")
        )
        self.assertNotIn(secret, written)
        self.assertNotIn("transcript", written)

    def test_hook_fault_is_recorded_and_surfaced_once(self):
        from plugins.symphony.symphony import runtime

        runtime._record_fault(RuntimeError("boom"), self.environ)
        self.assertTrue((self.state_root.parent / "faults.log").exists())

        status = self.context(handle(self.payload("$symphony:symphony status"), self.environ))

        self.assertIn("RuntimeError", status)
        self.assertFalse((self.state_root.parent / "faults.log").exists())
        repeated = self.context(handle(self.payload("$symphony:symphony status"), self.environ))
        self.assertNotIn("RuntimeError", repeated)


if __name__ == "__main__":
    unittest.main()
