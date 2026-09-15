---
name: symphony
description: Route project work through bounded assessment and mode-appropriate execution. Use when a Symphony lifecycle hook activates a run, the user invokes a Symphony command, or the user explicitly asks for Symphony orchestration. Persistent project enablement survives later sessions until disabled.
---

# Symphony

Use a weak root safely by making it a thin session keeper. A bounded strongest/high assessor sizes the run; a separate execution lead performs the selected mode. Deterministic hooks keep an active-run receipt outside model context and guard normal stopping.

## Mandatory first gate

Before doing anything else, inspect the user prompt for an explicit `Orchestrator:` or `Effort:` declaration and compare it with trusted runtime metadata. A declaration is never evidence. If either value cannot be verified or differs, reply only with `Orchestrator mismatch`, the declared and actual values, and an instruction to start a correctly configured task. Do not give lifecycle advice, options, project analysis, or spawning guidance.

## Honor lifecycle context

When injected context names a Symphony run, its run id, recovery instruction, and completion receipt are authoritative. Keep one run and at most one active execution lead. Recovery may replace the lead after reconciling workers and making the prior lead terminal; retain the run id and use bounded lifecycle/document memory for its replacement.

Read-only inspection commands do not require an active run or a lead. For project work invoked without lifecycle context, state once that hook protection is not armed. Recommend `/symphony:start [--dry-run] <task>` for a guarded one-off run or `/symphony:enable [task]` for persistent project activation. Continue manually only when the user explicitly accepts the weaker guarantee.

The user commands are:

- `/symphony:enable [task]`: persistently enable this working tree and optionally start a run;
- `/symphony:disable`: disable future activation and gracefully stop an active run;
- `/symphony:start [--dry-run] <task>`: start one guarded run without changing project policy; `--dry-run` validates planned routing without spawning agents or writing project files;
- `/symphony:status`: report project profile/source and assessment revision, run mode/revision and reassessment due, and partial final-request usage observations without changing lifecycle state;
- `/symphony:assess [small|medium|large|auto]`: request fresh strong assessment, set a persistent manual project profile, or clear the override;
- `/symphony:agents [--all]`: list all observed subagents in the active run, including terminal agents; `--all` also includes every retained historical run;
- `/symphony:stop [--force]`: stop this run while preserving enablement; force releases stale protection;
- `/symphony:help`: show usage without starting a run.

For `/symphony:agents`, the root must use a live host agent-listing tool when exposed and prefer its status and metadata over lifecycle observations. Use the persistent ledger as recovery evidence and as fallback for fields the live tool does not expose. Report run id, agent id, status, role, model, effort, final-request total/input/output/cache-creation/cache-read tokens, run duration/tool uses, and usage source/token scope; every unavailable field is exactly `not exposed by host`. Claude Agent `PostToolUse` token fields are final-request observations, never whole-agent totals; duration and tool uses describe the whole agent run. Never infer usage or cost. Report and return without enabling Symphony, starting a run, spawning a lead, or changing agent status. Retain metadata only, never prompts, transcripts, reasoning, or worker output. A force-stopped run may still contain agents last observed as active.

For `/symphony:status`, report `Observed final-request tokens (partial)` plus agents lacking final-request totals. Do not present this observation as a complete agent total.

End every terminal control response with the exact injected `SYMPHONY_CONTROL_HANDLED` receipt on its own final line. This includes help, status, agents, empty enable/start, assessment, stop/disable, and invalid control input. It is a random, single-use authorization bound to the run, session, and host turn when exposed. The hook stores each session's pending authorization separately from lifecycle state, consumes it on matching Stop, and invalidates it on the next prompt in that same session. Other sessions keep their own authorizations. Never reuse it for later project work or emit a run-completion receipt for a control.

## Bootstrap assessment, then execution

The root may use any model or effort. The root does not inspect the project, inventory capabilities, choose document memory, or run verification. It reads the injected objective, run id, project profile, and required assessor profile, then performs only the announced spawn, registration, receipt relay, and blocking wait steps.

