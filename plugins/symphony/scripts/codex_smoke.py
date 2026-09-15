#!/usr/bin/env python3
"""Run isolated Codex smoke trials against this plugin candidate."""

import argparse
from collections import namedtuple
import hashlib
import json
import os
from pathlib import Path
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time


PLUGIN_ID = "symphony@symphony"
TrialPaths = namedtuple("TrialPaths", "root codex_home repo plugin_data artifacts")


class ProcessTimeout(RuntimeError):
    def __init__(self, command, timeout, stdout, stderr):
        super().__init__(f"command timed out after {timeout}s: {command[0]}")
        self.stdout = stdout
        self.stderr = stderr


class SmokeSkip(RuntimeError):
    pass


def build_install_commands(codex, marketplace):
    return [
        [codex, "plugin", "marketplace", "add", str(marketplace), "--json"],
        [codex, "plugin", "add", PLUGIN_ID, "--json"],
    ]


def build_exec_command(codex, repo, prompt, *, model, effort):
    return [
        codex, "--ask-for-approval", "never", "--sandbox", "workspace-write",
        "--cd", str(repo), "exec", "--json", "--ephemeral",
        "--model", model, "--config", f'model_reasoning_effort="{effort}"', prompt,
    ]


def run_process(command, *, env, timeout, cwd=None, input_text=None):
    if timeout <= 0:
        raise ProcessTimeout(command, timeout, "", "")
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdin=subprocess.PIPE if input_text is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(input=input_text, timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        stdout, stderr = process.communicate()
        raise ProcessTimeout(command, timeout, stdout, stderr)
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def parse_jsonl(raw):
    events = []
    for line_number, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid JSONL on line {line_number}: {error.msg}") from error
        if not isinstance(event, dict):
            raise ValueError(f"invalid JSONL object on line {line_number}")
        events.append(event)
    return events


def extract_host_usage(events):
    usage = []

    def visit(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "usage" and isinstance(child, dict):
                    usage.append(child)
                else:
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(events)
    return usage


def _agent_message_text(events):
    return [
        item["text"]
        for event in events
        if event.get("type") == "item.completed"
        for item in [event.get("item", {})]
        if item.get("type") == "agent_message" and isinstance(item.get("text"), str)
    ]


def assert_lifecycle(events, state, expected, *, require_completion=False):
    transcript = "\n".join(_agent_message_text(events))
    position = 0
    for fragment in expected:
        found = transcript.find(fragment, position)
        if found < 0:
            if fragment in transcript:
                raise AssertionError(f"lifecycle fragment out of order: {fragment}")
            raise AssertionError(f"missing lifecycle fragment: {fragment}")
        position = found + len(fragment)
    if require_completion and state.get("active_run", object()) is not None:
        raise AssertionError("lifecycle state still has an active_run")


def prepare_trial(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    codex_home = root / "codex-home"
    paths = TrialPaths(
        root,
        codex_home,
        root / "repo",
        codex_home / "plugins" / "data" / "symphony-symphony",
        root / "artifacts",
    )
    for path in paths[1:]:
        path.mkdir(parents=True)
    return paths


def trial_environment(paths, base=None):
    env = dict(os.environ if base is None else base)
    env.update({
        "CODEX_HOME": str(paths.codex_home),
        "PLUGIN_DATA": str(paths.plugin_data),
        "CLAUDE_PLUGIN_DATA": str(paths.plugin_data),
        "SYMPHONY_STOP_WAIT_SECONDS": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    return env


def scrub_trial_auth(paths):
    """Remove the disposable credential copy while retaining trial evidence."""
    (paths.codex_home / "auth.json").unlink(missing_ok=True)


def _inventory(root):
    inventory = {}
    for path in sorted(Path(root).rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        relative = path.relative_to(root).as_posix()
        inventory[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return inventory


def verify_installed_candidate(candidate, installed):
    source = _inventory(candidate)
    copy = _inventory(installed)
    changed = sorted(path for path, digest in source.items() if copy.get(path) != digest)
    if changed:
        raise RuntimeError("installed candidate differs: " + ", ".join(changed[:10]))


def candidate_hook_trust_edit(response, installed):
    installed = Path(installed).resolve()
    states = {}
    for entry in response.get("data", []):
        for hook in entry.get("hooks", []):
            if hook.get("pluginId") != PLUGIN_ID:
                continue
            source = Path(hook.get("sourcePath", "")).resolve()
            if installed != source and installed not in source.parents:
                raise RuntimeError(f"candidate hook is outside installed candidate: {source}")
            key = hook.get("key")
            current_hash = hook.get("currentHash")
            if not key or not isinstance(current_hash, str) or not current_hash.startswith("sha256:"):
                raise RuntimeError("candidate hook has incomplete trust metadata")
            states[key] = {"trusted_hash": current_hash}
    if not states:
        raise RuntimeError("installed candidate exposed no hooks to trust")
    return {
        "edits": [{"keyPath": "hooks.state", "value": states, "mergeStrategy": "upsert"}],
        "reloadUserConfig": True,
    }


def _app_server_request(codex, env, cwd, method, params, timeout):
    messages = (
        {"method": "initialize", "id": 1, "params": {
            "clientInfo": {"name": "symphony-smoke", "version": "1"},
        }},
        {"method": "initialized", "params": {}},
        {"method": method, "id": 2, "params": params},
    )
    command = [codex, "app-server", "--stdio"]
    process = subprocess.Popen(
        command,
        env=env,
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    response = None
    output = []
    deadline = time.monotonic() + timeout
    try:
        process.stdin.write("".join(json.dumps(message) + "\n" for message in messages))
        process.stdin.flush()
        while response is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([process.stdout], [], [], remaining)[0]:
                raise ProcessTimeout(command, timeout, "".join(output), "")
            line = process.stdout.readline()
            if not line:
                raise RuntimeError(f"Codex app-server exited before responding to {method}")
            output.append(line)
            item = parse_jsonl(line)[0]
            if item.get("id") == 2:
                response = item
    finally:
        if process.poll() is None:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            process.wait()
    stderr = process.stderr.read()
    if response is None:
        raise RuntimeError(f"Codex app-server returned no response for {method}")
    if "error" in response:
        raise RuntimeError(f"Codex app-server {method} failed: {response['error']}")
    return response.get("result", {}), stderr


def trust_candidate_hooks(codex, paths, installed, deadline):
    before, before_stderr = _app_server_request(
        codex,
        trial_environment(paths),
        paths.repo,
        "hooks/list",
        {"cwds": [str(paths.repo)]},
        _remaining(deadline),
    )
    edit = candidate_hook_trust_edit(before, installed)
    write_result, write_stderr = _app_server_request(
        codex,
        trial_environment(paths),
        paths.repo,
        "config/batchWrite",
        edit,
        _remaining(deadline),
    )
    after, after_stderr = _app_server_request(
        codex,
        trial_environment(paths),
        paths.repo,
        "hooks/list",
        {"cwds": [str(paths.repo)]},
        _remaining(deadline),
    )
    candidate = [
        hook
        for entry in after.get("data", [])
        for hook in entry.get("hooks", [])
        if hook.get("pluginId") == PLUGIN_ID
    ]
    if not candidate or any(hook.get("trustStatus") != "trusted" for hook in candidate):
        raise RuntimeError("candidate hooks were not persistently trusted")
    _write(paths.artifacts / "hook-trust.json", json.dumps({
        "before": before,
        "edit": edit,
        "write_result": write_result,
        "after": after,
    }, indent=2, sort_keys=True))
    _write(
        paths.artifacts / "hook-trust.stderr",
        before_stderr + write_stderr + after_stderr,
    )


def configure_memory_fixture(paths, fixture):
    if fixture == "absent":
        return
    code = {
        "failing": "raise SystemExit(1)",
        "hanging": "import time; time.sleep(600)",
    }[fixture]
    with (paths.codex_home / "config.toml").open("a", encoding="utf-8") as config:
        config.write(
            '\n[mcp_servers."codebase-memory-mcp"]\n'
            f'command = {json.dumps(sys.executable)}\n'
            f'args = ["-c", {json.dumps(code)}]\n'
            "startup_timeout_sec = 1\n"
        )


def _write(path, content):
    Path(path).write_text(content, encoding="utf-8")


def _run_checked(command, *, env, timeout, cwd=None, input_text=None):
    result = run_process(
        command, env=env, timeout=timeout, cwd=cwd, input_text=input_text,
    )
    if result.returncode:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(command)}\n{result.stderr.strip()}"
        )
    return result


def _load_state(plugin_data):
    documents = {}
    for path in sorted(Path(plugin_data).rglob("*.json")):
        relative = path.relative_to(plugin_data).as_posix()
        try:
            documents[relative] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            documents[relative] = {"capture_error": str(error)}
    states = [value for value in documents.values() if isinstance(value, dict) and "active_run" in value]
    return (states[-1] if states else {}), documents


def capture_trial(
    paths,
    raw,
    stderr,
    *,
    elapsed,
    outer_elapsed,
    command,
    memory_fixture,
    status,
    stage="execution",
    trial_started=True,
    error=None,
):
    _write(paths.artifacts / "events.jsonl", raw)
    _write(paths.artifacts / "codex.stderr", stderr)
    state, state_documents = _load_state(paths.plugin_data)
    _write(paths.artifacts / "state.json", json.dumps(state_documents, indent=2, sort_keys=True))
    repo_artifacts = {
        "files": sorted(
            path.relative_to(paths.repo).as_posix()
            for path in paths.repo.rglob("*")
            if path.is_file() and ".git" not in path.parts
        ),
    }
    _write(paths.artifacts / "repo-artifacts.json", json.dumps(repo_artifacts, indent=2))
    jsonl_error = None
    try:
        events = parse_jsonl(raw)
    except ValueError as parse_error:
        events = []
        jsonl_error = str(parse_error)
    summary = {
        "status": status,
        "elapsed_seconds": elapsed,
        "outer_elapsed_seconds": outer_elapsed,
        "host_usage": extract_host_usage(events),
        "command": command,
        "memory_fixture": memory_fixture,
        "stage": stage,
        "trial_started": trial_started,
    }
    if error:
        summary["error"] = error
    if jsonl_error:
        summary["jsonl_error"] = jsonl_error
    _write(paths.artifacts / "summary.json", json.dumps(summary, indent=2, sort_keys=True))
    return summary, events, state


def _remaining(deadline):
    return max(0.0, deadline - time.monotonic())


def run_trial(
    paths,
    *,
    codex,
    marketplace,
    prompt,
    model,
    effort,
    timeout,
    expected,
    require_completion,
    auth_file=None,
    allow_auth_skip=False,
    memory_fixture="absent",
):
    started_at = time.monotonic()
    deadline = started_at + timeout
    stage = "environment"
    command = []
    raw = ""
    stderr = ""
    trial_started_at = None
    try:
        env = trial_environment(paths)
        stage = "credential-copy"
        if auth_file:
            auth_file = Path(auth_file)
            if auth_file.is_file():
                shutil.copy2(auth_file, paths.codex_home / "auth.json")

        stage = "git-init"
        command = ["git", "init", "--quiet", str(paths.repo)]
        git = _run_checked(command, env=env, timeout=_remaining(deadline))
        _write(paths.artifacts / "git-init.stderr", git.stderr)

        install_records = []
        install_commands = build_install_commands(codex, marketplace)
        for index, command in enumerate(install_commands, 1):
            stage = "marketplace-install" if index == 1 else "plugin-install"
            result = _run_checked(command, env=env, timeout=_remaining(deadline))
            _write(paths.artifacts / f"install-{index}.stdout", result.stdout)
            _write(paths.artifacts / f"install-{index}.stderr", result.stderr)
            install_records.append(json.loads(result.stdout))

        stage = "candidate-verification"
        installed = Path(install_records[-1]["installedPath"]).resolve()
        command = ["verify-installed-candidate", str(installed)]
        home = paths.codex_home.resolve()
        if home not in installed.parents:
            raise RuntimeError(f"installed candidate escaped isolated Codex home: {installed}")
        verify_installed_candidate(Path(marketplace) / "plugins" / "symphony", installed)

        stage = "hook-trust"
        command = [codex, "app-server", "--stdio"]
        trust_candidate_hooks(codex, paths, installed, deadline)

        stage = "memory-fixture"
        command = ["configure-memory-fixture", memory_fixture]
        configure_memory_fixture(paths, memory_fixture)

        if not env.get("OPENAI_API_KEY"):
            stage = "authentication"
            command = [codex, "login", "status"]
            auth = run_process(command, env=env, timeout=_remaining(deadline))
            _write(paths.artifacts / "auth.stdout", auth.stdout)
            _write(paths.artifacts / "auth.stderr", auth.stderr)
            stderr = auth.stderr
            if auth.returncode:
                if allow_auth_skip:
                    raise SmokeSkip("Codex credentials are unavailable")
                raise RuntimeError("Codex authentication failed: " + auth.stderr.strip())

        stage = "execution"
        command = build_exec_command(codex, paths.repo, prompt, model=model, effort=effort)
        trial_started_at = time.monotonic()
        result = run_process(command, env=env, timeout=_remaining(deadline), cwd=paths.repo)
        raw = result.stdout
        stderr = result.stderr
        if result.returncode:
            raise RuntimeError(f"Codex trial failed ({result.returncode}): {result.stderr.strip()}")

        stage = "assertions"
        events = parse_jsonl(result.stdout)
        state, _ = _load_state(paths.plugin_data)
        assert_lifecycle(events, state, expected, require_completion=require_completion)
    except Exception as error:
        if isinstance(error, ProcessTimeout):
            if trial_started_at is not None:
                raw = error.stdout
            stderr = error.stderr
        status = "skipped" if isinstance(error, SmokeSkip) else "failed"
        capture_trial(
            paths,
            raw,
            stderr or str(error),
            elapsed=(time.monotonic() - trial_started_at) if trial_started_at else 0.0,
            outer_elapsed=time.monotonic() - started_at,
            command=command,
            memory_fixture=memory_fixture,
            status=status,
            stage=stage,
            trial_started=trial_started_at is not None,
            error=str(error),
        )
        raise
    elapsed = time.monotonic() - trial_started_at
    summary, _, _ = capture_trial(
        paths,
        raw,
        stderr,
        elapsed=elapsed,
        outer_elapsed=time.monotonic() - started_at,
        command=command,
        memory_fixture=memory_fixture,
        status="passed",
        stage="complete",
        trial_started=True,
    )
    summary.update({
        "installed_path": str(installed),
        "hook_trust": "persisted current hashes for installed candidate hooks only",
    })
    _write(paths.artifacts / "summary.json", json.dumps(summary, indent=2, sort_keys=True))
    return summary


def parse_args(argv=None):
    repo_root = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-marketplace", type=Path, default=repo_root)
    parser.add_argument("--output", type=Path, default=Path("codex-smoke-artifacts"))
    parser.add_argument("--name", default="control")
    parser.add_argument("--prompt", default="/symphony:help")
    parser.add_argument("--expect", action="append", default=[])
    parser.add_argument("--require-completion", action="store_true")
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--effort", default="low")
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--codex", default=shutil.which("codex") or "codex")
    parser.add_argument("--auth-file", type=Path)
    parser.add_argument("--allow-auth-skip", action="store_true")
    parser.add_argument(
        "--memory-fixture", choices=("absent", "failing", "hanging"), default="absent",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=True)
    trial_root = Path(tempfile.mkdtemp(prefix=f"{args.name}-", dir=args.output))
    paths = prepare_trial(trial_root)
    source_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    auth_file = args.auth_file or source_home / "auth.json"
    try:
        summary = run_trial(
            paths,
            codex=args.codex,
            marketplace=args.candidate_marketplace.resolve(),
            prompt=args.prompt,
            model=args.model,
            effort=args.effort,
            timeout=args.timeout,
            expected=args.expect,
            require_completion=args.require_completion,
            auth_file=auth_file,
            allow_auth_skip=args.allow_auth_skip,
            memory_fixture=args.memory_fixture,
        )
    except SmokeSkip as error:
        print(f"SKIP: {error}")
        return 0
    except Exception as error:
        print(f"FAIL: {error}; artifacts: {paths.artifacts}", file=sys.stderr)
        return 1
    finally:
        scrub_trial_auth(paths)
    print(f"PASS: {summary['outer_elapsed_seconds']:.2f}s; artifacts: {paths.artifacts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
