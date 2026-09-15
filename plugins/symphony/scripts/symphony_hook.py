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
MEMORY_ROOT = Path(".symphony") / "memory"
SUGGESTION_COOLDOWN_SECONDS = 30 * 24 * 60 * 60
MAX_MODE_HISTORY = 20
RAW_CONTROL_RE = re.compile(
    r"\A/symphony:(enable|disable|start|stop|status|agents|assess|help)(?:\s+([\s\S]*))?\Z",
    re.IGNORECASE,
)
CONTROL_RE = re.compile(
    r"^[ \t]*(?:<!--[ \t]*)?SYMPHONY_CONTROL:[ \t]*(enable|disable|start|stop|status|agents|assess|help)"
    r"(?=[ \t]*(?:-->|$))", re.IGNORECASE | re.MULTILINE,
)
SUGGESTION_RE = re.compile(r"SYMPHONY_SUGGESTED:([a-z0-9-]+)", re.IGNORECASE)
MODE_RE = re.compile(r"SYMPHONY_MODE:\s*(small|medium|large)", re.IGNORECASE)
ASSESSMENT_RE = re.compile(
    r"(?<![A-Za-z0-9_-])SYMPHONY_ASSESSMENT:([a-f0-9]{16}):(small|medium|large):(small|medium|large)"
    r"(?![A-Za-z0-9_-])",
    re.IGNORECASE,
)
ASSESSMENT_REASON_RE = re.compile(
    r"^[ \t]*SYMPHONY_ASSESSMENT_REASON:([^\r\n]*)\r?$", re.IGNORECASE | re.MULTILINE,
)
INSPECTION_RE = re.compile(r"(?:^|\n)<!-- SYMPHONY_AGENTS_INSPECTED:([a-f0-9]{32}) -->\s*\Z")
MEMORY_CHECKPOINT_RE = re.compile(
    r"(?<![A-Za-z0-9_-])SYMPHONY_MEMORY_CHECKPOINT:([a-f0-9]+):codebase-memory-mcp(?![A-Za-z0-9_-])",
    re.IGNORECASE,
)
MEMORY_UNAVAILABLE_RE = re.compile(
    r"(?<![A-Za-z0-9_-])SYMPHONY_MEMORY_UNAVAILABLE:([a-f0-9]+):codebase-memory-mcp(?![A-Za-z0-9_-])",
    re.IGNORECASE,
)


def memory_paths(project_root, run_id):
    root = Path(project_root)
    return root / MEMORY_ROOT / "current.md", root / MEMORY_ROOT / "history" / f"{run_id}.md"


def _record_memory_receipt(run, message, now):
    unavailable = MEMORY_UNAVAILABLE_RE.findall(message or "")
    if run["id"].lower() in {value.lower() for value in unavailable}:
        run["memory"]["enabled"] = False
        return True
    matches = MEMORY_CHECKPOINT_RE.findall(message or "")
    if run["id"].lower() not in {value.lower() for value in matches}:
        return False
    run["memory"]["enabled"] = True
    run["memory"]["checkpoint_at"] = int(now)
    return True


def _memory_checkpoint_error(run, project_root, final_message):
    memory = run.get("memory") or {}
    if not memory.get("enabled"):
        return None
    matches = {value.lower() for value in MEMORY_CHECKPOINT_RE.findall(final_message or "")}
    if run["id"].lower() not in matches:
        return "Symphony document memory is active; include its matching checkpoint receipt."
    current = Path(project_root) / memory["current"]
    try:
        if not current.is_file() or current.stat().st_size == 0:
            return f"Symphony document memory is missing or empty at {current}."
        if current.stat().st_mtime < run["created_at"]:
            return f"Symphony document memory is stale at {current}."
    except OSError as error:
        return f"Symphony could not validate document memory at {current}: {error}"
    return None


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


def inspection_path(data_dir, project_root, session_id):
    digest = hashlib.sha256(json.dumps(session_id).encode("utf-8")).hexdigest()
    return project_state_path(data_dir, project_root).with_suffix(f".inspection.{digest}.json")


