## Code Review Results

**Scope:** v0.21.2 (f165772) -> working tree at v1.0.0 (eba877b), 106 files, ~6.4k insertions / ~12k deletions
**Intent:** Clean-room rewrite of the Symphony Codex/Claude Code plugin: stdlib Python lifecycle reducer, routing matrix, atomic state store, provider adapters, hook runtime, role docs, smoke harness and CI, released as 1.0.0
**Mode:** markdown report-only

**Reviewers:** correctness, testing, maintainability, agent-native, security, reliability, api-contract, adversarial (in-process fallback)
- testing -- new test suite and smoke harness stand in for real hosts
- maintainability -- an entirely new 2.5k-line package
- agent-native -- skills, agents, commands and injected guidance are the product surface
- security -- hooks parse host payloads and persist state under the home directory
- reliability -- Stop gating, interrupt/resume, locking, fault handling
- api-contract -- hook response shapes and config fields are consumed by two hosts
- adversarial -- persistence writes, concurrency, a silent-pass CI/smoke mechanism; the Codex cross-model peer timed out, so the local reviewer covered the lens
- project standards -- not run (no CLAUDE.md, AGENTS.md or CODING_STANDARDS.md in the tree)

### Triage Groups

| Group | Findings | Context | Preferred Resolution | Why | Kind |
|-------|----------|---------|----------------------|-----|------|
| Completion livelock and lost reconciliation | #1, #2, #5, #6, #7 | Every path that can leave a run without a registered lead or with a stale child ends in a Stop that blocks forever | Decide the stop policy once (#1: honor stop_hook_active or a bounded retry, permit stop when no lead was ever registered, render actionable block text), then the mechanical fixes #5, #6, #7, then session-based reconciliation #2 | One policy plus four small reducer/runtime edits remove every known way to strand a session | decision gate (#1, #2), apply queue (#5, #6, #7) |
| Guard fidelity: CI and smokes | #8, #9, #13, #21 | The installed-package guards can go green while a real host would fail | Install the Codex CLI in CI (#8), make the Claude smoke send real-shaped payloads incl. PreToolUse/PostToolUse (#9), tighten the help grep (#21), then extend real-host smokes (#13) | Same root: stand-ins do not mirror the hosts | apply queue |
| Documented behavior the runtime never produces | #3, #10, #11, #15 | Docs and schema describe faulted state, capability refresh, indexed memory and six activation states that no code path writes | Decide per item: implement (faulted marker #3 is worth implementing) or delete the schema and narrow the docs (#10, #11, #15) | Agents are instructed to rely on mechanisms that do not exist | decision gate |
| State store durability | #12, #18, #19 | Enablement and privacy promises around ~/.symphony/state | Salvage enabled on corrupt files (#12), stop persisting raw host payloads (#18), derive the legacy key from the git toplevel like the pre-1.0 hook (#19) | Three local, testable edits in store.py / runtime._transition | apply queue |
| runtime.py structure | #16, #17, #20, #23 | Lifecycle bookkeeping hidden in assessment dict; one 200-line function; duplicated route/snapshot derivation | Do #16 first (first-class RunState fields), which makes #17 a straightforward split and removes the duplication in #20/#23 | One data-shape change unlocks the rest | apply queue |

### P0 -- Critical

| # | File | Issue | Reviewer | Confidence |
|---|------|-------|----------|------------|
| 1 | `plugins/symphony/symphony/runtime.py:97` | Stop hook livelocks: re-blocks on every stop_hook_active retry, no agent-reachable escape | adversarial, agent-native, fast-pass | 100 |

- **#1** -- On an enabled project every non-control prompt opens a run with no lead (runtime.py:108-111), so when the model answers directly the Stop hook returns `decision:block` with reason `lead_outcome_missing` (reducer.py:262). Claude Code re-fires Stop with `stop_hook_active:true`; nothing reads that flag and reduce() deliberately bypasses dedup for stop_requested, so the same block is emitted forever. The only exit is a user typing `stop --force`, which the block text never names. Validated by reproduction (three identical blocks in a row). Response: policy decision plus code. Honor stop_hook_active (or a bounded retry count), permit stop when no lead was ever registered, and render block reasons as actionable text that names the force-stop control.

### P1 -- High

