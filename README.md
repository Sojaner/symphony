<p align="center">
  <img src="plugins/symphony/assets/logo.png" alt="Symphony logo" width="220">
</p>

# Symphony

Symphony is a Codex and Claude Code plugin that routes project work through a strong execution lead. The lead chooses the smallest suitable execution mode, uses the models and workflow skills actually available in the current host, and keeps long-running delegated work recoverable.

The root agent can be cheap or weak. Deterministic lifecycle hooks make it a thin session keeper, while a strongest-available high-effort child owns decisions, implementation or delegation, integration, and verification.

## Execution modes

Each run selects exactly one mode:

- **Small:** the strong lead completes a straightforward task directly.
- **Medium:** the lead handles fast and integration-sensitive work while delegating independent or specialist units.
- **Large:** the lead plans, dispatches, and integrates dependency-aware parallel waves.

Mode is selected per run. Enabling Symphony never permanently classifies a project as small, medium, or large.

## Assessment and reassessment

`/symphony:assess [small|medium|large|auto]` requests reassessment or sets a persistent project profile. Use `/symphony:assess large` for this repository when its long-running shape calls for that profile; `auto` reverses the override. A project profile is not a per-run execution mode: a long-running large-profile project may still have a small task.

Each new or explicitly reassessed run first uses a separate read-only assessor: a bounded, read-only assessor that returns exactly one concise assessment result and does not implement. A mode-appropriate execution lead follows. Automatic reassessment boundaries are an owner prompt, final worker wave, interrupt, or resume. Before and after each spawned role, Symphony shows `Delegating:` and `Completed:` records so the routing is visible.

## Persistent project enablement

Symphony can remain enabled for a working tree across completed tasks, new sessions, resumes, and context compaction. Project policy and active-run state are stored in the host's writable plugin-data directory, not in the repository.

Completing or stopping a run clears only that run. The project remains enabled until `/symphony:disable` is used.

An active run records its owner session, objective, completion receipt, and tracked subagents. On resume, Symphony reconciles this record and the current worktree instead of launching a duplicate lead. A new session cannot silently take ownership of an unfinished run.

## Requirements

- Python 3 available to `/usr/bin/env python3` for the lifecycle hook.
- Trusted plugin hooks. Codex asks users to review plugin hook definitions before running them.
- A host model catalog that allows spawning a capable child with a model override.

If hooks are disabled, untrusted, or cannot run Python, the Symphony skill can still explain manual routing but cannot promise guarded stopping or recovery.

## Install

### Codex

```bash
codex plugin marketplace add Sojaner/symphony
codex plugin add symphony@symphony
```

Review and trust the bundled lifecycle hooks when Codex prompts. Start a new task after installation.

### Claude Code

```text
/plugin marketplace add Sojaner/symphony
/plugin install symphony@symphony
```

Start a new session after installation so Claude Code loads the commands, skill, and hooks.

## Commands

```text
/symphony:help
/symphony:enable [task]
/symphony:disable
/symphony:start <task>
/symphony:assess [small|medium|large|auto]
/symphony:status
/symphony:agents [--all]
/symphony:stop
/symphony:stop --force
```

- `help` displays usage without enabling or starting Symphony.
- `enable` persistently enables the current working tree and optionally starts a task.
- `disable` prevents future automatic activation and gracefully stops an active run.
- `start` starts one guarded run without changing project enablement.
- `assess` requests reassessment; `small`, `medium`, or `large` set a persistent project profile, while `auto` clears it.
- `status` reads policy and run state without changing either.
- `agents` lists the active run's subagents, including terminal agents; `--all` also includes retained historical runs. Model or effort that the host does not provide is shown as `not exposed by host`.
- `stop` ends the active run but preserves project enablement.
- `stop --force` releases stale protection. A tracked or untracked child may continue running, so use it only for recovery.

When a project is enabled, the first non-control project prompt in a later session automatically arms a guarded run and starts the strong-lead bootstrap.

## Document memory

Extended memory is available only when the strong lead verifies `codebase-memory-mcp`, a healthy project index, and usable memory-path coverage. The lead reads `.symphony/memory/current.md` directly for the active checkpoint and queries `.symphony/memory/history/<run-id>.md` through MCP graph/search tools; stale or uncovered history uses a targeted direct read while indexing catches up. Otherwise Symphony keeps using its compact lifecycle record and does not create or claim indexed memory.

`.symphony/memory/` is project-local and is not automatically ignored, committed, or deleted. `/symphony:disable` and `/symphony:stop --force` preserve it; deleting it manually disables historical recall until it is recreated. Keep no secrets, raw environment values, full transcripts, or copied source bodies in memory.

Manually ignoring `.symphony/memory/` disables indexed history until the ignore policy changes.

Hooks cannot call MCP. The strong lead activates memory only with its verified capability receipt, and hooks enforce that activation with the documented file and checkpoint-receipt checks.

If an activated run loses MCP/index health or either memory file, the lead reports the loss with its run-bound `SYMPHONY_MEMORY_UNAVAILABLE:<run-id>:codebase-memory-mcp` receipt. Hooks then disable extended memory, retain the last checkpoint time, and allow normal completion from compact lifecycle recovery. While memory remains active, a missing or stale final checkpoint still blocks completion.

## Lifecycle protection

