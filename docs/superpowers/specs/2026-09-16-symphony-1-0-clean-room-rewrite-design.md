# Symphony 1.0 Clean-Room Rewrite Design

**Status:** Approved design  
**Date:** 2026-09-16  
**Release target:** 1.0.0

## Objective

Symphony gives a weak or inexpensive root agent a reliable way to complete project work through the right model, effort, and delegation topology. It must optimize reliability, cost, and observability together:

- a thin root keeps the session alive and reports progress;
- a bounded strong assessor classifies substantive work;
- a fixed routing matrix selects an execution strategy;
- a task-sized lead owns execution, integration, verification, and communication;
- hooks enforce only lifecycle facts the host can observe;
- project enablement and curated context persist across sessions until disabled.

The rewrite replaces the current implementation rather than refactoring it. Its product rule is:

> Give each bounded task to the least expensive route capable of completing it; spend expensive reasoning on assessment, unresolved decisions, and high-risk review rather than administration.

## Why a clean-room rewrite

The existing implementation accumulated 2,232 hook-script lines, 5,510 test lines, 75 hook functions, and 78 fix commits among 130 repository commits. Most failures came from encoding agent prose as a lifecycle protocol: exact receipts, role-registration strings, format parsing, and repeated Stop corrections.

The rewrite preserves behavioral knowledge, not implementation structure. Implementation agents receive this specification, the implementation plan derived from it, public provider documentation, and new public fixtures. They must not inspect or copy the superseded production code, tests, or design documents. Git history remains the archive.

## Product contract

### Enablement

- Symphony is enabled per project and remains enabled across sessions, resumes, and context compaction until the user disables it.
- Every substantive task in an enabled project is governed automatically.
- Controls and deterministically trivial tasks do not pay for assessment.
- An explicit one-shot managed task does not enable the project.
- A one-task bypass executes outside Symphony without changing project enablement or mutating an active Symphony run.
- Disabling gracefully stops an active run, archives recovery context, then disables future automatic activation.

### Honest guarantees

Symphony guarantees that normal completion is blocked while host-observed tracked work remains active. Explicit user interruption and host-enforced overrides remain authoritative.

A run is called **guarded** only after a Symphony hook has executed successfully in the current provider session and written the expected heartbeat. Installed, configured, or trusted is not equivalent to executed.

### Provider parity

Codex and Claude Code share one semantic contract and one core state machine. Thin adapters expose provider-native commands, hook events, reload/trust instructions, and telemetry. Parity means equivalent outcomes, not identical provider mechanics.

## Architecture

Symphony contains five components.

### Provider adapters

Adapters translate provider commands and lifecycle payloads into canonical events and render provider-native responses. They contain no routing policy.

### Lifecycle reducer

The reducer is a pure transition function:

```text
(current state, canonical event) -> (new state, required actions)
```

It owns enablement, run ownership, active children, activation state, interruption recovery, shutdown, and completion eligibility. Replayed events are idempotent through stable event identities.

### Routing policy

Routing maps task size, complexity, and risk to abstract capability tiers, effort, topology, worker policy, and consultant capacity. Exact provider models are resolved separately from a capability snapshot.

### State store

The store atomically persists:

- project policy and enablement;
- provider hook activation and heartbeat metadata;
- capability cache;
- active-run snapshot and owner generation;
- bounded canonical event history;
- summaries for the most recent 20 runs.

Events are recorded facts. Snapshots are derived acceleration and may be rebuilt.

### Agent instructions

Assessor, lead, worker, and consultant instructions consume explicit work packets and return structured results. They do not mutate lifecycle through magic prose or final-answer receipts.

## Thin-root boundary

The root may only:

- announce the selected route;
- spawn the assessor or selected lead;
- register host-observed identities;
- relay bounded packets;
- wait through the host's blocking wait/result primitive;
- render the compact delegation view;
- return the integrated result when the reducer permits completion.

Repository inspection, capability research, implementation, specialist judgment, result integration, and verification belong below the root. The root reports only host-observed model, effort, lifecycle, token, and duration facts.

## Canonical lifecycle

1. An enabled project receives a substantive task.
2. The adapter emits `task_received`.
3. The reducer opens one run and requests assessment unless classification is deterministically trivial.
4. The assessor returns the assessment contract.
5. Routing resolves abstract roles against cached provider capabilities.
6. The root spawns and registers the selected lead.
7. The lead works directly or creates classified worker and consultant packets.
8. Host lifecycle events update each delegation's latest state while the root blocks on the host wait primitive.
9. The lead integrates results and verification evidence.
10. Completion is permitted only after the lead has returned an outcome and no tracked work remains active.
11. The run becomes historical while project enablement remains active.