| # | File | Issue | Reviewer | Confidence |
|---|------|-------|----------|------------|
| 2 | `plugins/symphony/symphony/runtime.py:76` | Stale delegations never reconciled: resume needs `active_agent_ids` no host sends | adversarial, reliability, fast-pass | 100 |
| 3 | `plugins/symphony/symphony/runtime.py:934` | Hook fault silently disables all guarding; documented `faulted` state never produced | reliability | 100 |
| 4 | `plugins/symphony/symphony/adapters.py:124` | Codex SubagentStop guidance rendered as `additionalContext`, which Codex drops there | api-contract | 75 |
| 5 | `plugins/symphony/symphony/reducer.py:127` | First lead after an interrupt is rejected when no lead was registered | correctness | 75 |
| 6 | `plugins/symphony/symphony/runtime.py:184` | Repeated identical control prompt is silently deduped in Claude sessions | correctness | 75 |
| 7 | `plugins/symphony/symphony/runtime.py:736` | Invalid consultant marker keyed by agent id can never be cleared by a retry | correctness | 75 |

- **#2** -- Neither host's SessionStart carries `active_agent_ids`, so `_resume_reconciled` is unreachable in production. A child whose SubagentStop is missed (timeout, kill, new session) stays `working` forever and blocks both `_stop_requested` and `_lead_completed`. Validated by reproduction. Response: record the session id on the run/delegations and reconcile when the session changes or a delegation is older than the current session; add a SessionStart fixture without the field.
- **#3** -- `main()` swallows every exception, prints one stderr line (invisible at exit 0) and returns 0, so an unwritable state dir leaves Stop and spawn gating silently off while the docs promise a `faulted` activation state that no code writes. Response: write a durable faulted marker outside the failed store path and surface it in `status`.
- **#4** -- runtime.py:90-93 defers SubagentStop guidance to PostToolUse only for Claude; for Codex it is emitted as `hookSpecificOutput.additionalContext` on SubagentStop. Codex hook docs (checked via context7) accept only `decision`/`reason` on that event with unknown fields rejected, so the assessor/consultant/lead retry nudges and the route-accepted message are dropped or fail the hook on Codex. Response: generalize the deferral and flush on the next event Codex accepts context on (SubagentStart or UserPromptSubmit).
- **#5** -- With status `interrupted` and `lead_identity` None, `_lead_started` treats the first lead as a replacement needing generation+1, but `_observe_delegation` only bumps the generation when a previous lead exists, so the spawn is rejected (unrendered), the lead's completion is ignored as stale, and Stop blocks with `lead_outcome_missing`. Validated by reproduction. Fix: `replacing = run.lead_identity is not None and identity != run.lead_identity`.
- **#6** -- Event ids are sha256 of the payload with no timestamp; control events derive from it without a salt, so enable -> disable -> enable in one Claude session leaves the project disabled and a second `/symphony:stop --force` is dropped. Codex is unaffected only because `turn_id` differs. Validated by reproduction. Fix: apply the `_task_event` collision guard (or an `observed_at` salt) to every control-derived event.
- **#7** -- `_set_invalid_consultant` only discards the identity of the consultant currently reporting; a retry has a new agent id, so `_invalid_consultants` keeps the old one and both lead completion and Stop stay blocked. Validated by reproduction. Fix: key the marker by objective, or clear terminal consultants when a newer one returns valid decisions.

### P2 -- Moderate

| # | File | Issue | Reviewer | Confidence |
|---|------|-------|----------|------------|
| 8 | `.github/workflows/ci.yml:46` | Real Codex CI smoke can never run: the codex CLI is never installed on the runner | adversarial, reliability | 100 |
| 9 | `plugins/symphony/scripts/package_smoke.py:151` | Claude smoke passes via payload fields Claude never sends and skips the PreToolUse gate | adversarial, correctness | 100 |
| 10 | `plugins/symphony/symphony/memory.py:43` | memory.py helpers and MemoryStatus have no production caller | agent-native, maintainability | 100 |
| 11 | `plugins/symphony/symphony/model.py:72` | Capability snapshot refresh is documented but nothing ever writes a snapshot | agent-native, maintainability | 100 |
| 12 | `plugins/symphony/symphony/store.py:357` | Corrupt or unreadable state file silently discards project enablement | reliability | 100 |
| 13 | `.github/workflows/ci.yml:58` | Real-host smoke steps never exercise the hook payload contract they depend on | testing | 75 |
| 14 | `plugins/symphony/symphony/reducer.py:133` | Stale-owner-generation guards in the reducer have zero test coverage | testing | 75 |
| 15 | `plugins/symphony/symphony/reducer.py:30` | Six of seven documented activation states are never produced by the runtime | agent-native | 75 |
| 16 | `plugins/symphony/symphony/reducer.py:94` | Delegation bookkeeping smuggled into RunState.assessment via underscore keys | maintainability | 75 |
| 17 | `plugins/symphony/symphony/runtime.py:199` | `_observe_delegation` mixes state sync, per-role policy and UX text in one 200-line function | maintainability | 75 |
| 18 | `plugins/symphony/symphony/runtime.py:98` | Stop and SubagentStop persist full `last_assistant_message` and prompts into ~/.symphony/state | security | 75 |
| 19 | `plugins/symphony/symphony/store.py:49` | Legacy key hashes cwd; the pre-1.0 hook hashed the git toplevel, so enablement is lost from subdirectories | adversarial | 75 |

