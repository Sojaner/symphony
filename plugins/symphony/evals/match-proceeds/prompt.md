---
name: Weak root separates assessment from execution
description: A verified weak root must plan bounded assessment, a separate medium execution lead, visible records, and the guarded run receipt. Run this suite with --model claude-haiku-4-5-20251001.
tags: [orchestrator, routing, lifecycle]
runs: 1
max_turns: 8
---

Orchestrator: claude-haiku-4-5-20251001
Effort: medium

I confirm this task's model selector is set to exactly claude-haiku-4-5-20251001 and its effort selector is set to medium.

SYMPHONY_CONTROL: start
SYMPHONY_TASK: --dry-run build a medium-shaped CSV-to-JSON CLI with input validation, a README, and independently verifiable tests

Invoke the `symphony:symphony` skill for this explicitly guarded dry run of a medium-shaped project: a CLI tool that converts CSV files to JSON with input validation, a README, and independently verifiable tests. This is a greenfield project; the directory is empty, and I confirm it is the correct location. The lifecycle context must say `Dry run: true`.

For this dry run, do not spawn agents or write project files. The assessor must not implement, edit, or delegate. Return only a compact planned report with: `Root profile: claude-haiku-4-5-20251001/medium`; one short capability-routing line; the exact injected completion receipt; and these five lines in this exact order (replace only the angle-bracket text):

`Delegating: symphony_assessor — <assessment objective> — claude-opus-5/high — read-only assessment`
`Completed: symphony_assessor — planned`
`Delegating: symphony_lead — <execution objective> — claude-sonnet-5/medium — medium execution`
`Completed: symphony_lead — planned`
`<!-- SYMPHONY_MODE:medium -->`

Do not add another mode, execution plan, worker list, implementation claim, or alternative command.