def default_state(project_root):
    return {
        "schema_version": SCHEMA_VERSION,
        "project_root": resolve_project_root(project_root),
        "enabled": False,
        "active_run": None,
        "run_history": [],
        "suggestions": {},
        "assessment": _default_assessment(),
        "corrupt": False,
        "warning": None,
    }


def _default_assessment():
    return {
        "profile": None,
        "source": None,
        "revision": 0,
        "reason": None,
        "assessed_at": None,
    }


def _valid_profile(value):
    return value is None or isinstance(value, str) and value in ("small", "medium", "large")


def _valid_assessment(assessment):
    return (
        isinstance(assessment, dict)
        and _valid_profile(assessment.get("profile"))
        and assessment.get("source") in (None, "automatic", "manual")
        and type(assessment.get("revision")) is int and assessment["revision"] >= 0
        and (assessment.get("reason") is None or isinstance(assessment["reason"], str))
        and (assessment.get("assessed_at") is None or type(assessment["assessed_at"]) is int)
    )


def _normalize_assessment_state(state):
    assessment = state.setdefault("assessment", _default_assessment())
    if not _valid_assessment(assessment):
        raise ValueError("invalid assessment state")
    run = state.get("active_run")
    if not isinstance(run, dict):
        return
    run.setdefault("mode_revision", 0)
    run.setdefault("assessment_due", True)
    run.setdefault("mode_history", [])
    run.setdefault("assessor_agent_id", None)
    if (
        type(run["mode_revision"]) is not int or run["mode_revision"] < 0
        or type(run["assessment_due"]) is not bool
        or not isinstance(run["mode_history"], list)
        or (run["assessor_agent_id"] is not None and not isinstance(run["assessor_agent_id"], str))
    ):
        raise ValueError("invalid assessment run state")


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


def read_project_state(data_dir, project_root, *, read_only=False):
    path = project_state_path(data_dir, project_root)
    if not path.exists():
        return default_state(project_root)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state, dict) or state.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("unsupported state schema")
        state.setdefault("suggestions", {})
        state.setdefault("active_run", None)
        state.setdefault("run_history", [])
        state.setdefault("enabled", False)
        state.setdefault("corrupt", False)
        state.setdefault("warning", None)
        _normalize_assessment_state(state)
        state["run_history"] = _run_history(state)
        if isinstance(state["active_run"], dict):
            run = state["active_run"]
            run["agent_records"] = {
                record["id"]: record for record in _agent_records(run)
            }
            current, history = memory_paths(state["project_root"], run["id"])
            memory = run.setdefault("memory", {})
            if not isinstance(memory, dict):
                raise ValueError("invalid run memory")
            memory.setdefault("enabled", False)
            memory.setdefault("checkpoint_at", None)
            if not isinstance(memory["enabled"], bool) or (
                memory["checkpoint_at"] is not None and type(memory["checkpoint_at"]) is not int
            ):
                raise ValueError("invalid run memory state")
            memory["current"] = str(current.relative_to(state["project_root"]))
            memory["history"] = str(history.relative_to(state["project_root"]))
        return state
    except (OSError, ValueError, json.JSONDecodeError) as error:
        if read_only:
            state = default_state(project_root)
            state["corrupt"] = True
            state["warning"] = f"Could not read Symphony state: {error}"
            return state
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


