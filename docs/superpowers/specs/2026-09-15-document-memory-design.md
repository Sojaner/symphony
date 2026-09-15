# Symphony Document Memory Design

## Purpose

Long-running Symphony work must retain its facts, decisions, constraints, progress, and verification evidence across context compaction, session resume, and replacement execution leads. Memory must remain fast to retrieve and must not claim codebase-memory-mcp support when that tool is unavailable.

## Decisions

### Use two memory tiers

Every active run keeps the existing compact lifecycle record in plugin data. This remains the deterministic recovery anchor and contains only the run id, objective, mode, owner, tracked agents, lifecycle status, and completion receipt.

Extended document memory is optional and enabled only after the strong execution lead verifies all of the following from the live runtime:

1. codebase-memory-mcp tools are exposed to the lead or root;
2. the current repository is indexed;
3. the memory paths have usable index coverage, or a targeted source-read fallback is available while the index refreshes.

If any check fails, Symphony continues with the compact lifecycle record and may issue its existing throttled capability suggestion. It does not create extended memory documents or pretend indexed retrieval is active.

### Keep indexed memory inside the project

When enabled, memory lives under:

```text
.symphony/memory/current.md
.symphony/memory/history/<run-id>.md
```

`current.md` is a bounded, directly readable checkpoint for the active project and run. Direct reading is faster and more reliable than an index query for this single hot document.

The history file is append-only for one run and contains durable checkpoints useful across later runs. codebase-memory-mcp indexes its Markdown sections for targeted lookup. Symphony does not add the directory to `.gitignore`; ignoring it could exclude it from indexing. The user decides whether to commit, ignore, or delete these project-local files after considering that choice's indexing consequences.

### Use a fixed document contract

`current.md` contains these sections in this order:

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

The retrieval index contains short search terms and references to relevant history headings, qualified code symbols, graph generation, and evidence paths. It does not duplicate full source, transcripts, or worker conversations.

Each history checkpoint records its timestamp, run id, mode, reason for the checkpoint, changed facts, decisions, evidence, and next action. The strong lead is the only writer. Workers return compact findings to the lead and never edit the shared memory documents concurrently.

### Update at stable boundaries

The lead refreshes `current.md`:

- after selecting or changing mode;
- after a material decision or discovery;
- before dispatching a worker wave;
- after integrating a worker wave;
- after verification changes the known state;
- immediately before successful or graceful completion.

At those same boundaries, the lead appends only the changed durable facts to the run history. This avoids token-heavy transcript mirroring and concurrent-write conflicts.

### Retrieval is bounded and evidence-first

On bootstrap, resume, or compaction, the root passes the candidate memory paths to the strong lead. The lead:

1. reads `current.md` directly when it exists;
2. checks codebase-memory-mcp project and index status;
3. queries indexed history only for facts needed by the current objective;
4. checks index coverage for every memory document used as evidence;
5. falls back to a targeted direct read for stale or uncovered sections;
6. gives workers only the relevant memory excerpt and graph findings.

The full history is never injected into every prompt. A worker receives the current objective, applicable invariants and decisions, exact evidence references, and its own acceptance criteria.

### Hooks enforce freshness without understanding prose

The hook script cannot invoke MCP tools or judge Markdown content. It therefore supplies deterministic paths and validates only observable lifecycle facts.

The run record stores a memory candidate path plus whether extended memory was activated and the last observed checkpoint time. The lead emits:

```text
<!-- SYMPHONY_MEMORY_CHECKPOINT:<run-id>:codebase-memory-mcp -->
```

on a material checkpoint. The first matching marker activates extended memory for the run; subagent-stop and normal-stop processing record the observation time. Successful completion of an activated memory run requires the matching marker in the final response and verifies that `current.md` is non-empty and was modified during the run. Runs without verified MCP memory remain valid under the existing mode and completion receipts.

Recovery context identifies whether extended memory was active, supplies both paths, and directs the replacement lead to validate the index before trusting historical retrieval.

### Memory is safe and user-controlled

Memory documents must not contain credentials, access tokens, private keys, raw environment values, personal data unnecessary to the task, or full transcripts. Store references to sensitive configuration, never its value.

Disabling Symphony stops future automatic activation but does not delete project memory. Force-stop also preserves it for diagnosis. Users can remove `.symphony/memory/` explicitly; Symphony treats missing files as memory unavailable and falls back to compact lifecycle state.

### Agent inspection is read-only and honest

`/symphony:agents` lists every subagent observed in the active run, including terminal agents. `/symphony:agents --all` also lists compact ledgers from completed, gracefully stopped, and force-stopped historical runs.

Each row contains run id, agent id, lifecycle status, role, model, and effort. Values come from live host listing data first and lifecycle event metadata second. A host that does not expose model or effort is reported as `not exposed by host`; Symphony never guesses. The hook retains metadata only, not prompts, transcripts, or worker output. The command is read-only and never enables Symphony, starts a run, or changes agent status.

## Files and interfaces

- `plugins/symphony/scripts/symphony_hook.py`: deterministic candidate paths, memory marker parsing, recovery context, and completion freshness checks.
- `plugins/symphony/tests/test_symphony_hook.py`: MCP-disabled fallback, memory activation receipts, compaction recovery, freshness, and missing-file tests.
- `plugins/symphony/skills/symphony/SKILL.md`: lead-owned checkpoint and indexed retrieval protocol.
- `plugins/symphony/skills/symphony/references/capability-routing.md`: codebase-memory-mcp activation and fallback rules.
- `plugins/symphony/commands/help.md` and `README.md`: memory behavior, location, retention, privacy, and deletion guidance.
- `plugins/symphony/commands/agents.md`: active-run agent inspection and the `--all` historical view.
- both plugin manifests: version `0.14.0`; version `0.13.0` is intentionally skipped. Marketplace manifests keep their existing source-only schema.

No daemon, database, new dependency, background indexer, or concurrent memory writer is introduced.

## Failure behavior

- Missing codebase-memory-mcp: do not create extended documents; continue with compact lifecycle recovery.
- Missing or unhealthy project index: do not activate extended memory until healthy; use the normal fallback and optional throttled suggestion.
- Stale index: read `current.md` directly and use targeted source fallback for required historical sections.
- Missing current document on recovery: report the loss, rebuild it from lifecycle state and verified worktree evidence only after MCP is healthy, and never invent lost decisions.
- Stale or absent final checkpoint: block normal completion and request a fresh self-contained checkpoint.
- Corrupt or malformed Markdown: preserve the file, report the problem, and rebuild to a new temporary document before atomic replacement.
- Explicit interrupt or host override: recover from the compact run record first, then validate document memory and index state.

## Verification

Test-driven implementation will first add failing lifecycle tests for:

1. runs without MCP memory remaining valid and document-free;
2. deterministic project-local memory paths in bootstrap and recovery context;
3. activation and checkpoint markers being retained;
4. compaction recovery injecting active-memory instructions;
5. completion blocking when an active memory checkpoint is missing, stale, or absent;
6. completion succeeding with a fresh current document and matching marker;
7. disable and force-stop preserving memory files;
8. documentation and manifests using version `0.14.0`.
9. active and historical agent listing with honest missing-metadata labels and no state mutation.

Repository verification will run the Python unit suite, compilation, plugin validators, diff checks, and hosted weak-root evals before release.