### Ownership and recovery

- Each run has one owner generation.
- Resume first reconciles host-observed agents.
- A live registered lead retains ownership.
- A missing or terminal lead is replaced only through a recorded safe ownership transition.
- Reassessment affects subsequent work; active work is not duplicated merely because its matrix cell changed.
- A lead is replaced only at a safe boundary or when unavailable or materially incapable.
- Interrupt records recoverable state. It never pretends the host action was prevented.
- Controls are inert, single-use operations. They cannot become task objectives, resume project work, or enter a Stop loop.

## Hook packaging, trust, and activation

### Packaging rules

- Each provider manifest declares its hook configuration through its supported plugin field.
- Hook commands use the provider's plugin-root placeholder (`${PLUGIN_ROOT}` or compatible `${CLAUDE_PLUGIN_ROOT}`).
- Commands are short native invocations of files included inside the materialized plugin package.
- No command contains an absolute versioned cache path or scans sibling cache versions.
- Release verification installs the candidate into a clean marketplace cache and proves every referenced executable exists.
- The manifest advertises lifecycle-hook capability.

Ponytail's simple manifest-declared hook plus plugin-root-relative command is the known-working packaging reference. Symphony does not copy Ponytail code.

### Activation states

The provider adapter exposes:

- `not_discovered`;
- `needs_review` or `pending_reload`;
- `active_unverified`;
- `guarded`;
- `policy_blocked`;
- `faulted`.

`SessionStart` and `UserPromptSubmit` write an atomic heartbeat containing provider session identity, plugin version/root identity, hook-schema version, and observation time. A matching current-session heartbeat is the programmatic proof of guarded execution.

Absence of a heartbeat is reported as **pending verification**, never repeatedly as "unarmed." The notice appears only in `status` or the first explicit managed-run attempt in that session. A host-reported command failure is recorded once as `faulted` with its event and source.

### Codex activation

- All non-managed plugin hooks require review/trust. Codex persists trust against the exact hook hash; changed definitions can require renewed review.
- `/hooks` is the supported human-visible view of source, trust, and enablement.
- An installation performed inside the running Codex plugin flow may refresh hook runtimes. The user reviews Symphony in `/hooks` and sends another prompt to create the heartbeat.
- An external `codex plugin add` or marketplace upgrade has no documented cross-process reload guarantee. The safe path is a new Codex session, `/hooks` review, then a prompt or `status` to confirm the heartbeat.
- `--dangerously-bypass-hook-trust` is reserved for already-vetted automated testing and is never normal user guidance.

### Claude Code activation

- Marketplace plugin installation is the plugin trust decision; workspace and managed hook policies remain separate.
- Current Claude Code supports `/reload-plugins`; `/reload-plugins --force` is the documented fallback when normal reload is declined. Restart is the fallback, not the default.
- External updates remain on the old loaded version until reload or restart.
- The first prompt after reload writes the heartbeat; Symphony does not assume reload re-fires `SessionStart`.
- `/hooks` is the human-visible registration view. Managed policies such as managed-only hooks or global hook disabling produce `policy_blocked`.

### Degraded execution

When activation cannot be confirmed, Symphony refuses to label the run guarded. It explains the provider-native recovery step once and offers an explicitly unguarded one-shot route. It never silently downgrades a requested guarded run.

## Assessment and routing

### Assessment contract

Every substantive or uncertain task receives a bounded `strongest/high` assessment. The assessor returns:

- `size`: `small | medium | large`;
- `complexity`: `simple | mixed | complex`;
- risk classification;
- concise rationale;
- recommended topology;
- abstract lead, worker, and consultant capability requirements.

The assessor selects needs, not exact model names. Assessment and execution are separate roles.

### Routing matrix

| Size / complexity | Lead | Execution pattern | Consultation |
|---|---|---|---|
| Small / simple | capable/medium | direct | none |
| Small / mixed | capable/high | direct | optional narrow |
| Small / complex | strongest/high | direct | optional independent check |
| Medium / simple | balanced/medium | direct plus mechanical delegation | none |
| Medium / mixed | balanced/high | selective delegation | optional narrow |
| Medium / complex | capable/high | selective delegation | reserve one slot |
| Large / simple | economy/low | administrative delegation | none |
| Large / mixed | economy/medium | administrative delegation | reserve one slot |
| Large / complex | economy/medium | administrative delegation | strongest/high bounded decisions |

