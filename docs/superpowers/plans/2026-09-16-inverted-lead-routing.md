# Inverted Lead Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enforce assessor-selected, cost-inverted lead routing and bounded consultant decisions while preserving Symphony's adaptive reassessment lifecycle.

**Architecture:** Extend the existing assessment receipt and persisted run state with one exact lead route. Reuse the shared spawn boundary to validate root leads and schedule labelled consultant/worker children, then validate consultant results into indexed decision packets that weak leads can apply mechanically. Existing lifecycle events remain the only reassessment clock.

**Tech Stack:** Python 3 standard library, `unittest`, JSON lifecycle hook declarations, Markdown skills and evals.

**Spec:** `docs/superpowers/specs/2026-09-16-inverted-lead-routing-design.md`

## Global Constraints

- The assessor is read-only and uses the strongest available general reasoning model at high effort.
- Small leads use a capable executor at medium or high effort and execute directly by default.
- Medium leads use a capable balanced model at medium effort and can decide without a consultant.
- Large consultant-heavy leads use the cheapest reliable coordinator at low or medium effort.
- Every actionable consultant decision has `small|medium|large` size and `low|medium|high` complexity.
- Consultant-heavy scheduling reserves capacity rather than keeping an idle consultant alive.
- Reassessment continues at owner prompts, planning boundaries, completed worker waves, interrupts, resumes, and material scope/risk changes.
- Add no dependency, timer, daemon, background monitor, or second state file.

## File Structure

- Modify `plugins/symphony/scripts/symphony_hook.py`: parse and persist lead routes, enforce spawn routing and worker capacity, validate consultation results, and retain reassessment behavior.
- Modify `plugins/symphony/tests/test_symphony_hook.py`: add red/green coverage for route receipts, both provider spawn boundaries, consultant decisions, capacity reservation, state recovery, and hook timeout declarations.
- Modify `plugins/symphony/hooks/codex.json`: declare Codex's three-second Interrupt timeout.
- Modify `plugins/symphony/skills/symphony/SKILL.md`: define the exact assessment route receipt, inverted execution roles, consultation packet, scheduling rules, and reassessment handoff.
- Modify `plugins/symphony/skills/symphony/references/model-routing.md`: provide the mechanical size/complexity routing matrix.
- Modify `plugins/symphony/commands/help.md` and `README.md`: describe adaptive inverted routing and consultant visibility.
- Modify `plugins/symphony/evals/hosted-registration-smoke/prompt.md` plus `graders/assessor-assignment.md`, `graders/lead-assignment.md`, and `graders/routing.md`: require the route receipt and cost-inverted lead behavior in hosted validation.

---

### Task 1: Persist an exact assessor-selected lead route

**Files:**
- Modify: `plugins/symphony/scripts/symphony_hook.py`
- Test: `plugins/symphony/tests/test_symphony_hook.py`

**Interfaces:**
- Produces: `LEAD_ROUTE_RE`, `_valid_lead_route(value) -> bool`, `_parse_lead_route(message, run_id) -> dict | None`, and `run["lead_route"]`.
- Route shape: `{"model": str, "effort": str, "consulting": "none" | "occasional" | "consultant-heavy", "max_parallel_workers": int}`.
- Receipt: `SYMPHONY_LEAD_ROUTE:<run-id>:<model>:<effort>:<consulting>:<max-parallel-workers>`.

- [ ] **Step 1: Write failing route-receipt tests**

Add tests beside the existing assessment receipt tests:

```python
def test_assessment_requires_and_persists_exact_lead_route(self):
    self.hook.handle_event(
        self.event("UserPromptSubmit", prompt="/symphony:start task"), self.data,
    )
    self.start_role("assessor", "assessor")
    run = self.state()["active_run"]
    message = (
        f"SYMPHONY_ASSESSMENT:{run['id']}:large:large\n"
        "SYMPHONY_ASSESSMENT_REASON:Several dependent worker waves\n"
        f"SYMPHONY_LEAD_ROUTE:{run['id']}:gpt-5.6-luna:low:consultant-heavy:1"
    )

    changed = self.hook._record_assessment_receipt(
        self.state(), self.state()["active_run"], message, 1_000, "assessor",
    )

    self.assertTrue(changed)
```