1. Emit `Delegating: symphony_assessor — <bounded objective> — <model>/<effort> — initial or required reassessment` before spawning exactly one `symphony_assessor` with no inherited turns, using the injected strongest-available general reasoning model profile at `high`. If `high` is unavailable, use that model's highest available effort. If no stronger child can be spawned, fail closed and explain which capability is missing; do not pretend a weak root is protected.
2. Give the assessor the injected objective, acceptance criteria, run id, project profile, lifecycle status, exact assessment receipt format, and the absolute paths to `references/model-routing.md` and `references/capability-routing.md`. The assessor owns initial discovery, capability routing, memory choice, and the verification strategy. It reads the current worktree, repository instructions, and its effective skill/tool catalog itself.

The assessor is read-only: it must not implement, edit, delegate, or become the execution lead. It returns exactly one concise assessment result: project profile, run mode, bounded reason, execution-lead model/effort, assignments, and verification strategy, followed by exactly these two lines:

```text
SYMPHONY_ASSESSMENT:<run-id>:<project-profile>:<run-mode>
SYMPHONY_ASSESSMENT_REASON:<single bounded line>
```

Bind `<run-id>` to the current run. The current run's root must relay both lines in an owner control response and wait for the Stop hook to persist the accepted assessment before execution, then emits `Completed: <agent id/role> — <status> — tokens <value or not exposed by host> — duration <value or not exposed by host>`.

3. Spawn the separate execution lead selected by the accepted assessment. Emit `Delegating: symphony_lead — <bounded objective> — <model>/<effort> — selected <mode> execution` first and give it the assessor result plus the bounded objective, run id, registration and completion receipts. The execution lead owns implementation and authoritative verification. After it is terminal, emit `Completed: <agent id/role> — <status> — tokens <value or not exposed by host> — duration <value or not exposed by host>`.

The execution lead returns and follows this record:

```text
mode: small | medium | large
why: <size, dependency, and risk evidence>
primary_workflow: <one skill or none>
supporting_capabilities: <skill/tool -> purpose>
assignments: <direct work and delegated units with model/effort>
schedule: <serial work or parallel waves>
verification: <authoritative checks>
suggestion: <one missing material capability or none>
```

Include `<!-- SYMPHONY_MODE:<small|medium|large> -->` with that record to report the accepted mode. Mode markers do not authorize mode changes; a changed mode requires a fresh authorized assessment receipt. Normal completion has no marker-only fallback.

If the user invokes `/symphony:start --dry-run <task>`, do not spawn agents or write project files. Derive the planned strongest/high assessor and the separate execution lead from the selected mode and live catalog; do not hard-code the execution lead or mode. Report planned `Delegating:` and `Completed:` records for both roles, one mode, capability routing, and mode marker; do not ask a follow-up question. These are planned records only, not claims that agents ran. Only a run persisted with `dry_run=true` may bypass accepted assessment; merely describing work as a dry run does not.

Keep the execution lead id while its context remains bounded. On resume or compaction, reconcile tracked workers first, then start a fresh execution lead from bounded lifecycle/document memory rather than indefinitely resuming context.

### Register host agent roles

`symphony_assessor` and `symphony_lead` name assignments, not required host agent types. Use a type exposed by the live host, such as Claude `general-purpose` or an installed plugin-scoped agent. The hook never trusts a role name in `agent_type` or worker text as authorization.

After the host returns an agent id, the owning root registers each role with an exact standalone line in its control response:

```text
SYMPHONY_REGISTER:<run-id>:assessor:<agent-id>
SYMPHONY_REGISTER:<run-id>:lead:<agent-id>
```

Emit only the applicable line for that agent. Registration requires a host-observed id belonging uniquely to the current run; it cannot replace an active role holder or give one agent both roles. For a synchronous Agent call, register after it returns and relay the assessor's assessment lines in the same control response. For a background call, register after launch; register the assessor before its completion if its SubagentStop should authorize the receipt directly. On a host whose trusted hook declaration includes child lifecycle events, an initial strong-assessment root relay is accepted only when it identifies the current registered assessor and that assessor is terminal; an ordinary same-mode relay likewise requires the current registered lead to be terminal. The hook blocks the control response from ending the run, persists valid registration/assessment, and continues the same run; omit the run-completion receipt from these control responses. This is the exception to waiting before a root response while agents are active: registration hands control back to the hook, then the root immediately resumes waiting.

