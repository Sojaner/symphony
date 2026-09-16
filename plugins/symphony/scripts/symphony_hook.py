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
AGENT_BASE_HEADINGS = ("Run", "Id", "Status", "Role", "Model", "Effort")
AGENT_MEASUREMENT_FIELDS = (
    ("Final-request total tokens", "final_request_total_tokens"),
    ("Final-request input tokens", "final_request_input_tokens"),
    ("Final-request output tokens", "final_request_output_tokens"),
    ("Final-request cache creation tokens", "final_request_cache_creation_tokens"),
    ("Final-request cache read tokens", "final_request_cache_read_tokens"),
    ("Duration (ms)", "duration_ms"),
    ("Run tool uses", "tool_uses"),
)
MODE_STRATEGIES = {
    "small": "lead executes directly",
    "medium": "lead plus at most two bounded independent workers",
    "large": "dependency-aware parallel worker waves",
}
NOT_EXPOSED = "not exposed by host"
RECORD_SEPARATOR_RE = re.compile(r"[ \t]+(?:—|–|-{1,2})[ \t]+")
PLACEHOLDER_RE = re.compile(r"<(?!!--)[^<>\r\n]*>")
ROUTING_LINE_RE = re.compile(r"^[ \t]*(?:[-*][ \t]+)?(?:\*\*)?routing:(?:\*\*)?[ \t]*([^\r\n]*)$", re.IGNORECASE | re.MULTILINE)
AUTHORITY_CONTEXT = (
    "Symphony is the orchestration authority for this active run. Supporting workflow skills are "
    "bounded techniques: they return artifacts and control to the Symphony lead, must not start a "
    "second orchestration lifecycle, and must not ask the user to choose direct versus delegated "
    "execution. The accepted Symphony mode selects direct execution or delegation."
)
RAW_CONTROL_RE = re.compile(
    r"\A/symphony:(enable|disable|start|stop|status|agents|assess|help)(?:\s+([\s\S]*))?\Z",
    re.IGNORECASE,
)
SYMPHONY_SKILL_RE = re.compile(r"\A[ \t]*\$symphony:symphony\b", re.IGNORECASE)
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


def _strategy(mode):
    return MODE_STRATEGIES.get((mode or "").lower(), "mode-appropriate execution")


def _mode_announcement(mode, reason=None):
    return f"`Mode: {mode} — {_strategy(mode)} — {reason or '<reason>'}`"


def _completion_record_state(message, agent_id, role):
    """Classify the visible `Completed:` record for one registered role holder.

    Returns "valid", "placeholder" (only a literal `<...>` status was reported), or "missing".
    Any dash variant separates the segments so a copied record is not rejected on punctuation.
    """
    pattern = re.compile(
        rf"^[ \t]*(?:[-*][ \t]+)?(?:\*\*)?completed:(?:\*\*)?[ \t]*"
        rf"`?{re.escape(agent_id)}`?[ \t]*/[ \t]*`?(?:symphony_)?{role}`?"
        rf"[ \t]+(?:—|–|-{{1,2}})[ \t]+([^\r\n]*)$",
        re.IGNORECASE | re.MULTILINE,
    )
    outcome = "missing"
    for match in pattern.finditer(message):
        status = RECORD_SEPARATOR_RE.split(match.group(1).strip(), maxsplit=1)[0].strip().strip("*` \t")
        if not re.search(r"[A-Za-z0-9]", status):
            continue
        if PLACEHOLDER_RE.fullmatch(status):
            outcome = "placeholder"
            continue
        return "valid"
    return outcome


def _routing_line(state, run):
    """Copy-ready routing disclosure from the ledger; unavailable identity values are explicit."""
    records = {record["id"]: record for record in _agent_records(run)}

    def describe(agent_id):
        record = records.get(agent_id) or {}
        model = record.get("model") if record.get("model") not in (None, "", NOT_EXPOSED) else "unknown"
        effort = record.get("effort") if record.get("effort") not in (None, "", NOT_EXPOSED) else "unknown"
        return f"{agent_id} {model}/{effort}"

    history = [entry for entry in run.get("mode_history") or [] if isinstance(entry, dict)]
    reason = (history[-1].get("reason") if history else None) or state.get("assessment", {}).get("reason") or "<reason>"
    workers = [
        f"{describe(record['id'])} — <assigned job>"
        for record in records.values()
        if record["id"] not in {run.get("assessor_agent_id"), run.get("lead_agent_id")}
        and not record.get("registered_role")
    ]
    return (
        f"Routing: mode {run['mode']} ({_strategy(run['mode'])}) — {reason}; "
        f"assessor {describe(run['assessor_agent_id'])}; lead {describe(run['lead_agent_id'])}; "
        f"workers {', '.join(workers) if workers else 'none'}"
    )


