# Symphony Reassessment and Usage Visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Separate project assessment from execution, make reassessment controllable and automatic at material boundaries, expose every delegation, and report authoritative per-agent usage without estimates.

**Architecture:** Extend the existing JSON lifecycle record with normalized assessment, mode-history, and optional usage metadata. The deterministic hook owns parsing, persistence, trigger flags, receipt validation, and host usage ingestion; the Symphony skill owns judgment, model routing, visible spawn/completion messages, and bounded handoffs. A new `/symphony:assess` command adjusts persistent project policy or requests reassessment, while `/status` and `/agents` expose the resulting state.

**Tech Stack:** Python 3 standard library, JSON lifecycle hooks, Markdown plugin commands/skills, `unittest`, Claude plugin validation/evals.

**Spec:** `docs/superpowers/specs/2026-09-15-reassessment-and-usage-visibility-design.md`

## Global Constraints

- Do not add dependencies or a monitoring service.
- Never estimate token usage or monetary cost.
- Render every unavailable usage field exactly as `not exposed by host`.
- Never retain prompts, transcripts, reasoning, worker output, secrets, or billing data.
- A manual project profile persists across runs until `/symphony:assess auto` clears it.
- Project profile and per-run execution mode remain separate.
- `/symphony:stop --force` must recover from malformed new state.
- Late agent events update only the uniquely owning active or historical run.
- Keep marketplace manifests unchanged.

---

### Task 1: Persistent project assessment and command surface

**Files:**
- Create: `plugins/symphony/commands/assess.md`
- Modify: `plugins/symphony/scripts/symphony_hook.py`
- Modify: `plugins/symphony/tests/test_symphony_hook.py`

**Interfaces:**
- Produces: normalized `state["assessment"]` with `profile`, `source`, `revision`, `reason`, and `assessed_at`.
- Produces: normalized active-run `mode_revision`, `assessment_due`, and `mode_history`.
- Produces: `_assessment_context(state) -> str` and the raw/template `/symphony:assess` route.
- Consumes: `_parse_prompt`, `_handle_prompt`, `read_project_state`, and atomic state writes.

- [ ] **Step 1: Add failing legacy-normalization and command tests**

Create v0.14-shaped state and assert project defaults `profile=None`, `source=None`, `revision=0`, `reason=None`, and `assessed_at=None`. An active run gains `mode_revision=0`, `assessment_due=True`, and an empty `mode_history`.

Add raw and shipped-template cases for no argument, `large`, and `auto`. Assert `large` persists a manual project profile and marks the active run due; `auto` clears it; no argument keeps it and marks due. Malformed values return usage guidance without mutation.

- [ ] **Step 2: Run focused tests to verify RED**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  plugins.symphony.tests.test_symphony_hook.SymphonyHookTests.test_assessment_state_normalizes_legacy_runs \
  plugins.symphony.tests.test_symphony_hook.SymphonyHookTests.test_assess_commands_persist_clear_and_request_profiles -v
```

Expected: FAIL because assessment state and command parsing do not exist.

- [ ] **Step 3: Implement minimal normalization and command handling**

Extend raw and marker control regexes with `assess`. Accept only spec enum/source/value types; invalid structures use the existing corruption path so force-stop remains usable.

Create `commands/assess.md` with description `Reassess Symphony or set the persistent project profile`, argument hint `[small|medium|large|auto]`, `SYMPHONY_CONTROL: assess`, and the standard `$ARGUMENTS` marker.

Handle `assess` before auto-activation. A manual profile sets `source="manual"`, increments revision, stores a bounded override reason/time, and marks the active run due. `auto` resets profile/source/reason/time, increments revision, and marks due. No argument only marks due and returns assessment instructions.

- [ ] **Step 4: Run full tests and commit**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s plugins/symphony/tests -v
git diff --check
git add plugins/symphony/commands/assess.md plugins/symphony/scripts/symphony_hook.py plugins/symphony/tests/test_symphony_hook.py
git commit -m "feat: add persistent Symphony assessment"
```

---

### Task 2: Authorized assessment receipts and reassessment triggers

**Files:**
- Modify: `plugins/symphony/scripts/symphony_hook.py`
- Modify: `plugins/symphony/tests/test_symphony_hook.py`

**Interfaces:**
- Consumes: Task 1 assessment/run fields.
- Produces: `ASSESSMENT_RE`, `ASSESSMENT_REASON_RE`, and `_record_assessment_receipt(state, run, message, now, agent_id=None) -> bool`.
- Produces: deterministic `assessment_due` transitions and bounded append-only mode history.

- [ ] **Step 1: Add failing authorization tests**

Use exact receipt `SYMPHONY_ASSESSMENT:0123456789abcdef:large:medium` plus `SYMPHONY_ASSESSMENT_REASON:Long-running repository with two independent work units`. Cover a registered assessor, wrong run, malformed enum, unowned agent, automatic overwrite of a manual profile, bounded reason, repeated same-mode assessment, and actual mode/profile changes. Revisions/history change only for accepted receipts.

