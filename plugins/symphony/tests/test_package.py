import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
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
    def test_both_provider_manifests_declare_the_same_released_version(self):
        from plugins.symphony.symphony import PLUGIN_VERSION

        codex = load_json(".codex-plugin/plugin.json")["version"]
        claude = load_json(".claude-plugin/plugin.json")["version"]
        self.assertEqual(codex, claude, "provider manifests must not drift apart")
        self.assertEqual(codex, PLUGIN_VERSION, "the package must report what it ships as")
        self.assertRegex(codex, r"^\d+\.\d+\.\d+$")

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

    def test_hook_starts_when_datetime_utc_is_unavailable(self):
        """A generic python3 hook must work on Python 3.10, before UTC existed."""
        from plugins.symphony.symphony.store import StateStore

        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            project.mkdir()
            state_root = Path(directory) / "state"
            script = PLUGIN / "scripts" / "symphony_hook.py"
            result = subprocess.run(
                [sys.executable, "-c", (
                    "import datetime, runpy, sys; "
                    "datetime.__dict__.pop('UTC', None); "
                    "runpy.run_path(sys.argv[1], run_name='__main__')"
                ), str(script)],
                input=json.dumps({
                    "hook_event_name": "SessionStart",
                    "session_id": "python310-smoke",
                    "cwd": str(project),
                }),
                capture_output=True,
                text=True,
                env={
                    "PATH": os.environ.get("PATH", ""),
                    "SYMPHONY_STATE_DIR": str(state_root),
                    "SYMPHONY_PROVIDER": "codex",
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            activation = StateStore(state_root).load(project).activation["codex"]
            self.assertEqual(activation["session_id"], "python310-smoke")
            self.assertEqual(activation["state"], "guarded")

    def test_codex_windows_hooks_use_quote_free_module_command(self):
        for handler in handlers("hooks/codex.json"):
            command = handler["commandWindows"]
            self.assertIn("set SYMPHONY_PROVIDER=codex&&", command)
            self.assertIn("set PYTHONPATH=%PLUGIN_ROOT%\\scripts&& python -m symphony_hook", command)
            self.assertNotIn('"', command)
            self.assertNotIn("${PLUGIN_ROOT}", command)

    @unittest.skipUnless(os.name == "nt", "runs the Windows shell command")
    def test_codex_windows_hooks_run_without_a_working_py_launcher(self):
        from plugins.symphony.symphony import HOOK_SCHEMA_VERSION
        from plugins.symphony.symphony.store import StateStore

        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            root = home / "Symphony Plugin With Spaces"
            shutil.copytree(PLUGIN, root)
            project = home / "Current Project"
            project.mkdir()
            (project / "symphony_hook.py").write_text("raise SystemExit(79)\n")
            state_root = home / "state"
            launcher_dir = home / "broken launcher"
            launcher_dir.mkdir()
            (launcher_dir / "py.exe").write_bytes(b"not a Windows executable")
            env = os.environ.copy()
            env.update({
                "PATH": os.pathsep.join((str(launcher_dir), str(Path(sys.executable).parent),
                                          str(Path(os.environ["SystemRoot"]) / "System32"))),
                "PLUGIN_ROOT": str(root),
                "SYMPHONY_STATE_DIR": str(state_root),
                "PYTHONDONTWRITEBYTECODE": "1",
            })
            python = subprocess.run(["cmd", "/d", "/s", "/c", "python --version"],
                                    capture_output=True, text=True, env=env, check=False)
            self.assertEqual(python.returncode, 0, python.stderr)
            broken_py = subprocess.run(["cmd", "/d", "/s", "/c", "py -3 --version"],
                                       capture_output=True, text=True, env=env, check=False)
            self.assertNotEqual(broken_py.returncode, 0)
            config = load_json("hooks/codex.json")
            # Codex wraps commandWindows in quotes when calling cmd.exe /C.
            def run_hook(command: str, payload: str, environment: dict[str, str]):
                return subprocess.run(f'cmd.exe /C "{command}"', input=payload,
                                      capture_output=True, text=True, env=environment,
                                      cwd=project, check=False)

            payload = json.dumps({"hook_event_name": "SessionStart", "session_id": "windows-session",
                                  "cwd": str(project)})
            for event, groups in config["hooks"].items():
                command = next(handler["commandWindows"] for group in groups
                               for handler in group["hooks"])
                event_payload = json.dumps({"hook_event_name": event, "session_id": "windows-session",
                                            "cwd": str(project)})
                result = run_hook(command, event_payload, env)
                self.assertEqual(result.returncode, 0, f"{event}: {result.stderr}")
            activation = StateStore(state_root).load(project).activation["codex"]
            self.assertEqual(activation["session_id"], "windows-session")
            self.assertEqual(activation["state"], "guarded")
            self.assertEqual(activation["plugin_version"], load_json(".codex-plugin/plugin.json")["version"])
            self.assertTrue(os.path.samefile(activation["plugin_root"], root))
            self.assertEqual(activation["hook_schema_version"], HOOK_SCHEMA_VERSION)
            self.assertTrue(activation["observed_at"])

            (launcher_dir / "python.exe").write_bytes(b"not a Windows executable")
            missing_state = home / "missing-python-state"
            env["SYMPHONY_STATE_DIR"] = str(missing_state)
            start = config["hooks"]["SessionStart"][0]["hooks"][0]["commandWindows"]
            failed = run_hook(start, payload, env)
            self.assertNotEqual(failed.returncode, 0)
            self.assertFalse(missing_state.exists())

    def test_hook_manifests_contain_only_supported_events(self):
        self.assertEqual(
            set(load_json("hooks/codex.json")["hooks"]),
            {"SessionStart", "UserPromptSubmit", "SubagentStart", "SubagentStop", "Stop", "Interrupt"},
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
        from plugins.symphony.symphony.runtime import CONTROLS

        self.assertEqual({path.stem for path in (PLUGIN / "commands").glob("*.md")}, CONTROLS)

    def test_every_control_is_documented_where_a_user_would_look(self):
        """A control nobody can discover may as well not exist.

        Adding one means touching four lists, and nothing checked that they
        agreed, so the help quietly fell behind the code.
        """
        from plugins.symphony.symphony.runtime import CONTROLS, _help

        readme = (PLUGIN.parent.parent / "README.md").read_text(encoding="utf-8")
        claude_help = (PLUGIN / "commands/help.md").read_text(encoding="utf-8")
        # assertTrue, not assertIn: a failed assertIn prints the whole README.
        for name in sorted(CONTROLS):
            for missing, where in (
                (f"/symphony:{name}" not in claude_help, "the Claude help command"),
                (f"/symphony:{name}" not in readme, "the README's Claude list"),
                (f"$symphony:symphony {name}" not in readme, "the README's Codex list"),
                (name not in _help("claude"), "the Claude help line"),
                (name not in _help("codex"), "the Codex help line"),
            ):
                self.assertFalse(missing, f"control {name!r} is missing from {where}")

    def test_role_contracts_classify_each_actionable_packet(self):
        roles = (PLUGIN / "skills/symphony/references/role-contracts.md").read_text(encoding="utf-8")
        self.assertIn("size", roles)
        self.assertIn("complexity", roles)
        self.assertIn("Each consultant decision is classified separately", roles)
        self.assertIn('fork_turns="none"', roles)
        self.assertIn("SYMPHONY_ASSESSMENT:", roles)
        self.assertIn("one `SYMPHONY_DECISION` line per actionable decision", roles)

    def test_claude_role_agents_pin_model_and_effort_in_their_names(self):
        agents = list((PLUGIN / "agents").glob("symphony-*.md"))
        self.assertGreaterEqual(len(agents), 4)
        for path in agents:
            text = path.read_text(encoding="utf-8")
            model = re.search(r"^model: (\S+)$", text, re.MULTILINE)
            effort = re.search(r"^effort: (\S+)$", text, re.MULTILINE)
            self.assertIsNotNone(model, path.name)
            self.assertIsNotNone(effort, path.name)
            self.assertIn(f"-{model.group(1)}-{effort.group(1)}", path.stem)
        assessor = next((PLUGIN / "agents").glob("symphony-assessor-*-high.md")).read_text(encoding="utf-8")
        self.assertIn("SYMPHONY_ASSESSMENT:", assessor)
        for path in (PLUGIN / "agents").glob("symphony-consultant-*.md"):
            self.assertIn("SYMPHONY_DECISION:", path.read_text(encoding="utf-8"))

    def test_capability_routing_assigns_supporting_workflows(self):
        routing = (PLUGIN / "skills/symphony/references/capability-routing.md").read_text(encoding="utf-8")
        for capability in ("Ponytail", "Context7", "Compound Engineering", "Superpowers", "Codebase Memory", "Matt Pocock"):
            self.assertIn(capability, routing)
        for status in ("absent", "disabled", "failed", "incompatible", "incomplete"):
            self.assertIn(status, routing)
        self.assertIn("native practice", routing)
        readme = (PLUGIN.parent.parent / "README.md").read_text(encoding="utf-8")
        self.assertTrue("does not persist cross-session" in readme, "README overstates notice deduplication")

    def test_generated_agents_keep_phase_practice_and_evidence_policy(self):
        roles = {
            "assessor": ("applicable phase practices", "native fallbacks", "evidence needed"),
            "lead": ("ce-plan", "ce-work", "ce-code-review", "Verify the integrated"),
            "worker": ("Codebase Memory", "Context7", "TDD", "verify your result"),
            "consultant": ("ce-code-review", "code-review", "review independently"),
        }
        for role, phrases in roles.items():
            agents = list((PLUGIN / "agents").glob(f"symphony-{role}-*.md"))
            self.assertTrue(agents, role)
            for path in agents:
                body = path.read_text(encoding="utf-8")
                for phrase in (*phrases, "advertised and callable", "native fallback", "artifact or fresh command/result", "incomplete"):
                    self.assertIn(phrase, body, path.name)
                self.assertNotIn("1% chance", body, path.name)

        root = (PLUGIN / "skills/symphony/SKILL.md").read_text(encoding="utf-8")
        for phrase in ("brainstorming", "native practice", "acceptance evidence", "fresh verification"):
            self.assertIn(phrase, root)
        result = subprocess.run(
            [sys.executable, str(PLUGIN / "scripts/generate_agents.py"), "--check"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
