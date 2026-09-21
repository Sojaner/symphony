import importlib.util
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


def load():
    path = Path(__file__).resolve().parents[1] / "scripts" / "refresh_profiles.py"
    spec = importlib.util.spec_from_file_location("refresh_profiles", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def roster(*slugs, efforts=("low", "medium", "high")):
    return [
        {
            "slug": slug,
            "visibility": "list",
            "supported_reasoning_levels": [{"effort": effort} for effort in efforts],
        }
        for slug in slugs
    ]


CURATED = [
    {
        "id": "full",
        "tiers": {
            "economy": "gpt-5.6-luna",
            "balanced": "gpt-5.6-terra",
            "capable": "gpt-5.6-sol",
            "strongest": "gpt-6-astra",
        },
        "efforts": {},
        "requires_all": ["gpt-6-astra"],
    },
    {
        "id": "base",
        "tiers": dict.fromkeys(
            ("economy", "balanced", "capable", "strongest"), "gpt-5.5"
        ),
        "efforts": {},
        "requires_all": [],
    },
]


class SubstitutionTests(unittest.TestCase):
    """Tier assignment is a cost judgement; only availability is a provider fact."""

    def setUp(self):
        self.refresh = load()

    def tiers(self, available, profile=0):
        return self.refresh.codex_profiles(roster(*available), CURATED)[profile]["tiers"]

    def test_a_complete_roster_leaves_every_assignment_alone(self):
        available = ("gpt-5.5", "gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol", "gpt-6-astra")
        self.assertEqual(self.tiers(available), CURATED[0]["tiers"])

    def test_a_retired_model_is_replaced_by_the_next_one_down(self):
        available = ("gpt-5.5", "gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol")
        # gpt-6-astra is gone, so strongest drops to the best still offered.
        self.assertEqual(self.tiers(available)["strongest"], "gpt-5.6-sol")
        self.assertEqual(self.tiers(available)["economy"], "gpt-5.6-luna")

    def test_substitution_never_silently_promotes_a_tier(self):
        # gpt-5.6-luna is gone. economy must not jump up to terra while a
        # cheaper option still exists.
        available = ("gpt-5.5", "gpt-5.6-terra", "gpt-5.6-sol", "gpt-6-astra")
        self.assertEqual(self.tiers(available)["economy"], "gpt-5.5")

    def test_the_cheapest_tier_falls_upward_only_when_nothing_is_cheaper(self):
        available = ("gpt-5.6-terra", "gpt-5.6-sol", "gpt-6-astra")
        self.assertEqual(self.tiers(available)["economy"], "gpt-5.6-terra")

    def test_efforts_come_from_the_roster_not_from_a_guess(self):
        available = ("gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol", "gpt-6-astra")
        profiles = self.refresh.codex_profiles(
            roster(*available, efforts=("low", "medium", "high", "xhigh", "max")), CURATED
        )
        self.assertEqual(
            profiles[0]["efforts"]["gpt-6-astra"], ["low", "medium", "high", "xhigh", "max"]
        )

    def test_preview_or_unsupported_efforts_are_not_shipped(self):
        profiles = self.refresh.codex_profiles(
            roster("gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol", "gpt-6-astra", efforts=("minimal", "ultra", "high")),
            CURATED,
        )
        self.assertEqual(profiles[0]["efforts"]["gpt-5.6-terra"], ["high"])

    def test_an_unrecognisable_roster_stops_rather_than_guessing(self):
        with self.assertRaises(SystemExit):
            self.refresh.codex_profiles(roster("some-unknown-model"), CURATED)

    def test_a_hidden_model_is_not_available(self):
        entries = roster("gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol")
        entries.append({"slug": "gpt-6-astra", "visibility": "hide"})
        with TemporaryDirectory() as directory:
            home = Path(directory)
            (home / "models_cache.json").write_text(json.dumps({"models": entries}))
            visible = {entry["slug"] for entry in self.refresh.codex_roster(home)}
        self.assertNotIn("gpt-6-astra", visible)

    def test_a_missing_roster_stops_rather_than_shipping_a_guess(self):
        with TemporaryDirectory() as directory:
            with self.assertRaises(SystemExit):
                self.refresh.codex_roster(Path(directory) / "absent")


class ShippedProfileTests(unittest.TestCase):
    def test_the_shipped_assignments_are_a_fixed_point_of_the_derivation(self):
        """A refresh against a complete roster must change nothing."""
        refresh = load()
        document = json.loads(refresh.PROFILES.read_text())
        current = document["providers"]["codex"]["profiles"]
        full = ("gpt-5.5", "gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol", "gpt-6-astra")
        derived = refresh.codex_profiles(roster(*full), current)
        self.assertEqual(
            [profile["tiers"] for profile in derived],
            [profile["tiers"] for profile in current],
        )
        self.assertEqual(
            [profile.get("requires_all") for profile in derived],
            [profile.get("requires_all") for profile in current],
        )


if __name__ == "__main__":
    unittest.main()
