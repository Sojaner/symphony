# Symphony Codex Sanity Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to execute this plan task-by-task.

**Goal:** Make Symphony reliable and observable under a weak Codex root, prove every command and lifecycle boundary through deterministic tests and bounded real-Codex trials, then release v0.16.0.

**Architecture:** Keep the existing Python lifecycle hook. Reduce the root protocol to spawn/register/relay/wait, move discovery and optional MCP work into assessor/lead roles, add exact control/dry-run/ownership guards, and add one standard-library live-Codex harness.

**Tech Stack:** Python 3 standard library, Codex lifecycle hooks, Markdown skills/commands, `unittest`, Codex CLI JSONL.

**Spec:** `docs/superpowers/specs/2026-09-15-codex-sanity-loop-design.md`

## Global Constraints

- Work on `main` is explicitly authorized.
- Add no dependency, daemon, scheduler, token estimate, or transcript storage.
- The root must perform no repository or MCP discovery before assessor delegation.
- Controls terminate without resuming project work or mutating unrelated lifecycle state.
- Only explicit dry-run state bypasses accepted-assessment completion.
- Ownership transfers only from an interrupted run with no active registered agents.
- MCP failure is bounded and non-fatal.
- Tests precede each behavior change and must be observed failing for the intended reason.
- Real-Codex trials use isolated state, hard deadlines, and candidate plugin files.
- Plugin manifests end at `0.16.0`; marketplace manifests remain unchanged.

---

### Task 1: Make the root protocol thin and assessment completion enforceable

**Files:**
- Modify: `plugins/symphony/scripts/symphony_hook.py`
- Modify: `plugins/symphony/skills/symphony/SKILL.md`
- Modify: `plugins/symphony/skills/symphony/references/capability-routing.md`
- Modify: `plugins/symphony/tests/test_symphony_hook.py`

- [ ] Add failing tests proving bootstrap contains no root repository/MCP work, and normal completion fails without an accepted current assessment or with a live registered assessor.
- [ ] Run those tests and record the expected failures.
- [ ] Reduce bootstrap to objective/run/profile plus exact delegation, registration, receipt, and wait instructions. Move discovery, capability routing, memory choice, and verification to assessor/lead guidance.
- [ ] Make root-relayed assessment fallback conditional on absence of child-lifecycle support; require the registered assessor otherwise.
- [ ] Add exact `Delegating:`, observed-only `Waiting:`, and `Completed:` contracts.
- [ ] Run focused and full unit tests; commit.

### Task 2: Make controls, dry-run, and interrupted ownership exact

**Files:**
- Modify: `plugins/symphony/scripts/symphony_hook.py`
- Modify: `plugins/symphony/tests/test_symphony_hook.py`
- Modify: `plugins/symphony/commands/*.md`

- [ ] Add failing table-driven tests for every command during no-run, starting, active, and stopping states, including `agents --all`, empty enable, invalid assess, and malformed controls.
- [ ] Add failing tests for explicit dry-run state and cross-session interrupted ownership with and without live agents.
- [ ] Run the focused tests and record the expected failures.
- [ ] Add the minimum single-use control receipt, explicit `dry_run`, and atomic interrupted ownership transfer fields/guards.
- [ ] Verify controls never enter the Stop wait path or resume work, dry-run is the only bypass, and foreign live runs stay inspection-only.
- [ ] Run focused and full unit tests; commit.

### Task 3: Bound optional memory and add the real-Codex harness

**Files:**
- Modify: `plugins/symphony/skills/symphony/SKILL.md`
- Modify: `plugins/symphony/skills/symphony/references/capability-routing.md`
- Create: `plugins/symphony/scripts/codex_smoke.py`
- Modify: `plugins/symphony/tests/test_symphony_hook.py`
- Modify: `.github/workflows/plugin-eval.yml`

- [ ] Add failing contract tests: small skips memory; medium/large uses at most one disposable bounded probe; failure/hang falls back; assessor is spawned first.
- [ ] Add failing harness tests for command construction, hard timeout, JSONL parsing, isolated paths, and lifecycle assertions without invoking Codex.
- [ ] Run the focused tests and record the expected failures.
- [ ] Implement the minimum stdlib harness and skill guidance. Reuse the existing hook state and CLI; do not add a monitor or orchestration framework.
- [ ] Add a bounded CI smoke when Codex and credentials are available; otherwise emit an explicit skip while deterministic tests remain required.
- [ ] Run unit tests, plugin validators, and cheap live control trials; commit.

### Task 4: Run the adversarial Codex fix/retest loop

**Files:**
- Modify only files implicated by observed failures.
- Add one regression test per distinct root cause.

- [ ] Run command/state cases first, then three fresh weak Luna/low small trials, recovery/compaction/foreign-session cases, memory absent/failing/hanging, and medium/large delegation cases.
- [ ] For each failure: preserve its event/state evidence, diagnose the shared root cause, add a failing regression, implement the smallest fix, and rerun the targeted live case.
- [ ] Repeat until every finite matrix case is terminal, bounded, observable, and internally consistent.
- [ ] Run the full matrix once on the final tree and commit any fixes.

### Task 5: Document, review, release, and verify the installed package

**Files:**
- Modify: `README.md`
- Modify: `plugins/symphony/commands/help.md`
- Modify: `plugins/symphony/.claude-plugin/plugin.json`
- Modify: `plugins/symphony/.codex-plugin/plugin.json`
- Modify: `plugins/symphony/tests/test_symphony_hook.py`

- [ ] Add a failing release-contract test for the new controls, ownership, weak-root protocol, memory fallback, live harness, and version `0.16.0`.
- [ ] Update documentation and both plugin manifests; do not change marketplace versions.
- [ ] Run the complete unit suite, validators, diff checks, and full live matrix.
- [ ] Obtain a strongest/high whole-change review; fix and re-review any load-bearing finding.
- [ ] Push `main`, observe CI and release publication to terminal success, install the published plugin fresh, and run one final weak-root smoke.
- [ ] Send the configured completion notification only after the installed smoke is green.
