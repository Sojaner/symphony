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
TOKEN_USAGE_FIELDS = (
    "final_request_total_tokens", "final_request_input_tokens", "final_request_output_tokens",
    "final_request_cache_creation_tokens", "final_request_cache_read_tokens",
)
RUN_USAGE_FIELDS = ("duration_ms", "tool_uses")
USAGE_FIELDS = TOKEN_USAGE_FIELDS + RUN_USAGE_FIELDS
AGENT_TABLE_HEADINGS = (
    "Run", "Id", "Status", "Role", "Model", "Effort", "Final-request total tokens",
    "Final-request input tokens", "Final-request output tokens",
    "Final-request cache creation tokens", "Final-request cache read tokens", "Run duration (ms)",
    "Run tool uses", "Usage source/token scope",
)
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
    r"^[ \t]*(?:`|\*\*)?SYMPHONY_ASSESSMENT_REASON:[ \t]*(?:`|\*\*)?[ \t]*"
    r"([^\r\n]*?)[ \t]*(?:`|\*\*)?[ \t]*\r?$",
    re.IGNORECASE | re.MULTILINE,
)
REGISTRATION_RE = re.compile(
    r"^[ \t]*(?:`|\*\*)?SYMPHONY_REGISTER:([a-f0-9]{16}):(assessor|lead):"
    r"([A-Za-z0-9_-]{1,128})[ \t]*(?:`|\*\*)?[ \t]*$",
    re.MULTILINE,
)
CONTROL_RECEIPT_RE = re.compile(r"(?:^|\n)<!-- SYMPHONY_CONTROL_HANDLED:([a-f0-9]{32}) -->\s*\Z")
RAW_CONTROL_PREFIX_RE = re.compile(r"\A/symphony:", re.IGNORECASE)
MARKER_CONTROL_PREFIX_RE = re.compile(
    r"^[ \t]*(?:<!--[ \t]*)?SYMPHONY_CONTROL\b", re.IGNORECASE | re.MULTILINE,
)
MEMORY_CHECKPOINT_RE = re.compile(
    r"(?<![A-Za-z0-9_-])SYMPHONY_MEMORY_CHECKPOINT:([a-f0-9]+):codebase-memory-mcp(?![A-Za-z0-9_-])",
    re.IGNORECASE,
)
MEMORY_UNAVAILABLE_RE = re.compile(
    r"(?<![A-Za-z0-9_-])SYMPHONY_MEMORY_UNAVAILABLE:([a-f0-9]+):codebase-memory-mcp(?![A-Za-z0-9_-])",
    re.IGNORECASE,
)
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
HOOK_DECLARATION_PATH = PLUGIN_ROOT / "hooks" / (
    "codex.json" if os.environ.get("PLUGIN_DATA") else "hooks.json"
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
    run.setdefault("strong_assessment_required", not _has_accepted_assessment(run))
    run.setdefault("dry_run", False)
    run.setdefault("interrupted_at", None)
    run.setdefault("interruption_recovery_eligible", False)
    run.setdefault("previous_owner_session_id", None)
    run.setdefault("ownership_transferred_at", None)
    if (
        type(run["mode_revision"]) is not int or run["mode_revision"] < 0
        or type(run["assessment_due"]) is not bool
        or type(run["strong_assessment_required"]) is not bool
        or type(run["dry_run"]) is not bool
        or type(run["interruption_recovery_eligible"]) is not bool
        or (run["interrupted_at"] is not None and type(run["interrupted_at"]) is not int)
        or (run["previous_owner_session_id"] is not None
            and not isinstance(run["previous_owner_session_id"], str))
        or (run["ownership_transferred_at"] is not None
            and type(run["ownership_transferred_at"]) is not int)
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


def _new_run(payload, objective, now, project_root, *, dry_run=False):
    run_id = secrets.token_hex(8)
    current, history = memory_paths(project_root, run_id)
    return {
        "id": run_id,
        "owner_session_id": payload.get("session_id", "unknown"),
        "previous_owner_session_id": None,
        "ownership_transferred_at": None,
        "interrupted_at": None,
        "interruption_recovery_eligible": False,
        "dry_run": dry_run,
        "status": "starting",
        "mode": None,
        "mode_revision": 0,
        "assessment_due": True,
        "strong_assessment_required": True,
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


def _has_accepted_assessment(run):
    return (
        run.get("mode") in ("small", "medium", "large")
        and type(run.get("mode_revision")) is int and run["mode_revision"] > 0
    )


def _require_reassessment(run, records=None):
    """Set reassessment due and invalidate any narrower synchronous-lead clearance."""
    records = records or {record["id"]: record for record in _agent_records(run)}
    for record in records.values():
        record.pop("initial_lead_stop_reassessment", None)
    run["agent_records"] = records
    run["assessment_due"] = True
    return records


def _host_reports_child_lifecycle():
    """Read the installed host declaration; prompts and event payloads are not authority."""
    try:
        hooks = json.loads(HOOK_DECLARATION_PATH.read_text(encoding="utf-8"))["hooks"]
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(hooks, dict):
        return None
    configured = {event for event in ("SubagentStart", "SubagentStop") if event in hooks}
    if not configured:
        return False
    return True if len(configured) == 2 else None


def _bootstrap_context(run, state, *, recovery=False, accepted_recovery=False, now=None):
    action = "Recover" if recovery else "Start"
    if run["dry_run"]:
        return (
            f"{action} Symphony dry run. Run id: {run['id']}. Objective: {run['objective']}. "
            f"Project profile: {state['assessment']['profile'] or 'automatic'} "
            f"(revision {state['assessment']['revision']}). Dry run: true. "
            "Do not spawn agents or write project files. Report, in order, `Root profile:`, "
            "`Capability routing:`, planned `Delegating:` and `Completed:` records for a separate "
            "strongest/high assessor and mode-appropriate execution lead, exactly one planned mode marker, and "
            f"`<!-- {run['receipt']} -->`."
        )
    route = (
        "Reconcile observed agents, then emit `Delegating: symphony_lead — <bounded objective> — "
        "<model>/<effort> — accepted-mode reassessment`, spawn a fresh separate mode-appropriate "
        "execution lead, register it, and wait. "
        if recovery and accepted_recovery else
        "Emit `Delegating: symphony_assessor — <bounded objective> — <model>/<effort> — initial or "
        "required reassessment`, spawn one strongest-available general reasoning model at high effort "
        "with no inherited turns as read-only symphony_assessor, register it, and wait. After its "
        "accepted receipt, emit `Delegating: symphony_lead — <bounded objective> — <model>/<effort> — "
        "selected <mode> execution`, spawn a separate mode-appropriate execution lead, register it, "
        "and wait. The assessor must not implement or become the lead. "
        if run["assessment_due"] else
        "Reconcile observed agents, then emit `Delegating: symphony_lead — <bounded objective> — "
        "<model>/<effort> — selected <mode> execution`, spawn a fresh separate mode-appropriate "
        "execution lead, register it, and wait. "
    )
    return (
        f"{action} Symphony run. Run id: {run['id']}. "
        f"Objective: {run['objective']}. Project profile: {state['assessment']['profile'] or 'automatic'} "
        f"(revision {state['assessment']['revision']}). You are the thin root/session keeper; perform "
        f"only announce, spawn, register, relay, and wait control work. {route}"
        "Use these exact visible record forms: `Delegating: <role> — <bounded objective> — "
        "<model>/<effort> — <reason>`; `Waiting: <role or wave> — <bounded in-progress fact>`; "
        "`Completed: <agent id/role> — <status> — tokens <value or not exposed by host> — duration "
        "<value or not exposed by host>`. `Waiting:` may contain only observed lifecycle state. "
        f"After the host exposes an id, emit `SYMPHONY_REGISTER:{run['id']}:assessor:<agent-id>` or "
        f"`SYMPHONY_REGISTER:{run['id']}:lead:<agent-id>` as applicable. "
        "For the initial execution lead and each recovery lead, immediately end a final-channel response "
        "containing its exact registration line, without run completion. Commentary does not persist registration. "
        "After the hook acknowledges registration, call the blocking wait for that same lead. "
        "Relay exactly "
        f"`SYMPHONY_ASSESSMENT:{run['id']}:<project-profile>:<run-mode>` and "
        "`SYMPHONY_ASSESSMENT_REASON:<single bounded line>` from the terminal assessor or same-mode "
        "lead. Both receipt size fields must be small, medium, or large; automatic is a source, "
        "not a project size, and must never appear in the receipt. "
        "After the registration handoff, immediately call the host blocking wait/result operation and continue "
        "until every observed agent is terminal. Finish only with the accepted mode marker and "
        f"`<!-- {run['receipt']} -->`."
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
    usage = _usage_aggregates(state)
    assessment = state["assessment"]
    return (
        f"Symphony project enabled: {str(state['enabled']).lower()}. Active run: {run_text}. "
        f"Project profile: {assessment['profile'] or 'unassessed'}; "
        f"profile source: {assessment['source'] or 'none'}; assessment revision: {assessment['revision']}; "
        f"mode revision: {run['mode_revision'] if run else 'none'}; "
        f"reassessment due: {str(run['assessment_due']).lower() if run else 'no active run'}. "
        f"Observed final-request tokens (partial): {usage['total_tokens']}; "
        f"agents lacking final-request totals: {usage['missing_total_tokens']}."
    )


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


def _reassessment_context(run):
    return (
        f"Continue Symphony run {run['id']}. Reassessment is due: the current execution lead cheaply "
        "reassesses from current evidence. For the same mode it returns exactly "
        f"`SYMPHONY_ASSESSMENT:{run['id']}:<project-profile>:<run-mode>` and "
        "`SYMPHONY_ASSESSMENT_REASON:<single bounded line>`; the current run's root must relay both "
        "lines because an execution-lead SubagentStop receipt is not authorized. A proposed mode change "
        "or unresolved high-risk ambiguity requires a new strong assessor; do not launch duplicate work."
    )


def _record_assessment_receipt(state, run, message, now, agent_id=None):
    if not run["assessment_due"]:
        return False
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
    mode = mode.lower()
    if agent_id is None and _host_reports_child_lifecycle() is not False:
        records = {record["id"]: record for record in _agent_records(run)}
        role = "assessor" if run.get("strong_assessment_required") else "lead"
        if any(registered_run == run["id"] and registered_role == role
               and registered_id != run.get(f"{role}_agent_id")
               for registered_run, registered_role, registered_id in REGISTRATION_RE.findall(message or "")):
            return False
        authority = records.get(run.get(f"{role}_agent_id"))
        if not authority or authority["status"] != "terminal":
            return False
    if (
        not run.get("strong_assessment_required")
        and _has_accepted_assessment(run)
        and mode != run["mode"]
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
    run["mode"] = mode
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
    run["strong_assessment_required"] = False
    return True


def _register_roles(state, run, message):
    """Only the owning root's control response may assign host-observed agents."""
    records = {record["id"]: record for record in _agent_records(run)}
    changed = False
    for run_id, role, agent_id in REGISTRATION_RE.findall(message):
        if run_id != run["id"] or agent_id not in records:
            continue
        if records[agent_id].get("registered_role") not in {None, role}:
            continue
        if role == "assessor" and records[agent_id].get("assessment_superseded"):
            continue
        if role == "lead" and records[agent_id].get("lead_ineligible"):
            continue
        if any(agent_id == record["id"] for past in state["run_history"] for record in _agent_records(past)):
            continue
        key = f"{role}_agent_id"
        current_id = run.get(key)
        other_id = run.get("lead_agent_id" if role == "assessor" else "assessor_agent_id")
        if agent_id == other_id or (
            current_id and current_id != agent_id and records.get(current_id, {}).get("status") != "terminal"
        ):
            continue
        if role == "assessor" and current_id != agent_id:
            records = _require_reassessment(run, records)
            run["strong_assessment_required"] = True
        if role == "lead" and current_id != agent_id:
            # Registration may follow child dispatch or even synchronous
            # completion. Reconcile only children born in this lead's window;
            # a fresh dispatch after its terminal event remains eligible.
            for candidate in records.values():
                if (candidate["id"] != agent_id and not candidate.get("registered_role")
                        and agent_id in candidate.get("active_agent_ids_at_start", [])):
                    candidate["lead_ineligible"] = True
            if records[agent_id].pop("initial_lead_stop_reassessment", False):
                run["assessment_due"] = False
        run[key] = agent_id
        records[agent_id]["role"] = f"symphony_{role}"
        records[agent_id]["registered_role"] = role
        changed = True
    if changed:
        run["agent_records"] = records
    return changed


def _has_active_non_lead_agent(run):
    excluded = {run.get("lead_agent_id"), run.get("assessor_agent_id")}
    return any(
        record["status"] == "active" and record["id"] not in excluded
        and not record.get("initial_lead_candidate")
        for record in _agent_records(run)
    )


def _pending_initial_lead_id(run):
    if not run or run.get("lead_agent_id"):
        return None
    candidates = [
        record["id"] for record in _agent_records(run)
        if record["status"] == "terminal"
        and record.get("initial_lead_candidate")
        and record.get("initial_lead_stop_reassessment")
        and not record.get("lead_ineligible")
    ]
    return candidates[0] if len(candidates) == 1 else None


def _agent_record(payload, started_at=None):
    return {
        "id": payload["agent_id"],
        "parent_session_id": payload.get("session_id"),
        "status": "active",
        "role": payload.get("agent_type") or "not exposed by host",
        "model": payload.get("model") or "not exposed by host",
        "effort": payload.get("reasoning_effort") or payload.get("effort") or "not exposed by host",
        "started_at": started_at,
        "stopped_at": None,
    }


def _clean_usage(value):
    if not isinstance(value, dict):
        return {}
    usage = {field: value[field] for field in USAGE_FIELDS
             if type(value.get(field)) is int and value[field] >= 0}
    if type(value.get("observed_at")) is int and value["observed_at"] >= 0:
        usage["observed_at"] = value["observed_at"]
    if isinstance(value.get("source"), str) and value["source"]:
        usage["source"] = value["source"]
    if isinstance(value.get("scope"), str) and value["scope"]:
        usage["scope"] = value["scope"]
    return usage


def _usage_record(payload, now):
    response = payload.get("tool_response")
    if not isinstance(response, dict) or (
        response.get("async_launched") is True or response.get("status") == "async_launched"
    ):
        return None
    agent_id = response.get("agentId")
    if not isinstance(agent_id, str) or not agent_id:
        return None
    raw_usage = response.get("usage")
    raw_usage = raw_usage if isinstance(raw_usage, dict) else {}
    locations = {
        "final_request_total_tokens": (response, ("totalTokens", "total_tokens")),
        "final_request_input_tokens": (raw_usage, ("inputTokens", "input_tokens")),
        "final_request_output_tokens": (raw_usage, ("outputTokens", "output_tokens")),
        "final_request_cache_creation_tokens": (raw_usage, ("cacheCreationInputTokens", "cache_creation_input_tokens")),
        "final_request_cache_read_tokens": (raw_usage, ("cacheReadInputTokens", "cache_read_input_tokens")),
        "duration_ms": (response, ("totalDurationMs", "total_duration_ms")),
        "tool_uses": (response, ("totalToolUseCount", "total_tool_use_count")),
    }
    usage = {}
    for field, (container, names) in locations.items():
        for name in names:
            value = container.get(name)
            if type(value) is int and value >= 0:
                usage[field] = value
                break
    if not usage:
        return None
    return {
        "id": agent_id,
        **usage,
        "observed_at": int(now),
        "source": "claude-post-tool-use",
        "scope": "final-agent-request",
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
        usage = _clean_usage(value.get("usage"))
        if usage:
            record["usage"] = usage
        if value.get("registered_role") in {"assessor", "lead"}:
            record["registered_role"] = value["registered_role"]
        if value.get("assessment_superseded") is True:
            record["assessment_superseded"] = True
        if value.get("lead_ineligible") is True:
            record["lead_ineligible"] = True
        if value.get("initial_lead_candidate") is True:
            record["initial_lead_candidate"] = True
        if value.get("initial_lead_stop_reassessment") is True:
            record["initial_lead_stop_reassessment"] = True
        active_at_start = value.get("active_agent_ids_at_start")
        if isinstance(active_at_start, list):
            record["active_agent_ids_at_start"] = sorted({
                agent_id for agent_id in active_at_start if isinstance(agent_id, str) and agent_id
            })
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


def _usage_aggregates(state):
    runs = ([state["active_run"]] if isinstance(state.get("active_run"), dict) else [])
    runs.extend(run for run in state.get("run_history", []) if isinstance(run, dict))
    records = [record for run in runs for record in _agent_records(run)]
    totals = [record.get("usage", {}).get("final_request_total_tokens") for record in records]
    return {
        "total_tokens": sum(value for value in totals if type(value) is int),
        "missing_total_tokens": sum(type(value) is not int for value in totals),
    }


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
        lines.extend([
            "| " + " | ".join(AGENT_TABLE_HEADINGS) + " |",
            "| " + " | ".join("---" for _ in AGENT_TABLE_HEADINGS) + " |",
        ])
        for record in records:
            values = [item["id"], record["id"], record["status"]]
            values.extend(record.get(field) or "not exposed by host" for field in ("role", "model", "effort"))
            usage = _clean_usage(record.get("usage"))
            values.extend(usage.get(field, "not exposed by host") for field in USAGE_FIELDS)
            source = usage.get("source")
            scope = usage.get("scope")
            values.append(f"{source}/{scope}" if source and scope else "not exposed by host")
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


def _looks_like_control(prompt):
    stripped = (prompt or "").strip()
    return bool(RAW_CONTROL_PREFIX_RE.match(stripped) or MARKER_CONTROL_PREFIX_RE.search(stripped))


def _dry_run_task(control, task):
    if control != "start" or not task.startswith("--dry-run"):
        return False, task
    suffix = task[len("--dry-run"):]
    if suffix and not suffix[0].isspace():
        return False, task
    return True, suffix.strip()


def _active_agent_ids(run):
    observed = run.get("agents", [])
    observed = observed if isinstance(observed, list) else []
    return sorted({
        record["id"] for record in _agent_records(run) if record["status"] == "active"
    } | {agent_id for agent_id in observed if isinstance(agent_id, str)})


def _foreign_run_context(run):
    agents = _active_agent_ids(run)
    suffix = f" Active registered agents: {', '.join(agents)}." if agents else ""
    return (
        f"This run is owned by session {run.get('owner_session_id')}; this session is inspection-only."
        + suffix
    )


def _known_session_id(value):
    return isinstance(value, str) and bool(value) and value.lower() != "unknown"


def _owns_run(run, session_id):
    return _known_session_id(session_id) and session_id == run.get("owner_session_id")


def _transfer_interrupted_run(run, session_id, now):
    if (
        not run.get("interruption_recovery_eligible")
        or _active_agent_ids(run)
        or not _known_session_id(session_id)
    ):
        return False
    run["previous_owner_session_id"] = run.get("owner_session_id")
    run["owner_session_id"] = session_id
    run["ownership_transferred_at"] = int(now)
    run["interruption_recovery_eligible"] = False
    return True


def _is_terminal_control(prompt, control, task):
    return _looks_like_control(prompt) and not (
        control in {"start", "enable"} and bool(task)
    )


def _invalid_control_args(control, args):
    value = args.strip()
    return (
        control in {"help", "status", "disable"} and bool(value)
        or control == "agents" and value not in {"", "--all"}
        or control == "stop" and value not in {"", "--force"}
    )


def _handle_prompt(payload, state, now):
    prompt = payload.get("prompt") or ""
    control, task, args = _parse_prompt(prompt)
    dry_run, task = _dry_run_task(control, task)

    if (
        control is None and _looks_like_control(prompt)
        or _invalid_control_args(control, args)
    ):
        return HookResult(context="Invalid Symphony control. Use /symphony:help for valid commands.")
    if control == "start" and not task:
        return HookResult(context="Usage: /symphony:start [--dry-run] <task>")

    if control == "help":
        return HookResult()
    if control == "status":
        return HookResult(context=_status_context(state))
    if control == "agents":
        return HookResult(context=_agents_context(state, include_history="--all" in args.split()))
    run = state.get("active_run")
    foreign_run = run and not _owns_run(run, payload.get("session_id"))
    project_request = control is None or control in {"start", "enable"} and bool(task)
    if foreign_run:
        if not project_request or not _transfer_interrupted_run(run, payload.get("session_id"), now):
            return HookResult(context=_foreign_run_context(run))
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
            _require_reassessment(state["active_run"])
            state["active_run"]["strong_assessment_required"] = True
            state["active_run"]["assessor_agent_id"] = None
            for record in state["active_run"].get("agent_records", {}).values():
                if record["status"] == "terminal":
                    record["assessment_superseded"] = True
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
        if run.get("status") == "stopping":
            return HookResult(context="This Symphony run is stopping; reconcile tracked agents before continuing.")
        if project_request and _owns_run(run, payload.get("session_id")):
            run["interruption_recovery_eligible"] = False
        if control is None:
            _require_reassessment(run)
        if foreign_run:
            return HookResult(context=_bootstrap_context(
                run, state, recovery=True,
                accepted_recovery=(
                    _has_accepted_assessment(run) and not run["strong_assessment_required"]
                ), now=now,
            ))
        if run["strong_assessment_required"]:
            return HookResult(context=_bootstrap_context(run, state, now=now))
        if run["assessment_due"]:
            return HookResult(context=_reassessment_context(run))
        return HookResult(context=f"Continue Symphony run {run['id']}; do not launch a duplicate lead.")

    if start_requested or (state.get("enabled") and control is None):
        objective = task if start_requested else prompt
        state["active_run"] = _new_run(
            payload, objective, now, state["project_root"], dry_run=dry_run,
        )
        return HookResult(context=_bootstrap_context(state["active_run"], state, now=now))

    return HookResult()


def _handle_stop(payload, data_dir, project_root, now, stop_wait_seconds):
    control_receipt = CONTROL_RECEIPT_RE.search(payload.get("last_assistant_message") or "")
    if control_receipt:
        with project_lock(data_dir, project_root):
            pending_path = inspection_path(data_dir, project_root, payload.get("session_id"))
            try:
                pending = json.loads(pending_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pending = None
            state = read_project_state(data_dir, project_root, read_only=True)
            if (
                isinstance(pending, dict)
                and pending.get("nonce") == control_receipt.group(1)
                and pending.get("run_id") == (state.get("active_run") or {}).get("id")
                and pending.get("session_id") == payload.get("session_id")
                and (pending.get("turn_id") is None or pending["turn_id"] == payload.get("turn_id"))
            ):
                pending_path.unlink()
                run = state.get("active_run")
                if (pending.get("stop_requested") and run
                        and _owns_run(run, payload.get("session_id"))
                        and run.get("status") == "stopping"):
                    run["stop_acknowledged"] = not any(
                        isinstance(task, dict) and task.get("status") in {"running", "pending"}
                        for task in payload.get("background_tasks", [])
                    )
                    if run["stop_acknowledged"] and not _active_agent_ids(run):
                        _archive_run(state, "stopped", now)
                        state["active_run"] = None
                    write_project_state(data_dir, state, now)
                return HookResult()
    initial_state = read_project_state(data_dir, project_root)
    if initial_state.get("corrupt"):
        return HookResult(block=True, reason="Symphony state is corrupt; use /symphony:stop --force to recover.")
    if not initial_state.get("active_run"):
        return HookResult()
    initial_run = initial_state["active_run"]
    owner_session_id = initial_run.get("owner_session_id")
    session_id = payload.get("session_id")
    if _known_session_id(owner_session_id) and not _owns_run(initial_run, session_id):
        return HookResult(block=True, reason=_foreign_run_context(initial_run))
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
        if background_tasks and run.get("stop_acknowledged"):
            run["stop_acknowledged"] = False
            memory_changed = True
        owner_session_id = run.get("owner_session_id")
        session_id = payload.get("session_id")
        known_owner = not payload.get("agent_id") and all(
            isinstance(value, str) and value and value.lower() != "unknown"
            for value in (owner_session_id, session_id)
        )
        registration_changed = (
            _register_roles(state, run, message)
            if known_owner and session_id == owner_session_id else False
        )
        memory_changed = memory_changed or registration_changed
        assessment_changed = (
            _record_assessment_receipt(state, run, message, now)
            if known_owner and session_id == owner_session_id else False
        )
        control_ack = (
            f"Symphony registered roles: assessor={run.get('assessor_agent_id')}, lead={run.get('lead_agent_id')}. "
            if registration_changed else ""
        ) + (
            f"Symphony accepted assessment: mode={run['mode']}, mode revision={run['mode_revision']}. "
            if assessment_changed else ""
        )
        if (run["assessment_due"] and "SYMPHONY_ASSESSMENT:" in message.upper()
                and not ASSESSMENT_RE.search(message)):
            if memory_changed:
                write_project_state(data_dir, state, now)
            role = "assessor" if run["strong_assessment_required"] else "lead"
            return HookResult(block=True, reason=(
                "Invalid assessment receipt: project-profile and run-mode must each be small, medium, or large; "
                "automatic is a source, not a project size. "
                f"Ask the same {role} for a corrected receipt and relay its actual corrected result: "
                f"SYMPHONY_ASSESSMENT:{run['id']}:<project-profile>:<run-mode> and "
                "SYMPHONY_ASSESSMENT_REASON:<single bounded line>. "
                "Do not implement in the root, invent a correction, or repeat the rejected receipt."
            ))
        if background_tasks:
            if memory_changed or assessment_changed:
                write_project_state(data_dir, state, now)
            task_ids = [str(task.get("id") or task.get("task_id") or "unknown") for task in background_tasks]
            return HookResult(
                block=True,
                reason=(
                    control_ack + "Symphony detected active Claude background tasks: " + ", ".join(task_ids) +
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
                    control_ack + "Symphony still has tracked agents: " + ", ".join(agents) +
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
            if run["dry_run"]:
                return HookResult(
                    block=True,
                    reason=(
                        control_ack + "Symphony dry run remains active. Reissue one self-contained "
                        "planned report with `Root profile:`, `Capability routing:`, planned `Delegating:` "
                        "and `Completed:` records for the separate strongest/high assessor and "
                        "mode-appropriate execution lead, exactly one `<!-- SYMPHONY_MODE:<mode> -->`, "
                        f"and the exact run completion receipt `<!-- {run['receipt']} -->`."
                    ),
                )
            return HookResult(
                block=True,
                reason=(
                    control_ack + "Symphony run remains active. Reconcile worker results, integrate and verify the "
                    f"objective, then include `{run['receipt']}` in the final assistant message."
                ),
            )
        modes = MODE_RE.findall(message)
        if len(modes) != 1:
            if memory_changed or assessment_changed:
                write_project_state(data_dir, state, now)
            if run["dry_run"]:
                return HookResult(
                    block=True,
                    reason=(
                        "Symphony dry-run completion is missing its single selected mode. Reissue one "
                        "self-contained planned report with `Root profile:`, `Capability routing:`, planned "
                        "`Delegating:` and `Completed:` records for the separate strongest/high assessor "
                        "and mode-appropriate execution lead, exactly one "
                        "`<!-- SYMPHONY_MODE:<mode> -->`, and the exact run completion receipt."
                    ),
                )
            return HookResult(
                block=True,
                reason=(
                    "Symphony completion is missing its single selected mode. Reissue a "
                    "self-contained final report with the actual root profile, mode-appropriate execution "
                    "lead, applicable capability routing, exactly one of small, medium, or large, "
                    "`<!-- SYMPHONY_MODE:<mode> -->`, and the exact run completion receipt."
                ),
            )
        mode = modes[0].lower()
        if not known_owner or session_id != owner_session_id or (
            _has_accepted_assessment(run) and mode != run["mode"]
        ):
            if memory_changed or assessment_changed:
                write_project_state(data_dir, state, now)
            return HookResult(block=True, reason="Symphony completion must come from the owning root and match the accepted assessment mode.")
        if run["dry_run"]:
            normalized = message.lower().replace("completed: planned symphony_", "completed: symphony_")
            records = (
                "root profile:",
                "capability routing:",
                "delegating: symphony_assessor",
                "completed: symphony_assessor",
                "delegating: symphony_lead",
                "completed: symphony_lead",
                "symphony_mode:",
                run["receipt"].lower(),
            )
            positions = [normalized.find(record) for record in records]
            if any(position < 0 for position in positions) or positions != sorted(positions):
                write_project_state(data_dir, state, now)
                return HookResult(
                    block=True,
                    reason=(
                        "Symphony dry-run completion requires one self-contained planned report in this "
                        "order: `Root profile:`; `Capability routing:`; planned `Delegating:` and `Completed:` "
                        "records for the separate strongest/high assessor; planned `Delegating:` and "
                        "`Completed:` records for the mode-appropriate execution lead; exactly one mode "
                        "marker; and the exact run completion receipt. Reissue the complete report."
                    ),
                )
        if not run["dry_run"] and (
            not _has_accepted_assessment(run) or run["assessment_due"]
        ):
            if memory_changed or assessment_changed:
                write_project_state(data_dir, state, now)
            pending_lead = _pending_initial_lead_id(run)
            if pending_lead:
                return HookResult(
                    block=True,
                    reason=(
                        "Symphony is waiting only for the initial synchronous lead registration. "
                        f"Register the existing terminal lead with `SYMPHONY_REGISTER:{run['id']}:lead:"
                        f"{pending_lead}` in the owning root's next final-channel self-contained report, "
                        "repeating the full integrated deliverable rather than a summary, both assessor "
                        "and lead completion records with role, status, tokens, and duration, the selected "
                        "mode, and the exact run completion receipt. Do not spawn, "
                        "resume, or call Agent for a correction; no additional assessment receipt is "
                        "required for this lead's own synchronous stop."
                    ),
                )
            role = "assessor" if run["strong_assessment_required"] else "lead"
            current_role_id = run.get(f"{role}_agent_id")
            terminal_ids = [record["id"] for record in _agent_records(run)
                            if record["status"] == "terminal"
                            and (record["id"] == current_role_id if current_role_id else (
                                record.get("registered_role") in {None, role}
                                and not (role == "lead" and record.get("lead_ineligible"))
                                and not (role == "assessor" and record.get("assessment_superseded"))
                            ))]
            return HookResult(
                block=True,
                reason=(
                    "Symphony completion requires an accepted current assessment before normal project work can finish. "
                    f"In your next final response, register the current terminal {role} with "
                    f"`SYMPHONY_REGISTER:{run['id']}:{role}:<agent-id>` and relay its exact "
                    "SYMPHONY_ASSESSMENT and SYMPHONY_ASSESSMENT_REASON lines. Commentary does not persist "
                    f"registration. Eligible terminal {role} ids: {', '.join(terminal_ids) or 'none'}. "
                    f"Ask the same {role} for any missing assessment receipt. If it is unavailable, "
                    "dispatch a fresh role holder after the previous holder is terminal; existing execution-wave "
                    "children cannot be promoted to lead. Do not invent or reuse another role's receipt."
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
        if run["dry_run"]:
            run["mode"] = mode
        _archive_run(state, "completed", now)
        state["active_run"] = None
        state["warning"] = None
        write_project_state(data_dir, state, now)
        return HookResult()


def handle_event(payload, data_dir, now=None, stop_wait_seconds=None):
    # Codex child turns retain the root session_id and identify themselves separately.
    # Only lifecycle observation events may update the root's run from a child.
    if _known_session_id(payload.get("agent_id")) and payload.get("hook_event_name") in {
        "SessionStart", "UserPromptSubmit", "PreToolUse", "Stop", "Interrupt",
    }:
        return HookResult()
    current = int(time.time() if now is None else now)
    project_root = resolve_project_root(payload.get("cwd"))
    event = payload.get("hook_event_name")
    if event == "Stop":
        wait = stop_wait_seconds
        if wait is None:
            wait = int(os.environ.get("SYMPHONY_STOP_WAIT_SECONDS", "55"))
        return _handle_stop(payload, Path(data_dir), project_root, current, wait)

    prompt = payload.get("prompt") or ""
    control, task, args = _parse_prompt(prompt) if event == "UserPromptSubmit" else (None, "", "")
    _, terminal_task = _dry_run_task(control, task)
    terminal_control = event == "UserPromptSubmit" and _is_terminal_control(
        prompt, control, terminal_task,
    )
    invalid_control = (
        control is None and _looks_like_control(prompt)
        or _invalid_control_args(control, args)
        or control == "assess" and args.strip().lower()
        not in {"", "small", "medium", "large", "auto"}
    )
    read_only = (
        control in {"agents", "status", "help"}
        or invalid_control
        or control == "start" and not terminal_task
    )
    with project_lock(data_dir, project_root):
        state = read_project_state(data_dir, project_root, read_only=read_only)
        original_state = json.dumps(state, sort_keys=True)
        result = HookResult()
        if event == "PreToolUse" and payload.get("tool_name") in {"Agent", "spawn_agent"}:
            run = state.get("active_run")
            pending_lead = _pending_initial_lead_id(run)
            if run and _owns_run(run, payload.get("session_id")) and pending_lead:
                result = HookResult(block=True, reason=(
                    "Symphony must register the existing terminal lead before another spawn. "
                    f"End the owning root's next final-channel self-contained report with "
                    f"`SYMPHONY_REGISTER:{run['id']}:lead:{pending_lead}`, the full integrated deliverable "
                    "rather than a summary, both assessor and lead completion records with role, status, "
                    "tokens, and duration, the selected mode, and the exact run completion receipt. Do not "
                    "spawn, resume, or call Agent for a correction; "
                    "no additional assessment receipt is required."
                ))
            elif run and _owns_run(run, payload.get("session_id")) and run["strong_assessment_required"]:
                pending = [record["id"] for record in _agent_records(run)
                           if record.get("parent_session_id") in {None, run["owner_session_id"]}
                           and (record["status"] == "active" or (
                               not record.get("assessment_superseded")
                               and record.get("registered_role") != "lead"
                           ))]
                if pending:
                    result = HookResult(block=True, reason=(
                        "Symphony requires the assessment handoff before another spawn. "
                        "Wait for the assessor to become terminal, then end this root response in the final channel with "
                        f"`SYMPHONY_REGISTER:{run['id']}:assessor:<agent-id>` (observed ids: {', '.join(pending)}), "
                        "and its exact SYMPHONY_ASSESSMENT and SYMPHONY_ASSESSMENT_REASON lines. "
                        "Do not call another spawn or include run completion. The Stop hook will persist "
                        "the assessment and return control for execution. "
                        + " ".join(f"SYMPHONY_REGISTER:{run['id']}:assessor:{agent_id}" for agent_id in pending)
                    ))
                elif payload.get("tool_name") == "spawn_agent":
                    arguments = payload.get("tool_input") or {}
                    model = arguments.get("model") if isinstance(arguments, dict) else None
                    if not isinstance(model, str) or not model.strip():
                        result = HookResult(block=True, reason=(
                            "Symphony's assessor requires an explicit model override to the strongest "
                            "available general reasoning model at high effort. Omitting model inherits "
                            "the weak root. Retry spawn_agent with the named model from the live host "
                            "catalog and reasoning_effort=high; do not proceed with an inherited model."
                        ))
        elif event == "SessionStart":
            run = state.get("active_run")
            if run:
                if _owns_run(run, payload.get("session_id")):
                    accepted_recovery = (
                        _has_accepted_assessment(run) and not run["strong_assessment_required"]
                    )
                    _require_reassessment(run)
                    result = HookResult(context=_bootstrap_context(
                        run, state, recovery=True, accepted_recovery=accepted_recovery, now=current,
                    ))
                else:
                    result = HookResult(context=_foreign_run_context(run))
            elif state.get("enabled"):
                result = HookResult(
                    context=(
                        "Symphony is enabled for this project. The first non-control project prompt "
                        "will start a new guarded run with bounded assessment and a separate execution lead."
                    )
                )
        elif event == "UserPromptSubmit":
            pending_path = inspection_path(data_dir, project_root, payload.get("session_id"))
            pending_path.unlink(missing_ok=True)
            result = _handle_prompt(payload, state, current)
            if terminal_control:
                nonce = secrets.token_hex(16)
                _atomic_write(pending_path, {
                    "nonce": nonce,
                    "run_id": (state.get("active_run") or {}).get("id"),
                    "session_id": payload.get("session_id"),
                    "turn_id": payload.get("turn_id"),
                    **({"stop_requested": True} if (
                        control in {"stop", "disable"}
                        and not _invalid_control_args(control, args)
                        and bool(state.get("active_run"))
                        and _owns_run(state["active_run"], payload.get("session_id"))
                    ) else {}),
                })
                result.context = (result.context + "\n" if result.context else "") + (
                    "End this control response with the following single-use receipt; "
                    "do not reuse it for later project work or report run completion:\n"
                    f"<!-- SYMPHONY_CONTROL_HANDLED:{nonce} -->"
                )
        elif event == "SubagentStart":
            run = state.get("active_run")
            agent_id = payload.get("agent_id")
            records = {record["id"]: record for record in _agent_records(run)} if run else {}
            record = records.get(agent_id)
            if run and agent_id and (record is None or record["status"] == "terminal"):
                run["agents"] = sorted(set(run.get("agents", [])) | {agent_id})
                if record is None:
                    record = _agent_record(payload, current)
                    active_at_start = sorted(record["id"] for record in records.values() if record["status"] == "active")
                    if active_at_start:
                        record["active_agent_ids_at_start"] = active_at_start
                    if (
                        _has_accepted_assessment(run)
                        and not run.get("strong_assessment_required")
                        and not run.get("lead_agent_id")
                        and not active_at_start
                        and not any(candidate.get("initial_lead_candidate") for candidate in records.values())
                    ):
                        # A synchronous initial lead has no host id until its call returns.
                        # Mark the first post-assessment top-level child as a candidate so
                        # its own stop is not mistaken for a completed worker wave. Root
                        # registration remains the only role authorization.
                        record["initial_lead_candidate"] = True
                    # A replacement lead must be a fresh dispatch after the
                    # prior lead is terminal, not a child from its active wave.
                    if records.get(run.get("lead_agent_id"), {}).get("status") == "active":
                        record["lead_ineligible"] = True
                else:
                    record["status"] = "active"
                    record["stopped_at"] = None
                    record.pop("assessment_superseded", None)
                run.setdefault("agent_records", {})[agent_id] = record
                run["last_event"] = event
                run["status"] = "active"
                result = HookResult(
                    context=(
                        f"You are an assigned child in Symphony run {run['id']}; inherited root bootstrap does not apply. "
                        "Perform your assigned role directly. A symphony_assessor is read-only and does not need spawn or wait tools: "
                        "inspect the repository and return its assessment, never delegate another assessor or lead. "
                        "A symphony_lead executes and verifies its assignment. Follow explicitly assigned skills; "
                        "return the requested receipts, capability receipt, changed files, checks, and blockers. "
                        "Assessment project-profile and run-mode fields must each be small, medium, or large."
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
                    if field == "role" and agent_id in {run.get("assessor_agent_id"), run.get("lead_agent_id")}:
                        continue
                    if isinstance(exposed[field], str) and exposed[field] != "not exposed by host":
                        record[field] = exposed[field]
                record["status"] = "terminal"
                if was_active:
                    record["stopped_at"] = current
                run["agent_records"] = records if run is active else list(records.values())
                if run is active:
                    run["agents"] = sorted(set(run.get("agents", [])) - {agent_id})
                    run["last_event"] = event
                    _record_memory_receipt(run, payload.get("last_assistant_message"), current)
                    if was_active:
                        _record_assessment_receipt(
                            state, run, payload.get("last_assistant_message"), current, agent_id,
                        )
                    if (
                        was_active
                        and agent_id not in {run.get("lead_agent_id"), run.get("assessor_agent_id")}
                        and not _has_active_non_lead_agent(run)
                    ):
                        if record.get("initial_lead_candidate") and not run["assessment_due"]:
                            record["initial_lead_stop_reassessment"] = True
                            run["assessment_due"] = True
                        else:
                            _require_reassessment(run)
                    if (run.get("status") == "stopping" and run.get("stop_acknowledged")
                            and not _active_agent_ids(run)):
                        _archive_run(state, "stopped", current)
                        state["active_run"] = None
        elif event == "PostToolUse" and payload.get("tool_name") == "Agent":
            observed = _usage_record(payload, current)
            if observed:
                active = state.get("active_run")
                runs = ([active] if isinstance(active, dict) else []) + [
                    run for run in state.get("run_history", []) if isinstance(run, dict)
                ]
                owners = [
                    run for run in runs
                    if any(record["id"] == observed["id"] for record in _agent_records(run))
                ]
                if len(owners) == 1:
                    run = owners[0]
                    records = {record["id"]: record for record in _agent_records(run)}
                    record = records[observed["id"]]
                    record["usage"] = {
                        **_clean_usage(record.get("usage")),
                        **{field: observed[field] for field in USAGE_FIELDS if field in observed},
                        "observed_at": observed["observed_at"],
                        "source": observed["source"],
                        "scope": observed["scope"],
                    }
                    run["agent_records"] = records if run is active else list(records.values())
        elif event == "Interrupt":
            run = state.get("active_run")
            if run and _owns_run(run, payload.get("session_id")):
                run["last_event"] = event
                run["interrupted_at"] = current
                run["interruption_recovery_eligible"] = True
                _require_reassessment(run)
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
