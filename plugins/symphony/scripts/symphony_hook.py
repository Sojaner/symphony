#!/usr/bin/env python3
"""Persistent lifecycle guard shared by the Codex and Claude plugins."""

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
import time


SCHEMA_VERSION = 1
SUGGESTION_COOLDOWN_SECONDS = 30 * 24 * 60 * 60
CONTROL_RE = re.compile(r"SYMPHONY_CONTROL:\s*([a-z-]+)", re.IGNORECASE)
TASK_RE = re.compile(r"SYMPHONY_TASK:\s*([^\n]*)", re.IGNORECASE)
ARGS_RE = re.compile(r"SYMPHONY_ARGS:\s*([^\n]*)", re.IGNORECASE)
SUGGESTION_RE = re.compile(r"SYMPHONY_SUGGESTED:([a-z0-9-]+)", re.IGNORECASE)
MODE_RE = re.compile(r"SYMPHONY_MODE:\s*(small|medium|large)", re.IGNORECASE)


class HookResult:
    def __init__(self, *, context="", block=False, reason=""):
        self.context = context
        self.block = block
        self.reason = reason


def resolve_project_root(cwd):
    cwd_path = Path(cwd or os.getcwd()).resolve()
    try:
        completed = subprocess.run(
            ["git", "-C", str(cwd_path), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        )
        return str(Path(completed.stdout.strip()).resolve())
    except (OSError, subprocess.SubprocessError):
        return str(cwd_path)


def project_state_path(data_dir, project_root):
    root = resolve_project_root(project_root)
    digest = hashlib.sha256(root.encode("utf-8")).hexdigest()[:24]
    return Path(data_dir) / "projects" / f"{digest}.json"


def default_state(project_root):
    return {
        "schema_version": SCHEMA_VERSION,
        "project_root": resolve_project_root(project_root),
        "enabled": False,
        "active_run": None,
        "suggestions": {},
        "corrupt": False,
        "warning": None,
    }


def _atomic_write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def read_project_state(data_dir, project_root):
    path = project_state_path(data_dir, project_root)
    if not path.exists():
        return default_state(project_root)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state, dict) or state.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("unsupported state schema")
        state.setdefault("suggestions", {})
        state.setdefault("active_run", None)
        state.setdefault("enabled", False)
        state.setdefault("corrupt", False)
        state.setdefault("warning", None)
        return state
    except (OSError, ValueError, json.JSONDecodeError) as error:
        stamp = f"{int(time.time())}.{os.getpid()}"
        quarantine = path.with_name(f"{path.name}.corrupt.{stamp}")
        try:
            os.replace(path, quarantine)
        except OSError:
            quarantine = path
        state = default_state(project_root)
        state["corrupt"] = True
        state["warning"] = f"State was corrupt and quarantined at {quarantine}: {error}"
        _atomic_write(path, state)
        return state


@contextmanager
def project_lock(data_dir, project_root, timeout=5):
    state_path = project_state_path(data_dir, project_root)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = state_path.with_suffix(".lock")
    deadline = time.monotonic() + timeout
    while True:
        try:
            lock_path.mkdir(mode=0o700)
            break
        except FileExistsError:
            try:
                if time.time() - lock_path.stat().st_mtime > 30:
                    lock_path.rmdir()
                    continue
            except (FileNotFoundError, OSError):
                continue
            if time.monotonic() >= deadline:
                raise TimeoutError(f"timed out waiting for state lock {lock_path}")
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            lock_path.rmdir()
        except FileNotFoundError:
            pass


def write_project_state(data_dir, state, now=None):
    state["updated_at"] = int(time.time() if now is None else now)
    _atomic_write(project_state_path(data_dir, state["project_root"]), state)


def can_suggest(state, capability, now=None):
    current = int(time.time() if now is None else now)
    previous = state.get("suggestions", {}).get(capability)
    return previous is None or current - int(previous) >= SUGGESTION_COOLDOWN_SECONDS


