from dataclasses import replace
import unittest

from plugins.symphony.symphony.model import Action, Delegation, Event, ProjectState, RunState
from plugins.symphony.symphony.reducer import reduce


NOW = "2026-09-17T12:00:00+00:00"


def event(kind, *, event_id=None, **payload):
    return Event(event_id or f"event-{kind}", kind, NOW, payload)


def delegation(identity, state="working", role="worker"):
    return Delegation(
        identity=identity,
        role=role,
        objective=f"Objective for {identity}",
        state=state,
        requested_tier="balanced",
        requested_effort="medium",
        updated_at=NOW,
    )


def running_state(*, enabled=True, status="active", lead="lead-1", delegations=(), outcome=None):
    return ProjectState(
        enabled=enabled,
        active_run=RunState(
            run_id="run-1",
            task="Ship Symphony",
            status=status,
            lead_identity=lead,
            delegations=tuple(delegations),
            outcome=outcome,
            started_at=NOW,
            updated_at=NOW,
        ),
    )


class LifecycleReducerTests(unittest.TestCase):
    def test_enablement_and_session_heartbeat_persist(self):
        state, actions = reduce(ProjectState(), event("enable"))
        state, heartbeat_actions = reduce(
            state,
            event(
                "session_heartbeat",
                provider="codex",
                session_id="session-1",
                plugin_version="1.0.0",
                plugin_root="/plugins/symphony/1.0.0",
                hook_schema_version=1,
            ),
        )

        self.assertTrue(state.enabled)
        self.assertEqual(state.activation["codex"]["state"], "guarded")
        self.assertEqual(state.activation["codex"]["session_id"], "session-1")
        self.assertEqual(state.activation["codex"]["plugin_root"], "/plugins/symphony/1.0.0")
        self.assertEqual(actions, (Action("project_enabled"),))
        self.assertEqual(heartbeat_actions, ())

    def test_one_shot_managed_task_does_not_enable_project(self):
        state, actions = reduce(
            ProjectState(enabled=False),
            event("task_received", run_id="run-once", task="One task", one_shot=True),
        )

        self.assertFalse(state.enabled)
        self.assertEqual(state.active_run.run_id, "run-once")
        self.assertEqual(state.active_run.status, "assessing")
        self.assertEqual(actions, (Action("request_assessment", {"run_id": "run-once"}),))

    def test_disabled_project_ignores_an_ordinary_task(self):
        original = ProjectState(enabled=False)

        state, actions = reduce(original, event("task_received", task="Ordinary task"))

        self.assertEqual(state, original)
        self.assertEqual(actions, ())

    def test_bypass_is_inert_with_respect_to_project_and_run_state(self):
        original = running_state()

        state, actions = reduce(original, event("bypass", task="Run outside Symphony"))

        self.assertEqual(state.enabled, original.enabled)
        self.assertEqual(state.active_run, original.active_run)
        self.assertEqual(actions, (Action("execute_bypass", {"task": "Run outside Symphony"}),))

    def test_assessment_events_advance_only_the_current_run(self):
        original = running_state(status="assessing", lead=None)
        requested, request_actions = reduce(original, event("assessment_requested"))
        accepted, accepted_actions = reduce(
            requested,
            event("assessment_accepted", size="medium", complexity="mixed", risk="normal"),
        )

        self.assertEqual(request_actions, (Action("spawn_assessor", {"run_id": "run-1"}),))
        self.assertEqual(accepted.active_run.status, "assessed")
        self.assertEqual(accepted.active_run.assessment["size"], "medium")
        self.assertEqual(accepted_actions, (Action("route_run", {"run_id": "run-1"}),))

    def test_owner_generation_changes_only_for_a_safe_replacement(self):
        original = running_state(status="active")
        rejected, rejected_actions = reduce(
            original,
            event("lead_started", identity="lead-2", owner_generation=2),
        )
        interrupted, _ = reduce(original, event("interrupt", reason="host interrupt"))
        recovering, recovery_actions = reduce(
            interrupted,
            event("resume_reconciled", active_ids=[]),
        )
        replaced, replacement_actions = reduce(
            recovering,
            event("lead_started", identity="lead-2", owner_generation=2),
        )

        self.assertEqual(rejected.active_run.lead_identity, "lead-1")
        self.assertEqual(rejected.active_run.owner_generation, 1)
        self.assertEqual(rejected_actions, (Action("reject_lead_replacement", {"identity": "lead-2"}),))
        self.assertEqual(recovery_actions, (Action("replace_lead", {"owner_generation": 2}),))
        self.assertEqual(replaced.active_run.lead_identity, "lead-2")
        self.assertEqual(replaced.active_run.owner_generation, 2)
        self.assertEqual(replacement_actions, ())

    def test_delegation_updates_replace_the_identity_latest_record(self):
        original = running_state(delegations=[delegation("worker-1", "working")])

        state, actions = reduce(
            original,
            event(
                "delegation_updated",
                identity="worker-1",
                role="worker",
                objective="Objective for worker-1",
                state="completed",
                requested_tier="balanced",
                requested_effort="medium",
            ),
        )

        self.assertEqual(len(state.active_run.delegations), 1)
        self.assertEqual(state.active_run.delegations[0].state, "completed")
        self.assertEqual(actions, ())

    def test_normal_stop_is_blocked_while_a_child_is_active(self):
        original = running_state(delegations=[delegation("worker-1")])
        stop = event("stop_requested")

        state, actions = reduce(original, stop)
        replayed, replay_actions = reduce(state, stop)

        self.assertEqual(state.active_run, original.active_run)
        self.assertEqual(actions, (Action("block_stop", {"active": ["worker-1"]}),))
        self.assertEqual(replayed, state)
        self.assertEqual(replay_actions, actions)

    def test_lead_completion_archives_run_and_permits_completion(self):
        original = running_state(delegations=[delegation("worker-1", "completed")])

        state, actions = reduce(
            original,
            event(
                "lead_completed",
                identity="lead-1",
                owner_generation=1,
                outcome={"summary": "Done", "verified": True},
            ),
        )

        self.assertIsNone(state.active_run)
        self.assertEqual(state.recent_runs[-1].status, "completed")
        self.assertEqual(state.recent_runs[-1].outcome["summary"], "Done")
        self.assertEqual(actions, (Action("permit_completion", {"run_id": "run-1"}),))

    def test_lead_completion_waits_for_active_children(self):
        original = running_state(delegations=[delegation("worker-1")])

        state, actions = reduce(
            original,
            event(
                "lead_completed",
                identity="lead-1",
                owner_generation=1,
                outcome={"summary": "Done"},
            ),
        )

        self.assertEqual(state.active_run.status, "completing")
        self.assertEqual(state.active_run.outcome, {"summary": "Done"})
        self.assertEqual(actions, (Action("wait_for_delegations", {"active": ["worker-1"]}),))

    def test_lead_completion_requires_an_outcome(self):
        original = running_state()

        state, actions = reduce(
            original,
            event("lead_completed", identity="lead-1", owner_generation=1),
        )

        self.assertEqual(state.active_run, original.active_run)
        self.assertEqual(actions, (Action("block_completion", {"reason": "outcome_missing"}),))

    def test_replayed_event_is_idempotent(self):
        enable = event("enable", event_id="stable-event")
        state, _ = reduce(ProjectState(), enable)

        replayed, actions = reduce(state, enable)

        self.assertEqual(replayed, state)
        self.assertEqual(actions, ())

    def test_interrupt_preserves_recoverable_run_and_live_lead_on_resume(self):
        original = running_state(delegations=[delegation("lead-1", role="lead")])
        interrupted, interrupt_actions = reduce(original, event("interrupt", reason="user interrupt"))
        resumed, resume_actions = reduce(
            interrupted,
            event("resume_reconciled", active_ids=["lead-1"]),
        )

        self.assertEqual(interrupted.active_run.status, "interrupted")
        self.assertEqual(interrupt_actions, (Action("preserve_recovery_context", {"run_id": "run-1"}),))
        self.assertEqual(resumed.active_run.status, "active")
        self.assertEqual(resumed.active_run.lead_identity, "lead-1")
        self.assertEqual(resume_actions, ())

    def test_recovery_can_assign_a_new_generation_when_no_lead_was_registered(self):
        original = running_state(status="interrupted", lead=None)
        recovering, actions = reduce(original, event("resume_reconciled", active_ids=[]))

        replaced, replacement_actions = reduce(
            recovering,
            event("lead_started", identity="lead-2", owner_generation=2),
        )

        self.assertEqual(actions, (Action("replace_lead", {"owner_generation": 2}),))
        self.assertEqual(replaced.active_run.lead_identity, "lead-2")
        self.assertEqual(replaced.active_run.owner_generation, 2)
        self.assertEqual(replacement_actions, ())

    def test_reassessment_preserves_active_ownership_and_work(self):
        original = running_state(delegations=[delegation("worker-1")])

        state, actions = reduce(original, event("reassess", reason="scope drift"))

        self.assertEqual(state.active_run.lead_identity, "lead-1")
        self.assertEqual(state.active_run.delegations, original.active_run.delegations)
        self.assertTrue(state.needs_reassessment)
        self.assertEqual(actions, (Action("request_assessment", {"run_id": "run-1"}),))

    def test_reassessment_acceptance_preserves_recovery_and_lifecycle_metadata(self):
        original = running_state(status="recovering")
        original = replace(
            original,
            active_run=replace(
                original.active_run,
                assessment={
                    "size": "small",
                    "complexity": "simple",
                    "_invalid_consultants": ["consultant-1"],
                    "_pending_delegations": [{"role": "worker"}],
                },
            ),
        )

        accepted, _ = reduce(
            original,
            event("assessment_accepted", size="medium", complexity="mixed", risk="normal"),
        )

        self.assertEqual(accepted.active_run.status, "recovering")
        self.assertEqual(accepted.active_run.assessment["size"], "medium")
        self.assertEqual(
            accepted.active_run.assessment["_invalid_consultants"],
            ["consultant-1"],
        )
        self.assertEqual(
            accepted.active_run.assessment["_pending_delegations"],
            [{"role": "worker"}],
        )

    def test_disable_waits_for_observed_agents_before_archiving(self):
        original = running_state(delegations=[delegation("worker-1")])

        state, actions = reduce(original, event("disable"))

        self.assertFalse(state.enabled)
        self.assertEqual(state.active_run.status, "stopping")
        self.assertEqual(
            actions,
            (
                Action("stop_delegations", {"active": ["worker-1"]}),
                Action("project_disabled"),
            ),
        )

        stopped, stop_actions = reduce(
            state,
            event("delegation_updated", identity="worker-1", state="completed"),
        )
        self.assertIsNone(stopped.active_run)
        self.assertEqual(stopped.recent_runs[-1].status, "disabled")
        self.assertEqual(stop_actions, (Action("archive_run", {"run_id": "run-1"}),))

    def test_force_stop_archives_without_disabling_the_project(self):
        original = running_state(delegations=[delegation("worker-1")])

        state, actions = reduce(original, event("force_stop"))

        self.assertTrue(state.enabled)
        self.assertIsNone(state.active_run)
        self.assertEqual(state.recent_runs[-1].status, "force_stopped")
        self.assertEqual(
            actions,
            (
                Action("stop_delegations", {"active": ["worker-1"]}),
                Action("archive_run", {"run_id": "run-1"}),
                Action("permit_stop"),
            ),
        )

    def test_unknown_event_does_not_change_state(self):
        original = running_state()

        state, actions = reduce(original, event("future_event"))

        self.assertEqual(state, original)
        self.assertEqual(actions, ())


if __name__ == "__main__":
    unittest.main()
