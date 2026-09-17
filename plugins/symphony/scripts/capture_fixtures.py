#!/usr/bin/env python3
"""Record real hook payloads from an installed session.

The 1.0.0 review found the suite green against payload shapes neither host
sends. Invented fixtures cannot catch that; captured ones can. This installs a
plugin whose only hook writes its stdin to disk, drives one real session, and
saves what the host actually sent.

    capture_fixtures.py --provider claude
    capture_fixtures.py --provider codex

Captures are sanitised before they are written: absolute paths, session and
turn identifiers, and anything user-identifying are replaced with placeholders,
because these files are committed.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "captured"

CLAUDE_EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop")
CODEX_EVENTS = ("SessionStart", "UserPromptSubmit", "SubagentStart", "SubagentStop", "Stop")

HOOK = """#!/usr/bin/env python3
import json, os, pathlib, sys
raw = sys.stdin.read()
out = pathlib.Path(os.environ["SYMPHONY_CAPTURE_DIR"])
out.mkdir(parents=True, exist_ok=True)
try:
    payload = json.loads(raw)
except ValueError:
    payload = {"unparsed": raw}
name = str(payload.get("hook_event_name") or "unknown")
(out / f"{name}.json").write_text(json.dumps(payload, indent=2, sort_keys=True))
"""


def build_plugin(root: Path, provider: str) -> Path:
    plugin = root / "plugins" / "capture"
    (plugin / "hooks").mkdir(parents=True)
    (plugin / "scripts").mkdir(parents=True)
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".codex-plugin").mkdir(parents=True)

    manifest = {"name": "capture", "version": "0.0.1", "description": "Record hook payloads."}
    (plugin / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest))
    (plugin / ".codex-plugin" / "plugin.json").write_text(
        json.dumps({**manifest, "hooks": "./hooks/codex.json"})
    )
    (plugin / "scripts" / "capture.py").write_text(HOOK)
    (plugin / "scripts" / "capture.py").chmod(0o755)

    placeholder = "${CLAUDE_PLUGIN_ROOT}" if provider == "claude" else "${PLUGIN_ROOT}"
    command = f'python3 "{placeholder}/scripts/capture.py"'
    events = CLAUDE_EVENTS if provider == "claude" else CODEX_EVENTS
    config = {
        "hooks": {
            event: [{"hooks": [{"type": "command", "command": command, "timeout": 10}]}]
            for event in events
        }
    }
    name = "hooks.json" if provider == "claude" else "codex.json"
    (plugin / "hooks" / name).write_text(json.dumps(config, indent=2))

    (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (root / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps(
            {
                "name": "capture",
                "owner": {"name": "symphony"},
                "plugins": [{"name": "capture", "source": "./plugins/capture"}],
            },
            indent=2,
        )
    )
    return plugin


def sanitise(payload: dict, root: Path) -> dict:
    """Replace anything machine- or user-specific before this is committed."""
    text = json.dumps(payload)
    text = text.replace(str(root), "/captured/workspace")
    text = text.replace(str(Path.home()), "/captured/home")
    substitutions = (
        (r'"session_id": "[^"]*"', '"session_id": "captured-session"'),
        (r'"turn_id": "[^"]*"', '"turn_id": "captured-turn"'),
        (r'"prompt_id": "[^"]*"', '"prompt_id": "captured-prompt"'),
        (r'"agent_id": "[^"]*"', '"agent_id": "captured-agent"'),
        (r"[\w.+-]+@[\w-]+\.[\w.]+", "captured@example.invalid"),
        (r"(?<!/captured)/home/[^/\"]+", "/captured/home"),
        (r"/Users/[^/\"]+", "/captured/home"),
        # Transcript paths embed a run identifier and a mangled temp directory.
        (r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "captured-uuid"),
        (r"/(?:tmp|var/folders)/[^\"]*symphony-capture[^\"/]*", "/captured/workspace"),
        (r"-tmp-symphony-capture[^\"/]*", "-captured-workspace"),
    )
    for pattern, replacement in substitutions:
        text = re.sub(pattern, replacement, text)
    return json.loads(text)


def capture(provider: str) -> int:
    workspace = Path(tempfile.mkdtemp(prefix=f"symphony-capture-{provider}-"))
    captured = workspace / "captured"
    project = workspace / "project"
    project.mkdir()
    (project / "README.md").write_text("capture workspace\n")
    build_plugin(workspace, provider)

    env = os.environ.copy()
    env["SYMPHONY_CAPTURE_DIR"] = str(captured)

    try:
        if provider == "claude":
            # The signed-in config supplies auth; project scope keeps the hook
            # out of every other session on this machine, including this one.
            run(["claude", "plugin", "marketplace", "add", str(workspace)], env)
            run(
                ["claude", "plugin", "install", "-y", "--scope", "project", "capture@capture"],
                env,
                cwd=project,
            )
            run(
                [
                    "claude", "--print", "--model", "haiku", "--output-format", "text",
                    "reply with the single word ok",
                ],
                env,
                cwd=project,
            )
        else:
            env["CODEX_HOME"] = str(workspace / "codex-home")
            Path(env["CODEX_HOME"]).mkdir(parents=True)
            run(["codex", "plugin", "marketplace", "add", str(workspace)], env)
            run(["codex", "plugin", "add", "capture@capture"], env)
            run(
                [
                    "codex", "exec", "--dangerously-bypass-hook-trust",
                    "--skip-git-repo-check", "-C", str(project),
                    "reply with the single word ok",
                ],
                env,
            )
    finally:
        if provider == "claude":
            run(["claude", "plugin", "marketplace", "remove", "capture"], env)

    files = sorted(captured.glob("*.json")) if captured.exists() else []
    if not files:
        print(f"::error::{provider} session produced no hook payloads")
        return 1

    destination = FIXTURES / provider
    destination.mkdir(parents=True, exist_ok=True)
    for path in files:
        payload = sanitise(json.loads(path.read_text()), workspace)
        (destination / path.name).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        print(f"captured {provider}/{path.name}: {sorted(payload)}")
    shutil.rmtree(workspace, ignore_errors=True)
    return 0


def run(argv: list[str], env: dict, cwd: Path | None = None) -> None:
    completed = subprocess.run(
        argv, env=env, cwd=cwd, capture_output=True, text=True, timeout=300
    )
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip().splitlines()
        print(f"::warning::{argv[0]} {argv[1]} exited {completed.returncode}: {detail[-1:]}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("claude", "codex"), required=True)
    args = parser.parse_args()
    return capture(args.provider)


if __name__ == "__main__":
    sys.exit(main())
