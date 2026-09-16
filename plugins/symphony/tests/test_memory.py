import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from plugins.symphony.symphony.memory import (
    ALLOWED_CONTEXT_SECTIONS,
    capability_refresh_due,
    context_update_path,
    record_missing_capability_suggestion,
    validated_context_sections,
)
from plugins.symphony.symphony.model import CapabilitySnapshot, MemoryStatus, ProjectState


class MemoryTests(unittest.TestCase):
    def test_document_memory_is_disabled_until_a_healthy_probe_is_recorded(self):
        self.assertEqual(ProjectState().memory, MemoryStatus())
        with TemporaryDirectory() as directory:
            project = Path(directory)
            self.assertIsNone(context_update_path(project))
            self.assertIsNone(context_update_path(project, MemoryStatus()))
            self.assertIsNone(context_update_path(project, MemoryStatus(True, "healthy", None)))
            self.assertEqual(
                context_update_path(
                    project,
                    MemoryStatus(True, "healthy", "2026-09-17T10:00:00+00:00"),
                ),
                project / ".symphony" / "context.md",
            )

    def test_context_updates_accept_only_curated_sections(self):
        sections = {section: f"summary for {section}" for section in ALLOWED_CONTEXT_SECTIONS}
        self.assertEqual(validated_context_sections(sections), sections)
        for forbidden in ("transcript", "raw_tool_output", "routine_progress"):
            with self.subTest(forbidden=forbidden), self.assertRaises(ValueError):
                validated_context_sections({forbidden: "must not persist"})

    def test_context_updates_reject_secret_patterns(self):
        for secret in (
            "password = hunter2",
            "Authorization: Bearer abcdefghijklmnop",
            "-----BEGIN PRIVATE KEY-----",
        ):
            with self.subTest(secret=secret), self.assertRaises(ValueError):
                validated_context_sections({"current_goals": secret})

    def test_capability_refresh_uses_the_twenty_four_hour_window(self):
        snapshot = CapabilitySnapshot(
            provider="codex",
            available_models=(),
            supported_efforts={},
            tiers={},
            source="cache",
            provider_version="1.0.0",
            refreshed_at="2026-09-16T10:00:00+00:00",
        )
        self.assertFalse(capability_refresh_due(snapshot, datetime(2026, 9, 17, 10, tzinfo=UTC)))
        self.assertTrue(capability_refresh_due(snapshot, datetime(2026, 9, 17, 10, 0, 1, tzinfo=UTC)))

    def test_missing_capability_suggestions_are_deduplicated_per_version(self):
        state = ProjectState()
        state, should_suggest = record_missing_capability_suggestion(state, "context7", "1.0.0")
        self.assertTrue(should_suggest)

        unchanged, should_suggest = record_missing_capability_suggestion(state, "context7", "1.0.0")
        self.assertFalse(should_suggest)
        self.assertIs(unchanged, state)

        upgraded, should_suggest = record_missing_capability_suggestion(state, "context7", "1.1.0")
        self.assertTrue(should_suggest)
        self.assertEqual(upgraded.capability_suggestions["context7"], ("1.0.0", "1.1.0"))


if __name__ == "__main__":
    unittest.main()
