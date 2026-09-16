# Symphony 1.0 Clean-Room Rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Symphony's receipt-driven implementation with a provider-neutral lifecycle reducer, capability-aware routing, verified hook heartbeat, and native Codex/Claude adapters released as 1.0.0.

**Architecture:** A small Python standard-library package owns canonical state, routing, persistence, and hook responses. Thin provider manifests and one hook entry point translate host payloads into reducer events; skills and Claude commands instruct roles without controlling lifecycle through response formatting.

**Tech Stack:** Python 3.11+ standard library, `unittest`, JSON plugin manifests/hooks, Markdown skills and Claude commands, GitHub Actions, Codex CLI, Claude Code CLI.

**Spec:** `docs/superpowers/specs/2026-09-16-symphony-1-0-clean-room-rewrite-design.md`

## Global Constraints

- Clean-room implementation: do not inspect or copy superseded production code, tests, evals, or design documents.
- Preserve only project enablement and user configuration from pre-1.0 state; archive incompatible active-run data.
- Hooks enforce observed lifecycle state only; no final-answer receipt parsing or prose-format enforcement.
- Use only the Python standard library in production.
- Provider hook commands use `${PLUGIN_ROOT}` or `${CLAUDE_PLUGIN_ROOT}` and never absolute versioned cache paths.
- Missing token, duration, model, or effort facts are omitted rather than inferred.
- Ponytail is the cross-cutting simplicity constraint.
- Context7 is used only for dated current provider/model/API facts.
- Compound Engineering owns plan execution, review, shipping, and CI observation.
- Superpowers supplies brainstorming, TDD, and verification disciplines without reopening Symphony's route.
- Release target is `1.0.0`.
- Preserve unrelated `.codex/config.toml`; never stage it.

---

## Target file map

```text
plugins/symphony/
├── .claude-plugin/plugin.json          # Claude package identity
├── .codex-plugin/plugin.json           # Codex package identity and hook entry
├── commands/                           # Claude-native semantic controls
│   ├── agents.md
│   ├── bypass.md
│   ├── disable.md
│   ├── enable.md
│   ├── help.md
│   ├── reassess.md
│   ├── start.md
│   ├── status.md
│   └── stop.md
├── hooks/
│   ├── codex.json                      # Codex-supported lifecycle events
│   └── hooks.json                      # Claude-supported lifecycle events
├── scripts/
│   └── symphony_hook.py                # stdin/stdout entry point only
├── symphony/
│   ├── __init__.py
│   ├── adapters.py                     # canonical input/output per provider
│   ├── model.py                        # immutable domain records + JSON conversion
│   ├── reducer.py                      # pure lifecycle transitions
│   ├── routing.py                      # fixed matrix + capability resolution
│   ├── runtime.py                      # event handling and model-visible guidance
│   └── store.py                        # project keys, locks, atomic persistence/migration
├── skills/symphony/
│   ├── SKILL.md                        # thin-root orchestration procedure
│   └── references/
│       ├── capability-routing.md       # refresh/cache/tool routing rules
│       ├── provider-activation.md      # trust/reload/heartbeat UX
│       └── role-contracts.md           # assessor/lead/worker/consultant packets
└── tests/
    ├── fixtures/                       # shared Codex/Claude event contracts
    ├── test_adapters.py
    ├── test_package.py
    ├── test_reducer.py
    ├── test_routing.py
    ├── test_runtime.py
    └── test_store.py
```

The old scripts, tests, evals, commands, references, and superseded design/plan documents are deleted before production implementation begins. The approved spec and this plan remain the only implementation knowledge sources.

---

### Task 1: Establish the clean-room package contract

**Files:**
- Delete: `plugins/symphony/scripts/*`
- Delete: `plugins/symphony/tests/*`
- Delete: `plugins/symphony/evals/*`
- Delete: superseded `docs/superpowers/plans/*` and `docs/superpowers/specs/*`, excluding this plan and its spec
- Create: `plugins/symphony/tests/test_package.py`
- Create: `plugins/symphony/symphony/__init__.py`
- Modify: `plugins/symphony/.codex-plugin/plugin.json`
- Modify: `plugins/symphony/.claude-plugin/plugin.json`
- Modify: `plugins/symphony/hooks/codex.json`
- Modify: `plugins/symphony/hooks/hooks.json`

