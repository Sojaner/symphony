# Symphony Codex Sanity Loop Design

## Problem

The v0.15.0 protocol asks a weak root to load the complete Symphony skill, inventory capabilities, ground the repository, and probe optional memory before it delegates. A real `gpt-5.6-luna`/low Codex run followed those instructions and spent more than two minutes in bootstrap without spawning the required assessor. The hook did preserve an interrupted run, but its completion and ownership guards are weaker than its prose: normal completion can bypass assessment, control turns can enter the Stop wait path, and a new session cannot safely take ownership of an interrupted run.

## Goals

- Make the weakest supported root a small, deterministic relay.
- Require a completed, accepted assessment before normal project work can finish.
- Keep all inspection and validation controls terminal and side-effect-free.
- Recover interrupted runs across sessions without overlapping live agents.
- Keep optional codebase memory from blocking bootstrap.
- Show delegation, waiting, completion, model, effort, and available usage honestly.
- Exercise the shipped protocol through real Codex, not only hook unit tests.

## Non-goals

- Implement a host-specific scheduler or background service.
- Estimate tokens that Codex does not expose.
- Make an unavailable MCP server a project-work blocker.
- Promise support for arbitrary future Codex CLI event formats.

## Runtime contract

The root performs only control work: read the injected bootstrap packet, announce and spawn the named agent, register its identity, relay its terminal receipt, and wait while agents remain active. Repository discovery, capability selection, optional MCP use, mode choice, implementation, and verification belong to the assessor or execution lead.

A normal run is terminal only when its current assessment revision has an accepted receipt and, on hosts that report child lifecycle events, the registered assessor has terminated. A root-relayed receipt remains available only for hosts that cannot report child lifecycle events. The existing dry-run path is the sole explicit validation bypass and is recorded as such in run state.

The root uses three visible records:

```text
Delegating: <role> — <bounded objective> — <model>/<effort> — <reason>
Waiting: <role or wave> — <bounded in-progress fact>
Completed: <agent id/role> — <status> — tokens <value or not exposed by host> — duration <value or not exposed by host>
```

`Waiting:` may report only observed lifecycle state. It must not speculate about checks, blockers, or results.

## Control turns

`help`, `status`, `agents`, `agents --all`, empty `enable`, and invalid control input receive a single-use control receipt. Their Stop event terminates immediately without resuming project work, waiting for active children, changing lifecycle ownership, or completing the run. Assessment controls may mutate only their documented policy fields and then terminate the same way.

`/symphony:start --dry-run ...` creates a run whose state explicitly records `dry_run=true`; only that run may use validation completion without an accepted assessor.

## Interrupted ownership

An interrupted run transfers ownership to a new session only when no registered agent remains active. The transfer is atomic under the existing state lock and records the prior owner and transfer time. If an agent is still active, the new session is inspection-only and receives recovery guidance. Ordinary active runs never transfer merely because another session sends a prompt.

## Assessment and memory

The bootstrap packet contains the objective, run id, required assessor profile, registration/receipt syntax, and nothing that requires repository or MCP discovery by the root. The assessor decides project profile, run mode, execution lead, capability use, and verification strategy.

Small runs skip document-memory probing. Medium and large runs may ask a disposable worker to probe codebase-memory once with a hard host-side deadline. Missing, failing, or hanging MCP capability produces a bounded fallback to repository documents and source inspection; it does not delay assessor creation or normal completion. No monitor service is added.

## Real Codex validation

A standard-library harness runs a candidate plugin in isolated temporary repositories and plugin data. Every trial has a hard deadline and captures JSONL events, lifecycle state, artifacts, elapsed time, and host-reported usage. It must never script the expected agent transcript into the task prompt.

The finite release matrix covers:

- all commands with no run, starting, active, stopping, invalid, and historical inputs;
- weak Luna/low small work, medium delegation, and large delegation;
- interruption before and after assessment, during a worker wave, compaction/resume, and foreign sessions;
- memory absent, failing, and hanging;
- missing receipts, stale receipts, wrong modes, active children, and corrupt state;
- visible delegation/wait/completion records and honest unavailable usage.

Cheap deterministic and control cases run first. The weak-root scenario must pass three consecutive isolated trials. Any failure is fixed with a regression test, then the targeted live case is rerun; the full matrix runs once on the final tree.

## Release

The release is `0.16.0`. Unit tests, plugin validation, live candidate tests, a strongest/high whole-change review, push CI, release publication, and a fresh installed-package smoke are required before completion is reported.
