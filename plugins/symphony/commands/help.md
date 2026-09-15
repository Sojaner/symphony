---
description: Show Symphony usage without enabling or starting it
---

<!-- SYMPHONY_CONTROL: help -->

# Symphony commands

- `/symphony:enable [task]` — enable Symphony for this working tree and optionally start a task.
- `/symphony:disable` — disable future automatic activation and gracefully stop an active run.
- `/symphony:start <task>` — start one guarded run without changing project enablement.
- `/symphony:status` — show project and active-run state without changing it.
- `/symphony:agents [--all]` — list active-run subagents, including terminal agents; `--all` includes retained historical runs. Missing model or effort is `not exposed by host`.
- `/symphony:stop` — gracefully stop the active run while keeping Symphony enabled.
- `/symphony:stop --force` — release stale protection; a background agent may still be running.
- `/symphony:help` — show this help without starting a run.

Each run selects one mode: **small** for direct work by the strong lead, **medium** for mixed direct work and selective delegation, or **large** for delegation waves. Enabled projects start a guarded run automatically on their next non-control project prompt.

## Document memory

Extended memory requires a strong-lead verification of `codebase-memory-mcp`, healthy indexing, and usable memory-path coverage. The lead reads `.symphony/memory/current.md` directly and queries `.symphony/memory/history/<run-id>.md` with MCP graph/search tools; stale history uses a targeted direct read. Without that verification, Symphony uses compact lifecycle recovery and does not create indexed memory.

`.symphony/memory/` is project-local: Symphony does not automatically ignore, commit, or delete it. `/symphony:disable` and `/symphony:stop --force` preserve it; manual deletion disables historical recall until recreated. Never store secrets, raw environment values, full transcripts, or copied source bodies there. Hooks cannot call MCP, so activation is the strong lead's verified capability receipt, enforced by memory-file and checkpoint-receipt checks.

Manually ignoring `.symphony/memory/` disables indexed history until the ignore policy changes.

If active memory loses MCP/index health or either required file, the lead reports that loss with the run-bound `SYMPHONY_MEMORY_UNAVAILABLE:<run-id>:codebase-memory-mcp` receipt. This disables extended memory while retaining the last checkpoint time, so the run can finish normally from compact lifecycle recovery. Available memory still requires a fresh final checkpoint.
