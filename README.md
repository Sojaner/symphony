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
/symphony:status
/symphony:stop
/symphony:stop --force
```

- `help` displays usage without enabling or starting Symphony.
- `enable` persistently enables the current working tree and optionally starts a task.
- `disable` prevents future automatic activation and gracefully stops an active run.
- `start` starts one guarded run without changing project enablement.
- `status` reads policy and run state without changing either.
- `stop` ends the active run but preserves project enablement.
- `stop --force` releases stale protection. A tracked or untracked child may continue running, so use it only for recovery.

When a project is enabled, the first non-control project prompt in a later session automatically arms a guarded run and starts the strong-lead bootstrap.

## Lifecycle protection

The hook layer runs before model reasoning and at agent lifecycle events:

1. `SessionStart` restores enabled-project and interrupted-run context.
2. `UserPromptSubmit` applies commands or arms the next project run.
3. `SubagentStart` and `SubagentStop` maintain the tracked-agent ledger.
4. `Stop` waits briefly for tracked agents, then blocks normal stopping until every result is reconciled and the final completion receipt matches.
5. `Interrupt` records recovery context where the host exposes the event.

Neither Codex nor Claude Code lets a plugin prevent every explicit interrupt or host-enforced Stop override. Symphony therefore guarantees normal-Stop protection and recoverable state, not an uninterruptible process.

## Strong-lead bootstrap

The hook gives the root one narrow instruction: spawn a strongest-available general reasoning model at high effort with no inherited turns. That child becomes `symphony_lead` and receives:

- the task outcome and acceptance criteria;
- the actual root model and effort;
- live child models, efforts, and concurrency;
- effective skills and tools;
- repository instructions and current worktree state;
- the run record and exact completion receipt;
- the bundled model- and capability-routing references.

For a small task, this same child continues directly as the implementer. Symphony does not pay for a separate classifier and executor.

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

The Claude eval suite verifies that a weak root routes to a strong lead and that an explicit false runtime declaration still stops before project work:

```bash
claude plugin eval ./plugins/symphony \
  --trust-plugin \
  --model claude-haiku-4-5-20251001
```

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
```

## References

- [Codex hooks](https://developers.openai.com/codex/hooks)
- [OpenAI model guidance](https://developers.openai.com/api/docs/guides/latest-model)
- [Claude Code hooks](https://code.claude.com/docs/en/hooks)
- [Claude Code subagents](https://code.claude.com/docs/en/sub-agents)