def _new_run(payload, objective, now):
    run_id = secrets.token_hex(8)
    return {
        "id": run_id,
        "owner_session_id": payload.get("session_id", "unknown"),
        "status": "starting",
        "mode": None,
        "lead_agent_id": None,
        "agents": [],
        "objective": objective.strip()[:8000],
        "receipt": f"SYMPHONY_RUN_COMPLETE:{run_id}",
        "created_at": int(now),
        "last_event": payload.get("hook_event_name"),
    }


def _bootstrap_context(run, state, *, recovery=False, now=None):
    action = "Recover" if recovery else "Start"
    current = int(time.time() if now is None else now)
    cooldowns = ", ".join(
        sorted(
            capability
            for capability in state.get("suggestions", {})
            if not can_suggest(state, capability, current)
        )
    ) or "none"
    agents = ", ".join(run.get("agents", [])) or "none"
    return (
        f"{action} Symphony run {run['id']}. Invoke the installed Symphony skill first and "
        "follow it for this run. You are the thin root/session keeper. "
        "Before project work, spawn one strongest-available general reasoning model at high "
        "effort with no inherited turns as the strong execution lead. Give it the objective, "
        "actual root model/effort, live model and concurrency catalog, effective skills and "
        "tools, repository instructions, current worktree state, and this run record. The lead "
        "must select exactly one mode label: small, medium, or large; it "
        "then owns decisions, integration, and verification. Use one primary workflow skill, "
        "with Ponytail, Context7, and Codebase Memory only where applicable. Track every spawned "
        "agent and block on host wait/result tools until it returns. Do not end the run until "
        "all agents are terminal and the final assistant message contains exactly one mode marker "
        f"and `<!-- {run['receipt']} -->`. If the user explicitly requests a dry run or forbids "
        "spawning or writes, obey that constraint: plan without acting and report only the actual "
        "root profile, planned strongest/high lead, one mode, applicable capability routing, mode "
        "marker, and completion receipt; do not ask a follow-up question. "
        "Explicit interrupts cannot be prevented; reconcile this run on resume. "
        f"Run status: {run['status']}; owner session: {run['owner_session_id']}; "
        f"tracked agents: {agents}; recorded mode: {run.get('mode') or 'unselected'}. "
        f"Capabilities currently under suggestion cooldown: {cooldowns}. "
        f"Objective: {run['objective']}"
    )


def _status_context(state):
    run = state.get("active_run")
    if not run:
        run_text = "none"
    else:
        run_text = (
            f"{run['id']} ({run['status']}), owner={run['owner_session_id']}, "
            f"mode={run.get('mode') or 'unselected'}, agents={','.join(run.get('agents', [])) or 'none'}"
        )
    return f"Symphony project enabled: {str(state['enabled']).lower()}. Active run: {run_text}."