- **#8** -- The workflow installs Claude Code but never Codex, so the `command -v codex` guard always fails and the step exits 0 with a notice on every run. Fix: install the Codex CLI before the step and skip only on a missing key.
- **#9** -- The Claude managed-run smoke sends `status`, `model`, `model_reasoning_effort` and an underscore `agent_type` that Claude never emits, and never sends PreToolUse/PostToolUse, so it passes through a fallback path the real host never takes. Fix: mirror the payloads that tests/test_runtime.py already models.
- **#10 / #11** -- Design call: either implement the capability refresh writer and memory probe or delete `capabilities`, `memory`, `capability_suggestions`, the memory.py helpers and their serializers, and narrow capability-routing.md. Removable if deleted: roughly 120 lines across 3 files plus tests.
- **#12** -- Validated: a truncated file whose bytes still contain `"enabled":true` reloads as disabled. Salvage `enabled`/`configuration` the way `_migrated_state` does.
- **#15** -- provider-activation.md lists seven `activation.state` values; the reducer only ever writes `guarded`. Narrow the table or describe the others as symptoms, not stored states.
- **#18** -- Validated: a Stop on a never-enabled project wrote `ghp_`, `sk-proj-` and a postgres URL from `last_assistant_message` verbatim into the state file. Reduce a derived lifecycle-only event for stop/interrupt and drop `outcome.message`.
- **#19** -- Validated against `git show f165772:...symphony_hook.py:194-212`. Derive the legacy key from `git rev-parse --show-toplevel` with a cwd fallback and try both.

### P3 -- Low

| # | File | Issue | Reviewer | Confidence |
|---|------|-------|----------|------------|
| 20 | `plugins/symphony/symphony/runtime.py:470` | Capability-snapshot lookup and route resolution recomputed twice per lead spawn | maintainability | 100 |
| 21 | `.github/workflows/ci.yml:79` | `grep -qi symphony` on the Claude help output also passes on error text | adversarial | 75 |
| 22 | `plugins/symphony/symphony/runtime.py:153` | `$symphony:symphony` anywhere in a prompt (any provider) is parsed as a control | adversarial | 75 |
| 23 | `plugins/symphony/symphony/runtime.py:474` | Required lead model/effort extraction duplicated between `_prepare_delegation` and `_observe_delegation` | maintainability | 75 |

### Actionable Findings

| # | File | Issue | Route | Notes |
|---|------|-------|-------|-------|
| 1 | `runtime.py:97` | Stop livelock, no stop_hook_active handling | `manual -> downstream-resolver` | policy decision then code; suggested_fix present |
| 2 | `runtime.py:76` | Stale delegations never reconciled | `manual -> downstream-resolver` | suggested_fix present |
| 3 | `runtime.py:934` | Hook fault silently unguards | `manual -> downstream-resolver` | suggested_fix present |
| 4 | `adapters.py:124` | Codex SubagentStop additionalContext dropped | `manual -> downstream-resolver` | suggested_fix present |
| 5 | `reducer.py:127` | First lead after interrupt rejected | `gated_auto -> downstream-resolver` | one-line fix + test |
| 6 | `runtime.py:184` | Identical control prompts deduped | `gated_auto -> downstream-resolver` | salt control-derived ids |
| 7 | `runtime.py:736` | Invalid consultant marker never cleared | `gated_auto -> downstream-resolver` | suggested_fix present |
| 8 | `ci.yml:46` | Codex CI smoke never runs | `manual -> downstream-resolver` | install codex CLI |
| 9 | `package_smoke.py:151` | Claude smoke uses unreal payloads | `manual -> downstream-resolver` | suggested_fix present |
| 12 | `store.py:357` | Corrupt state drops enablement | `gated_auto -> downstream-resolver` | suggested_fix present |
| 13 | `ci.yml:58` | Real-host smokes don't exercise payload contract | `manual -> downstream-resolver` | design input |
| 14 | `reducer.py:133` | Stale-owner guards untested | `gated_auto -> downstream-resolver` | two reducer tests |
| 16 | `reducer.py:94` | Underscore-key bookkeeping | `manual -> downstream-resolver` | structural |
| 17 | `runtime.py:199` | 200-line `_observe_delegation` | `manual -> downstream-resolver` | split per role |
| 18 | `runtime.py:98` | Raw payloads persisted | `gated_auto -> downstream-resolver` | suggested_fix present |
| 19 | `store.py:49` | Legacy key mismatch | `gated_auto -> downstream-resolver` | suggested_fix present |
| 20 | `runtime.py:470` | Duplicate snapshot/route resolution | `gated_auto -> downstream-resolver` | helper extraction |
| 21 | `ci.yml:79` | Weak help grep | `gated_auto -> downstream-resolver` | literal match |
| 22 | `runtime.py:153` | Marker parsed from arbitrary text | `gated_auto -> downstream-resolver` | provider-gate the marker |
| 23 | `runtime.py:474` | Duplicated route extraction | `gated_auto -> downstream-resolver` | helper extraction |