The hook layer runs before model reasoning and at agent lifecycle events:

1. `SessionStart` restores enabled-project and interrupted-run context.
2. `UserPromptSubmit` applies commands or arms the next project run.
3. `SubagentStart` and `SubagentStop` maintain the tracked-agent ledger.
4. `Stop` waits briefly for tracked agents, then blocks normal stopping until every result is reconciled and the final completion receipt matches.
5. `Interrupt` records recovery context where the host exposes the event.

Neither Codex nor Claude Code lets a plugin prevent every explicit interrupt or host-enforced Stop override. Symphony therefore guarantees normal-Stop protection and recoverable state, not an uninterruptible process.

## Assessment and execution bootstrap

The hook has the root first spawn a separate read-only assessor: the strongest available general reasoning model at high effort with no inherited turns. After its authorized assessment receipt, the root spawns a separate mode-appropriate `symphony_lead`. Both receive:

- the task outcome and acceptance criteria;
- the actual root model and effort;
- live child models, efforts, and concurrency;
- effective skills and tools;
- repository instructions and current worktree state;
- the run record and exact assessment/completion receipts;
- the bundled model- and capability-routing references.

For a small task, the selected lead continues directly as the implementer. The assessor never implements.

## Usage visibility

Usage is authoritative host observations only; Symphony never estimates usage or cost. Claude synchronous Agent usage may be exposed, but background Agent usage and Codex usage remain `not exposed by host`. Token fields describe the final request only; duration and tool count describe the agent run. No hard token or cost budget is promised.

## Workflow and evidence capabilities

Symphony inspects the effective catalog for the root and each child. It never treats marketplace installation or files on disk as proof that a skill or tool is usable by that agent.

| Capability | Symphony role |
|---|---|
| Superpowers | Primary workflow for its design, planning, debugging, TDD, subagent execution, or verification flows. |
| Compound Engineering | Primary workflow for end-to-end work, planning, reviews, POV decisions, PRs, or long-running delivery. |
| Matt Pocock skills | Primary workflow for focused design, diagnosis, TDD, review, domain modeling, research, or agent documentation. |
| Ponytail | Optional cross-cutting simplicity constraint for coding and design. |
| Context7 | Current official documentation for version-sensitive framework and host behavior. |
| Codebase Memory MCP | Structural code discovery, callers, dependencies, architecture, and blast-radius evidence. |

Explicit user requests and repository instructions come first. Symphony normally selects one primary workflow owner; it does not stack overlapping Superpowers, Compound Engineering, and Matt Pocock ceremonies on one unit.

When Codebase Memory is available, the root checks the project index and graph before delegating. Worker packets include qualified symbols, relevant traces, index freshness, and coverage gaps. A worker without graph tools uses that evidence and does not claim direct MCP access. Targeted source search remains the fallback for literals, configuration, non-code files, and graph coverage gaps.

## Missing capabilities

A missing optional capability does not stop a task when a valid fallback exists. If the absence materially affected the result, Symphony may suggest it in the final handoff:

- at most one suggestion per run;
- at most once per capability and project every 30 days;
- never during active work unless the missing capability blocks an explicit user guarantee;
- never installed or enabled automatically.

## Update

Codex:

```bash
codex plugin marketplace upgrade symphony
codex plugin add symphony@symphony
```

Claude Code:

```text
/plugin marketplace update symphony
```

Start a new task after updating so the host reloads hooks and skills.

## Development and testing

Run the deterministic lifecycle tests:

```bash
python3 -m unittest discover -s plugins/symphony/tests -v
```

Validate the Claude plugin:

```bash
claude plugin validate ./plugins/symphony
```

The Claude eval suite checks planned assessor/medium-lead routing and mismatch refusal. A separate read-only smoke case exercises two real sequential agents and owner-root registration/assessment receipts in the evaluator's isolated workspace:

```bash
claude plugin eval ./plugins/symphony \
  --model claude-haiku-4-5-20251001
```

The hosted evaluator requires early-access enablement. Local lifecycle tests verify the event protocol; dry-run reports do not prove real spawns or receipt acceptance. The read-only smoke has not produced hosted evidence locally because the installed evaluator exits at its early-access gate.

CI runs validation and evals on every push and pull request. A successful push to `main` creates a GitHub release when the manifest version is new.

## Repository layout

```text
.agents/plugins/marketplace.json       Codex marketplace manifest
.claude-plugin/marketplace.json        Claude Code marketplace manifest
plugins/symphony/.codex-plugin/        Codex plugin manifest
plugins/symphony/.claude-plugin/       Claude Code plugin manifest
plugins/symphony/commands/             User command surfaces
plugins/symphony/hooks/                Claude and Codex hook declarations
plugins/symphony/scripts/              Shared lifecycle implementation
plugins/symphony/tests/                Deterministic lifecycle tests
plugins/symphony/evals/                Claude plugin eval suite
plugins/symphony/skills/symphony/      Execution and capability-routing instructions
.symphony/memory/                      Runtime-created project-local document memory
```

## References

- [Codex hooks](https://developers.openai.com/codex/hooks)
- [OpenAI model guidance](https://developers.openai.com/api/docs/guides/latest-model)
- [Claude Code hooks](https://code.claude.com/docs/en/hooks)
- [Claude Code subagents](https://code.claude.com/docs/en/sub-agents)