Risk may elevate effort, require an independent check, or reserve consultation. It does not silently change size or complexity.

### Capability resolution

The fixed matrix uses abstract tiers: `economy`, `balanced`, `capable`, and `strongest`. The provider snapshot contains available models, supported efforts, relative tier mapping, source, provider version, and refresh time.

Resolution order:

1. live provider capabilities;
2. cached provider snapshot;
3. conservative shipped fallback.

At session startup and reassessment, snapshots older than 24 hours refresh without blocking use of an existing cache. Context7 verifies current official model and effort guidance when available. The cache stores dated conclusions so ordinary tasks do not repeat research.

When no suitable assessor can run, Symphony selects and discloses a conservative provider route. A weak root does not perform an improvised assessment.

## Lead, worker, and consultant behavior

- Small leads execute directly unless work is long-running and mechanical.
- Medium leads execute integration and quick work, delegating bounded units selectively. They are capable enough to decide when optional consultation is unavailable.
- Large leads are inexpensive administrators. They delegate project work and use reserved consultant capacity for narrow decisions.
- Reserved consultant capacity means an available concurrency slot, not an idle running agent.
- Agent depth is root -> lead -> worker/consultant. Workers do not recursively orchestrate.
- Every actionable worker packet includes its own size and complexity.
- Every actionable consultant decision includes decision-local size and complexity.
- A consultant may split one question into multiple decisions; each decision is separately classified.
- Large leads use the disclosed conservative fallback when required consultation cannot be obtained. They do not absorb specialist reasoning silently.

A worker packet contains only objective, ownership, relevant evidence, constraints, acceptance check, and return contract.

## Reassessment

Reassessment occurs only at material evidence boundaries:

- explicit user request;
- a new task;
- an approved plan;
- completion of a worker wave;
- resume or context compaction;
- interruption;
- reported scope or risk drift.

An unchanged evidence fingerprint skips reassessment mechanically. Reassessment may update topology and routes for subsequent work while preserving active ownership.

## Workflow capability routing

Symphony owns topology, delegation, lifecycle, reassessment, and completion. Supporting systems never ask the user to choose a second execution topology after Symphony has selected one.

| Phase | Primary capability | Supporting constraint |
|---|---|---|
| Requirements and architecture | Superpowers brainstorming | Matt Pocock grilling for ambiguous or contested decisions |
| Current external API, model, or library facts | Context7 | Official sources and dated conclusions |
| Implementation planning | Compound Engineering `ce-plan` | Ponytail prunes speculative work |
| Code execution | Compound Engineering `ce-work` | Superpowers TDD for behavioral changes |
| Review | Compound Engineering `ce-code-review` | Superpowers verification-before-completion |
| Commit, release, and monitoring | Relevant Compound Engineering shipping workflow | Symphony retains lifecycle ownership |
| Every design and code phase | Ponytail | Smallest correct solution; native features before custom machinery |

Missing capabilities use the closest native process. Symphony recommends each useful missing capability at most once per project per Symphony version and never installs it automatically.

## Persistent memory

Compact lifecycle state is always available. Extended document memory is enabled only after Codebase Memory MCP confirms healthy indexing and usable coverage.

The indexed `.symphony/context.md` contains only:

- current goals;
- settled decisions and sources;
- constraints and safety boundaries;
- stable architecture facts;
- verified outcomes;
- unresolved risks;
- concise continuation state.

It excludes transcripts, secrets, raw tool output, inferred telemetry, and routine progress. It updates only after an approved design decision, material reassessment, verified milestone, or handoff. Retrieval uses Codebase Memory before linear file reads. If Codebase Memory is absent or unhealthy, document memory is disabled without delaying the run.

## Commands and visibility

| Intent | Codex | Claude Code |
|---|---|---|
| Enable project | `$symphony:symphony enable` | `/symphony:enable` |
| One managed task | `$symphony:symphony <task>` | `/symphony:start <task>` |
| One ungoverned task | `$symphony:symphony bypass <task>` | `/symphony:bypass <task>` |
| Other controls | `$symphony:symphony <control>` | `/symphony:<control>` |

Controls are `disable`, `status`, `agents [--all]`, `reassess`, `stop [--force]`, and `help`. Help is provider-specific and never recommends unsupported syntax.

`status` reports enablement, hook activation, guarded/degraded state, assessment cell, topology, lead identity, and the compact delegation view.

