# Symphony 1.0.1 remediation and 1.1.0 capability profiles

**Goal:** Close the validated livelock and persistence defects found in the 1.0.0 review, then replace the unbuildable runtime capability discovery with CI-maintained entitlement profiles.

**Status:** Plan agreed 2026-09-17. Wave 1 complete (commits A and B). Wave 2 not started.

**Branch:** `claude/code-review-testing-8c2fa0`

**Sources:** review reports in `docs/reviews/`, decision records in `docs/adr/`, glossary in `CONTEXT.md`.

## Interruption recovery

If this plan is resumed in a new session, read in this order: `CONTEXT.md` for vocabulary, `docs/adr/0001`-`0003` for the three load-bearing decisions, `docs/reviews/2026-09-17-symphony-1-0-code-review.md` for the numbered findings referenced below, then the checkboxes here for position. Findings are cited as `#N` and resolve against the code review report.

Determine position from git: if `HEAD` has no commit titled `fix: end Symphony stop livelock`, wave 1 commit A has not landed. If that commit exists but none titled `refactor: delete unreachable Symphony schema`, commit B has not landed. The manifests read `1.0.1` only after commit A.

## Settled decisions

These were decided in a grilling session and must not be silently revisited. Each cites the question that settled it.

- A run begins at assessor spawn, not at the prompt; the prompt injects guidance only; a run with zero delegations never blocks Stop. (Q1, Q6; ADR 0001)
- Stop safety is both a circuit breaker on the host's already-active flag and session-based reconciliation of unobserved delegations. (Q2)
- Where the spec and host capabilities disagree, the spec moves. (Q3)
- The persisted `capabilities` field is deleted, not implemented. (Q5; ADR 0002)
- Model currency is owned by a weekly CI workflow, not the maintainer and not runtime discovery. (Q5, Q13)
- The nine-cell size/complexity grid stays in Python; only the tier-to-model profiles are machine-edited JSON. (Q20)
- Claude agent files are generated from the profiles. (Q10, Q14)
- CI verifies every model in a new matrix with an existence check, not a full task, and fails loudly when it cannot verify. (Q11, Q25)
- Failure notification is a direct Telegram Bot API call plus a tracking issue. Telex is MCP-only and stays out of CI. (Q13, Q21)
- Runtime probes entitlement once per provider session with a two-second timeout; it never reads credential files. (Q16, Q18)
- A tier clamp is gated behind a `proceed` control accepted for the session; an effort clamp discloses and proceeds. (Q15, Q17, Q23, Q24)
- Persistence uses a strict allowlist. Enforced where an event becomes durable, not at the adapter: the runtime must read markers and the already-active flag from the live payload, so translation cannot be the choke point. (Q19; ADR 0003)
- Two waves. The marketplace pin and the 1.0.1 tag land in the same commit. (Q8, Q12, Q22)

---

## Wave 1 — release 1.0.1

### Commit A: `fix: end Symphony stop livelock and tighten persistence`

Bumps the version and cuts the release.