**Interfaces:**
- Produces: installable version `1.0.0`; hook command `python3 "${PLUGIN_ROOT}/scripts/symphony_hook.py"` for Codex and `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/symphony_hook.py"` for Claude.
- Produces: package constant `PLUGIN_VERSION = "1.0.0"` and `HOOK_SCHEMA_VERSION = 1`.

- [ ] **Step 1: Remove superseded artifacts without reading them**

Use `apply_patch` deletion and preserve only the approved spec, this plan, logo, manifests, and directories named in the target map.

- [ ] **Step 2: Write failing package tests**

```python
def test_both_manifests_are_version_1_0_0():
    assert codex_manifest()["version"] == "1.0.0"
    assert claude_manifest()["version"] == "1.0.0"

def test_hook_commands_are_plugin_root_relative_and_materialized():
    for provider, manifest in hook_manifests().items():
        for command in command_handlers(manifest):
            assert "PLUGIN_ROOT" in command
            assert "/cache/" not in command
            assert referenced_script(command).is_file(), provider

def test_hook_manifests_contain_only_supported_events():
    assert set(codex_hooks()) == {
        "SessionStart", "UserPromptSubmit", "PreToolUse",
        "SubagentStart", "SubagentStop", "Stop", "Interrupt"
    }
    assert set(claude_hooks()) == {
        "SessionStart", "UserPromptSubmit", "PreToolUse",
        "SubagentStart", "SubagentStop", "PostToolUse", "Stop"
    }
```

- [ ] **Step 3: Run the package test and verify RED**

Run: `python3 -m unittest plugins.symphony.tests.test_package -v`  
Expected: failures for missing 1.0 package and entry script.

- [ ] **Step 4: Write minimal manifests and hook files**

Use one short command per event. Codex `Interrupt` timeout is `3`; ordinary hooks use `10`; Stop uses `30`. Declare `Instructions` and `Lifecycle hooks` capabilities. Do not add post-install scripts or a daemon.

- [ ] **Step 5: Run package tests and validate both providers**

Run:

```bash
python3 -m unittest plugins.symphony.tests.test_package -v
claude plugin validate ./plugins/symphony
git diff --check
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add -A -- docs/superpowers plugins/symphony
git commit -m "refactor: establish Symphony 1.0 clean-room package"
```

---

### Task 2: Define the domain model and atomic store

**Files:**
- Create: `plugins/symphony/symphony/model.py`
- Create: `plugins/symphony/symphony/store.py`
- Create: `plugins/symphony/tests/test_store.py`

**Interfaces:**
- Produces: `Event`, `Action`, `Delegation`, `RunState`, `ProjectState`, and `CapabilitySnapshot` dataclasses.
- Produces: `StateStore(root: Path)`, `load(project: Path) -> ProjectState`, `save(project: Path, state: ProjectState) -> None`, and `project_key(project: Path) -> str`.

- [ ] **Step 1: Write failing serialization and persistence tests**

Cover round-trip JSON, canonical project keys, atomic replacement, a retained 20-run limit, corrupt-state preservation, and pre-1.0 migration that imports only enablement/configuration.

```python
def test_corrupt_state_is_preserved_and_rebuilt(tmp_path):
    path = state_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("not-json")
    state = StateStore(tmp_path / "data").load(tmp_path)
    assert state.needs_reassessment is True
    assert list(path.parent.glob(path.name + ".corrupt-*"))
```

- [ ] **Step 2: Run store tests and verify RED**

Run: `python3 -m unittest plugins.symphony.tests.test_store -v`  
Expected: import failure for the missing domain package.

- [ ] **Step 3: Implement immutable records and explicit JSON converters**

Use dataclasses, plain dictionaries, UTC ISO timestamps, and schema version `1`. Do not use pickle, a database, or generic reflection-based migration.

- [ ] **Step 4: Implement atomic persistence**