def _new_run(payload, objective, now, project_root):
    run_id = secrets.token_hex(8)
    current, history = memory_paths(project_root, run_id)
    return {
        "id": run_id,
        "owner_session_id": payload.get("session_id", "unknown"),
        "status": "starting",
        "mode": None,
        "mode_revision": 0,
        "assessment_due": True,
        "mode_history": [],
        "lead_agent_id": None,
        "assessor_agent_id": None,
        "agents": [],
        "agent_records": {},
        "objective": objective.strip()[:8000],
        "receipt": f"SYMPHONY_RUN_COMPLETE:{run_id}",
        "created_at": int(now),
        "last_event": payload.get("hook_event_name"),
        "memory": {
            "enabled": False,
            "current": str(current.relative_to(project_root)),
            "history": str(history.relative_to(project_root)),
            "checkpoint_at": None,
        },
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
    current_memory, history_memory = memory_paths(state["project_root"], run["id"])
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
        f"tracked agents: {agents}; recorded mode: {run.get('mode') or 'unselected'} "
        f"(revision {run['mode_revision']}, reassessment due={str(run['assessment_due']).lower()}); "
        f"project profile: {state['assessment']['profile'] or 'automatic'} "
        f"(revision {state['assessment']['revision']}). "
        f"Capabilities currently under suggestion cooldown: {cooldowns}. "
        f"Memory candidates: current={current_memory}; history={history_memory}. "
        f"Extended memory: enabled={str(run['memory']['enabled']).lower()}; "
        f"checkpoint_at={run['memory']['checkpoint_at']}. "
        "Verify codebase-memory-mcp plus a healthy index before creating either file; "
        "otherwise leave extended memory disabled. "
        "If active memory becomes unavailable because MCP/index health fails or either memory file "
        "disappears, report the loss and emit "
        f"`<!-- SYMPHONY_MEMORY_UNAVAILABLE:{run['id']}:codebase-memory-mcp -->` "
        "to persist compact lifecycle fallback; retain the last checkpoint time and finish with "
        "the normal mode and completion receipts. While memory is available, a matching fresh "
        "checkpoint remains required. "
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


def _assessment_context(state):
    assessment = state["assessment"]
    run = state.get("active_run")
    profile = assessment["profile"] or "automatic"
    source = assessment["source"] or "automatic"
    run_text = (
        f"Active run {run['id']} is due for reassessment; choose its mode independently."
        if run else "No run is active; the next run will require assessment."
    )
    return (
        f"Symphony assessment requested. Project profile: {profile} ({source}, revision "
        f"{assessment['revision']}). {run_text} Use current objective and repository evidence; "
        "a manual profile is project context, not a forced run mode."
    )


def _record_assessment_receipt(state, run, message, now, agent_id=None):
    matches = ASSESSMENT_RE.findall(message or "")
    reasons = ASSESSMENT_REASON_RE.findall(message or "")
    if len(matches) != 1 or len(reasons) != 1:
        return False
    receipt_run_id, profile, mode = matches[0]
    if receipt_run_id.lower() != run.get("id", "").lower():
        return False
    if state.get("active_run") is not run or (
        agent_id is not None and agent_id != run.get("assessor_agent_id")
    ):
        return False
    reason = reasons[0].strip()[:500]
    if not reason:
        return False
    assessment = state["assessment"]
    if assessment["source"] != "manual":
        assessment.update({
            "profile": profile.lower(),
            "source": "automatic",
            "revision": assessment["revision"] + 1,
            "reason": reason,
            "assessed_at": int(now),
        })
    run["mode"] = mode.lower()
    run["mode_revision"] += 1
    run["mode_history"] = (run["mode_history"] + [{
        "mode": run["mode"],
        "profile": assessment["profile"],
        "source": assessment["source"],
        "reason": reason,
        "revision": run["mode_revision"],
        "assessed_at": int(now),
    }])[-MAX_MODE_HISTORY:]
    run["assessment_due"] = False
    return True


def _has_active_non_lead_agent(run):
    excluded = {run.get("lead_agent_id"), run.get("assessor_agent_id")}
    return any(
        record["status"] == "active" and record["id"] not in excluded
        for record in _agent_records(run)
    )


def _agent_record(payload, started_at=None):
    return {
        "id": payload["agent_id"],
        "status": "active",
        "role": payload.get("agent_type") or "not exposed by host",
        "model": payload.get("model") or "not exposed by host",
        "effort": payload.get("reasoning_effort") or payload.get("effort") or "not exposed by host",
        "started_at": started_at,
        "stopped_at": None,
    }


def _agent_records(run):
    raw = run.get("agent_records")
    values = raw.values() if isinstance(raw, dict) else raw if isinstance(raw, list) else []
    records = {}
    for value in values:
        if not isinstance(value, dict) or not isinstance(value.get("id"), str) or not value["id"]:
            continue
        if value.get("status") not in ("active", "terminal"):
            continue
        record = {field: value.get(field) for field in _agent_record({"agent_id": value["id"]})}
        for field in ("role", "model", "effort"):
            if not isinstance(record[field], str) or not record[field]:
                record[field] = "not exposed by host"
        records[record["id"]] = record
    agents = run.get("agents")
    for agent_id in agents if isinstance(agents, list) else []:
        if isinstance(agent_id, str) and agent_id:
            records.setdefault(agent_id, _agent_record({"agent_id": agent_id}))
    return list(records.values())


def _run_history(state):
    history = state.get("run_history")
    if not isinstance(history, list):
        return []
    return [
        {**run, "agent_records": _agent_records(run)} for run in history
        if isinstance(run, dict) and isinstance(run.get("id"), str) and run["id"]
        and isinstance(run.get("status"), str)
    ]


def _archive_run(state, status, now):
    run = state.get("active_run")
    if run:
        state["run_history"] = _run_history(state)
        state["run_history"].append({
            "id": run["id"],
            "objective": run["objective"],
            "mode": run.get("mode"),
            "status": status,
            "created_at": run["created_at"],
            "completed_at": int(now),
            "agent_records": _agent_records(run),
        })


def _agents_context(state, include_history=False):
    if state.get("corrupt"):
        return f"Symphony agent ledger unavailable: state is corrupt. {state.get('warning') or ''}"
    run = state.get("active_run")
    lines = ["Symphony agent ledger (lifecycle observations)."]
    if not run:
        lines.append("No active Symphony run.")
    runs = [(run, _agent_records(run))] if run else []
    if include_history:
        runs.extend((past, past["agent_records"]) for past in _run_history(state))
    for item, records in runs:
        lines.append(f"Run {item['id']} ({item['status']}):")
        if not records:
            lines.append("No observed subagents.")
            continue
        lines.extend(["| Run | Id | Status | Role | Model | Effort |", "| --- | --- | --- | --- | --- | --- |"])
        for record in records:
            values = [item["id"], record["id"], record["status"]]
            values.extend(record.get(field) or "not exposed by host" for field in ("role", "model", "effort"))
            lines.append("| " + " | ".join(str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ") for value in values) + " |")
    return "\n".join(lines)


def _marker_argument(prompt, name):
    comment = re.search(rf"<!--[ \t]*SYMPHONY_{name}:[ \t]*([\s\S]*?)-->", prompt, re.IGNORECASE)
    bare = re.search(
        rf"^[ \t]*SYMPHONY_{name}:[ \t]*([\s\S]*?)(?=^[ \t]*SYMPHONY_[A-Z_]+:|\Z)",
        prompt, re.IGNORECASE | re.MULTILINE,
    ) if not comment else None
    match = comment or bare
    value = match.group(1).strip() if match else ""
    return "" if value == "$ARGUMENTS" else value


def _parse_prompt(prompt):
    raw = RAW_CONTROL_RE.fullmatch(prompt.strip())
    if raw:
        return raw.group(1).lower(), (raw.group(2) or "").strip(), (raw.group(2) or "").strip()
    control_match = CONTROL_RE.search(prompt)
    control = control_match.group(1).lower() if control_match else None
    return control, _marker_argument(prompt, "TASK"), _marker_argument(prompt, "ARGS")


def _handle_prompt(payload, state, now):
    prompt = payload.get("prompt") or ""
    control, task, args = _parse_prompt(prompt)

    if control == "help":
        return HookResult()
    if control == "status":
        return HookResult(context=_status_context(state))
    if control == "agents":
        return HookResult(context=_agents_context(state, include_history="--all" in args.split()))
    if control == "assess":
        profile = args.strip().lower()
        if profile not in {"", "small", "medium", "large", "auto"}:
            return HookResult(context="Usage: /symphony:assess [small|medium|large|auto]")
        if profile in {"small", "medium", "large"}:
            state["assessment"] = {
                "profile": profile,
                "source": "manual",
                "revision": state["assessment"]["revision"] + 1,
                "reason": f"Manual project profile override: {profile}.",
                "assessed_at": int(now),
            }
        elif profile == "auto":
            state["assessment"] = {
                "profile": None,
                "source": None,
                "revision": state["assessment"]["revision"] + 1,
                "reason": None,
                "assessed_at": None,
            }
        if state.get("active_run"):
            state["active_run"]["assessment_due"] = True
        return HookResult(context=_assessment_context(state))
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
        if "--force" in args.split():
            orphaned = list((state.get("active_run") or {}).get("agents", []))
            _archive_run(state, "force-stopped", now)
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
        if control is None:
            run["assessment_due"] = True
        if run.get("status") == "stopping":
            return HookResult(context="This Symphony run is stopping; reconcile tracked agents before continuing.")
        return HookResult(context=f"Continue Symphony run {run['id']}; do not launch a duplicate lead.")

    if start_requested or (state.get("enabled") and control is None):
        objective = task if start_requested else prompt
        state["active_run"] = _new_run(payload, objective, now, state["project_root"])
        return HookResult(context=_bootstrap_context(state["active_run"], state, now=now))

    return HookResult()


def _handle_stop(payload, data_dir, project_root, now, stop_wait_seconds):
    inspection = INSPECTION_RE.search(payload.get("last_assistant_message") or "")
    if inspection:
        with project_lock(data_dir, project_root):
            pending_path = inspection_path(data_dir, project_root, payload.get("session_id"))
            try:
                pending = json.loads(pending_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pending = None
            state = read_project_state(data_dir, project_root, read_only=True)
            if (
                isinstance(pending, dict)
                and pending.get("nonce") == inspection.group(1)
                and pending.get("run_id") == (state.get("active_run") or {}).get("id")
                and pending.get("session_id") == payload.get("session_id")
                and (pending.get("turn_id") is None or pending["turn_id"] == payload.get("turn_id"))
            ):
                pending_path.unlink()
                return HookResult()
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
    if not background_tasks:
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
        message = payload.get("last_assistant_message") or ""
        memory_changed = _record_memory_receipt(run, message, now)
        assessment_changed = (
            _record_assessment_receipt(state, run, message, now)
            if payload.get("session_id") == run.get("owner_session_id") else False
        )
        if background_tasks:
            if memory_changed or assessment_changed:
                write_project_state(data_dir, state, now)
            task_ids = [str(task.get("id") or task.get("task_id") or "unknown") for task in background_tasks]
            return HookResult(
                block=True,
                reason=(
                    "Symphony detected active Claude background tasks: " + ", ".join(task_ids) +
                    ". Collect or stop them before ending the run."
                ),
            )
        agents = list(run.get("agents", []))
        if agents:
            if memory_changed or assessment_changed:
                write_project_state(data_dir, state, now)
            return HookResult(
                block=True,
                reason=(
                    "Symphony still has tracked agents: " + ", ".join(agents) +
                    ". Call the host's blocking wait/result tool and integrate every result before stopping."
                ),
            )
        if run.get("status") == "stopping":
            _archive_run(state, "stopped", now)
            state["active_run"] = None
            write_project_state(data_dir, state, now)
            return HookResult()
        if run["receipt"] not in message:
            if memory_changed or assessment_changed:
                write_project_state(data_dir, state, now)
            return HookResult(
                block=True,
                reason=(
                    "Symphony run remains active. Reconcile worker results, integrate and verify the "
                    f"objective, then include `{run['receipt']}` in the final assistant message."
                ),
            )
        mode = MODE_RE.search(message)
        if not mode:
            if memory_changed or assessment_changed:
                write_project_state(data_dir, state, now)
            return HookResult(
                block=True,
                reason=(
                    "Symphony completion is missing its single selected mode. Reissue a "
                    "self-contained final report with the actual root profile, strongest/high "
                    "lead, applicable capability routing, exactly one of small, medium, or large, "
                    "`<!-- SYMPHONY_MODE:<mode> -->`, and the exact run completion receipt."
                ),
            )
        memory_error = _memory_checkpoint_error(run, project_root, message)
        if memory_error:
            write_project_state(data_dir, state, now)
            return HookResult(block=True, reason=memory_error)
        suggestions = SUGGESTION_RE.findall(message)
        if suggestions:
            capability = suggestions[0].lower()
            if can_suggest(state, capability, now):
                state.setdefault("suggestions", {})[capability] = int(now)
        if mode:
            run["mode"] = mode.group(1).lower()
        _archive_run(state, "completed", now)
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

    control = _parse_prompt(payload.get("prompt") or "")[0] if event == "UserPromptSubmit" else None
    read_only = control == "agents"
    with project_lock(data_dir, project_root):
        state = read_project_state(data_dir, project_root, read_only=read_only)
        original_state = json.dumps(state, sort_keys=True)
        result = HookResult()
        if event == "SessionStart":
            run = state.get("active_run")
            if run:
                run["assessment_due"] = True
                result = HookResult(context=_bootstrap_context(run, state, recovery=True, now=current))
            elif state.get("enabled"):
                result = HookResult(
                    context=(
                        "Symphony is enabled for this project. The first non-control project prompt "
                        "will start a new guarded run with a strong execution lead."
                    )
                )
        elif event == "UserPromptSubmit":
            pending_path = inspection_path(data_dir, project_root, payload.get("session_id"))
            pending_path.unlink(missing_ok=True)
            result = _handle_prompt(payload, state, current)
            if read_only:
                nonce = secrets.token_hex(16)
                _atomic_write(pending_path, {
                    "nonce": nonce,
                    "run_id": (state.get("active_run") or {}).get("id"),
                    "session_id": payload.get("session_id"),
                    "turn_id": payload.get("turn_id"),
                })
                result.context += (
                    "\nEnd only this inspection response with the following single-use receipt; "
                    "do not reuse it for later project work or report run completion:\n"
                    f"<!-- SYMPHONY_AGENTS_INSPECTED:{nonce} -->"
                )
        elif event == "SubagentStart":
            run = state.get("active_run")
            agent_id = payload.get("agent_id")
            if run and agent_id:
                run["agents"] = sorted(set(run.get("agents", [])) | {agent_id})
                run.setdefault("agent_records", {})[agent_id] = _agent_record(payload, current)
                run["last_event"] = event
                run["status"] = "active"
                if (payload.get("agent_type") or "").lower() == "symphony_assessor":
                    run["assessor_agent_id"] = agent_id
                elif not run.get("lead_agent_id") and "symphony" in (payload.get("agent_type") or "").lower():
                    run["lead_agent_id"] = agent_id
                result = HookResult(
                    context=(
                        f"You are part of Symphony run {run['id']}. Follow the explicitly assigned "
                        "skills and return a capability receipt, changed files, checks, and blockers."
                    )
                )
        elif event == "SubagentStop":
            agent_id = payload.get("agent_id")
            active = state.get("active_run")
            runs = ([active] if active else []) + state["run_history"]
            owners = [run for run in runs if any(record["id"] == agent_id for record in _agent_records(run))]
            if agent_id and len(owners) == 1:
                run = owners[0]
                records = {record["id"]: record for record in _agent_records(run)}
                record = records[agent_id]
                was_active = record["status"] == "active"
                exposed = _agent_record(payload)
                for field in ("role", "model", "effort"):
                    if isinstance(exposed[field], str) and exposed[field] != "not exposed by host":
                        record[field] = exposed[field]
                record["status"] = "terminal"
                record["stopped_at"] = current
                run["agent_records"] = records if run is active else list(records.values())
                if run is active:
                    run["agents"] = sorted(set(run.get("agents", [])) - {agent_id})
                    run["last_event"] = event
                    mode = MODE_RE.search(payload.get("last_assistant_message") or "")
                    if mode:
                        run["mode"] = mode.group(1).lower()
                    _record_memory_receipt(run, payload.get("last_assistant_message"), current)
                    _record_assessment_receipt(
                        state, run, payload.get("last_assistant_message"), current, agent_id,
                    )
                    if (
                        was_active
                        and agent_id not in {run.get("lead_agent_id"), run.get("assessor_agent_id")}
                        and not _has_active_non_lead_agent(run)
                    ):
                        run["assessment_due"] = True
        elif event == "Interrupt":
            run = state.get("active_run")
            if run:
                run["last_event"] = event
                run["interrupted_at"] = current
                run["assessment_due"] = True
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
    if event in {"SessionStart", "UserPromptSubmit", "SubagentStart"}:
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