Exercise the receipt through `handle_event` as the authoritative assertion: confirm `lead_route`, the matching `mode_history` snapshot, `mode_revision == 1`, and `strong_assessment_required is False`. Add table cases rejecting a missing route, wrong run id, unsupported consulting strategy, negative/non-integer worker limit, small mode without `none`, medium mode without `occasional`, large mode without `consultant-heavy`, and effort outside each mode's allowed range.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  plugins.symphony.tests.test_symphony_hook.SymphonyHookTests.test_assessment_requires_and_persists_exact_lead_route -v
```

Expected: FAIL because the route receipt is neither parsed nor persisted.

- [ ] **Step 3: Implement the minimal route parser and state validation**

Add a boundary-safe receipt regex and these helpers near the existing assessment regexes:

```python
LEAD_ROUTE_RE = re.compile(
    r"(?<![A-Za-z0-9_-])SYMPHONY_LEAD_ROUTE:([a-f0-9]{16}):"
    r"([A-Za-z0-9._-]+):(low|medium|high|xhigh|max|ultra):"
    r"(none|occasional|consultant-heavy):(\d+)(?![A-Za-z0-9_-])",
    re.IGNORECASE,
)

def _valid_lead_route(value):
    return (
        isinstance(value, dict)
        and isinstance(value.get("model"), str) and bool(value["model"])
        and value.get("effort") in {"low", "medium", "high", "xhigh", "max", "ultra"}
        and value.get("consulting") in {"none", "occasional", "consultant-heavy"}
        and type(value.get("max_parallel_workers")) is int
        and value["max_parallel_workers"] >= 0
    )
```

`_parse_lead_route` must require exactly one matching line for the current run and enforce:

```python
ROUTE_POLICY = {
    "small": ({"none"}, {"medium", "high"}),
    "medium": ({"occasional"}, {"medium"}),
    "large": ({"consultant-heavy"}, {"low", "medium"}),
}
```

Initialize `lead_route` to `None` in `_new_run`. Preserve and validate it in `_normalize_assessment_state`; an active legacy run with an accepted mode but no valid route becomes `assessment_due=True` and `strong_assessment_required=True`. Require a valid route in `_has_accepted_assessment`. Store the route in `_record_assessment_receipt` and copy it into each `mode_history` entry.

- [ ] **Step 4: Run focused and neighboring assessment tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  plugins.symphony.tests.test_symphony_hook.SymphonyHookTests.test_assessment_requires_and_persists_exact_lead_route \
  plugins.symphony.tests.test_symphony_hook.SymphonyHookTests.test_spawn_boundary_accepts_registered_codex_assessor_receipt \
  plugins.symphony.tests.test_symphony_hook.SymphonyHookTests.test_codex_spawn_metadata_auto_registers_role_holders -v
```

Expected: PASS after existing assessment fixtures are updated to include exact route receipts.

- [ ] **Step 5: Commit the route contract**

```bash
git add plugins/symphony/scripts/symphony_hook.py plugins/symphony/tests/test_symphony_hook.py
git commit -m "feat(routing): persist assessor-selected lead routes"
```

---

### Task 2: Enforce cost-inverted lead spawns and fix Interrupt timeout

**Files:**
- Modify: `plugins/symphony/scripts/symphony_hook.py`
- Modify: `plugins/symphony/hooks/codex.json`
- Test: `plugins/symphony/tests/test_symphony_hook.py`

**Interfaces:**
- Consumes: validated `run["lead_route"]` from Task 1.
- Produces: `_lead_route_error(run, model, effort) -> str | None` and a Codex Interrupt declaration with `timeout: 3`.

- [ ] **Step 1: Write failing tests for exact routing and timeout**

Add a provider table that starts and accepts an assessment selecting `gpt-5.6-luna/low` for a large run, then submits:

```python
codex_wrong = self.event(
    "PreToolUse", tool_name="spawn_agent",
    tool_input={
        "task_name": "symphony_lead__gpt_6_astra__high",
        "model": "gpt-6-astra", "reasoning_effort": "high",
    },
)
claude_wrong = self.event(
    "PreToolUse", tool_name="Agent",
    tool_input={
        "description": "symphony_lead [opus/high]: execute",
        "model": "opus", "effort": "high",
    },
)
```

