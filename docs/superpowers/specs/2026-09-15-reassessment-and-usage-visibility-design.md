# Symphony Reassessment and Usage Visibility Design

## Problem

Symphony currently makes one shallow per-run mode choice, then keeps the strongest available high-effort assessment agent as the execution lead. That couples classification to execution, makes a medium run unnecessarily expensive, and lets the initial mode remain sticky even when the work proves larger or smaller. Delegations are visible only through an on-demand agent listing, and lifecycle records do not retain authoritative usage reported by the host.

Project scale and task execution mode are different facts. A repository can be a long-running large project while a particular task is small. Symphony must retain both without making project age alone force every task into heavy orchestration.

## Goals

- Persist a project profile independently from each run's execution mode.
- Let users request reassessment or set and clear a project-profile override.
- Trigger inexpensive reassessment at material lifecycle boundaries.
- Separate the bounded strong assessor from the mode-appropriate execution lead.
- Make every delegation visible before launch and after completion.
- Report exact per-agent usage when the host exposes it and clearly label missing data.
- Reduce coordinator context growth and reserve expensive models and efforts for work that needs them.

## Non-goals

- Estimate tokens or monetary cost when the host does not expose authoritative usage.
- Promise a hard token budget that the hook cannot enforce.
- Classify every task as large merely because the repository is old or large.
- Store prompts, transcripts, reasoning, worker output, or billing data.
- Add a monitoring service or dependency.

## State model

Project lifecycle state gains a normalized `assessment` object:

```json
{
  "profile": "small | medium | large | null",
  "source": "automatic | manual | null",
  "revision": 0,
  "reason": null,
  "assessed_at": null
}
```

An active run gains:

```json
{
  "mode": "small | medium | large | null",
  "mode_revision": 0,
  "assessment_due": true,
  "mode_history": []
}
```

Each mode-history row contains only mode, project profile, source, bounded reason, revision, and timestamp. Legacy state receives safe defaults during normal read normalization. A manual project profile persists across completed runs and sessions until cleared.

Agent records gain an optional `usage` object. It may contain only host-reported token totals and breakdowns, duration, tool-use count, observation time, and source. Missing numeric values remain absent internally and render as exactly `not exposed by host`.

## Assessment command

Add one command surface:

- `/symphony:assess` marks the current project/run due and requests an evidence-based reassessment.
- `/symphony:assess small|medium|large` stores that persistent manual project profile and reassesses the active run. It does not force every run to use the same execution mode.
- `/symphony:assess auto` removes the manual profile and requests a fresh automatic project assessment.

The command does not itself guess a run mode. The hook updates deterministic policy state and injects the assessment request. `/symphony:status` reports project profile, source, assessment revision, active run mode, mode revision, and whether reassessment is due.

## Assessment protocol

At a new run, a strong assessor receives a bounded, read-only assignment. It uses the strongest available general reasoning model at high effort, no inherited turns, and returns exactly one concise assessment result. It must not implement, edit, delegate, or become the execution lead implicitly.

The assessor considers:

- the current objective and acceptance criteria;
- repository size and architecture evidence;
- git history and worktree state;
- retained Symphony run history and document memory;
- unresolved domains, verification surfaces, and migration/security risk;
- independently dispatchable work and expected coordination overhead.

It returns project profile, run mode, a short evidence-based reason, execution-lead model/effort, assignments, and verification strategy. The root visibly announces the assessment delegation and its result.

The lifecycle receipt is run-bound and exact:

```text
SYMPHONY_ASSESSMENT:<run-id>:<project-profile>:<run-mode>
SYMPHONY_ASSESSMENT_REASON:<single bounded line>
```

The hook accepts it only from the current run's registered assessor or from the root's final/control response where the run id matches. It increments revisions and retains prior modes. A manual project profile cannot be overwritten by an automatic receipt; the assessor uses it as project context while still choosing the task's run mode.

## Reassessment triggers

The deterministic hook marks `assessment_due` without attempting model judgment when:

- a run starts without a current assessment receipt;
- an active run receives a new non-control user prompt that expands or changes the objective;
- a session resumes or compacts;
- the last active non-lead agent in a dispatched wave becomes terminal;
- the user invokes `/symphony:assess` or changes the project profile.

The current execution lead performs the cheap reassessment at ordinary boundaries. A new strong assessor is required only for initial assessment, explicit `/symphony:assess`, a proposed mode change, or unresolved high-risk ambiguity. If evidence is unchanged, the lead clears the due flag with the same profile/mode receipt and a short reason; it does not spawn extra workers.

## Mode-specific execution

Assessment and execution use different agents:

- **Small:** a capable direct executor at medium effort by default. Raise only the narrow unresolved unit. No workers merely to justify Symphony.
- **Medium:** a balanced agentic execution lead at medium effort. It performs quick and integration-sensitive work, delegates verbose discovery and independent specialist units, and normally runs no more than two workers concurrently.
- **Large:** a capable coordinator at medium or high effort based on risk. It delegates implementation in dependency-aware waves. The strongest/high model is reserved for architecture, hard diagnosis, irreversible decisions, and final high-risk review.

Every route uses the cheapest live model/effort that meets the unit's requirements. Effort is a budget, not a quality ranking. A mode change can replace the execution lead with the newly appropriate profile.

On resume or after compaction, Symphony starts a fresh execution lead from bounded lifecycle/document memory instead of resuming an indefinitely growing lead context. Existing worker ownership is reconciled first.

## Delegation visibility

Before every assessment, lead, worker, or reviewer spawn, the root emits a concise commentary line containing:

```text
Delegating: <role> — <bounded objective> — <model>/<effort> — <reason>
```

After completion it emits:

```text
Completed: <agent id/role> — <status> — tokens <value or not exposed by host> — duration <value or not exposed by host>
```

This is a required execution contract because lifecycle hooks cannot reliably create host-visible chat messages for every spawn. It complements rather than replaces `/symphony:agents`.

`/symphony:agents` adds total tokens, input, output, cache creation/read, duration, tool uses, and usage source. `/symphony:agents --all` retains the same columns for historical runs. `/symphony:status` reports known token totals plus the count of agents with unreported usage; it never presents a partial total as complete.

## Usage collection

The shared hook accepts usage only from host event fields associated with a known agent id. For Claude, a `PostToolUse` hook scoped to the Agent tool records synchronous completion fields such as `totalTokens`, the `usage` breakdown, `totalDurationMs`, and `totalToolUseCount`. Background launches commonly lack these values and remain unreported until a later host event supplies them.

Codex and future host events use the same normalized extractor when equivalent fields exist. When the installed host does not expose usage to plugin hooks or live agent tools, Symphony reports `not exposed by host`. It never reads agent transcripts to synthesize totals.

Late usage follows the same ownership rule as late SubagentStop: update the uniquely owning active or historical run, never the newest run by default. Malformed, negative, boolean, or unowned usage values are ignored. Usage collection never blocks Stop or force-stop recovery.

## Token-control rules

- The strong assessor is one bounded assignment with one concise result and never implements.
- Execution leads start at the mode-specific defaults above, not strongest/high universally.
- Worker packets contain only acceptance criteria and relevant evidence.
- Verbose exploration, logs, broad searches, and mechanical checks go to cheaper focused workers when delegation saves parent context.
- A worker is not spawned unless its context isolation, parallelism, or specialization repays dispatch overhead.
- Reassessment reuses current evidence and does not repeat a full repository scan without detected drift.
- Exact usage is observational. Routing rules, bounded contexts, and lead rotation are the enforceable cost controls.

## Failure behavior

- Missing assessor capability fails closed before project work, as today.
- Missing usage data does not block work; it is labeled honestly.
- Invalid assessment receipts do not change profile or mode.
- A corrupt assessment or usage object is normalized or ignored without preventing `/symphony:stop --force`.
- A failed reassessment preserves the previous mode as a hint and leaves `assessment_due=true`.
- Manual profile changes are explicit and reversible with `/symphony:assess auto`.

## Testing

Deterministic tests must cover:

- legacy-state normalization;
- manual profile persistence and `auto` clearing;
- raw and command-template assessment parsing;
- initial and reassessment receipts, authorization, revisions, and mode history;
- every reassessment trigger without duplicate strong assessments;
- manual profile precedence over automatic receipts;
- mode-specific model/effort and concurrency instructions;
- required before/after delegation commentary contract;
- Claude synchronous Agent usage ingestion;
- missing, malformed, unowned, late, and background usage;
- active and `--all` usage rendering and honest partial totals;
- status output and read-only inspection behavior;
- Stop, force-stop, memory, interrupted-session, and late-agent regressions.

Hosted evaluation must verify that a weak root visibly announces the bounded assessor, chooses a separate mode-appropriate execution lead, and does not keep the strongest/high assessor as a medium execution lead.

## Documentation and migration

README and `/symphony:help` explain the two-level assessment, reassessment command, visible delegation messages, usage limitations, and token-control defaults. Existing enabled projects and active runs migrate on first lifecycle read. No repository is hard-coded by path: this project can be set to a persistent large profile with `/symphony:assess large`, while each task remains independently sized.