The chat shows at most five delegation records. Each identity has one latest record. Selection priority is failed, active/waiting, then recently completed. Labels include role plus requested model/effort:

```text
Working: worker [capable/medium] — <identity> — <bounded objective>
Failed: consultant [strongest/high] — <identity> — <bounded decision>
Completed: worker [balanced/medium] — <identity> — <bounded objective>
```

`agents --all` shows the latest state of every delegation in the current run and retained 20-run history. It does not replay every transition. Token and duration fields are omitted when the provider does not expose them.

## Failure handling

- Stale capability data uses the cache immediately while refresh proceeds.
- Missing optional workflows use the closest native process.
- Missing Codebase Memory disables extended memory.
- Corrupt state is preserved for diagnosis. Symphony rebuilds only provider-observable facts and requires reassessment.
- Duplicate events are idempotent.
- Missing packaged files and nonzero hook exits are packaging or execution faults, not trust failures.
- Unknown activation remains pending verification rather than being mislabeled.
- No daemon, external database, transcript mirroring, inferred cost, automatic plugin installation, secret persistence, or workspace rollback is introduced.

## Migration and cutover

Version 1.0 imports only project enablement and user configuration. It archives incompatible active-run state and requires a fresh assessment. Superseded implementation code, tests, and design documents are removed from the active branch; Git history remains the archive.

The release is a replacement only after both provider candidates pass installed-package acceptance. Until then, the current stable release remains available.

## Acceptance evidence

The rewrite uses a small table-driven suite:

1. Pure reducer tests cover all transitions, completion eligibility, replay, and idempotency.
2. One nine-cell routing test verifies abstract routes.
3. Shared contract fixtures verify semantic parity across adapters.
4. Adapter tests cover native commands, malformed controls, hook normalization, provider help, and activation-state rendering.
5. Materialized-package tests validate manifests, root-relative commands, referenced executables, and absence of versioned paths.
6. Installed Codex smokes cover in-session activation, external upgrade plus restart, `/hooks` trust/review, heartbeat, denied trust, active-child Stop protection, interruption/resume, and marketplace path replacement.
7. Installed Claude smokes cover `/reload-plugins`, forced reload fallback, restart fallback, heartbeat, managed-policy blocking, active-child Stop protection, interruption/resume, and marketplace path replacement.
8. Both providers cover automatic project activation, one-shot execution, bypass, assessor fallback, route isolation, safe-boundary reassessment, five-record status capping, optional-capability loss, and a complete real lifecycle.

The release gate requires deterministic tests, provider validation, real installed-plugin smoke evidence, clean package contents, and a successful end-to-end run on both providers.

## Retained failure lessons

These are requirements, not implementation suggestions:

- Project size and task size are separate axes.
- The assessor never becomes the lead implicitly.
- A lead never inherits the assessor's expensive route.
- The root waits through a host blocking primitive; commentary such as "standing by" is not waiting.
- Delegation visibility stores the latest record rather than replacing history with generic waiting labels.
- Supporting workflows cannot reopen Symphony's topology decision.
- Lifecycle correctness comes from reducer state and host events, not final-answer strings.
- Help and malformed controls remain inert in every lifecycle state.
- Active work cannot be completed, promoted, or transferred through stale or untracked receipts.
- Resume reconciles live host state before creating new ownership.
- Marketplace upgrades never leave commands pointing at a removed versioned cache path.
- Hook trust, hook discovery, hook execution, and packaged-file failure are distinct states.
- Metrics not exposed by the host are omitted.
- Optional memory and capability discovery never become startup bottlenecks.

## Provider research baseline

Implementation may refresh these facts through Context7 and official documentation when provider versions change, but it need not rediscover the product decisions above.

- Codex hook trust and plugin hook packaging: <https://learn.chatgpt.com/codex/hooks>
- Codex implementation baseline inspected at `openai/codex` commit `73bf1812722f9b6fdbfac4997b2d96d7b64ff7c3`.
- Claude hook configuration: <https://code.claude.com/docs/en/hooks>
- Claude plugin discovery and reload: <https://code.claude.com/docs/en/discover-plugins>
- Claude hook debugging: <https://code.claude.com/docs/en/hooks-guide>
- Context7 library baselines: `/openai/codex` and `/anthropics/claude-code`.

## Completion criteria

The rewrite is complete when version 1.0.0 satisfies every acceptance item, both installed providers demonstrate guarded lifecycle behavior, superseded artifacts are absent from the active branch, migration preserves enablement/configuration, and the compact user experience no longer relies on receipt parsing or repeated activation warnings.