If the host exposes no child lifecycle events, role registration is unavailable; the root keeps the host ids for reconciliation and relays assessment receipts through the owning-root Stop path. Never claim the persistent ledger registered an unobserved id. A fresh `/symphony:assess` invalidates the prior assessor authorization; reconcile it and register the new assessor. Replayed terminal completions never reassess the run.

## Delegation visibility

Before every assessor, lead, worker, or reviewer spawn, its dispatcher emits `Delegating: <role> — <bounded objective> — <model>/<effort> — <reason>`. While blocked on active agents, it may emit `Waiting: <role or wave> — <bounded in-progress fact>`. `Waiting:` may report only observed lifecycle state; never speculate about checks, blockers, or results. After each completion, it emits `Completed: <agent id/role> — <status> — tokens <value or not exposed by host> — duration <value or not exposed by host>`. These commentary records complement the persistent agent ledger; never invent usage that the host did not expose.

## Ordinary reassessment

The lifecycle record distinguishes ordinary reassessment due from strong assessment required. Initial runs and explicit `/symphony:assess` require a strong assessor until its authorized receipt clears that requirement. An owner prompt, final worker wave, interrupt, or resume marks ordinary reassessment due only: the current execution lead cheaply reassesses from current evidence, or recovery starts a fresh mode-appropriate lead for that cheap reassessment. For unchanged mode it returns the same two-line current-run receipt above; the current run's root must relay it because an execution-lead `SubagentStop` receipt is not authorized. A proposed mode change or unresolved high-risk ambiguity requires a new strong assessor before further execution. Reassessment reuses current evidence; do not repeat a broad repository scan without drift.

## Select exactly one mode

Choose after a shallow task and project scan. Do not ask the weak root to choose.

### Small

Use when the task is straightforward, sequential, and likely below fifteen minutes for one capable agent. Use a capable direct executor at `medium`. Do not spawn workers merely to justify Symphony.

### Medium

Use when the task mixes fast local work with one or more independent or specialized units. Use a balanced agentic execution lead at `medium`; it performs quick and integration-sensitive work and runs at most two workers concurrently.

### Large

Use when there are at least three independently dispatchable units, multiple domains or verification surfaces, or a long/high-risk execution path. Use a capable coordinator at `medium` or `high` based on risk; it decomposes work into dependency-aware waves and integrates every result. Reserve strongest/high for narrow hard decisions, architecture, irreversible choices, and high-risk final review.

If evidence changes, the lead may reclassify the active run and records why. Mode is per run, never permanent project configuration.

## Route capabilities

Explicit user skill requests and repository instructions win. Select at most one primary workflow owner for each unit. Superpowers, Compound Engineering, and Matt Pocock workflows overlap; do not stack their planning or delivery ceremonies on one unit. Ponytail may add a simplicity constraint. Context7 and Codebase Memory are evidence tools, not workflow owners.

The assessor selects capabilities and the execution lead names every selected skill in its own or a worker assignment so the host's native skill rules load it. A worker must begin its result with:

```text
capabilities: available=<used names>; missing=<requested names or none>
```

If a selected capability is not exposed to that worker, use a documented fallback or reassign the unit. Never infer child access from root access.

When Codebase Memory MCP tools are available to the assessor or execution lead, use them before filesystem search for structural discovery. Check index status, query the graph, inspect exact snippets, and check coverage for material paths. Before worker delegation, the execution lead passes project id, graph generation, qualified symbols, relevant traces, coverage gaps, and source fallbacks. A child without MCP access works from that packet and never claims MCP access.

## Extended document memory

Activate this only after the assessor or execution lead verifies usable codebase-memory-mcp tools and a healthy index for the current project. Otherwise do not create `.symphony/memory/`; continue with compact lifecycle recovery.

The execution lead is the only writer. It atomically replaces `.symphony/memory/current.md` and appends changed durable facts to `.symphony/memory/history/<run-id>.md` after selecting or changing mode, after a material decision or discovery, before dispatching a worker wave, after integrating a worker wave, after verification changes the known state, and immediately before successful or graceful completion.

`current.md` is compact and bounded. It contains, in order: Run; Objective and acceptance criteria; Invariants and constraints; Decisions and rationale; Important discoveries; Completed work; Pending work; Verification evidence; Risks and blockers; Retrieval index.

```markdown
# Symphony Current Memory

## Run
## Objective and acceptance criteria
## Invariants and constraints
## Decisions and rationale
## Important discoveries
## Completed work
## Pending work
## Verification evidence
## Risks and blockers
## Retrieval index
```