Assert both are blocked with the selected `gpt-5.6-luna/low` route in the correction. Assert matching provider calls pass. Also assert a large lead cannot reuse the registered assessor's exact model/effort pair, even if a malformed state claims that route.

Extend `test_hook_declarations_and_manifest_are_wired`:

```python
codex = json.loads((PLUGIN_ROOT / "hooks" / "codex.json").read_text())
interrupt = codex["hooks"]["Interrupt"][0]["hooks"][0]
self.assertEqual(3, interrupt["timeout"])
```

- [ ] **Step 2: Run focused tests and verify RED**

Run the new routing test and `test_hook_declarations_and_manifest_are_wired`. Expected: the wrong lead is accepted and Interrupt is `10`.

- [ ] **Step 3: Add one shared lead-route guard**

After provider-label validation in `_spawn_label_error`, validate only owner lead spawns against `run["lead_route"]`. Compare exact model strings and normalized effort strings. Return one correction naming the accepted route and strategy. Read the registered assessor record from `_agent_records(run)` and reject an identical model/effort pair for `consultant-heavy` execution.

Do not hardcode provider model names or infer tiers in the hook; the accepted receipt is the source of truth.

- [ ] **Step 4: Set Codex Interrupt timeout to the host maximum**

Change only the `Interrupt` command hook in `plugins/symphony/hooks/codex.json` from `10` to `3`. Leave Stop at `65` and other lifecycle hooks at `10`.

- [ ] **Step 5: Run the focused tests and verify GREEN**

Run the route and declaration tests. Expected: PASS with no warning-producing declaration.

- [ ] **Step 6: Commit the enforcement boundary**

```bash
git add plugins/symphony/scripts/symphony_hook.py plugins/symphony/hooks/codex.json plugins/symphony/tests/test_symphony_hook.py
git commit -m "fix(routing): enforce cost-inverted lead selection"
```

---

### Task 3: Track consultant spawns and reserve consultant-heavy capacity

**Files:**
- Modify: `plugins/symphony/scripts/symphony_hook.py`
- Test: `plugins/symphony/tests/test_symphony_hook.py`

**Interfaces:**
- Consumes: `lead_route.consulting` and `lead_route.max_parallel_workers`.
- Produces: `_spawn_role(payload) -> str | None`, `run["pending_child_spawns"]`, and child records whose `role` is `symphony_consultant` or `symphony_<worker-role>`.

- [ ] **Step 1: Write failing child-boundary tests**

Create an accepted large run with a registered active lead and `max_parallel_workers=1`. Submit a lead-originated Codex `PreToolUse` with `agent_id="lead"` and labels:

```python
worker = {
    "task_name": "symphony_worker__gpt_5_6_luna__low",
    "model": "gpt-5.6-luna", "reasoning_effort": "low",
}
consultant = {
    "task_name": "symphony_consultant__gpt_6_astra__high",
    "model": "gpt-6-astra", "reasoning_effort": "high",
}
```

Assert the first worker passes, its `SubagentStart` receives the pending worker metadata, a second concurrent worker is blocked, and the consultant still passes. Mirror label and capacity assertions for Claude descriptions. Add a medium/occasional case proving no slot is reserved and its capable lead receives explicit fallback guidance when a consultant cannot be launched.

- [ ] **Step 2: Run the focused tests and verify RED**

Expected: Codex child `PreToolUse` is ignored by the current early return and no capacity limit is applied.

- [ ] **Step 3: Route registered-lead child spawns through the existing boundary**

Narrow the child-event early return so `PreToolUse` from the registered active lead reaches spawn validation; continue ignoring root-control events from all other children. Extract role from the already-required task name or Claude description. Accept `consultant` and bounded worker slugs; reject a child attempting `assessor` or `lead`.

Append only the metadata needed for lifecycle binding:

```python
run.setdefault("pending_child_spawns", []).append({
    "parent_agent_id": payload.get("agent_id"),
    "role": role,
    "model": model,
    "effort": effort,
})
```

On the next non-owner `SubagentStart`, consume the oldest pending child entry and copy its role/model/effort into the record. Normalize this bounded list on state read and discard entries whose parent is no longer active during recovery.

