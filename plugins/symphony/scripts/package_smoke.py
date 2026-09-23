#!/usr/bin/env python3
"""Materialize a Symphony candidate and exercise its hook lifecycle."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


PROVIDERS = ("codex", "claude")
SCENARIOS = ("activation", "managed-run", "unmarked-spawn", "interrupt-resume", "upgrade")


class SmokeFailure(RuntimeError):
    pass


def _plugin_source(candidate: Path) -> Path:
    nested = candidate / "plugins" / "symphony"
    source = nested if nested.is_dir() else candidate
    if not (source / ".codex-plugin" / "plugin.json").is_file():
        raise SmokeFailure(f"candidate has no Symphony plugin: {candidate}")
    return source


def _manifest(root: Path, provider: str) -> dict[str, Any]:
    path = root / f".{provider}-plugin" / "plugin.json"
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise SmokeFailure(f"invalid {provider} manifest: {path}") from exc


def _materialize(source: Path, home: Path, version: str) -> Path:
    destination = home / "plugins" / "cache" / "symphony" / "symphony" / version
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination)
    return destination


def _set_materialized_version(root: Path, version: str) -> None:
    for provider in PROVIDERS:
        path = root / f".{provider}-plugin" / "plugin.json"
        manifest = json.loads(path.read_text())
        manifest["version"] = version
        path.write_text(json.dumps(manifest, indent=2) + "\n")
    package = root / "symphony" / "__init__.py"
    if package.is_file():
        package.write_text(
            re.sub(
                r'(?m)^(PLUGIN_VERSION\s*=\s*)["\'][^"\']+["\']',
                rf'\1"{version}"',
                package.read_text(),
            )
        )


def _hook_config(root: Path, provider: str) -> dict[str, Any]:
    if provider == "codex":
        hook_ref = _manifest(root, provider).get("hooks", "./hooks/codex.json")
        path = root / str(hook_ref).removeprefix("./")
    else:
        path = root / "hooks" / "hooks.json"
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise SmokeFailure(f"invalid {provider} hook config: {path}") from exc


def _event_command(config: dict[str, Any], event: str) -> str:
    try:
        groups = config["hooks"][event]
        for group in groups:
            for hook in group.get("hooks", []):
                if hook.get("type") == "command":
                    return hook["command"]
    except (KeyError, TypeError):
        pass
    raise SmokeFailure(f"no command hook for {event}")


def _command_argv(command: str, root: Path, provider: str) -> list[str]:
    placeholder = "${PLUGIN_ROOT}" if provider == "codex" else "${CLAUDE_PLUGIN_ROOT}"
    if placeholder not in command:
        raise SmokeFailure(f"hook command must use {placeholder} plugin-root placeholder")
    argv = shlex.split(command.replace(placeholder, str(root)))
    scripts = [Path(value) for value in argv[1:] if value.endswith(".py")]
    if not scripts or not scripts[0].is_file():
        target = scripts[0] if scripts else root / "<unknown>"
        raise SmokeFailure(f"missing hook executable: {target}")
    try:
        scripts[0].resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise SmokeFailure(f"hook executable escapes materialized plugin: {scripts[0]}") from exc
    return argv


def _validate_package(root: Path, provider: str) -> dict[str, Any]:
    codex_version = str(_manifest(root, "codex").get("version", ""))
    claude_version = str(_manifest(root, "claude").get("version", ""))
    if not codex_version or codex_version != claude_version:
        raise SmokeFailure("Codex and Claude manifest versions must match")
    config = _hook_config(root, provider)
    commands = []
    for event in config.get("hooks", {}):
        command = _event_command(config, event)
        _command_argv(command, root, provider)
        commands.append(command)
    return {"version": codex_version, "config": config, "commands": commands}


ASSESSMENT_MARKER = (
    'SYMPHONY_ASSESSMENT: {"size":"small","complexity":"simple","risk":"normal",'
    '"rationale":"package smoke","topology":"direct"}'
)


def _role_model(root: Path, provider: str, agent_role: str) -> tuple[str, str]:
    """The model and effort this role must run at, per the shipped matrix."""
    profiles = json.loads((root / "profiles.json").read_text(encoding="utf-8"))["providers"][provider]["profiles"]
    profile = profiles[0]
    if agent_role == "assessor":
        return profile["tiers"]["strongest"], "high"
    choice = profile["matrix"]["small/simple"]
    return choice["model"], choice["effort"]


def _payload(
    root: Path,
    provider: str,
    event: str,
    project: Path,
    session: str,
    agent_role: str = "lead",
    stop_hook_active: bool = False,
    source: str = "startup",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "session_id": session,
        "cwd": str(project),
        "hook_event_name": event,
        "permission_mode": "default",
    }
    if event == "SessionStart":
        # A resumed session is the host telling us the previous process ended.
        # Without it, a fresh session is indistinguishable from a second
        # terminal and must not seize a run whose owner is still reporting.
        payload["source"] = source
    if provider == "codex":
        payload.update({"turn_id": f"turn-{session}", "model": "fake-codex"})
    model, effort = _role_model(root, provider, agent_role)
    if event == "UserPromptSubmit":
        payload["prompt"] = (
            "$symphony:symphony exercise the package lifecycle"
            if provider == "codex"
            else "SYMPHONY_CONTROL: start\nARGUMENTS: exercise the package lifecycle"
        )
    elif event in ("PreToolUse", "PostToolUse"):
        # Claude reports a spawn before launch; Codex registers no such hook.
        payload["tool_name"] = "Agent"
        payload["tool_input"] = {
            "subagent_type": f"symphony-{agent_role}-{model}-{effort}",
            "prompt": f"SYMPHONY_ROLE: {agent_role}\nexercise the package lifecycle",
        }
    elif event in ("SubagentStart", "SubagentStop"):
        payload["agent_id"] = f"fake-{agent_role}"
        if provider == "codex":
            # Codex exposes the child's own model and effort on the event.
            payload.update(
                {
                    "agent_type": f"symphony_{agent_role}_{model.replace('-', '_').replace('.', '_')}_{effort}",
                    "model": model,
                    "model_reasoning_effort": effort,
                }
            )
        else:
            payload["agent_type"] = f"symphony-{agent_role}-{model}-{effort}"
        if event == "SubagentStop":
            payload["status"] = "completed"
            payload["last_assistant_message"] = (
                ASSESSMENT_MARKER if agent_role == "assessor" else "done"
            )
    elif event == "Stop":
        payload.update(
            {"stop_hook_active": stop_hook_active, "last_assistant_message": "done"}
        )
    return payload


def _run_event(
    root: Path,
    provider: str,
    event: str,
    project: Path,
    state_dir: Path,
    session: str,
    agent_role: str = "lead",
    stop_hook_active: bool = False,
    source: str = "startup",
) -> dict[str, Any] | None:
    config = _hook_config(root, provider)
    argv = _command_argv(_event_command(config, event), root, provider)
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(state_dir.parent),
            "SYMPHONY_STATE_DIR": str(state_dir),
            "SYMPHONY_PLUGIN_ROOT": str(root),
            "SYMPHONY_PLUGIN_VERSION": str(_manifest(root, provider)["version"]),
            "SYMPHONY_SMOKE_PROVIDER": provider,
            "PLUGIN_ROOT": str(root),
            "CLAUDE_PLUGIN_ROOT": str(root),
        }
    )
    completed = subprocess.run(
        argv,
        input=json.dumps(
            _payload(root, provider, event, project, session, agent_role, stop_hook_active, source)
        ),
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
        check=False,
    )
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise SmokeFailure(f"{event} hook exited {completed.returncode}: {detail}")
    output = completed.stdout.strip()
    if not output:
        return None
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise SmokeFailure(f"{event} hook emitted non-JSON output") from exc


def _send_raw(
    root: Path, provider: str, event: str, state_dir: Path, payload: dict[str, Any]
) -> dict[str, Any] | None:
    """Drive one hook with a hand-built payload, for cases the builder cannot express."""
    argv = _command_argv(_event_command(_hook_config(root, provider), event), root, provider)
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(state_dir.parent),
            "SYMPHONY_STATE_DIR": str(state_dir),
            "SYMPHONY_PLUGIN_ROOT": str(root),
            "SYMPHONY_PLUGIN_VERSION": str(_manifest(root, provider)["version"]),
            "SYMPHONY_SMOKE_PROVIDER": provider,
            "PLUGIN_ROOT": str(root),
            "CLAUDE_PLUGIN_ROOT": str(root),
        }
    )
    completed = subprocess.run(
        argv, input=json.dumps(payload), capture_output=True, text=True, env=env, timeout=15, check=False
    )
    if completed.returncode:
        raise SmokeFailure(f"{event} hook exited {completed.returncode}: {completed.stderr.strip()}")
    return json.loads(completed.stdout) if completed.stdout.strip() else None


def _state_documents(state_dir: Path) -> list[Any]:
    documents = []
    for path in state_dir.rglob("*.json") if state_dir.exists() else ():
        try:
            documents.append(json.loads(path.read_text()))
        except (OSError, json.JSONDecodeError):
            continue
    if not documents:
        raise SmokeFailure("hook wrote no durable JSON state")
    return documents


def _contains(value: Any, expected: str) -> bool:
    if value == expected:
        return True
    if isinstance(value, dict):
        return any(_contains(child, expected) for child in value.values())
    if isinstance(value, list):
        return any(_contains(child, expected) for child in value)
    return False


def _has_active_run(value: Any) -> bool:
    return isinstance(value, dict) and value.get("active_run") is not None


def _has_guarded_heartbeat(
    value: Any,
    provider: str,
    session: str,
    version: str,
    root: Path,
) -> bool:
    if not isinstance(value, dict):
        return False
    activation = value.get("activation", {}).get(provider, {})
    return (
        activation.get("state") == "guarded"
        and activation.get("session_id") == session
        and activation.get("plugin_version") == version
        and activation.get("plugin_root") == str(root)
        and activation.get("hook_schema_version") == 1
        and bool(activation.get("observed_at"))
    )


def _blocks_stop(output: dict[str, Any] | None) -> bool:
    return bool(output and (output.get("decision") == "block" or output.get("continue") is False))


def _next_patch(version: str) -> str:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version)
    if not match:
        raise SmokeFailure(f"upgrade smoke requires a release semver, got {version!r}")
    major, minor, patch = (int(part) for part in match.groups())
    return f"{major}.{minor}.{patch + 1}"


def _exercise(
    provider: str, source: Path, scenario: str, home: Path
) -> dict[str, Any]:
    source_version = str(_manifest(source, provider).get("version", ""))
    root = _materialize(source, home, source_version)
    package = _validate_package(root, provider)
    project = home / "project"
    project.mkdir(parents=True, exist_ok=True)
    state_dir = home / "state"
    events: list[str] = []
    activation = ["needs_review" if provider == "codex" else "pending_reload"]
    result: dict[str, Any] = {
        "ok": True,
        "provider": provider,
        "scenario": scenario,
        "version": package["version"],
        "install_root": str(root),
        "activation": activation,
        "events": events,
    }

    def send(
        event: str,
        session: str = "fake-session",
        agent_role: str = "lead",
        stop_hook_active: bool = False,
        source: str = "startup",
    ) -> dict[str, Any] | None:
        output = _run_event(
            root, provider, event, project, state_dir, session, agent_role,
            stop_hook_active, source,
        )
        events.append(event)
        if event == "UserPromptSubmit":
            documents = _state_documents(state_dir)
            if not any(
                _has_guarded_heartbeat(
                    document,
                    provider,
                    session,
                    str(_manifest(root, provider)["version"]),
                    root,
                )
                for document in documents
            ):
                raise SmokeFailure("UserPromptSubmit did not persist a complete guarded heartbeat")
        return output

    if scenario == "activation":
        send("UserPromptSubmit")
        activation.append("guarded")
    elif scenario == "managed-run":
        send("UserPromptSubmit")
        if any(_has_active_run(document) for document in _state_documents(state_dir)):
            raise SmokeFailure("a prompt must not open a run before the assessor spawns")
        if _blocks_stop(send("Stop")):
            raise SmokeFailure("a prompt that spawned nothing must not block Stop")
        if provider == "claude":
            # Only Claude reports a spawn before launch.
            denied = send("PreToolUse", agent_role="assessor")
            if denied and denied.get("hookSpecificOutput", {}).get("permissionDecision") == "deny":
                raise SmokeFailure(f"a correctly marked assessor spawn was denied: {denied}")
        send("SubagentStart", agent_role="assessor")
        if not any(_has_active_run(document) for document in _state_documents(state_dir)):
            raise SmokeFailure("the assessor spawn did not open a run")
        send("SubagentStop", agent_role="assessor")
        if provider == "claude":
            send("PostToolUse", agent_role="assessor")
        send("SubagentStart")
        if not _blocks_stop(send("Stop")):
            raise SmokeFailure("Stop was not blocked while a tracked child was active")
        if not _blocks_stop(send("Stop")):
            raise SmokeFailure("a second Stop without the retry flag must still block")
        if _blocks_stop(send("Stop", stop_hook_active=True)):
            raise SmokeFailure("a repeated Stop must release the session, never loop")
        if any(_has_active_run(document) for document in _state_documents(state_dir)):
            raise SmokeFailure("the released run was not archived")
        activation.append("guarded")
    elif scenario == "unmarked-spawn":
        if provider != "claude":
            # Codex registers no pre-spawn hook, so there is nothing to deny.
            send("UserPromptSubmit")
            activation.append("guarded")
            return result
        enable = _payload(root, provider, "UserPromptSubmit", project, "fake-session")
        enable["prompt"] = "SYMPHONY_CONTROL: enable"
        _send_raw(root, provider, "UserPromptSubmit", state_dir, enable)
        events.append("UserPromptSubmit")
        unmarked = _payload(root, provider, "PreToolUse", project, "fake-session")
        unmarked["tool_input"] = {"prompt": "do the work"}
        output = _send_raw(root, provider, "PreToolUse", state_dir, unmarked)
        events.append("PreToolUse")
        decision = (output or {}).get("hookSpecificOutput", {})
        if decision.get("permissionDecision") != "deny":
            raise SmokeFailure(f"an unmarked agent spawn was not denied: {output!r}")
        activation.append("guarded")
    elif scenario == "interrupt-resume":
        send("UserPromptSubmit", "before-interrupt")
        send("SubagentStart", "before-interrupt", agent_role="assessor")
        if not any(_has_active_run(document) for document in _state_documents(state_dir)):
            raise SmokeFailure("the assessor spawn did not open a run")
        if provider == "codex":
            send("Interrupt", "before-interrupt")
        send("SessionStart", "resumed-session", source="resume")
        documents = _state_documents(state_dir)
        if not any(_has_active_run(document) for document in documents):
            raise SmokeFailure("resume lost the interrupted active run")
        if not any(_contains(document, "interrupted") for document in documents):
            raise SmokeFailure("resume did not reconcile the delegation the host can no longer run")
        activation.append("guarded")
    elif scenario == "upgrade":
        send("UserPromptSubmit", "old-session")
        documents = _state_documents(state_dir)
        if not any(
            _contains(document, source_version) and _contains(document, str(root))
            for document in documents
        ):
            raise SmokeFailure(f"heartbeat did not record version/root for {source_version}")
        new_version = _next_patch(source_version)
        new_root = _materialize(source, home, new_version)
        _set_materialized_version(new_root, new_version)
        _validate_package(new_root, provider)
        old_root = root
        root = new_root
        send("UserPromptSubmit", "reloaded-session")
        documents = _state_documents(state_dir)
        if not any(
            _contains(document, new_version) and _contains(document, str(new_root))
            for document in documents
        ):
            raise SmokeFailure(f"heartbeat did not record version/root for {new_version}")
        activation.append("guarded")
        result.update(
            {
                "heartbeat_versions": [source_version, new_version],
                "loaded_roots": [str(old_root), str(new_root)],
                "install_root": str(new_root),
            }
        )
    else:  # argparse and run_smoke callers share the same validation.
        raise SmokeFailure(f"unknown scenario: {scenario}")

    _state_documents(state_dir)
    return result


def run_smoke(
    provider: str,
    candidate: Path | str,
    scenario: str,
    home: Path | None = None,
) -> dict[str, Any]:
    """Run one isolated fake-provider smoke and return its JSON artifact."""
    if provider not in PROVIDERS:
        return {"ok": False, "provider": provider, "scenario": scenario, "error": "unknown provider"}
    activation = ["needs_review" if provider == "codex" else "pending_reload"]
    if scenario not in SCENARIOS:
        return {"ok": False, "provider": provider, "scenario": scenario, "error": "unknown scenario"}
    try:
        source = _plugin_source(Path(candidate).resolve())
        if home is not None:
            return _exercise(provider, source, scenario, home.resolve())
        with tempfile.TemporaryDirectory(prefix=f"symphony-{provider}-smoke-") as temporary:
            return _exercise(provider, source, scenario, Path(temporary))
    except (OSError, SmokeFailure, subprocess.SubprocessError) as exc:
        activation.append("faulted")
        return {
            "ok": False,
            "provider": provider,
            "scenario": scenario,
            "activation": activation,
            "error": str(exc),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=PROVIDERS, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--scenario", choices=SCENARIOS, required=True)
    args = parser.parse_args()
    result = run_smoke(args.provider, args.candidate, args.scenario)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