Each history checkpoint records its timestamp, run id, mode, reason for the checkpoint, changed facts, decisions, evidence, and next action. The retrieval index contains short search terms and references to relevant history headings, qualified code symbols, graph generation, and evidence paths.

On recovery, read `current.md` directly, check index status, query only relevant history sections, run `check_index_coverage` for every memory path used, and fall back to targeted direct reads for stale or uncovered sections. Give workers only relevant invariants, decisions, evidence references, and acceptance criteria.

After a durable checkpoint, the lead emits `<!-- SYMPHONY_MEMORY_CHECKPOINT:<run-id>:codebase-memory-mcp -->`. The root relays it in the root-final response alongside the exact completion receipt. Never write secrets, environment values, unnecessary personal data, transcripts, or copied source bodies.

Recovery context includes persisted `enabled` and `checkpoint_at`. If codebase-memory-mcp becomes unavailable, its index becomes unhealthy, or either required memory file disappears, report the loss and emit `<!-- SYMPHONY_MEMORY_UNAVAILABLE:<run-id>:codebase-memory-mcp -->`. The root relays that run-bound receipt; SubagentStop or Stop disables extended memory while retaining the last checkpoint time. Continue from compact lifecycle state and verified worktree evidence, without inventing lost facts. Normal mode and completion receipts can then finish the run without force-stop. An unavailable receipt takes precedence over a checkpoint in the same response. Reactivate only after renewed capability verification and a fresh durable checkpoint. While memory remains available, a missing or stale final checkpoint still blocks completion.

## Dispatch and wait

Every worker packet contains:

```text
objective: one independently verifiable result
ownership: exact files, modules, or research question
context: only required evidence, including current-memory path and relevant indexed history findings
constraints: interfaces, selected skills, decisions to preserve, and workers return facts for the lead checkpoint
done: observable acceptance checks
return: capability receipt, conclusions, changed files, checks, blockers
```

The execution lead tracks every worker id. After dispatch, it immediately calls the host's blocking wait/result operation in the same turn. It keeps waiting until every worker is terminal. A commentary update, promise to check later, or final response while a worker is active abandons the run.

Inspect artifacts and run authoritative checks in the execution lead. Worker success claims are not verification.

## Recover after resume or compaction

Lifecycle context identifying an existing run triggers recovery, not a cold duplicate:

1. Reconcile every tracked agent id and collect terminal results.
2. Start a fresh execution lead from bounded lifecycle/document memory; it inspects current worktree changes and the saved objective.
3. Bypass the assessor when accepted evidence has a valid mode and `mode_revision > 0` and strong assessment required is false; ordinary reassessment due still starts a fresh mode-appropriate execution lead from bounded lifecycle/document memory for cheap reassessment. A legacy mode alone is not accepted evidence. Otherwise require a new assessor for initial/explicit assessment, a proposed mode change, or unresolved high-risk decision.
4. Preserve the prior mode as a hint and change it only when current evidence warrants reclassification.

An explicit interrupt may bypass Stop hooks. The run record is the recovery source; never assume an interrupted worker completed.

## Stop and complete

For graceful stop, dispatch no new work, interrupt tracked agents, wait for them to become terminal, preserve workspace changes, and report incomplete integration or verification. Force-stop is only the stale-state escape hatch and may leave an agent writing in the background.

For successful completion:

1. Ensure all tracked agents are terminal.
2. Require the execution lead's integrated artifacts and authoritative verification result.
3. Report at most one missing capability whose absence materially affected this run and whose cooldown permits it. When reporting one, include `<!-- SYMPHONY_SUGGESTED:<capability-id> -->`.
4. Include `<!-- SYMPHONY_RUN_COMPLETE:<run-id> -->` in the root-final response using the exact injected receipt for every successful run. When extended memory is active, also relay the lead's exact `<!-- SYMPHONY_MEMORY_CHECKPOINT:<run-id>:codebase-memory-mcp -->` marker in that response. The Stop hook clears the run only when the required receipt and checkpoint match.

Do not promise that Symphony can prevent user interrupts or host-enforced Stop overrides. The guarantee is recovery plus normal-Stop protection while hooks remain trusted and Python 3 is available.