- [ ] **Step 4: Enforce consultant-heavy worker capacity**

Before accepting a lead-originated worker spawn, count active worker records plus pending worker spawns. When `consulting == "consultant-heavy"`, block at `max_parallel_workers`; consultant spawns do not consume that worker allowance. When the allowance is zero, require consultation to run serially before workers.

Do not reserve an idle agent. For `occasional`, do not apply a consultant reservation; inject context telling the capable lead to decide the bounded issue itself if the host rejects consultant dispatch.

- [ ] **Step 5: Run focused child lifecycle tests and verify GREEN**

Run the new tests plus existing role-label, worker-wave, interruption, and recovery tests. Expected: PASS and no duplicate root-role registration.

- [ ] **Step 6: Commit child scheduling**

```bash
git add plugins/symphony/scripts/symphony_hook.py plugins/symphony/tests/test_symphony_hook.py
git commit -m "feat(routing): reserve consultant-heavy decision capacity"
```

---

### Task 4: Validate per-decision consultation packets

**Files:**
- Modify: `plugins/symphony/scripts/symphony_hook.py`
- Test: `plugins/symphony/tests/test_symphony_hook.py`

**Interfaces:**
- Produces: `_consultation_result(message, run_id) -> (dict | None, str | None)`.
- Valid result shape: `{"decisions": [{"index": int, "size": str, "complexity": str, "decision": str, "action": str}]}`.
- Non-actionable terminal shape: `SYMPHONY_CONSULTATION_BLOCKED:<run-id>:<bounded reason>`.

- [ ] **Step 1: Write failing parser tests**

Use this accepted packet:

```text
SYMPHONY_CONSULTATION:<run-id>
SYMPHONY_DECISION_COUNT:2
SYMPHONY_DECISION:1:small:low:Keep the existing parser boundary
SYMPHONY_ACTION:1:Patch the shared parser and run its focused test
SYMPHONY_DECISION:2:medium:high:Require exact route receipts
SYMPHONY_ACTION:2:Add state validation before enabling lead execution
```

Assert ordered parsed dictionaries. Reject missing or duplicate indexes, gaps, count mismatch, absent actions, empty text, invalid size/complexity, wrong run id, duplicate consultation headers, and a consultation-level size/complexity substituted for per-decision values. Accept exactly one explicit blocked receipt with a nonempty bounded reason.

- [ ] **Step 2: Run parser tests and verify RED**

Expected: FAIL because no consultation parser exists.

- [ ] **Step 3: Implement strict line parsers**

Use anchored multiline regexes for the header, count, indexed decision, indexed action, and blocked receipt. Require exactly one header, exactly one count, decision/action index sets equal to `range(1, count + 1)`, and preserve index order. Cap each free-text field at 500 characters after requiring a nonempty single line.

- [ ] **Step 4: Persist consultation outcomes at SubagentStop**

When a terminal record's trusted bound role is `consultant`, parse its `last_assistant_message`. Store a valid packet under `record["consultation"]`; store a bounded error under `record["consultation_error"]`. Preserve both fields in `_agent_records` and archived history.

Return corrective hook context for an invalid packet. At root completion, reject any consultant record with `consultation_error` so an active decision cannot silently disappear. A `SYMPHONY_CONSULTATION_BLOCKED` record is terminal evidence: occasional leads use their documented self-decision fallback; consultant-heavy leads serialize a replacement or trigger strong reassessment.

- [ ] **Step 5: Run consultation and completion tests**

Expected: valid split decisions persist through archive/reload; invalid packets block completion; unrelated completed worker records remain intact.

- [ ] **Step 6: Commit consultation validation**

```bash
git add plugins/symphony/scripts/symphony_hook.py plugins/symphony/tests/test_symphony_hook.py
git commit -m "feat(routing): validate consultant decision packets"
```

---

### Task 5: Teach weak leads the mechanical routing protocol