Write a sibling temporary file, `flush`, `fsync`, and `os.replace`. Use a per-project lock file with `fcntl.flock` on Unix and a process-local lock fallback where `fcntl` is unavailable.

- [ ] **Step 5: Verify GREEN**

Run: `python3 -m unittest plugins.symphony.tests.test_store -v`  
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add plugins/symphony/symphony/model.py plugins/symphony/symphony/store.py plugins/symphony/tests/test_store.py
git commit -m "feat: add durable Symphony lifecycle state"
```

---

### Task 3: Implement the pure lifecycle reducer

**Files:**
- Create: `plugins/symphony/symphony/reducer.py`
- Create: `plugins/symphony/tests/test_reducer.py`

**Interfaces:**
- Consumes: domain records from Task 2.
- Produces: `reduce(state: ProjectState, event: Event) -> tuple[ProjectState, tuple[Action, ...]]`.
- Produces event kinds: `session_heartbeat`, `enable`, `disable`, `bypass`, `task_received`, `assessment_requested`, `assessment_accepted`, `lead_started`, `delegation_updated`, `lead_completed`, `interrupt`, `resume_reconciled`, `reassess`, `stop_requested`, and `force_stop`.

- [ ] **Step 1: Write table-driven failing transition tests**

Include enablement persistence, one-shot runs, inert controls, owner generations, active-child Stop blocking, completion permission, replay idempotency, interruption recovery, safe lead replacement, disable/archive ordering, and force stop.

```python
def test_normal_stop_is_blocked_while_a_child_is_active():
    state = running_state(delegations=[active_worker("w1")])
    next_state, actions = reduce(state, event("stop_requested"))
    assert next_state == state
    assert actions == (Action("block_stop", {"active": ["w1"]}),)
```

- [ ] **Step 2: Run reducer tests and verify RED**

Run: `python3 -m unittest plugins.symphony.tests.test_reducer -v`  
Expected: reducer import failure.

- [ ] **Step 3: Implement the smallest transition table**

Use one dispatcher dictionary from event kind to a focused function. Reject unknown events without changing state. Record processed event ids in a bounded deque so replay returns no actions.

- [ ] **Step 4: Verify GREEN**

Run: `python3 -m unittest plugins.symphony.tests.test_reducer -v`  
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add plugins/symphony/symphony/reducer.py plugins/symphony/tests/test_reducer.py
git commit -m "feat: add deterministic Symphony lifecycle reducer"
```

---

### Task 4: Implement capability snapshots and the nine-cell router

**Files:**
- Create: `plugins/symphony/symphony/routing.py`
- Create: `plugins/symphony/tests/test_routing.py`

**Interfaces:**
- Produces: `Assessment(size, complexity, risk, rationale, topology)`.
- Produces: `Route(lead_tier, lead_effort, execution, consultation)`.
- Produces: `route_for(assessment: Assessment) -> Route`.
- Produces: `resolve_tier(route: Route, snapshot: CapabilitySnapshot) -> ResolvedRoute`.
- Produces: `snapshot_is_stale(snapshot, now, max_age=timedelta(hours=24)) -> bool`.

- [ ] **Step 1: Write one failing matrix test and resolver edge cases**

Assert all nine exact rows from the spec, risk elevation without axis mutation, unsupported effort fallback, relative tier selection, stale cache behavior, and conservative fallback when no suitable assessor exists.

- [ ] **Step 2: Run routing tests and verify RED**

Run: `python3 -m unittest plugins.symphony.tests.test_routing -v`  
Expected: routing import failure.

- [ ] **Step 3: Implement the literal matrix and resolver**

Use a dictionary keyed by `(size, complexity)`. Sort model capabilities by declared relative tier and choose the least expensive entry satisfying the abstract route. Never hard-code current provider model names into the matrix.

- [ ] **Step 4: Verify GREEN**