def _routing_state(message):
    """Classify the visible `Routing:` disclosure and return (state, block).

    The state is "valid", "placeholder", or "missing"; the block is the disclosure text.
    """
    outcome, kept = "missing", ""
    for match in ROUTING_LINE_RE.finditer(message):
        block = match.group(1)
        # A bulleted or indented continuation belongs to the disclosure; any other line ends it.
        for line in message[match.end():].splitlines()[1:]:
            if not re.match(r"[ \t]*[-*][ \t]+\S|[ \t]+\S", line):
                break
            block += "\n" + line
        if not re.search(r"[A-Za-z0-9]", block):
            continue
        if PLACEHOLDER_RE.search(block):
            outcome, kept = "placeholder", block
            continue
        outcome, kept = "valid", block
    return outcome, kept


def _routing_mismatches(block, run):
    """Compare the structured routing disclosure with the run and host ledger."""
    records = {record["id"]: record for record in _agent_records(run)}
    mismatches = []
    plain = re.sub(r"[`*]", "", block)
    segments = [
        re.sub(r"^[ \t]*[-*][ \t]+", "", segment).strip()
        for segment in re.split(r"[;\r\n]+", plain)
        if segment.strip()
    ]
    expected_mode = f"mode {run['mode']} ({_strategy(run['mode'])})"
    if not any(segment.lower().startswith(expected_mode.lower() + " ") for segment in segments):
        mismatches.append(f"mode must read {run['mode']} ({_strategy(run['mode'])})")

    for role in ("assessor", "lead"):
        agent_id = run.get(f"{role}_agent_id")
        record = records.get(agent_id) or {}
        expected = "/".join(
            record.get(field) if record.get(field) not in (None, "", NOT_EXPOSED) else "unknown"
            for field in ("model", "effort")
        )
        routed = next((segment for segment in segments if segment.lower().startswith(role + " ")), None)
        if routed is None:
            mismatches.append(f"{role} {agent_id} {expected} is missing")
            continue
        if routed.lower() != f"{role} {agent_id} {expected}".lower():
            mismatches.append(f"{role} {agent_id} must read {expected}")

    workers = [
        record for record in records.values()
        if record["id"] not in {run.get("assessor_agent_id"), run.get("lead_agent_id")}
        and not record.get("registered_role")
    ]
    worker_segment = next((segment for segment in segments if segment.lower().startswith("workers ")), "")
    for record in workers:
        expected = "/".join(
            record.get(field) if record.get(field) not in (None, "", NOT_EXPOSED) else "unknown"
            for field in ("model", "effort")
        )
        if not re.search(
            rf"(?<![A-Za-z0-9_-]){re.escape(record['id'])}(?![A-Za-z0-9_-])[ \t]+"
            rf"{re.escape(expected)}[ \t]+(?:—|–|-{{1,2}})[ \t]+[^,;\r\n]*[A-Za-z0-9]",
            worker_segment,
            re.IGNORECASE,
        ):
            mismatches.append(f"worker {record['id']} is missing")
    return mismatches


def _requested_routing(payload):
    """Model and effort the root actually requested for a Claude Agent call, keyed by the returned id."""
    response = payload.get("tool_response")
    arguments = payload.get("tool_input")
    if not isinstance(response, dict) or not isinstance(arguments, dict):
        return None
    agent_id = response.get("agentId")
    if not isinstance(agent_id, str) or not agent_id:
        return None
    requested = {}
    model = arguments.get("model")
    if isinstance(model, str) and model.strip():
        requested["model"] = model.strip()
    effort = arguments.get("reasoning_effort") or arguments.get("effort")
    if not effort and isinstance(arguments.get("description"), str):
        labelled = re.match(r"^symphony_[a-z0-9_]+ \[[^/\]]+/([^\]]+)\]:", arguments["description"])
        effort = labelled.group(1) if labelled else None
    if isinstance(effort, str) and effort.strip():
        requested["effort"] = effort.strip()
    return (agent_id, requested) if requested else None