**Files:**
- Modify: `plugins/symphony/skills/symphony/SKILL.md`
- Modify: `plugins/symphony/skills/symphony/references/model-routing.md`
- Modify: `plugins/symphony/commands/help.md`
- Modify: `README.md`
- Modify: `plugins/symphony/evals/hosted-registration-smoke/prompt.md`
- Modify: `plugins/symphony/evals/hosted-registration-smoke/graders/assessor-assignment.md`
- Modify: `plugins/symphony/evals/hosted-registration-smoke/graders/lead-assignment.md`
- Modify: `plugins/symphony/evals/hosted-registration-smoke/graders/routing.md`
- Test: `plugins/symphony/tests/test_symphony_hook.py`

**Interfaces:**
- Consumes: receipt and packet formats from Tasks 1 and 4.
- Produces: one authoritative workflow instruction and deterministic eval expectations.

- [ ] **Step 1: Write failing documentation-contract tests**

Extend the existing skill/document tests to require these exact concepts and receipt prefixes:

```python
required = (
    "SYMPHONY_LEAD_ROUTE:<run-id>:<model>:<effort>:<consulting>:<max-parallel-workers>",
    "SYMPHONY_DECISION:<index>:<small|medium|large>:<low|medium|high>:<precise decision>",
    "SYMPHONY_ACTION:<index>:<mechanical action or mapping>",
    "capacity, not an idle long-lived agent",
    "decide the bounded question itself",
    "route or consulting-strategy change",
)
```

Assert the model-routing reference contains one explicit per-decision matrix and that the hosted eval requires a large administrative lead route different from its assessor route.

- [ ] **Step 2: Run documentation-contract tests and verify RED**

Expected: FAIL because current skills still direct the assessor to return only two receipt lines and describe large leads as capable coordinators.

- [ ] **Step 3: Update the Symphony skill in place**

Change the bootstrap assessment contract to three exact lines. Make the root copy the accepted route rather than choose a lead. Replace the mode prose with the approved inverted responsibilities. Add the consultation packet, explicit blocked receipt, provider-visible `consultant` role labels, reserved-capacity rule, and occasional-lead fallback.

Keep existing reassessment events and require unchanged reassessments to repeat the persisted route. Route or strategy changes require a strongest/high assessor, increment `mode_revision`, terminate the old lead/children, and start a fresh matching lead.

- [ ] **Step 4: Add the mechanical decision-routing matrix**

In `model-routing.md`, map each decision independently:

| Decision size | Complexity | Default handling |
|---|---|---|
| Small | Low | Cheapest suitable mechanical worker, or capable occasional lead directly |
| Small | Medium/High | Capable focused worker; high only for unresolved reasoning |
| Medium | Low/Medium | Balanced worker at medium |
| Medium | High | Capable worker at high with bounded verification |
| Large | Any | Decompose or trigger reassessment before dispatch |

State that live provider ids come from the host catalog and the hook enforces the assessor's exact lead route, not hardcoded model tiers.

- [ ] **Step 5: Update help, README, and hosted evals**

Describe assessment, the selected route, consultant strategy, decision size/complexity, and adaptive reassessment without duplicating the full skill. Update deterministic graders and hosted prompts so old assessor/high lead reuse is a failing example and large administrative routing is passing.

- [ ] **Step 6: Run documentation and eval contract tests**

Expected: PASS; all deterministic grader regexes compile in Node.

- [ ] **Step 7: Commit agent instructions and evals**

```bash
git add README.md plugins/symphony/commands/help.md \
  plugins/symphony/skills/symphony/SKILL.md \
  plugins/symphony/skills/symphony/references/model-routing.md \
  plugins/symphony/evals plugins/symphony/tests/test_symphony_hook.py
git commit -m "docs(routing): teach adaptive consultant orchestration"
```

---

### Task 6: Run the complete regression and provider sanity loop

**Files:**
- Modify only files implicated by a failing check.
- Test: `plugins/symphony/tests/test_symphony_hook.py`

**Interfaces:**
- Consumes: all prior tasks.
- Produces: local evidence that lifecycle, recovery, routing, evaluation, and provider manifests remain valid.

- [ ] **Step 1: Run the full unit suite**

```bash
PYTHONDONTWRITEBYTECODE=1 SYMPHONY_STOP_WAIT_SECONDS=0 \
  python3 -m unittest discover -s plugins/symphony/tests -v
```

Expected: all tests PASS with no warnings.

- [ ] **Step 2: Validate the Claude plugin manifest**