**Lifecycle**
- [x] `runtime.py` `_handle_prompt`: a non-control prompt in an enabled project no longer emits `task_received`; it injects assessment guidance composed from the live payload. (#1, Q1/Q6)
- [x] `runtime.py` `_prepare_delegation`: an assessor spawn opens the run. Carry the task text from the spawn packet, not from persisted state.
- [x] `reducer.py` `_stop_requested`: a run with no delegations and no outcome permits stop instead of blocking. (#1)
- [x] `runtime.py` `_transition`: read `stop_hook_active` from the Stop payload. When set on an already-blocked run, permit completion, mark the run `abandoned`, and name the unreconciled identities. (#1, Q2)
- [x] `reducer.py` `_lead_started`: `replacing = run.lead_identity is not None and identity != run.lead_identity`, so the first lead after an interrupt registers at the current generation. (#5)
- [x] `runtime.py` `_derived`: salt control-derived event ids with `observed_at` so a repeated control is not deduped. (#6)
- [x] `runtime.py` `_set_invalid_consultant`: key invalid markers by objective, not agent id, so a retry clears them. (#7)
- [x] `runtime.py` `_transition`: generalise the parent-action deferral to Codex; do not emit `additionalContext` on a Codex `SubagentStop`. (#4)
- [x] Record the provider session id on the run and on delegations; on a heartbeat from a new session, mark unobserved delegations interrupted and enter recovering. (#2, Q2)
- [x] `runtime.py` `main`: on an exception, write a durable faulted marker outside the failed store path and surface it in `status`. (#3)
- [x] Add `abandoned` to `RunState` and surface it in `status` and archived runs. (Q7)

**Persistence**
- [x] `model.py` `persistable`, applied on the reducer's history append: drop everything outside the ADR 0003 allowlist before an event becomes durable. (#18, Q19)
- [x] `runtime.py`: stop writing `outcome["message"]` from the agent's final message; keep only the marker-derived fields.
- [x] `store.py` `_load_unlocked`: salvage `enabled` and `configuration` from a corrupt file before rebuilding. (#12)
- [x] `store.py` `legacy_project_key`: derive from `git rev-parse --show-toplevel` with a resolved-cwd fallback, and try both keys on import. (#19)

**Guard fidelity**
- [x] `package_smoke.py` `_payload`: Claude payloads drop `status`, `model`, `model_reasoning_effort`; use hyphenated `symphony:symphony-<role>-<model>-<effort>`. (#9, Q26)
- [x] `package_smoke.py` `_exercise`: send `PreToolUse` before each `SubagentStart` and `PostToolUse` after each `SubagentStop`; assert the deny path for a generic agent spawn.
- [x] Add a smoke scenario that re-fires Stop with `stop_hook_active: true` and asserts completion is permitted. (Q26)
- [x] `ci.yml`: install the Codex CLI; the real smoke skips only on a missing credential, never on a missing binary. (#8)
- [x] `ci.yml`: replace `grep -qi symphony` with a literal match on the help output. (#21)

**Cleanup that rides along** (files this commit already opens)
- [x] Merge `_prompt_stop_actions` into `_render_actions`.
- [x] Inline `_route_marker`, `_assessment_marker`, `_decision_marker`.
- [x] Collapse the duplicated required-model extraction into one helper. (#23)
- [x] Collapse the duplicated snapshot lookup. (#20)
- [x] Drop `_help` duplication of `commands/help.md`.

**Spec and docs**
- [x] Apply the 15 proposed fixes in `docs/reviews/2026-09-17-symphony-1-0-spec-review.md` to the design spec: recovery boundary, activation states narrowed to guarded and pending verification, receipts ban narrowed to lifecycle transitions, Codex detect-not-prevent parity, safe-boundary definition, persisted-data section, migration contract, risk taxonomy, topology authority, Codex `start` grammar, cheap-root precondition, cost acceptance item, captured-fixture requirement. (Q3)
- [x] README: same corrections where it repeats those claims.

**Release**
- [x] Add a version to the plugin entry in `.claude-plugin/marketplace.json`. (Q12)
  - **Open question for the user.** The intended caret pin is not achievable with this layout. `claude plugin validate` reports that for a path source (`./plugins/symphony`) the entry version is silently ignored and `plugin.json` wins, so installs still track the default branch. Pinning requires changing the entry to a git source with a tag constraint, which is a distribution change the user has not agreed to. The entry now simply matches `1.0.1`.
- [x] Set both manifests to `1.0.1`. Same commit as the pin. (Q22)
- [x] Full verification: `python3 -m unittest discover -s plugins/symphony/tests`, all eight smokes, `git diff --check`, `claude plugin validate` on the marketplace and the plugin.
- [x] Commit, push, watch CI, notify the maintainer.

### Commit B: `refactor: delete unreachable Symphony schema`

No version bump, so no release fires.

- [x] Delete `state.capabilities`, `CapabilitySnapshot` persistence, `MemoryStatus`, `capability_suggestions`, and their store serializers. Keep `fallback_snapshot`. (Q5, ADR 0002)
- [x] Delete `memory.py` except `redact_secrets`: `ALLOWED_CONTEXT_SECTIONS`, `context_update_path`, `validated_context_sections`, `capability_refresh_due`, `record_missing_capability_suggestion`.
- [x] Delete `snapshot_is_stale`.
- [x] Delete `Delegation.tokens` and `duration_seconds` plumbing.
- [~] **Withdrawn:** delete `detect_provider`. The manifests do set the variable, but this is the path every runtime test exercises, and without it a missing variable raises inside a hook that exits zero, leaving the session silently unguarded.
- [x] Reduce `HookResult` to its stdout payload; `stderr` and `exit_code` were never set to anything but their defaults.
- [~] **Withdrawn:** delete `StateStore.load` and `save`. They have no production callers but 67 test call sites; deleting nine lines by rewriting 67 call sites moves complexity into the tests rather than removing it.
- [x] Replace the hand-rolled `_to_dict` helpers with `dataclasses.asdict`; keep the validators.
- [x] Drop `ResolvedRoute`; have `resolve_tier` return the flattened mapping.
- [~] **Withdrawn:** drop the `fcntl`-less threading lock fallback. Deleting it turns a weak lock into a hard crash that the hook's own error handling would swallow. It already carries a comment naming its ceiling, so it stays as the documented shortcut it is.
- [x] Simplify `_has_active_run` to the known state shape. `_contains` keeps its exact-value recursion: a substring match on the serialised document would be shorter but looser, and loosening a guard is what this release exists to stop.
- [x] Delete the tests for the removed helpers.
- [x] Review, test, commit, push. Manifests stay at `1.0.1`.

---

## Wave 2 — release 1.1.0, capability profiles

Not started. Depends on wave 1.

- [ ] Add `plugins/symphony/profiles.json`: per-provider, per-entitlement tier-to-model-and-effort profiles. The nine-cell grid stays in `routing.py`. (Q20)
- [ ] Generator emits `agents/*.md` from the profiles; CI asserts every Claude map token has a matching agent file. (Q10, Q14)
- [ ] Runtime profile selection: Codex reads `~/.codex/models_cache.json`; Claude shells out to `claude auth status`. Once per provider session, two-second timeout, stored beside the activation record. Never read `auth.json` or any credential file. (Q16, Q18)
- [ ] Clamp policy: route to the best entitled option. Tier clamp gates behind `proceed`; effort clamp discloses and proceeds. No probe result means lowest entitlement, disclosed. (Q15, Q24)
- [ ] Add the `proceed` control: `commands/proceed.md`, the `CONTROLS` set, both help texts, and a session-scoped acceptance flag. The block message states exactly what to type. (Q17, Q23)
- [ ] Weekly scheduled workflow: probe entitlements, derive profiles, existence-check every model in the new matrix, regenerate agent files, open a PR. Fail loudly on any verification that cannot run; open or update a tracking issue and send a Telegram message via the Bot API using `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. (Q11, Q13, Q21, Q25)
- [ ] Replace invented fixtures with payloads captured from real installed sessions on both hosts. (Q26)
- [ ] Bump to `1.1.0` and release.
