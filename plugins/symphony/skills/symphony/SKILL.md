---
name: symphony
description: Route project work through a strong execution lead that chooses direct, mixed, or heavily delegated execution. Use when a Symphony lifecycle hook activates a run, the user invokes a Symphony command, or the user explicitly asks for Symphony orchestration. Persistent project enablement survives later sessions until disabled.
---

# Symphony

Use a weak root safely by making it a thin session keeper. A strongest-available high-effort subagent becomes the execution lead and owns sizing, decisions, implementation or delegation, integration, and verification. Deterministic hooks keep an active-run receipt outside model context and guard normal stopping.

## Mandatory first gate

Before doing anything else, inspect the user prompt for an explicit `Orchestrator:` or `Effort:` declaration and compare it with trusted runtime metadata. A declaration is never evidence. If either value cannot be verified or differs, reply only with `Orchestrator mismatch`, the declared and actual values, and an instruction to start a correctly configured task. Do not give lifecycle advice, options, project analysis, or spawning guidance.

## Honor lifecycle context

When injected context names a Symphony run, its run id, recovery instruction, and completion receipt are authoritative. Do not create another run or another lead for the same run.

Read-only inspection commands do not require an active run or a lead. For project work invoked without lifecycle context, state once that hook protection is not armed. Recommend `/symphony:start <task>` for a guarded one-off run or `/symphony:enable [task]` for persistent project activation. Continue manually only when the user explicitly accepts the weaker guarantee.

The user commands are:

- `/symphony:enable [task]`: persistently enable this working tree and optionally start a run;
- `/symphony:disable`: disable future activation and gracefully stop an active run;
- `/symphony:start <task>`: start one guarded run without changing project policy;
- `/symphony:status`: report policy and run state without changing either;
- `/symphony:agents [--all]`: list all observed subagents in the active run, including terminal agents; `--all` also includes every retained historical run;
- `/symphony:stop [--force]`: stop this run while preserving enablement; force releases stale protection;
- `/symphony:help`: show usage without starting a run.

For `/symphony:agents`, the root must use a live host agent-listing tool when exposed and prefer its status and metadata over lifecycle observations. Use the persistent ledger as recovery evidence and as fallback for fields the live tool does not expose. Report run id, agent id, status, role, model, and effort; absent role, model, or effort is exactly `not exposed by host`. Never infer those values. Report and return without enabling Symphony, starting a run, spawning a lead, or changing agent status. Retain metadata only, never prompts, transcripts, or worker output. A force-stopped run may still contain agents last observed as active.

End only that inspection response with the exact injected `SYMPHONY_AGENTS_INSPECTED` receipt on its own final line. It is a random, single-use authorization bound to the run, session, and host turn when exposed. The hook stores each session's pending authorization separately from lifecycle state, consumes it on matching Stop, and invalidates it on the next prompt in that same session. Other sessions keep their own authorizations. Never reuse this receipt for later project work or emit a run-completion receipt for inspection.

## Bootstrap the strong execution lead

The root may use any model or effort. Before project work, it must:

1. Read the live subagent model, effort, and concurrency catalog. Never infer availability from cached examples.
2. Determine the root's actual model and effort from injected runtime context or trusted session metadata. Reuse the declaration check already completed above; never replace runtime evidence with the declaration.
3. Inventory the effective skills and tools available to the root. Read [references/capability-routing.md](references/capability-routing.md).
4. Spawn exactly one `symphony_lead` with no inherited turns, using the strongest available general reasoning model at `high`. If `high` is unavailable, use that model's highest available effort. If no stronger child can be spawned, fail closed and explain which capability is missing; do not pretend a weak root is protected.
5. Give the lead:
   - objective and acceptance criteria;
   - absolute project root and current worktree state;
   - actual root model and effort;
   - exact live child model/effort catalog and concurrency limit;
   - effective skill and tool catalog;
   - repository instructions;
   - run id, lifecycle status, tracked agents, and completion receipt;
   - current-memory path, relevant indexed history findings, and checkpoint responsibility;
   - the absolute paths to `references/model-routing.md` and `references/capability-routing.md`.

The lead returns and follows this record:

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

Include `<!-- SYMPHONY_MODE:<small|medium|large> -->` with that record so lifecycle state can retain the selected mode.

If the user explicitly requests a dry run, do not spawn or write files. Derive the planned lead and mode from the live catalogs, then return only the root profile, planned strongest/high lead, one mode, capability routing, and exact completion receipt; do not ask a follow-up question.