def _handle_prompt(payload, state, now):
    prompt = payload.get("prompt") or ""
    control_match = CONTROL_RE.search(prompt)
    control = control_match.group(1).lower() if control_match else None
    task_match = TASK_RE.search(prompt)
    task = task_match.group(1).strip() if task_match else ""
    if task == "$ARGUMENTS":
        task = ""
    args_match = ARGS_RE.search(prompt)
    args = args_match.group(1).strip() if args_match else ""
    if args == "$ARGUMENTS":
        args = ""

    if control == "help":
        return HookResult()
    if control == "status":
        return HookResult(context=_status_context(state))
    if control == "enable":
        state["enabled"] = True
        if not task:
            return HookResult(context="Symphony is enabled persistently for this project.")
    if control == "disable":
        state["enabled"] = False
        if state.get("active_run"):
            state["active_run"]["status"] = "stopping"
            return HookResult(
                context=(
                    "Symphony is disabled for future project work. Gracefully stop the active "
                    "run: dispatch no new work, interrupt tracked agents, preserve workspace "
                    "changes, and let the Stop hook clear the reconciled run."
                )
            )
        return HookResult(context="Symphony is disabled for this project.")
    if control == "stop":
        if "--force" in args:
            orphaned = list((state.get("active_run") or {}).get("agents", []))
            state["active_run"] = None
            state["corrupt"] = False
            state["warning"] = "Forced stop released lifecycle protection."
            suffix = f" Tracked agents that may still be running: {', '.join(orphaned)}." if orphaned else ""
            return HookResult(context="Symphony was force-stopped; an untracked agent may still be running." + suffix)
        if state.get("active_run"):
            state["active_run"]["status"] = "stopping"
            return HookResult(
                context="Gracefully stop this run: dispatch no new work and interrupt tracked agents."
            )
        return HookResult(context="No Symphony run is active.")

    start_requested = control in {"start", "enable"} and bool(task)
    if state.get("corrupt"):
        return HookResult(
            context=(
                f"Symphony state is corrupt: {state.get('warning')}. Run "
                "`/symphony:stop --force` before starting new work."
            )
        )

    run = state.get("active_run")
    if run:
        if run.get("owner_session_id") != payload.get("session_id"):
            return HookResult(
                context="Recovery required. " + _bootstrap_context(run, state, recovery=True, now=now)
            )
        if run.get("status") == "stopping":
            return HookResult(context="This Symphony run is stopping; reconcile tracked agents before continuing.")
        return HookResult(context=f"Continue Symphony run {run['id']}; do not launch a duplicate lead.")

    if start_requested or (state.get("enabled") and control is None):
        objective = task if start_requested else prompt
        state["active_run"] = _new_run(payload, objective, now)
        return HookResult(context=_bootstrap_context(state["active_run"], state, now=now))

    return HookResult()


def _handle_stop(payload, data_dir, project_root, now, stop_wait_seconds):
    initial_state = read_project_state(data_dir, project_root)
    if initial_state.get("corrupt"):
        return HookResult(block=True, reason="Symphony state is corrupt; use /symphony:stop --force to recover.")
    if not initial_state.get("active_run"):
        return HookResult()
    background_tasks = [
        task
        for task in payload.get("background_tasks", [])
        if isinstance(task, dict) and task.get("status") in {"running", "pending"}
    ]
    if background_tasks:
        task_ids = [str(task.get("id") or task.get("task_id") or "unknown") for task in background_tasks]
        return HookResult(
            block=True,
            reason=(
                "Symphony detected active Claude background tasks: " + ", ".join(task_ids) +
                ". Collect or stop them before ending the run."
            ),
        )
    deadline = time.monotonic() + max(0, stop_wait_seconds)
    while True:
        state = read_project_state(data_dir, project_root)
        agents = list((state.get("active_run") or {}).get("agents", []))
        if not agents or time.monotonic() >= deadline:
            break
        time.sleep(min(0.25, max(0, deadline - time.monotonic())))

    with project_lock(data_dir, project_root):
        state = read_project_state(data_dir, project_root)
        if state.get("corrupt"):
            return HookResult(block=True, reason="Symphony state is corrupt; use /symphony:stop --force to recover.")
        run = state.get("active_run")
        if not run:
            return HookResult()
        agents = list(run.get("agents", []))
        if agents:
            return HookResult(
                block=True,
                reason=(
                    "Symphony still has tracked agents: " + ", ".join(agents) +
                    ". Call the host's blocking wait/result tool and integrate every result before stopping."
                ),
            )
        message = payload.get("last_assistant_message") or ""
        if run.get("status") == "stopping":
            state["active_run"] = None
            write_project_state(data_dir, state, now)
            return HookResult()
        if run["receipt"] not in message:
            return HookResult(
                block=True,
                reason=(
                    "Symphony run remains active. Reconcile worker results, integrate and verify the "
                    f"objective, then include `{run['receipt']}` in the final assistant message."
                ),
            )
        mode = MODE_RE.search(message)
        if not mode:
            return HookResult(
                block=True,
                reason=(
                    "Symphony completion is missing its single selected mode. Reissue a "
                    "self-contained final report with the actual root profile, strongest/high "
                    "lead, applicable capability routing, exactly one of small, medium, or large, "
                    "`<!-- SYMPHONY_MODE:<mode> -->`, and the exact run completion receipt."
                ),
            )
        suggestions = SUGGESTION_RE.findall(message)
        if suggestions:
            capability = suggestions[0].lower()
            if can_suggest(state, capability, now):
                state.setdefault("suggestions", {})[capability] = int(now)
        if mode:
            run["mode"] = mode.group(1).lower()
        state["active_run"] = None
        state["warning"] = None
        write_project_state(data_dir, state, now)
        return HookResult()