def _label_slug(value):
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _spawn_label_error(run, payload):
    arguments = payload.get("tool_input")
    if not isinstance(arguments, dict):
        return "Symphony role spawns require structured tool input with an explicit model/effort label."
    model = arguments.get("model")
    if not isinstance(model, str) or not model.strip():
        return "Symphony role spawns require an explicit model so the provider-visible label is truthful."
    owner_spawn = _owns_run(run, payload.get("session_id"))
    role = "assessor" if owner_spawn and run["strong_assessment_required"] else "lead" if owner_spawn else None
    effort = arguments.get("reasoning_effort") or arguments.get("effort")
    tool_name = payload.get("tool_name")
    if tool_name == "spawn_agent":
        if not isinstance(effort, str) or not effort.strip():
            return "Codex Symphony role spawns require an explicit reasoning_effort for their visible label."
        expected_role = role or "<role>"
        expected = f"symphony_{expected_role}__{_label_slug(model)}__{_label_slug(effort)}"
        task_name = arguments.get("task_name")
        if role:
            valid = task_name == expected
        else:
            valid = isinstance(task_name, str) and bool(re.fullmatch(
                rf"symphony_[a-z0-9_]+__{re.escape(_label_slug(model))}__{re.escape(_label_slug(effort))}",
                task_name,
            ))
        return None if valid else f"Use provider-visible Codex task_name `{expected}` for this spawn."
    description = arguments.get("description")
    expected_role = role or "<role>"
    expected_effort = effort if isinstance(effort, str) and effort.strip() else (
        "high" if role == "assessor" else "<effort>"
    )
    expected = f"symphony_{expected_role} [{model}/{expected_effort}]:"
    role_pattern = re.escape(role) if role else "[a-z0-9_]+"
    if expected_effort != "<effort>":
        pattern = rf"^symphony_{role_pattern} \[{re.escape(model)}/{re.escape(expected_effort)}\]:"
    elif role:
        pattern = rf"^symphony_{re.escape(role)} \[{re.escape(model)}/[a-z0-9_-]+\]:"
    else:
        pattern = rf"^symphony_[a-z0-9_]+ \[{re.escape(model)}/[a-z0-9_-]+\]:"
    return None if isinstance(description, str) and re.match(pattern, description) else (
        f"Start the provider-visible Claude description with `{expected}` for this spawn."
    )


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
        f"Reconcile observed agents, announce {_mode_announcement(run['mode'])}, then emit "
        "`Delegating: lead [<model>/<effort>] — pending — <bounded objective>`, spawn a fresh "
        "separate mode-appropriate execution lead, and wait. "
        if recovery and accepted_recovery else
        "Emit `Delegating: assessor [<model>/<effort>] — pending — <bounded objective>`, spawn one "
        "strongest-available general reasoning model at high effort "
        "with no inherited turns as read-only symphony_assessor, and wait. After its "
        "accepted receipt, announce `Mode: <mode> — <strategy> — <reason>`, "
        "emit `Delegating: lead [<model>/<effort>] — pending — <bounded objective>`, spawn a "
        "separate mode-appropriate execution lead, "
        "and wait. The assessor must not implement or become the lead. "
        if run["assessment_due"] else
        f"Reconcile observed agents, announce {_mode_announcement(run['mode'])}, then emit "
        "`Delegating: lead [<model>/<effort>] — pending — <bounded objective>`, "
        "spawn a fresh separate mode-appropriate execution lead, and wait. "
    )
    return (
        f"{action} Symphony run. Run id: {run['id']}. "
        f"Objective: {run['objective']}. Project profile: {state['assessment']['profile'] or 'automatic'} "
        f"(revision {state['assessment']['revision']}). You are the thin root/session keeper; perform "
        f"only announce, spawn, bind/register, relay, and wait control work. {AUTHORITY_CONTEXT} {route}"
        "Make provider labels identify routing: Codex `task_name` is "
        "`symphony_<role>__<model-slug>__<effort-slug>`; Claude `description` starts "
        "`symphony_<role> [<model>/<effort>]:`. Prefix root progress with `thin orchestrator "
        "[<model>/<effort>]:` when trusted runtime metadata exposes both values; otherwise use "
        "`thin orchestrator:`. After each observed lifecycle change, before every wait, and in the final "
        "response, replay one cumulative `Delegation log:` derived from the ledger. It contains one "
        "`Delegating: <role> [<model>/<effort>] — <agent-id> — <objective>` line and one current "
        "`Waiting:` or `Completed:` line per agent. Never emit a standalone `Waiting:` update. Add tokens or duration "
        "only when exposed. Announce `Mode: <mode> — <strategy> — <reason>` once the hook accepts an "
        "assessment. The final completion response must be self-contained: repeat the integrated "
        "deliverable, cumulative delegation log, both assessor and lead `Completed: "
        "<agent-id>/<role> — <status>` summary records, and a `Routing:` line naming the mode "
        "strategy plus each agent's actual model/effort and assigned job from the lead's delegation "
        "summary, a `Verification:` line, the selected mode, and the exact completion receipt. "
        "Codex `spawn_agent` calls are registered automatically by binding the root's pre-tool request "
        "to the next observed child UUID; wait in the same turn and use the `agent_id` returned by the child. "
        "Never guess an id from a task name. On a host without automatic binding, register only an exact "
        f"host-returned id with `SYMPHONY_REGISTER:{run['id']}:<role>:<agent-id>`. "
        "Relay exactly "
        f"`SYMPHONY_ASSESSMENT:{run['id']}:<project-profile>:<run-mode>` and "
        "`SYMPHONY_ASSESSMENT_REASON:<single bounded line>` from the terminal assessor or same-mode "
        "lead. Both receipt size fields must be small, medium, or large; automatic is a source, "
        "not a project size, and must never appear in the receipt. "
        "Immediately call the host blocking wait/result operation after every spawn and continue until every "
        "observed agent is terminal. Finish only with the accepted mode marker and "
        f"`<!-- {run['receipt']} -->`."
    )


