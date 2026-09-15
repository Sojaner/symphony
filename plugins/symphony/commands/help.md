---
description: Show Symphony usage without enabling or starting it
---

<!-- SYMPHONY_CONTROL: help -->

# Symphony commands

- `/symphony:enable [task]` — enable Symphony for this working tree and optionally start a task.
- `/symphony:disable` — disable future automatic activation and gracefully stop an active run.
- `/symphony:start <task>` — start one guarded run without changing project enablement.
- `/symphony:status` — show project and active-run state without changing it.
- `/symphony:agents [--all]` — list active-run subagents, including terminal agents; `--all` includes retained historical runs.
- `/symphony:stop` — gracefully stop the active run while keeping Symphony enabled.
- `/symphony:stop --force` — release stale protection; a background agent may still be running.
- `/symphony:help` — show this help without starting a run.

Each run selects one mode: **small** for direct work by the strong lead, **medium** for mixed direct work and selective delegation, or **large** for delegation waves. Enabled projects start a guarded run automatically on their next non-control project prompt.
