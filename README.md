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

Each new or explicitly reassessed run first uses a separate read-only assessor: a bounded, read-only assessor that returns exactly one concise assessment result and does not implement. A mode-appropriate execution lead follows. Automatic reassessment boundaries are an owner prompt, final worker wave, interrupt, or resume. Every wait and final response replays the cumulative delegation history so earlier routing remains visible.

## Persistent project enablement

Symphony can remain enabled for a working tree across completed tasks, new sessions, resumes, and context compaction. Project policy and active-run state are stored in the host's writable plugin-data directory, not in the repository.

Completing or stopping a run clears only that run. The project remains enabled until `/symphony:disable` is used.

An active run records its owner session, objective, completion receipt, and tracked subagents. On resume, Symphony reconciles this record and the current worktree instead of launching a duplicate lead. Only an interrupted run with no active registered agents can transfer to a new owner; the atomic transfer records the previous owner and consumes recovery eligibility. Other sessions are inspection-only, and the previous owner cannot mutate the transferred run.

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

For medium and large runs, allow the lead to spawn workers by setting `agents.max_depth = 2` in your Codex configuration (root → lead → worker). The default depth of one only supports direct root children. The live harness supplies this setting in its isolated host; Symphony does not rewrite your configuration.

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
/symphony:start [--dry-run] <task>
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
- `$symphony:symphony <task>` starts a guarded one-off run; command names such as `$symphony:symphony help` are controls and do not start project work.
- `assess` requests reassessment; `small`, `medium`, or `large` set a persistent project profile, while `auto` clears it.
- `status` reads policy and run state without changing either.
- `agents` lists the active run's subagents, including terminal agents; `--all` also includes retained historical runs. Model or effort that the host does not provide is shown as `not exposed by host`.
- `stop` ends the active run but preserves project enablement.
- `stop --force` releases stale protection. A tracked or untracked child may continue running, so use it only for recovery.

Help, status, agents (including `--all`), empty enable, assessment controls, and invalid controls end with a single-use control receipt. They do not resume or complete project work, wait for children, or transfer ownership. Assessment controls change only their documented policy. Only an explicit `start --dry-run` run can bypass an accepted current assessment; normal completion requires that assessment and terminal registered children.

When a project is enabled, the first non-control project prompt in a later session automatically arms a guarded run and starts the strong-lead bootstrap.

## Document memory

Small runs skip optional memory. Medium and large runs may use at most one disposable probe only with a verified host timeout or cancellation that makes the probe terminal within its bound. Otherwise they skip it. Missing, failing, or hanging memory uses fallback to repository documents and source inspection; optional MCP cannot hold up bootstrap or completion.

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
4. `PreToolUse` blocks premature root execution delegation until the assessor receipt has been persisted.
5. `Stop` waits briefly for tracked agents, then blocks normal stopping until the current assessment is accepted, every result is reconciled, and the final completion receipt matches. Control receipts terminate immediately.
6. `Interrupt` records owner-scoped recovery context where the host exposes the event.

Neither Codex nor Claude Code lets a plugin prevent every explicit interrupt or host-enforced Stop override. Symphony therefore guarantees normal-Stop protection and recoverable state, not an uninterruptible process.

## Assessment and execution bootstrap

The root has only five duties: announce, spawn, bind/register, relay, and wait. It performs no repository, capability, or MCP discovery before delegating. The injected packet supplies the objective, run id, required assessor profile, and exact receipt syntax. The root first spawns a separate read-only assessor at the strongest available general reasoning model and high effort with no inherited turns, then relays its terminal receipt. Codex binds each root spawn request to the observed child UUID automatically; other hosts register the exact id they return. After acceptance, the root spawns the mode-appropriate `symphony_lead`.

The assessor and execution lead discover repository instructions, worktree state, usable models, skills, tools, concurrency, and routing references themselves. On hosts that expose child lifecycle events, assessment requires the current registered terminal assessor. Hosts without those events retain the documented owner receipt fallback.

For a small task, the selected lead continues directly as the implementer. The assessor never implements.

## Usage visibility

Usage is authoritative host observations only; Symphony never estimates usage or cost. Claude synchronous Agent usage may be exposed; unavailable background or Codex usage is omitted. Token fields describe the final request only; host duration and tool count describe the agent run, while lifecycle duration is observed wall time. No hard token or cost budget is promised.

Provider task labels carry the role and requested routing: Codex uses `symphony_<role>__<model-slug>__<effort-slug>`, while Claude descriptions start `symphony_<role> [<model>/<effort>]:`. Progress and the final response replay a cumulative `Delegation log:` with every observed `Delegating:`, `Waiting:`, and `Completed:` state, so a later waiting update cannot hide earlier delegations. Token and duration segments appear only when exposed. After an accepted assessment the root announces `Mode: <mode> — <strategy> — <reason>`, so the user sees whether the lead executes directly, adds bounded workers, or runs dependency-aware waves.

The final completion response is self-contained: it repeats the integrated deliverable, cumulative delegation log, assessor and lead completion summary records, one `Routing:` line naming the mode strategy plus every agent's actual model/effort and assigned job, authoritative verification, selected mode, and completion receipt. The hook checks the report against its lifecycle ledger.

## Workflow and evidence capabilities

Symphony inspects the effective catalog for the root and each child. It never treats marketplace installation or files on disk as proof that a skill or tool is usable by that agent.

| Capability | Symphony role |
|---|---|
| Superpowers | Bounded design, planning, debugging, TDD, implementation, or verification technique. |
| Compound Engineering | Bounded planning, review, POV, delivery, or PR technique. |
| Matt Pocock skills | Bounded design, diagnosis, TDD, review, domain-modeling, research, or agent-documentation technique. |
| Ponytail | Optional cross-cutting simplicity constraint for coding and design. |
| Context7 | Current official documentation for version-sensitive framework and host behavior. |
| Codebase Memory MCP | Structural code discovery, callers, dependencies, architecture, and blast-radius evidence. |

Explicit user requests and repository instructions come first. During an active run, Symphony owns routing, delegation, waiting, reassessment, and completion. It selects at most one supporting Superpowers, Compound Engineering, or Matt Pocock technique for a unit; that technique returns its artifact to the Symphony lead and does not offer a second execution handoff.

When bounded Codebase Memory use is available, the assessor or lead checks the project index and graph. Worker packets include qualified symbols, relevant traces, index freshness, and coverage gaps. A worker without graph tools uses that evidence and does not claim direct MCP access. Targeted source search remains the fallback for literals, configuration, non-code files, and graph coverage gaps.

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

Run a bounded real-Codex candidate trial:

```bash
python3 plugins/symphony/scripts/codex_smoke.py \
  --candidate-marketplace . --output /tmp/symphony-smoke \
  --name control-help --prompt /symphony:help \
  --expect SYMPHONY_CONTROL_HANDLED: --timeout 120
```

Each trial installs the candidate through a local marketplace into a fresh Codex home, repository, and plugin-data directory, verifies installed bytes, trusts only candidate hooks, and removes its credential copy. It retains JSONL, lifecycle state, artifacts, timing, and host usage. Installation, authentication, timeout, and assertion failures fail the local gate; an optional CI skip is not a release pass.

The 0.16.0 release gate runs deterministic command/state and adversarial receipt cases first, then live weak-root, nested medium/large worker, recovery/compaction where supported, memory absent/failing/hanging, and visibility cases. Medium/large passes require actual worker lifecycle records. Luna/low small work must pass three consecutive fresh trials after the last relevant change; a later change resets the count. Unsupported host scenarios are recorded as blockers, never passed by simulation.

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