def _status_context(state):
    run = state.get("active_run")
    if not run:
        run_text = "none"
    else:
        run_text = (
            f"{run['id']} ({run['status']}), owner={run['owner_session_id']}, "
            f"mode={run.get('mode') or 'unselected'}"
            + (f" ({_strategy(run['mode'])})" if run.get("mode") else "")
            + f", agents={','.join(run.get('agents', [])) or 'none'}"
        )
    usage = _usage_aggregates(state)
    assessment = state["assessment"]
    context = (
        f"Symphony project enabled: {str(state['enabled']).lower()}. Active run: {run_text}. "
        f"Project profile: {assessment['profile'] or 'unassessed'}; "
        f"profile source: {assessment['source'] or 'none'}; assessment revision: {assessment['revision']}; "
        f"mode revision: {run['mode_revision'] if run else 'none'}; "
        f"reassessment due: {str(run['assessment_due']).lower() if run else 'no active run'}."
    )
    if usage["observed_totals"]:
        context += f" Observed final-request tokens (partial): {usage['total_tokens']}."
    return context


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
    """Assign host-observed agents from an owner relay or Codex lifecycle binding."""
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
        "observed_totals": sum(type(value) is int for value in totals),
    }


def _measurement_values(record):
    usage = _clean_usage(record.get("usage"))
    values = {field: usage.get(field) for _, field in AGENT_MEASUREMENT_FIELDS}
    if type(values["duration_ms"]) is not int:
        started = record.get("started_at")
        stopped = record.get("stopped_at")
        if type(started) is int and type(stopped) is int and stopped >= started:
            values["duration_ms"] = (stopped - started) * 1_000
    return usage, values


def _agent_display_label(run, record):
    if record["id"] == run.get("assessor_agent_id"):
        role = "assessor"
    elif record["id"] == run.get("lead_agent_id"):
        role = "lead"
    else:
        observed = record.get("role")
        role = f"worker:{observed}" if observed not in (None, "", NOT_EXPOSED) else "worker"
    model = record.get("model")
    effort = record.get("effort")
    identity = [value for value in (model, effort) if value not in (None, "", NOT_EXPOSED)]
    return f"{role} [{'/'.join(identity)}]" if identity else role


