import json
import re
import unittest
from pathlib import Path


PLUGIN = Path(__file__).resolve().parents[1]


def load_json(relative: str) -> dict:
    return json.loads((PLUGIN / relative).read_text(encoding="utf-8"))


def handlers(relative: str):
    for groups in load_json(relative)["hooks"].values():
        for group in groups:
            yield from group["hooks"]


class PackageContractTests(unittest.TestCase):
    def test_both_manifests_are_version_1_0_0(self):
        self.assertEqual(load_json(".codex-plugin/plugin.json")["version"], "1.0.0")
        self.assertEqual(load_json(".claude-plugin/plugin.json")["version"], "1.0.0")

    def test_hook_commands_are_root_relative_and_materialized(self):
        for provider, relative, root_name in (
            ("codex", "hooks/codex.json", "PLUGIN_ROOT"),
            ("claude", "hooks/hooks.json", "CLAUDE_PLUGIN_ROOT"),
        ):
            for handler in handlers(relative):
                command = handler["command"]
                self.assertIn(root_name, command, provider)
                self.assertNotIn("/cache/", command, provider)
                match = re.search(r"scripts/symphony_hook\.py", command)
                self.assertIsNotNone(match, provider)
                self.assertTrue((PLUGIN / match.group(0)).is_file(), provider)

    def test_hook_manifests_contain_only_supported_events(self):
        self.assertEqual(
            set(load_json("hooks/codex.json")["hooks"]),
            {"SessionStart", "UserPromptSubmit", "PreToolUse", "SubagentStart", "SubagentStop", "Stop", "Interrupt"},
        )
        self.assertEqual(
            set(load_json("hooks/hooks.json")["hooks"]),
            {"SessionStart", "UserPromptSubmit", "PreToolUse", "SubagentStart", "SubagentStop", "PostToolUse", "Stop"},
        )

    def test_provider_help_uses_only_native_command_syntax(self):
        codex = (PLUGIN / "skills/symphony/SKILL.md").read_text(encoding="utf-8")
        claude = (PLUGIN / "commands/help.md").read_text(encoding="utf-8")
        self.assertIn("`$symphony:symphony ...`", codex)
        self.assertIn("Codex does not support `/symphony:*`", codex)
        self.assertIn("`/symphony:help`", claude)
        self.assertNotIn("$symphony", claude)

    def test_all_control_wrappers_exist(self):
        expected = {"agents", "bypass", "disable", "enable", "help", "reassess", "start", "status", "stop"}
        self.assertEqual({path.stem for path in (PLUGIN / "commands").glob("*.md")}, expected)

    def test_role_contracts_classify_each_actionable_packet(self):
        roles = (PLUGIN / "skills/symphony/references/role-contracts.md").read_text(encoding="utf-8")
        self.assertIn("size", roles)
        self.assertIn("complexity", roles)
        self.assertIn("Each consultant decision is classified separately", roles)

    def test_capability_routing_assigns_supporting_workflows(self):
        routing = (PLUGIN / "skills/symphony/references/capability-routing.md").read_text(encoding="utf-8")
        for capability in ("Ponytail", "Context7", "Compound Engineering", "Superpowers"):
            self.assertIn(capability, routing)


if __name__ == "__main__":
    unittest.main()