Run: `python3 -m unittest plugins.symphony.tests.test_routing -v`  
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add plugins/symphony/symphony/routing.py plugins/symphony/tests/test_routing.py
git commit -m "feat: add capability-aware Symphony routing"
```

---

### Task 5: Add provider adapters and activation heartbeat

**Files:**
- Create: `plugins/symphony/symphony/adapters.py`
- Create: `plugins/symphony/tests/fixtures/codex/*.json`
- Create: `plugins/symphony/tests/fixtures/claude/*.json`
- Create: `plugins/symphony/tests/test_adapters.py`

**Interfaces:**
- Produces: `detect_provider(payload: dict) -> str`.
- Produces: `event_from_payload(provider: str, payload: dict) -> Event`.
- Produces: `render(provider: str, actions: tuple[Action, ...]) -> HookResult`.
- Produces: `HookResult(stdout: str, stderr: str, exit_code: int)`.

- [ ] **Step 1: Write shared failing contract tests**

Fixtures cover SessionStart, UserPromptSubmit, PreToolUse spawn, SubagentStart, SubagentStop, Stop, Codex Interrupt, and Claude PostToolUse. Test malformed JSON as a non-blocking fault and unknown events as no-ops.

Assert Codex output uses `hookSpecificOutput.additionalContext`, `decision:block` for continuation, and JSON stdout on Stop. Assert Claude uses event-appropriate `additionalContext` and Stop blocking semantics.

- [ ] **Step 2: Run adapter tests and verify RED**

Run: `python3 -m unittest plugins.symphony.tests.test_adapters -v`  
Expected: adapter import failure.

- [ ] **Step 3: Implement event normalization and output rendering**

Trust only provider fields documented in the spec baseline. Never parse transcript files. Normalize role names from observed agent metadata; preserve unknown values rather than guessing.

- [ ] **Step 4: Implement heartbeat actions**

Both SessionStart and UserPromptSubmit emit `session_heartbeat` with session id, plugin version, hook schema, provider, and current time. `guarded` is rendered only after the matching heartbeat is durably saved.

- [ ] **Step 5: Verify GREEN**

Run: `python3 -m unittest plugins.symphony.tests.test_adapters -v`  
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add plugins/symphony/symphony/adapters.py plugins/symphony/tests/fixtures plugins/symphony/tests/test_adapters.py
git commit -m "feat: normalize Codex and Claude hook events"
```

---

### Task 6: Wire runtime behavior and compact visibility

**Files:**
- Create: `plugins/symphony/symphony/runtime.py`
- Create: `plugins/symphony/scripts/symphony_hook.py`
- Create: `plugins/symphony/tests/test_runtime.py`

**Interfaces:**
- Consumes: adapters, reducer, router, and store.
- Produces: `handle(payload: dict, environ: Mapping[str, str]) -> HookResult`.
- Produces: `compact_delegations(state: ProjectState, limit: int = 5) -> tuple[Delegation, ...]`.
- Produces: `main() -> int` hook entry point.

- [ ] **Step 1: Write failing end-to-end runtime tests**

Cover project enablement, auto-governed task injection, explicit one-shot, bypass, status, agents/all retention, reassess, disable, malformed controls, first pending-verification notice only, current heartbeat guarded state, cumulative latest status, five-record priority, missing metrics omission, Stop continuation, and interrupt recording.

```python
def test_compact_status_prioritizes_failed_then_active_then_recent_completed():
    rows = compact_delegations(state_with_six_mixed_delegations())
    assert len(rows) == 5
    assert [row.status for row in rows[:2]] == ["failed", "working"]
```

- [ ] **Step 2: Run runtime tests and verify RED**

Run: `python3 -m unittest plugins.symphony.tests.test_runtime -v`  
Expected: runtime import failure.

- [ ] **Step 3: Implement the orchestration guidance renderer**

Additional context must contain only the next bounded action: assess, spawn the resolved lead, wait, integrate, recover, or complete. It identifies Symphony as the sole topology authority and never requires exact completion strings.

- [ ] **Step 4: Implement the hook entry point**

Read one JSON object from stdin, call `handle`, write only the provider result, and exit with its code. On internal error, preserve a diagnostic under plugin data, emit one concise non-secret warning, and avoid blocking unrelated host work.

- [ ] **Step 5: Verify GREEN and full unit suite**

Run:

```bash
python3 -m unittest plugins.symphony.tests.test_runtime -v
python3 -m unittest discover -s plugins/symphony/tests -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add plugins/symphony/symphony/runtime.py plugins/symphony/scripts/symphony_hook.py plugins/symphony/tests/test_runtime.py
git commit -m "feat: run Symphony through canonical lifecycle state"
```

---

### Task 7: Write agent role contracts and native controls

**Files:**
- Replace: `plugins/symphony/skills/symphony/SKILL.md`
- Replace: `plugins/symphony/skills/symphony/references/*.md`
- Replace: `plugins/symphony/commands/*.md`
- Modify: `plugins/symphony/tests/test_package.py`

**Interfaces:**
- Produces exact work-packet fields: objective, ownership, evidence, constraints, acceptance check, return contract, size, complexity.
- Produces exact assessment fields: size, complexity, risk, rationale, topology, abstract role routes.
- Produces controls documented in the spec.

- [ ] **Step 1: Add failing documentation contract tests**

Assert Codex help contains only `$symphony:symphony`; Claude help contains only `/symphony:` commands; all controls exist; role contracts include decision-local size/complexity; workflow authority maps Ponytail, Context7, Compound Engineering, and Superpowers to their approved phases.

- [ ] **Step 2: Run package tests and verify RED**

Run: `python3 -m unittest plugins.symphony.tests.test_package -v`  
Expected: missing commands/references or old contract failures.

- [ ] **Step 3: Write the minimal Symphony skill**

The always-loaded section contains only activation check, thin-root loop, completion condition, and pointers. Put provider activation, capability refresh, and role-specific packets behind branch-specific references.

- [ ] **Step 4: Write Claude command wrappers**

Commands call the same semantic controls: enable, one-shot start, bypass, disable, status, agents with optional `--all`, reassess, stop with optional `--force`, and help. They do not embed a second orchestration workflow.

- [ ] **Step 5: Verify GREEN**

Run:

```bash
python3 -m unittest plugins.symphony.tests.test_package -v
claude plugin validate ./plugins/symphony
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add plugins/symphony/skills plugins/symphony/commands plugins/symphony/tests/test_package.py
git commit -m "docs: define Symphony 1.0 role and control contracts"
```

---

### Task 8: Add conditional indexed memory and capability refresh contracts

**Files:**
- Modify: `plugins/symphony/symphony/model.py`
- Modify: `plugins/symphony/symphony/runtime.py`
- Modify: `plugins/symphony/skills/symphony/references/capability-routing.md`
- Create: `plugins/symphony/tests/test_memory.py`

**Interfaces:**
- Produces: `MemoryStatus(enabled, reason, indexed_at)` in project state.
- Produces: `context_update_path(project) -> Path` for `.symphony/context.md` only after a healthy Codebase Memory probe is recorded.
- Produces actions `refresh_capabilities` and `update_context`; agents execute external tools while the reducer records their observed result.

- [ ] **Step 1: Write failing memory/capability tests**

Cover disabled-by-default memory, one bounded Codebase Memory health result, curated section allowlist, secret-pattern rejection, no transcript/raw-output storage, 24-hour capability staleness, cache-first routing, and deduplicated missing-capability suggestion per project/version.

- [ ] **Step 2: Run tests and verify RED**

Run: `python3 -m unittest plugins.symphony.tests.test_memory -v`  
Expected: missing memory contracts.

- [ ] **Step 3: Implement state and instruction support only**

Do not embed MCP or Context7 clients in the hook. The lead/assessor uses available tools and records bounded results through the canonical run protocol. The hook remains fast and offline.

- [ ] **Step 4: Verify GREEN**

Run: `python3 -m unittest plugins.symphony.tests.test_memory -v`  
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add plugins/symphony/symphony plugins/symphony/skills/symphony/references/capability-routing.md plugins/symphony/tests/test_memory.py
git commit -m "feat: add bounded Symphony capability and context memory"
```

---

### Task 9: Add installed-package smoke harnesses and CI

**Files:**
- Create: `plugins/symphony/scripts/package_smoke.py`
- Create: `plugins/symphony/tests/test_package_smoke.py`
- Modify: `.github/workflows/ci.yml` or the repository's existing release workflow
- Modify: marketplace manifests that publish Symphony
- Replace: `README.md`

**Interfaces:**
- Produces: `package_smoke.py --provider codex|claude --candidate <repo> --scenario <name>`.
- Produces scenarios `activation`, `managed-run`, `interrupt-resume`, and `upgrade`.

- [ ] **Step 1: Write failing smoke-harness tests**

Use temporary plugin homes and a fake provider process to assert materialized install paths, heartbeat version changes after reload/restart, trust-pending state, missing executable fault, and no absolute cache path.

- [ ] **Step 2: Run smoke tests and verify RED**

Run: `python3 -m unittest plugins.symphony.tests.test_package_smoke -v`  
Expected: missing smoke harness.

- [ ] **Step 3: Implement the minimum harness**

Keep orchestration outside the package. The harness installs a candidate, sends fixture payloads or launches a real provider when available, checks durable state, and emits one JSON result.

- [ ] **Step 4: Update CI and release packaging**

CI runs unit tests, `git diff --check`, Claude validation, package materialization, fake-provider smokes, and real provider smokes where the CLI and credentials are present. Release publishes only after the required jobs pass.

- [ ] **Step 5: Replace README with the 1.0 contract**

Document native command syntax, persistent enablement, hook trust/reload onboarding, guarded versus unguarded behavior, routing matrix, status/agents output, migration, and uninstall/disable consequences. Remove historical workaround instructions.

- [ ] **Step 6: Verify GREEN**

Run:

```bash
python3 -m unittest discover -s plugins/symphony/tests -v
claude plugin validate ./plugins/symphony
python3 plugins/symphony/scripts/package_smoke.py --provider codex --candidate . --scenario activation
python3 plugins/symphony/scripts/package_smoke.py --provider claude --candidate . --scenario activation
git diff --check
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add README.md .github plugins/symphony
git commit -m "test: verify installed Symphony 1.0 lifecycle"
```

---

### Task 10: Review, release, observe, and install

**Files:**
- Modify only files required by confirmed review findings.

**Interfaces:**
- Consumes: complete 1.0 diff and local verification evidence.
- Produces: reviewed commit on `main`, GitHub release `v1.0.0`, green CI, and marketplace installations for Codex and Claude.

- [ ] **Step 1: Run Ponytail simplification review**

Delete speculative abstractions, duplicated state, unused compatibility branches, and documentation repetition while keeping trust-boundary validation and lifecycle safety.

- [ ] **Step 2: Run Compound Engineering code review**

Review correctness, provider contracts, lifecycle safety, migration, packaging, tests, and skill authority. Apply only confirmed findings and add a failing regression test before each production fix.

- [ ] **Step 3: Run authoritative local verification**

```bash
python3 -m unittest discover -s plugins/symphony/tests -v
claude plugin validate ./plugins/symphony
python3 plugins/symphony/scripts/package_smoke.py --provider codex --candidate . --scenario activation
python3 plugins/symphony/scripts/package_smoke.py --provider claude --candidate . --scenario activation
git diff --check
git status --short
```

- [ ] **Step 4: Commit review fixes and push main**

Stage only intended Symphony files; exclude `.codex/config.toml`. Push the configured upstream.

- [ ] **Step 5: Watch CI and release to terminal success**

Use `gh run watch --exit-status` or bounded polling. If CI fails, reproduce locally, write a failing regression test, fix, push, and watch the replacement run.

- [ ] **Step 6: Upgrade marketplace installations**

After release visibility is confirmed:

```bash
codex plugin marketplace upgrade symphony
codex plugin add symphony@symphony
claude plugin marketplace update symphony
claude plugin update symphony@symphony
```

Verify both installed manifests report `1.0.0`. Codex external installation requires a new session and `/hooks` trust review before heartbeat verification; Claude uses `/reload-plugins` or restart.

- [ ] **Step 7: Send Telex completion**

Report outcome, commit, release URL, CI URL, installed versions, and required provider reload/restart actions.