def _delegation_snapshot(run):
    lines = ["Delegation log:"]
    records = sorted(
        _agent_records(run),
        key=lambda record: (record.get("started_at") or 0, record["id"]),
    )
    for record in records:
        label = _agent_display_label(run, record)
        lines.append(f"- Delegating: {label} — {record['id']} — {run['objective']}")
        state = "Completed" if record["status"] == "terminal" else "Waiting"
        lines.append(f"- {state}: {label} — {record['id']} — {record['status']}")
    return "\n".join(lines)


def _delegation_log_matches(message, run):
    plain = message.replace("`", "").replace("*", "")
    return all(
        line.replace("`", "").replace("*", "") in plain
        for line in _delegation_snapshot(run).splitlines()
    )


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
        prepared = [(record, *_measurement_values(record)) for record in records]
        measurements = [
            (heading, field) for heading, field in AGENT_MEASUREMENT_FIELDS
            if any(type(values.get(field)) is int for _, _, values in prepared)
        ]
        include_source = any(usage.get("source") or usage.get("scope") for _, usage, _ in prepared)
        headings = list(AGENT_BASE_HEADINGS) + [heading for heading, _ in measurements]
        if include_source:
            headings.append("Usage source/token scope")
        lines.extend([
            "| " + " | ".join(headings) + " |",
            "| " + " | ".join("---" for _ in headings) + " |",
        ])
        for record, usage, values_by_field in prepared:
            values = [item["id"], record["id"], record["status"]]
            values.extend(record.get(field) or "not exposed by host" for field in ("role", "model", "effort"))
            values.extend(
                "" if values_by_field.get(field) is None else values_by_field[field]
                for _, field in measurements
            )
            if include_source:
                source = usage.get("source")
                scope = usage.get("scope")
                values.append(f"{source}/{scope}" if source and scope else source or scope or "")
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
    return (_looks_like_control(prompt) or (
        SYMPHONY_SKILL_RE.match(prompt) and control is not None
    )) and not (
        control in {"start", "enable"} and bool(task)
    )


def _invalid_control_args(control, args):
    value = args.strip()
    return (
        control in {"help", "status", "disable"} and bool(value)
        or control == "agents" and value not in {"", "--all"}
        or control == "stop" and value not in {"", "--force"}
    )


def _parse_user_prompt(prompt):
    control, task, args = _parse_prompt(prompt)
    skill_match = SYMPHONY_SKILL_RE.match(prompt)
    if control is not None or not skill_match:
        return control, task, args
    skill_input = prompt[skill_match.end():].strip()
    candidate = _parse_prompt(f"/symphony:{skill_input.lstrip('/')}")
    candidate_control, _, candidate_args = candidate
    if candidate_control is None:
        return None, "", ""
    if skill_input.startswith("/") or (
        not _invalid_control_args(candidate_control, candidate_args)
        and not (
            candidate_control == "assess"
            and candidate_args.strip().lower() not in {"", "small", "medium", "large", "auto"}
        )
    ):
        return candidate
    return None, "", ""


