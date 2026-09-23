import importlib.util
import json
import unittest
from unittest.mock import patch
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


class RosterTests(unittest.TestCase):
    def setUp(self):
        self.refresh = load()

    def test_a_hidden_model_is_not_available(self):
        entries = roster("available")
        entries.append({"slug": "hidden", "visibility": "hide"})
        with TemporaryDirectory() as directory:
            home = Path(directory)
            (home / "models_cache.json").write_text(json.dumps({"models": entries}))
            visible = {entry["slug"] for entry in self.refresh.codex_roster(home)}
        self.assertEqual(visible, {"available"})

    def test_a_missing_roster_stops_rather_than_shipping_a_guess(self):
        with TemporaryDirectory() as directory:
            with patch.object(self.refresh, "_app_server_roster", return_value=roster("model")):
                self.assertEqual(
                    self.refresh.codex_roster(Path(directory) / "absent"),
                    roster("model"),
                )

    def test_a_missing_roster_and_failed_live_query_stops_instead_of_guessing(self):
        with TemporaryDirectory() as directory:
            with patch.object(self.refresh, "_app_server_roster", side_effect=RuntimeError("offline")):
                with self.assertRaises(SystemExit):
                    self.refresh.codex_roster(Path(directory) / "absent")


class ShippedProfileTests(unittest.TestCase):
    def test_shipped_matrices_cover_each_cell_and_back_the_tier_summary(self):
        refresh = load()
        document = json.loads(refresh.PROFILES.read_text())
        for provider, block in document["providers"].items():
            for profile in block["profiles"]:
                self.assertEqual(set(profile["matrix"]), set(refresh.CELLS))
                self.assertEqual(
                    profile["tiers"],
                    {tier: profile["matrix"][cell]["model"] for tier, cell in refresh.TIER_CELLS.items()},
                )
                for choice in profile["matrix"].values():
                    self.assertIn(choice["effort"], profile["efforts"][choice["model"]])


class SemanticMatrixTests(unittest.TestCase):
    def setUp(self):
        self.refresh = load()

    def result(self):
        order = ("gpt-5.5", "gpt-6-luna", "gpt-6-sol")
        effort_by_cell = {
            "small": ("medium", "high", "high"),
            "medium": ("low", "medium", "high"),
            "large": ("low", "low", "medium"),
        }
        ranks = {
            "small": (1, 1, 2),
            "medium": (1, 1, 2),
            "large": (0, 0, 1),
        }
        matrix = {}
        for size, models in ranks.items():
            for complexity, model_rank in zip(("simple", "mixed", "complex"), models):
                effort = effort_by_cell[size][("simple", "mixed", "complex").index(complexity)]
                matrix[f"{size}/{complexity}"] = {"model": order[model_rank], "effort": effort}
        fallback = {cell: {"model": "gpt-5.5", "effort": "low"} for cell in matrix}
        return {
            "model_order": list(order),
            "model_efforts": {model: ["low", "medium", "high"] for model in order},
            "profiles": [
                {"id": "full", "matrix": matrix},
                {"id": "base", "matrix": fallback},
            ],
            "rationale": "trade capability for cost by task cell",
        }

    def test_semantic_matrix_accepts_complete_monotonic_provider_data(self):
        entries = roster("gpt-5.5", "gpt-6-luna", "gpt-6-sol", efforts=("low", "medium", "high"))
        result = self.refresh.validate_matrix("codex", self.result(), [{"id": "full"}, {"id": "base"}], entries)
        self.assertEqual(len(result["profiles"][0]["matrix"]), 9)

    def test_semantic_matrix_rejects_a_missing_cell(self):
        result = self.result()
        del result["profiles"][0]["matrix"]["small/simple"]
        with self.assertRaises(SystemExit):
            self.refresh.validate_matrix("codex", result, [{"id": "full"}, {"id": "base"}], roster("gpt-5.5", "gpt-6-luna", "gpt-6-sol"))

    def test_semantic_matrix_rejects_nonmonotonic_capability(self):
        result = self.result()
        result["profiles"][0]["matrix"]["small/complex"]["model"] = "gpt-5.5"
        with self.assertRaises(SystemExit):
            self.refresh.validate_matrix("codex", result, [{"id": "full"}, {"id": "base"}], roster("gpt-5.5", "gpt-6-luna", "gpt-6-sol"))

    def test_semantic_matrix_rejects_incomplete_codex_effort_support(self):
        result = self.result()
        result["model_efforts"]["gpt-5.5"] = ["low"]
        entries = roster("gpt-5.5", "gpt-6-luna", "gpt-6-sol", efforts=("low", "medium", "high"))
        with self.assertRaises(SystemExit):
            self.refresh.validate_matrix("codex", result, [{"id": "full"}, {"id": "base"}], entries)

    def test_semantic_matrix_rejects_self_certified_model_order(self):
        result = self.result()
        result["profiles"][0]["matrix"] = {
            cell: {"model": "gpt-5.5", "effort": "low"} for cell in self.refresh.CELLS
        }
        result["profiles"][1]["matrix"] = {
            cell: {"model": "gpt-6-sol", "effort": "low"} for cell in self.refresh.CELLS
        }
        result["model_order"] = list(reversed(result["model_order"]))
        with self.assertRaises(SystemExit):
            self.refresh.validate_matrix(
                "codex", result, [{"id": "full"}, {"id": "base"}],
                roster("gpt-5.5", "gpt-6-luna", "gpt-6-sol"),
            )

    def test_agent_model_order_is_not_used_as_authoritative_ranking(self):
        result = self.result()
        result["model_order"] = list(reversed(result["model_order"]))
        validated = self.refresh.validate_matrix(
            "codex", result, [{"id": "full"}, {"id": "base"}],
            roster("gpt-5.5", "gpt-6-luna", "gpt-6-sol"),
        )
        self.assertEqual(validated["model_order"], ["gpt-5.5", "gpt-6-luna", "gpt-6-sol"])

    def test_fallback_cannot_promote_beyond_its_shipped_entitlement_floor(self):
        result = self.result()
        result["profiles"][1]["matrix"] = {
            cell: {"model": "gpt-6-sol", "effort": "low"} for cell in self.refresh.CELLS
        }
        result["model_order"] = ["gpt-5.5", "gpt-6-luna", "gpt-6-sol"]
        current = [
            {"id": "full", "matrix": {}},
            {"id": "base", "matrix": {
                cell: {"model": "gpt-5.5", "effort": "low"} for cell in self.refresh.CELLS
            }},
        ]
        with self.assertRaises(SystemExit):
            self.refresh.validate_matrix(
                "codex", result, current, roster("gpt-5.5", "gpt-6-luna", "gpt-6-sol"),
            )

    def test_future_major_codex_model_has_verified_future_rank(self):
        self.assertGreater(self.refresh._model_rank("codex", "gpt-7"), self.refresh._model_rank("codex", "gpt-6-astra"))
        self.assertGreater(self.refresh._model_rank("codex", "gpt-7-fast"), self.refresh._model_rank("codex", "gpt-6-astra"))


if __name__ == "__main__":
    unittest.main()
