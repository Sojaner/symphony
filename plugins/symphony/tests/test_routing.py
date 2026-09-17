import unittest
from datetime import UTC, datetime, timedelta

from plugins.symphony.symphony.model import CapabilitySnapshot
from plugins.symphony.symphony.routing import (
    Assessment,
    fallback_snapshot,
    resolve_tier,
    route_for,
)


class RoutingTests(unittest.TestCase):
    def test_nine_cell_matrix(self):
        expected = {
            ("small", "simple"): ("capable", "medium", "direct", "none"),
            ("small", "mixed"): ("capable", "high", "direct", "optional"),
            ("small", "complex"): ("strongest", "high", "direct", "independent-check"),
            ("medium", "simple"): ("balanced", "medium", "mixed", "none"),
            ("medium", "mixed"): ("balanced", "high", "mixed", "optional"),
            ("medium", "complex"): ("capable", "high", "mixed", "reserved"),
            ("large", "simple"): ("economy", "low", "delegated", "none"),
            ("large", "mixed"): ("economy", "medium", "delegated", "reserved"),
            ("large", "complex"): ("economy", "medium", "delegated", "strongest"),
        }
        actual = {}
        for axes in expected:
            route = route_for(Assessment(*axes))
            actual[axes] = (route.lead_tier, route.lead_effort, route.execution, route.consultation)
        self.assertEqual(actual, expected)

    def test_high_risk_elevates_effort_without_mutating_axes(self):
        assessment = Assessment("large", "simple", risk="high")
        route = route_for(assessment)
        self.assertEqual((assessment.size, assessment.complexity), ("large", "simple"))
        self.assertEqual(route.lead_effort, "medium")
        self.assertTrue(route.independent_review)

    def test_resolver_chooses_lowest_available_model_that_satisfies_tier(self):
        snapshot = CapabilitySnapshot(
            provider="codex",
            available_models=("cheap", "solid", "best"),
            supported_efforts={
                "cheap": ("low", "medium"),
                "solid": ("low", "medium"),
                "best": ("medium", "high"),
            },
            tiers={"economy": "cheap", "capable": "solid", "strongest": "best"},
            source="live",
            provider_version=None,
            refreshed_at="2026-09-17T00:00:00+00:00",
        )
        resolved = resolve_tier(route_for(Assessment("small", "simple")), snapshot)
        self.assertEqual((resolved["lead_model"], resolved["lead_effort"]), ("solid", "medium"))

    def test_resolver_falls_back_to_supported_effort(self):
        snapshot = CapabilitySnapshot(
            provider="claude",
            available_models=("one",),
            supported_efforts={"one": ("low", "medium")},
            tiers={"capable": "one"},
            source="live",
            provider_version=None,
            refreshed_at="2026-09-17T00:00:00+00:00",
        )
        resolved = resolve_tier(route_for(Assessment("small", "mixed")), snapshot)
        self.assertEqual(resolved["lead_effort"], "medium")
        self.assertTrue(resolved["degraded"])

    def test_shipped_provider_fallbacks_resolve_capable_leads(self):
        route = route_for(Assessment("small", "simple"))

        codex = resolve_tier(route, fallback_snapshot("codex"))
        claude = resolve_tier(route, fallback_snapshot("claude"))

        self.assertEqual((codex["lead_model"], codex["lead_effort"]), ("gpt-5.6-sol", "medium"))
        self.assertEqual((claude["lead_model"], claude["lead_effort"]), ("opus", "medium"))

    def test_invalid_axes_are_rejected(self):
        with self.assertRaises(ValueError):
            route_for(Assessment("gigantic", "simple"))


if __name__ == "__main__":
    unittest.main()
