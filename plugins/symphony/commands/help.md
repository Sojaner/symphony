---
description: Show Symphony usage without enabling or starting it
---

<!-- SYMPHONY_CONTROL: help -->

# Symphony commands

## Codex CLI

Codex exposes Symphony as a skill, not as `/symphony:*` slash commands. Begin the prompt with the invocation, optionally after “use”.

- `$symphony:symphony <task>` — shortest guarded one-off form.
- `$symphony:symphony start [--dry-run] <task>` — explicit guarded one-off form.
- `$symphony:symphony enable [task]`
- `$symphony:symphony disable`
- `$symphony:symphony assess [small|medium|large|auto]`
- `$symphony:symphony status`
- `$symphony:symphony agents [--all]`
- `$symphony:symphony stop [--force]`
- `$symphony:symphony help`

## Claude Code

- `/symphony:enable [task]` — enable Symphony for this working tree and optionally start a task.
- `/symphony:disable` — disable future automatic activation and gracefully stop an active run.
- `/symphony:start [--dry-run] <task>` — start one guarded run without changing project enablement; `--dry-run` validates only the planned routing and writes no project files.
- `/symphony:assess [small|medium|large|auto]` — request reassessment or set a persistent project profile; `/symphony:assess large` is suitable for this repository's long-running shape, and `auto` reverses the override.
- `/symphony:status` — show project and active-run state without changing it.
- `/symphony:agents [--all]` — list active-run subagents, including terminal agents; `--all` includes retained historical runs. Missing model or effort may be labeled unknown; unavailable token and duration measurements are omitted.
- `/symphony:stop` — gracefully stop the active run while keeping Symphony enabled.
- `/symphony:stop --force` — release stale protection; a background agent may still be running.
- `/symphony:help` — show this help without starting a run.

The Codex skill form accepts the same control names, so `$symphony:symphony help` is a control rather than a project task.

Each run selects one mode: **small** for direct work by a capable lead, **medium** for mixed direct work and selective delegation, or **large** for an inexpensive administrative lead with worker waves and reserved consultation capacity. A project profile is separate from the per-run execution mode: a long-running large-profile project may still have a small task. Enabled projects start a guarded run automatically on their next non-control project prompt.

New and explicitly reassessed runs use a separate read-only assessor at strongest/high. This bounded, read-only assessor returns exactly one concise assessment result, does not implement, and selects the exact lead route. Small leads execute directly, medium leads mix work with occasional consultation and can decide when a consultant is unavailable, and large leads use inexpensive administration plus reserved consultant capacity. Consultants may split a bounded question, but every actionable decision includes its own size and complexity for mechanical routing. Adaptive reassessment occurs at an owner prompt, planning boundary, final worker wave, interrupt, or resume. Provider task labels include each role's model/effort, and every wait plus the final response replays a cumulative `Delegation log:`.

The root's duties are announce, spawn, bind/register, relay, and wait; the assessor and lead own repository discovery, capability selection, and execution. Codex binds root spawn requests to observed lifecycle UUIDs automatically; other hosts register exact host-returned ids. Normal completion requires an accepted current assessment and terminal registered children. Only explicit `start --dry-run` bypasses assessment. Codex medium/large worker delegation requires `agents.max_depth = 2` (root → lead → worker).

Help, status, agents, empty enable, assessment controls, and invalid input terminate with a single-use control receipt without resuming project work, waiting for children, or transferring ownership. Only an interrupted run with no active registered agents can transfer atomically to a new session. Other sessions remain inspection-only; old ownership cannot be reused.

## Usage visibility

Usage is authoritative host observations only; Symphony never estimates usage or cost. Claude synchronous Agent usage may be exposed; unavailable background or Codex usage is omitted. Token fields are final-request scoped; host duration and tool count are agent-run scoped, and lifecycle duration is observed wall time. No hard token or cost budget is promised.

The snapshot contains each observed `Delegating:`, `Waiting:`, and `Completed:` state. `Waiting:` reports only observed in-progress lifecycle state and never appears without that cumulative snapshot. `Completed:` includes the agent id/role and terminal status, adding token or duration values only when exposed.

## Document memory

Small runs skip optional memory. Medium/large runs use at most one disposable probe only with a verified host timeout or cancellation that makes it terminal within its bound. Missing, failing, or hanging memory uses fallback to repository documents and source inspection.

Extended memory requires execution-lead verification of `codebase-memory-mcp`, healthy indexing, and usable memory-path coverage. The lead reads `.symphony/memory/current.md` directly and queries `.symphony/memory/history/<run-id>.md` with MCP graph/search tools; stale history uses a targeted direct read. Without that verification, Symphony uses compact lifecycle recovery and does not create indexed memory.

`.symphony/memory/` is project-local: Symphony does not automatically ignore, commit, or delete it. `/symphony:disable` and `/symphony:stop --force` preserve it; manual deletion disables historical recall until recreated. Never store secrets, raw environment values, full transcripts, or copied source bodies there. Hooks cannot call MCP, so activation is the execution lead's verified capability receipt, enforced by memory-file and checkpoint-receipt checks.

Manually ignoring `.symphony/memory/` disables indexed history until the ignore policy changes.

If active memory loses MCP/index health or either required file, the lead reports that loss with the run-bound `SYMPHONY_MEMORY_UNAVAILABLE:<run-id>:codebase-memory-mcp` receipt. This disables extended memory while retaining the last checkpoint time, so the run can finish normally from compact lifecycle recovery. Available memory still requires a fresh final checkpoint.

## Local verification

`python3 plugins/symphony/scripts/codex_smoke.py --prompt '$symphony:symphony help' --expect SYMPHONY_CONTROL_HANDLED: --timeout 120` tests an isolated candidate install and trusted candidate hooks with a hard deadline. Artifacts include JSONL, state, timing, and available usage; copied credentials are removed. Local release verification requires the complete finite matrix and three consecutive fresh Luna/low passes after the last relevant change. Medium/large cases require observed nested workers; unsupported host cases remain blockers. Optional CI skips do not satisfy this gate.

End with the exact injected `SYMPHONY_CONTROL_HANDLED` receipt on its own final line. Do not resume an active run or emit a run-completion receipt for help.