def _handle_prompt(payload, state, now):
    prompt = payload.get("prompt") or ""
    skill_match = SYMPHONY_SKILL_RE.match(prompt)
    skill_input = prompt[skill_match.end():].strip() if skill_match else ""
    control, task, args = _parse_user_prompt(prompt)
    dry_run, task = _dry_run_task(control, task)
    skill_task = skill_input if skill_match and control is None else ""

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

    start_requested = (
        control in {"start", "enable"} and bool(task)
    ) or bool(skill_task)
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
        objective = (skill_task or task) if start_requested else prompt
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
            f"Symphony accepted assessment: mode={run['mode']} ({_strategy(run['mode'])}), "
            f"mode revision={run['mode_revision']}, reason: {run['mode_history'][-1]['reason']}. "
            f"Announce {_mode_announcement(run['mode'], run['mode_history'][-1]['reason'])} visibly before "
            f"`Delegating: symphony_lead — <bounded objective> — <model>/<effort> — selected {run['mode']} execution`. "
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
                    control_ack + "Repeat this cumulative snapshot in the next visible progress update so "
                    "provider message replacement cannot hide earlier delegations:\n" +
                    _delegation_snapshot(run) + "\nCall the host's blocking wait/result tool for tracked agents: " +
                    ", ".join(agents) + "."
                ),
            )
        if run.pop("pending_spawn", None) is not None:
            memory_changed = True
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
                    "objective. Supporting workflow plans return to the lead: you must not ask the user to choose "
                    "direct versus delegated execution because the accepted Symphony mode makes that choice. "
                    f"Then include `{run['receipt']}` in the final assistant message."
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
                        "and lead completion records with role and status, plus tokens or duration only when "
                        "exposed, the selected "
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
        if not run["dry_run"] and run.get("assessor_agent_id") and run.get("lead_agent_id"):
            record_states = {
                role: _completion_record_state(message, run[f"{role}_agent_id"], role)
                for role in ("assessor", "lead")
            }
            routing_state, routing_block = _routing_state(message)
            routing_mismatches = _routing_mismatches(routing_block, run) if routing_state == "valid" else []
            visible_verification = re.search(
                r"(?mi)^[ \t]*(?:[-*][ \t]+)?(?:\*\*)?verification:(?:\*\*)?"
                r"[^\r\n]*[A-Za-z0-9][^\r\n]*$",
                message,
            )
            if (any(value != "valid" for value in record_states.values())
                    or routing_state != "valid" or routing_mismatches or not visible_verification):
                if memory_changed or assessment_changed:
                    write_project_state(data_dir, state, now)
                placeholder_roles = [role for role, value in record_states.items() if value == "placeholder"]
                return HookResult(block=True, reason=(
                    "Symphony completion requires one self-contained final report. Repeat the integrated "
                    "deliverable and both assessor and lead completion records. Use these exact registered "
                    "identities without editing them, replacing only `<status>` with each agent's observed "
                    "terminal status: "
                    f"`Completed: {run['assessor_agent_id']}/assessor — <status>` and "
                    f"`Completed: {run['lead_agent_id']}/lead — <status>`. "
                    + (
                        "Your last report still carried the literal `<status>` placeholder for the "
                        f"{' and '.join(placeholder_roles)} record; a placeholder is not a status. "
                        if placeholder_roles else ""
                    )
                    + "Add token or duration segments only when exposed. Include this routing disclosure, "
                    "replacing every `<...>` placeholder with the actual model, effort, reason, or assigned "
                    "job used, taken from your spawn calls and the lead's delegation summary: "
                    f"`{_routing_line(state, run)}`. "
                    + (
                        "Your last `Routing:` line still contained `<...>` placeholders. "
                        if routing_state == "placeholder" else ""
                    )
                    + (
                        "Your last `Routing:` line disagrees with the host ledger: "
                        + "; ".join(routing_mismatches) + ". Report the ledger values exactly. "
                        if routing_mismatches else ""
                    )
                    + "Then include a `Verification:` line with authoritative evidence, the "
                    "selected mode, and the exact run completion receipt."
                ))
            if not _delegation_log_matches(message, run):
                if memory_changed or assessment_changed:
                    write_project_state(data_dir, state, now)
                return HookResult(block=True, reason=(
                    "Symphony completion must preserve the cumulative delegation history. Include this "
                    "copy-ready block in the self-contained final report:\n" + _delegation_snapshot(run)
                ))
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
    control, task, args = _parse_user_prompt(prompt) if event == "UserPromptSubmit" else (None, "", "")
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
                    "rather than a summary, both assessor and lead completion records with role and status, "
                    "plus tokens or duration only when exposed, the selected mode, and the exact run completion "
                    "receipt. Do not "
                    "spawn, resume, or call Agent for a correction; "
                    "no additional assessment receipt is required."
                ))
            elif (run and _owns_run(run, payload.get("session_id"))
                  and payload.get("tool_name") == "spawn_agent"
                  and isinstance(run.get("pending_spawn"), dict)):
                result = HookResult(block=True, reason=(
                    "A prior Symphony role spawn is still awaiting its SubagentStart lifecycle event. "
                    "Do not dispatch another root role concurrently; wait for that child to start. If the "
                    "spawn failed, end this response so the Stop hook can discard the orphaned binding, "
                    "then retry."
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
            if run and not result.block:
                label_error = _spawn_label_error(run, payload)
                if label_error:
                    result = HookResult(block=True, reason=label_error)
            if (run and _owns_run(run, payload.get("session_id"))
                    and payload.get("tool_name") == "spawn_agent" and not result.block):
                arguments = payload.get("tool_input") or {}
                arguments = arguments if isinstance(arguments, dict) else {}
                run["pending_spawn"] = {
                    "role": "assessor" if run["strong_assessment_required"] else "lead",
                    "model": arguments.get("model"),
                    "effort": arguments.get("reasoning_effort") or arguments.get("effort"),
                }
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
                pending_spawn = (
                    run.pop("pending_spawn", None)
                    if payload.get("session_id") == run.get("owner_session_id") else None
                )
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
                        # explicit registration remains the fallback authorization.
                        record["initial_lead_candidate"] = True
                    # A replacement lead must be a fresh dispatch after the
                    # prior lead is terminal, not a child from its active wave.
                    if records.get(run.get("lead_agent_id"), {}).get("status") == "active":
                        record["lead_ineligible"] = True
                else:
                    record["status"] = "active"
                    record["stopped_at"] = None
                    record.pop("assessment_superseded", None)
                if isinstance(pending_spawn, dict):
                    for field in ("model", "effort"):
                        value = pending_spawn.get(field)
                        if isinstance(value, str) and value and record.get(field) in (None, "", NOT_EXPOSED):
                            record[field] = value
                run.setdefault("agent_records", {})[agent_id] = record
                role = pending_spawn.get("role") if isinstance(pending_spawn, dict) else None
                if role in {"assessor", "lead"}:
                    _register_roles(
                        state, run, f"SYMPHONY_REGISTER:{run['id']}:{role}:{agent_id}",
                    )
                run["last_event"] = event
                run["status"] = "active"
                result = HookResult(
                    context=(
                        f"You are an assigned child in Symphony run {run['id']}; inherited root bootstrap does not apply. "
                        f"Your host agent id is `{agent_id}`; include `agent_id: {agent_id}` in your result. "
                        f"Perform your assigned role directly. {AUTHORITY_CONTEXT} A symphony_assessor is read-only and does not need spawn or wait tools: "
                        "inspect the repository and return its assessment, never delegate another assessor or lead. "
                        "A symphony_lead executes and verifies its assignment and ends its result with "
                        "`Delegation summary:` listing each delegated worker as `<agent id> — <model>/<effort> — "
                        "<assigned job> — <outcome>`, or `Delegation summary: none`. Follow explicitly assigned skills; "
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
            requested = _requested_routing(payload)
            active = state.get("active_run")
            runs = ([active] if isinstance(active, dict) else []) + [
                run for run in state.get("run_history", []) if isinstance(run, dict)
            ]
            for agent_id, observation in ((observed["id"], observed) if observed else (None, None),
                                          requested or (None, None)):
                if agent_id is None:
                    continue
                owners = [
                    run for run in runs
                    if any(record["id"] == agent_id for record in _agent_records(run))
                ]
                if len(owners) != 1:
                    continue
                run = owners[0]
                records = {record["id"]: record for record in _agent_records(run)}
                record = records[agent_id]
                if observation is observed:
                    record["usage"] = {
                        **_clean_usage(record.get("usage")),
                        **{field: observed[field] for field in USAGE_FIELDS if field in observed},
                        "observed_at": observed["observed_at"],
                        "source": observed["source"],
                        "scope": observed["scope"],
                    }
                else:
                    # The request parameters are the root's own routing decision; a value the
                    # host exposed on the child's lifecycle events keeps precedence.
                    for field, value in observation.items():
                        if record.get(field) in (None, "", NOT_EXPOSED):
                            record[field] = value
                run["agent_records"] = records if run is active else list(records.values())
        elif event == "Interrupt":
            run = state.get("active_run")
            if run and _owns_run(run, payload.get("session_id")):
                run.pop("pending_spawn", None)
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