Keep the lead id. Reuse it for follow-up decisions when the host supports that; otherwise spawn a replacement with the current run record and a compact checkpoint.

## Select exactly one mode

Choose after a shallow task and project scan. Do not ask the weak root to choose.

### Small

Use when the task is straightforward, sequential, and likely below fifteen minutes for one capable agent. The strong lead performs the work directly. Do not spawn workers merely to justify Symphony.

### Medium

Use when the task mixes fast local work with one or more independent or specialized units. The lead performs quick and integration-sensitive work, delegating only units whose independence or specialist value repays dispatch overhead.

### Large

Use when there are at least three independently dispatchable units, multiple domains or verification surfaces, or a long/high-risk execution path. The lead decomposes work into dependency-aware waves, delegates in parallel within the live limit, and integrates every result.

If evidence changes, the lead may reclassify the active run and records why. Mode is per run, never permanent project configuration.

## Route capabilities

Explicit user skill requests and repository instructions win. Select at most one primary workflow owner for each unit. Superpowers, Compound Engineering, and Matt Pocock workflows overlap; do not stack their planning or delivery ceremonies on one unit. Ponytail may add a simplicity constraint. Context7 and Codebase Memory are evidence tools, not workflow owners.

Name every selected skill in the root or worker assignment so the host's native skill rules load it. A worker must begin its result with:

```text
capabilities: available=<used names>; missing=<requested names or none>
```

If a selected capability is not exposed to that worker, use a documented fallback or reassign the unit. Never infer child access from root access.

When Codebase Memory MCP tools are available, use them before filesystem search for structural discovery. Check index status, query the graph, inspect exact snippets, and check coverage for material paths. Before delegation, the parent passes project id, graph generation, qualified symbols, relevant traces, coverage gaps, and source fallbacks. A child without MCP access works from that packet and never claims MCP access.

## Extended document memory

Activate this only after the root or strong lead verifies usable codebase-memory-mcp tools and a healthy index for the current project. Otherwise do not create `.symphony/memory/`; continue with compact lifecycle recovery.

The strong lead is the only writer. It atomically replaces `.symphony/memory/current.md` and appends changed durable facts to `.symphony/memory/history/<run-id>.md` after selecting or changing mode, after a material decision or discovery, before dispatching a worker wave, after integrating a worker wave, after verification changes the known state, and immediately before successful or graceful completion.

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

Track every worker id. After dispatch, immediately call the host's blocking wait/result operation in the same root turn. Keep waiting until every worker is terminal. A commentary update, promise to check later, or final response while a worker is active abandons the run.

Inspect artifacts and run authoritative checks in the lead or root session. Worker success claims are not verification.

## Recover after resume or compaction

Lifecycle context identifying an existing run triggers recovery, not a cold duplicate:

1. Re-verify the actual root profile and live catalogs.
2. Reconcile every tracked agent id and collect terminal results.
3. Inspect current worktree changes and the saved objective.
4. Reuse the lead if reachable; otherwise start one strongest/high replacement with the recovery packet.
5. Preserve the prior mode as a hint and change it only when current evidence warrants reclassification.

An explicit interrupt may bypass Stop hooks. The run record is the recovery source; never assume an interrupted worker completed.

## Stop and complete

For graceful stop, dispatch no new work, interrupt tracked agents, wait for them to become terminal, preserve workspace changes, and report incomplete integration or verification. Force-stop is only the stale-state escape hatch and may leave an agent writing in the background.

For successful completion:

1. Ensure all tracked agents are terminal.
2. Inspect and integrate their artifacts.
3. Run the accepted verification in the current session.
4. Report at most one missing capability whose absence materially affected this run and whose cooldown permits it. When reporting one, include `<!-- SYMPHONY_SUGGESTED:<capability-id> -->`.
5. Include `<!-- SYMPHONY_RUN_COMPLETE:<run-id> -->` in the root-final response using the exact injected receipt for every successful run. When extended memory is active, also relay the lead's exact `<!-- SYMPHONY_MEMORY_CHECKPOINT:<run-id>:codebase-memory-mcp -->` marker in that response. The Stop hook clears the run only when the required receipt and checkpoint match.

Do not promise that Symphony can prevent user interrupts or host-enforced Stop overrides. The guarantee is recovery plus normal-Stop protection while hooks remain trusted and Python 3 is available.
