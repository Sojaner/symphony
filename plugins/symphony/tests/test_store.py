import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from plugins.symphony.symphony.model import (
    Action,
    CapabilitySnapshot,
    Delegation,
    Event,
    ProjectState,
    RunState,
)
from plugins.symphony.symphony.store import StateStore, project_key


class StateStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.data = self.root / "data"
        self.project = self.root / "project"
        self.project.mkdir()
        self.store = StateStore(self.data)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def state_path(self) -> Path:
        return self.data / f"{project_key(self.project)}.json"

    def test_round_trips_the_complete_domain_model_as_json(self):
        event = Event("event-1", "task_received", "2026-09-17T10:00:00+00:00", {"task": "ship"})
        action = Action("spawn_lead", {"tier": "capable"})
        delegation = Delegation(
            identity="agent-1",
            role="lead",
            objective="Ship the task",
            state="working",
            requested_tier="capable",
            requested_effort="high",
            updated_at="2026-09-17T10:01:00+00:00",
            tokens=120,
            duration_seconds=2.5,
        )
        run = RunState(
            run_id="run-1",
            task="Ship the task",
            status="active",
            owner_generation=2,
            lead_identity="agent-1",
            assessment={"size": "medium", "complexity": "mixed"},
            delegations=(delegation,),
            events=(event,),
            pending_actions=(action,),
            started_at="2026-09-17T10:00:00+00:00",
            updated_at="2026-09-17T10:01:00+00:00",
        )
        capability = CapabilitySnapshot(
            provider="codex",
            available_models=("gpt-5",),
            supported_efforts={"gpt-5": ("medium", "high")},
            tiers={"capable": "gpt-5"},
            source="live",
            provider_version="1.2.3",
            refreshed_at="2026-09-17T09:00:00+00:00",
        )
        state = ProjectState(
            enabled=True,
            configuration={"default_provider": "codex"},
            activation={"codex": {"state": "guarded", "session_id": "session-1"}},
            capabilities=(capability,),
            active_run=run,
            recent_runs=(run,),
            event_history=(event,),
        )

        self.store.save(self.project, state)

        self.assertEqual(self.store.load(self.project), state)
        self.assertEqual(json.loads(self.state_path().read_text())["schema_version"], 1)

    def test_project_key_uses_the_canonical_project_path(self):
        alias = self.project / ".." / self.project.name
        other = self.root / "other"
        other.mkdir()

        self.assertEqual(project_key(alias), project_key(self.project.resolve()))
        self.assertNotEqual(project_key(other), project_key(self.project))

    def test_save_atomically_replaces_a_sibling_temporary_file(self):
        self.store.save(self.project, ProjectState(enabled=False))
        real_replace = os.replace
        replacements = []

        def recording_replace(source, destination):
            replacements.append((Path(source), Path(destination)))
            real_replace(source, destination)

        with patch("plugins.symphony.symphony.store.os.replace", side_effect=recording_replace):
            self.store.save(self.project, ProjectState(enabled=True))

        self.assertEqual(len(replacements), 1)
        source, destination = replacements[0]
        self.assertEqual(source.parent, destination.parent)
        self.assertEqual(destination, self.state_path())
        self.assertFalse(source.exists())
        self.assertTrue(self.store.load(self.project).enabled)

    def test_save_retains_only_the_most_recent_twenty_runs(self):
        runs = tuple(RunState(run_id=f"run-{number}", task="task") for number in range(25))

        self.store.save(self.project, ProjectState(recent_runs=runs))

        self.assertEqual(
            [run.run_id for run in self.store.load(self.project).recent_runs],
            [f"run-{number}" for number in range(5, 25)],
        )

    def test_corrupt_state_is_preserved_and_rebuilt(self):
        path = self.state_path()
        path.parent.mkdir(parents=True)
        path.write_text("not-json", encoding="utf-8")

        state = self.store.load(self.project)

        self.assertTrue(state.needs_reassessment)
        archives = list(path.parent.glob(path.name + ".corrupt-*"))
        self.assertEqual(len(archives), 1)
        self.assertEqual(archives[0].read_text(encoding="utf-8"), "not-json")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["schema_version"], 1)

    def test_pre_1_0_migration_imports_only_enablement_and_configuration(self):
        path = self.state_path()
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {
                    "schema_version": 0,
                    "enabled": True,
                    "configuration": {"default_provider": "claude"},
                    "activation": {"codex": {"state": "guarded"}},
                    "active_run": {"run_id": "unsafe"},
                    "recent_runs": [{"run_id": "unsafe"}],
                    "event_history": [{"id": "unsafe"}],
                }
            ),
            encoding="utf-8",
        )

        state = self.store.load(self.project)

        self.assertTrue(state.enabled)
        self.assertEqual(state.configuration, {"default_provider": "claude"})
        self.assertEqual(state.activation, {})
        self.assertIsNone(state.active_run)
        self.assertEqual(state.recent_runs, ())
        self.assertEqual(state.event_history, ())
        self.assertTrue(state.needs_reassessment)
        self.assertEqual(len(list(path.parent.glob(path.name + ".pre-1.0-*"))), 1)


if __name__ == "__main__":
    unittest.main()