Decision gates outside the queue (owner human): #10, #11 (implement or delete capability/memory schema), #15 (narrow activation-state docs).

### Coverage

- Validation: one batch of 12 (#1-#9, #12, #18, #19), all validated true, 0 dropped; 0 skipped via the cross-model shortcut (no peer artifact). P2/P3 structural and testing items (#10, #11, #13-#17, #20-#23) were not validated.
- Cross-model adversarial peer: cross_model_route=codex, model_requested=gpt-5.6-luna, effort_requested=xhigh, receipt_supported=none, model_actual=unverified, effort_actual=unverified, independence_verified=false. Terminal state: timeout (hit the 1200s hard cap while still working, reaped, no schema-shaped output). The adversarial lens ran through the in-process fallback reviewer.
- Suppressed: 2 at anchor 50 (correctness: Codex child inherits parent model when transcript metadata is missing; maintainability: hand-rolled dataclass serializers). Fast-pass items withdrawn: 0 (both merged into #1 and #2).
- Soft-bucket demotions: 7 (Codex has no PreToolUse hook -> documented design, residual risk; Windows per-process fallback lock -> residual, platform not a stated target; lock acquisition timeout -> residual; transcript per-line size bound -> residual; route locked at PreToolUse even if spawn denied -> residual; transcript-failure and unknown-control test gaps -> testing gaps).
- Plan: no plan found under docs/plans (the legacy plan lives under docs/superpowers/plans and was not auto-discovered); settlement suppression not evaluated.
- Removable surface: ~120 lines / 3 files across #10, #11 if the delete option is chosen (signal, not a target).
- Residual risks: on Codex there is no pre-spawn enforcement, only SubagentStop-time text nudges (documented in role-contracts.md); on Codex the parent session `model` is recorded as a child's tier when the transcript lacks turn_context; a Claude subagent that starts without a matching PreToolUse can consume a lone pending delegation and inherit its role; `main()` treats corruption, permission errors and reducer bugs identically; CI pipes an unpinned remote installer into a job that later receives repository secrets; the per-project flock has no acquisition timeout and holds across fsync; the Windows fallback lock is per-process only.
- Testing gaps: Stop with `stop_hook_active:true`; SessionStart in a new session with a `working` delegation and no `active_agent_ids`; enable/disable/enable and double force-stop in one Claude session; lead registration after interrupt with `lead_identity` None; consultant retry under a new id; unwritable state dir; multi-process lock contention; Codex transcript missing/malformed; unknown control and `$symphony:symphony` composition paths; `_agent_label_model_effort` rejection branch; legacy file keyed by toplevel with a subdirectory cwd; PreToolUse deny/allow on the materialized Claude package; the plan promised fixtures for six event kinds and only two per provider ship.
- Deterministic verification run by the orchestrator: 107 unit tests pass, `git diff --check` clean, all 8 package smokes pass (codex/claude x activation, managed-run, interrupt-resume, upgrade), `claude plugin validate` passes for the marketplace and plugin manifests (Python 3.14.7).

---

> **Verdict:** Not ready
>
> **Reasoning:** The 1.0 promise is "block completion while tracked work is active, never permanently lock a session." Findings #1, #2, #5, #6 and #7 are five validated, reproducible ways an enabled project strands the session in a Stop loop that only a user-typed force-stop can break, and #6 shows even that escape can be silently dropped. Tests and smokes are green because they never send the payload shapes the hosts actually send (#8, #9, #13).
>
> **Fix order:** #1 stop policy -> #5, #6, #7 reducer/runtime fixes -> #2 session-based reconciliation -> #4 Codex context deferral -> #3 faulted marker -> #12, #18, #19 store -> #8, #9 guard fidelity -> decide #10/#11/#15 -> structure (#16, #17, #20, #23) -> #13, #14, #21, #22.