- [ ] **Step 2: Run new methods to verify RED**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  plugins.symphony.tests.test_symphony_hook.SymphonyHookTests.test_assessment_receipts_are_authorized_and_versioned -v
```

Expected: FAIL because parsing and authorization are absent.

- [ ] **Step 3: Implement receipt persistence**

Use boundary-safe regexes. Store `assessor_agent_id` when `SubagentStart.agent_type` is `symphony_assessor`. Accept automatic receipts only from that assessor and root relays only on the current run's Stop/control path. Preserve manual profile precedence. Increment revisions, append a bounded row, clear `assessment_due`, and cap a newline-free reason at 500 characters.

- [ ] **Step 4: Add failing `test_reassessment_triggers_at_material_boundaries`**

Require `assessment_due=True` after a new run, later non-control owner prompt, `SessionStart` recovery, `Interrupt`, and termination of the last active non-lead agent in a wave. Controls and assessor termination do not create duplicate triggers. A valid same-mode receipt clears the flag.

- [ ] **Step 5: Implement triggers, run, and commit**

Detect wave completion from active records excluding `lead_agent_id` and `assessor_agent_id`; do not add a scheduler. Recovery context includes profile/mode revisions and due state.

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s plugins/symphony/tests -v
git diff --check
git add plugins/symphony/scripts/symphony_hook.py plugins/symphony/tests/test_symphony_hook.py
git commit -m "feat: reassess Symphony at lifecycle boundaries"
```

---

### Task 3: Split assessment from execution and expose delegations

**Files:**
- Modify: `plugins/symphony/skills/symphony/SKILL.md`
- Modify: `plugins/symphony/skills/symphony/references/model-routing.md`
- Modify: `plugins/symphony/scripts/symphony_hook.py`
- Modify: `plugins/symphony/evals/match-proceeds/prompt.md`
- Modify: `plugins/symphony/evals/match-proceeds/graders/lead-profile.md`
- Create: `plugins/symphony/evals/match-proceeds/graders/delegation-visibility.md`
- Modify: `plugins/symphony/tests/test_symphony_hook.py`

**Interfaces:**
- Consumes: Task 2 receipts/due state/assessor id.
- Produces: bounded `symphony_assessor` then a separate mode-appropriate lead.
- Produces: mandatory `Delegating:` and `Completed:` commentary records.

- [ ] **Step 1: Add failing static contract tests**

Require `symphony_assessor`, `exactly one concise assessment result`, `must not implement`, `separate execution lead`, and the exact visibility record shapes from the spec. Assert the old strongest/high direct-implementation instruction is absent.

- [ ] **Step 2: Run the contract test to verify RED**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  plugins.symphony.tests.test_symphony_hook.HookDeclarationTests.test_skill_separates_assessment_execution_and_exposes_delegations -v
```

Expected: FAIL on the current bootstrap.

- [ ] **Step 3: Rewrite bootstrap and routing minimally**

The root visibly spawns a strongest/high, no-history, read-only assessor and requires its receipt before work. Route small to capable direct/medium, medium to balanced lead/medium with at most two workers, and large to capable medium-or-high coordination with delegated waves. Reserve strongest/high for narrow hard decisions or high-risk final review.

On resume/compaction, start a fresh execution lead from bounded memory rather than indefinitely resuming context. Preserve MCP, memory, capability, wait, Stop, and completion rules.

- [ ] **Step 4: Update hosted weak-root eval**

Make the match prompt medium-shaped and forbid assessor implementation. Require a visible assessor delegation, a separate non-strongest/high medium lead, and both visibility records. Keep mismatch refusal unchanged.

- [ ] **Step 5: Validate and commit**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s plugins/symphony/tests -v
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 /home/rojan/.codex/skills/.system/skill-creator/scripts/quick_validate.py plugins/symphony/skills/symphony
claude plugin validate ./plugins/symphony
git diff --check
git add plugins/symphony/skills/symphony/SKILL.md plugins/symphony/skills/symphony/references/model-routing.md plugins/symphony/scripts/symphony_hook.py plugins/symphony/evals/match-proceeds plugins/symphony/tests/test_symphony_hook.py
git commit -m "feat: separate Symphony assessment from execution"
```

---

### Task 4: Authoritative usage ingestion and reporting

**Files:**
- Modify: `plugins/symphony/scripts/symphony_hook.py`
- Modify: `plugins/symphony/hooks/hooks.json`
- Modify: `plugins/symphony/commands/agents.md`
- Modify: `plugins/symphony/commands/status.md`
- Modify: `plugins/symphony/skills/symphony/SKILL.md`
- Modify: `plugins/symphony/tests/test_symphony_hook.py`

**Interfaces:**
- Consumes: existing active/history owner resolution.
- Produces: `_usage_record(payload, now) -> dict | None`, usage merge, known/unknown aggregates, and a Claude `PostToolUse` Agent declaration.

- [ ] **Step 1: Add failing ingestion tests**

Use realistic Claude `PostToolUse` Agent payloads with `agentId`, `totalTokens`, `totalDurationMs`, `totalToolUseCount`, and token breakdowns. Cover background `async_launched`, partial usage, booleans, negatives, strings, malformed objects, unknown/ambiguous ids, late historical ownership, and Stop/force-stop independence.