```bash
claude plugin validate ./plugins/symphony
```

Expected: successful validation.

- [ ] **Step 3: Run the Claude eval command used by CI**

```bash
claude plugin eval ./plugins/symphony \
  --trust-plugin --ablation none \
  --model claude-haiku-4-5-20251001 --threshold 0.9 \
  --json eval-results.json --report eval-report.html \
  --keep-temp --no-publish
```

Expected: exit zero and a nonempty `eval-results.json`. If the evaluator reports that early access is unavailable, record that as unsupported local evidence and rely on the required CI job rather than treating it as a pass.

- [ ] **Step 4: Run an isolated installed-candidate Codex control smoke**

```bash
python3 plugins/symphony/scripts/codex_smoke.py \
  --candidate-marketplace . --output /tmp/symphony-routing-smoke \
  --name control-help --prompt /symphony:help \
  --expect SYMPHONY_CONTROL_HANDLED: --timeout 120
```

Expected: candidate bytes match the installed copy, the hook is trusted, the receipt appears, and the disposable credential copy is removed. Unit tests supply deterministic small, medium, large, consultation, reassessment, recovery, and mismatch coverage; do not spend live-model tokens duplicating those state-machine cases unless the deterministic or CI evidence disagrees.

- [ ] **Step 5: Fix regressions one at a time with a red test**

For each failure, add or narrow one reproducing test, verify it fails for the observed reason, make the smallest shared-boundary correction, and rerun the focused check before returning to the full suite.

- [ ] **Step 6: Verify the final diff and commit only verified repairs**

```bash
git diff --check
git status --short
```

If Task 6 required code changes, commit only those named files with a `fix:` message describing the repaired behavior. If no changes were required, create no empty commit.

---

### Task 7: Review, release 0.21.0, observe CI, and upgrade both installations

**Files:**
- Modify: `plugins/symphony/.codex-plugin/plugin.json`
- Modify: `plugins/symphony/.claude-plugin/plugin.json`

**Interfaces:**
- Consumes: verified implementation from Tasks 1–6.
- Produces: matching `0.21.0` manifests, a pushed `main`, green GitHub Actions and release `v0.21.0`, and verified Codex and Claude marketplace installations.

- [ ] **Step 1: Review the complete change against the spec**

Inspect `git diff origin/main...HEAD`, confirm every spec requirement has a test, run `git diff --check`, and verify no unrelated or generated artifacts are tracked. Fix each material finding with a reproducing test before release.

- [ ] **Step 2: Bump both plugin manifests to 0.21.0**

Change only each manifest's `version` field from `0.20.3` to `0.21.0`. Run the full unit suite and `claude plugin validate ./plugins/symphony` again after the bump.

- [ ] **Step 3: Commit the release**

```bash
git add plugins/symphony/.codex-plugin/plugin.json plugins/symphony/.claude-plugin/plugin.json
git commit -m "chore(release): promote Symphony 0.21.0"
```

- [ ] **Step 4: Push main and watch the exact workflow to terminal state**

```bash
git push origin main
gh run list --workflow plugin-eval.yml --branch main --limit 1
```

Copy the returned `databaseId` into `gh run watch DATABASE_ID --exit-status`. If CI fails, inspect `gh run view DATABASE_ID --log-failed`, reproduce the failure locally, add a red regression test, fix it, recommit, push, and watch the replacement run. Continue until the latest pushed commit is green and `gh release view v0.21.0` succeeds.

- [ ] **Step 5: Upgrade and verify the Codex marketplace installation**

```bash
codex plugin marketplace upgrade symphony
codex plugin add symphony@symphony
codex plugin list --json
```

Verify the installed Symphony entry resolves to version `0.21.0` and its hook path exists.

- [ ] **Step 6: Upgrade and verify the Claude marketplace installation**

Use Claude's plugin manager to run `/plugin marketplace update symphony`, reinstall or update `symphony@symphony`, and inspect the installed plugin metadata. Verify it resolves to version `0.21.0` and its hook path exists.

- [ ] **Step 7: Notify only after all terminal conditions pass**

Send the configured completion notification with commit, release, CI URL/status, and both verified installed versions. Report any unsupported local eval separately; it does not override a required green CI result.