def handle_event(payload, data_dir, now=None, stop_wait_seconds=None):
    current = int(time.time() if now is None else now)
    project_root = resolve_project_root(payload.get("cwd"))
    event = payload.get("hook_event_name")
    if event == "Stop":
        wait = stop_wait_seconds
        if wait is None:
            wait = int(os.environ.get("SYMPHONY_STOP_WAIT_SECONDS", "55"))
        return _handle_stop(payload, Path(data_dir), project_root, current, wait)

    with project_lock(data_dir, project_root):
        state = read_project_state(data_dir, project_root)
        original_state = json.dumps(state, sort_keys=True)
        result = HookResult()
        if event == "SessionStart":
            run = state.get("active_run")
            if run:
                result = HookResult(context=_bootstrap_context(run, state, recovery=True, now=current))
            elif state.get("enabled"):
                result = HookResult(
                    context=(
                        "Symphony is enabled for this project. The first non-control project prompt "
                        "will start a new guarded run with a strong execution lead."
                    )
                )
        elif event == "UserPromptSubmit":
            result = _handle_prompt(payload, state, current)
        elif event == "SubagentStart":
            run = state.get("active_run")
            agent_id = payload.get("agent_id")
            if run and agent_id:
                run["agents"] = sorted(set(run.get("agents", [])) | {agent_id})
                run["last_event"] = event
                run["status"] = "active"
                if not run.get("lead_agent_id") and "symphony" in (payload.get("agent_type") or "").lower():
                    run["lead_agent_id"] = agent_id
                result = HookResult(
                    context=(
                        f"You are part of Symphony run {run['id']}. Follow the explicitly assigned "
                        "skills and return a capability receipt, changed files, checks, and blockers."
                    )
                )
        elif event == "SubagentStop":
            run = state.get("active_run")
            agent_id = payload.get("agent_id")
            if run and agent_id:
                run["agents"] = sorted(set(run.get("agents", [])) - {agent_id})
                run["last_event"] = event
                mode = MODE_RE.search(payload.get("last_assistant_message") or "")
                if mode:
                    run["mode"] = mode.group(1).lower()
                result = HookResult(
                    context=(
                        f"Symphony agent {agent_id} is terminal. Collect and inspect its result, "
                        "update the run ledger, and integrate it before completion."
                    )
                )
        elif event == "Interrupt":
            run = state.get("active_run")
            if run:
                run["last_event"] = event
                run["interrupted_at"] = current
        if json.dumps(state, sort_keys=True) != original_state:
            write_project_state(data_dir, state, current)
        return result


def _data_dir_from_environment():
    value = os.environ.get("PLUGIN_DATA") or os.environ.get("CLAUDE_PLUGIN_DATA")
    if not value:
        raise RuntimeError("PLUGIN_DATA or CLAUDE_PLUGIN_DATA is required")
    return Path(value)


def format_output(payload, result):
    if result.block:
        return json.dumps({"decision": "block", "reason": result.reason})
    if not result.context:
        return ""
    event = payload.get("hook_event_name")
    if event in {"SessionStart", "UserPromptSubmit", "SubagentStart", "SubagentStop"}:
        return json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": event,
                    "additionalContext": result.context,
                }
            }
        )
    return result.context


def main():
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("hook input must be a JSON object")
        result = handle_event(payload, _data_dir_from_environment())
        output = format_output(payload, result)
        if output:
            print(output)
        return 0
    except Exception as error:
        print(f"Symphony hook failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