- [ ] **Step 2: Run new methods to verify RED**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  plugins.symphony.tests.test_symphony_hook.SymphonyHookTests.test_post_tool_use_records_owned_authoritative_usage \
  plugins.symphony.tests.test_symphony_hook.SymphonyHookTests.test_usage_ingestion_rejects_invalid_unowned_and_background_data -v
```

Expected: FAIL because `PostToolUse` is not declared or handled.

- [ ] **Step 3: Implement normalized extraction and merge**

Accept only non-negative integers where `type(value) is int`. Never read transcripts. Resolve unique ownership like SubagentStop, merge without erasing known fields, record source `claude-post-tool-use`, and persist under the existing lock. Add a Claude `PostToolUse` hook matched to `Agent`. Do not invent a Codex event.

- [ ] **Step 4: Add failing `test_agent_and_status_usage_are_honest_and_read_only`**

Require `/agents` and `--all` columns for total/input/output/cache creation/cache read tokens, duration, tool uses, and source, with exact unavailable labels. Require `/status` to report known totals plus the number of agents lacking totals, so partial totals are explicit. Preserve read-only inspection security.

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  plugins.symphony.tests.test_symphony_hook.SymphonyHookTests.test_agent_and_status_usage_are_honest_and_read_only -v
```

Expected: FAIL because the current tables omit usage.

- [ ] **Step 5: Implement, validate, and commit**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s plugins/symphony/tests -v
claude plugin validate ./plugins/symphony
git diff --check
git add plugins/symphony/scripts/symphony_hook.py plugins/symphony/hooks/hooks.json plugins/symphony/commands/agents.md plugins/symphony/commands/status.md plugins/symphony/skills/symphony/SKILL.md plugins/symphony/tests/test_symphony_hook.py
git commit -m "feat: expose Symphony delegation usage"
```

---

### Task 5: Documentation, migration, and v0.15.0

**Files:**
- Modify: `README.md`
- Modify: `plugins/symphony/commands/help.md`
- Modify: `plugins/symphony/.claude-plugin/plugin.json`
- Modify: `plugins/symphony/.codex-plugin/plugin.json`
- Modify: `plugins/symphony/tests/test_symphony_hook.py`

**Interfaces:**
- Consumes: Tasks 1–4 public behavior.
- Produces: documentation and plugin version `0.15.0`.

- [ ] **Step 1: Add failing release-contract test**

Require the assess command, two-level assessment, automatic boundaries, separate assessor, mode lead, visible records, authoritative usage only, exact fallback, Claude sync/background limitation, no hard-budget promise, and `0.15.0` in both plugin manifests. Marketplace manifests remain unchanged.

- [ ] **Step 2: Run the release test to verify RED**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  plugins.symphony.tests.test_symphony_hook.HookDeclarationTests.test_documentation_and_manifests_describe_assessment_release -v
```

Expected: FAIL because docs/manifests describe v0.14.0.

- [ ] **Step 3: Update docs/manifests**

Explain that a long-running large-profile project may have a small task. Document `/symphony:assess large` for this repository and `auto` as reversible. State sync Claude usage may be exposed while background/Codex remains unavailable. Change only plugin manifest versions to `0.15.0`.

- [ ] **Step 4: Run gates and commit**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s plugins/symphony/tests -v
PYTHONPYCACHEPREFIX=/tmp/symphony-v015-pycache python3 -m py_compile plugins/symphony/scripts/symphony_hook.py
claude plugin validate ./plugins/symphony
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 /home/rojan/.codex/skills/.system/skill-creator/scripts/quick_validate.py plugins/symphony/skills/symphony
git diff --check
git add README.md plugins/symphony/commands/help.md plugins/symphony/.claude-plugin/plugin.json plugins/symphony/.codex-plugin/plugin.json plugins/symphony/tests/test_symphony_hook.py
git commit -m "docs: release Symphony assessment controls in v0.15.0"
```

---

### Task 6: Whole-change review and hosted verification

**Files:**
- Review: every file changed since `7fd94ba`.
- Modify only for a verified defect.

**Interfaces:**
- Consumes: Tasks 1–5.
- Produces: independent review and release evidence; push remains separately authorized.

- [ ] **Step 1: Request independent whole-change review**

Give a fresh strong reviewer the spec, plan, base `7fd94ba`, and head. Require reproduction of authorization, triggers, medium routing, usage ownership, inspection read-only behavior, malformed recovery, and Stop behavior. Fix Critical/Important findings test-first and re-review exact fixes.

- [ ] **Step 2: Repeat local release gates**

Repeat Task 5 commands. Confirm both manifests are `0.15.0`, marketplace manifests unchanged, diff check clean, and worktree clean.

- [ ] **Step 3: Run hosted evaluation when credentials permit**

```bash
claude plugin eval ./plugins/symphony --model claude-haiku-4-5-20251001
```

Expected: visible bounded assessor, separate medium lead, and passing mismatch refusal.

- [ ] **Step 4: Report release readiness**

Report evidence, unavailable host fields, and exact commit. Do not push or release without separate authorization.
