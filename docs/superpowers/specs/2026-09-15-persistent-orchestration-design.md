# Persistent Symphony Orchestration Design

## Purpose

Symphony must remain dependable when the host root uses a weak model, when workers run longer than the root's first response, and when a session resumes after interruption or compaction. It must also choose between direct execution and delegation instead of imposing orchestration overhead on every task.

## Decisions

### Project policy and run state are separate

Symphony stores a per-user project policy outside the repository in the host-provided plugin data directory. The policy is keyed by the canonical working-tree root and remains enabled until the user disables it.

An active run has separate temporary state: run id, owning session, lifecycle status, selected mode, lead id, tracked subagents, and capability-suggestion timestamps. Verified completion clears the run without disabling the project.

No daemon, database, repository configuration file, or full transcript is introduced. State is JSON written atomically with user-only permissions.

### Deterministic hooks guard model behavior

Claude Code and Codex load host-specific hook declarations backed by one Python standard-library script. Hooks arm or recover a run before model reasoning, track subagent starts and stops, and block a normal Stop while tracked agents or integration work remain.

The Stop hook waits briefly for tracked workers before blocking. It accepts completion only when the final assistant message contains the exact run receipt injected at activation. Interrupts cannot be prevented by either host, so the persistent run record makes interruption recoverable rather than pretending interruption is impossible.

The script uses `PLUGIN_ROOT`/`PLUGIN_DATA` and their Claude-compatible aliases. Python 3 is a runtime prerequisite; when it is unavailable the hook fails visibly instead of silently claiming protection.

### The strong bootstrap becomes the execution lead

The root is a thin session keeper. It must spawn a strongest-available general model at high effort for cold bootstrap or recovery. The bootstrap receives the actual root profile, tool and skill catalogs, concurrency, project outcome, run receipt, and current run state.

The bootstrap selects one mode:

- **Small:** the strong lead completes the task directly.
- **Medium:** the strong lead performs fast work and delegates independent or specialized units.
- **Large:** the strong lead plans and integrates parallel delegation waves.

The modes are mutually exclusive. The same strong bootstrap continues as lead, avoiding a second strong-agent startup on small work. A resumed run receives a bounded recovery audit rather than a full cold scan.

### Persistent activation is explicit

`/symphony:enable` enables the current working tree for consecutive sessions. `/symphony:disable` disables future automatic activation and gracefully stops an active run. `/symphony:start` starts a one-off run without changing project policy. `/symphony:stop` ends only the active run, and `--force` clears stale protection after warning that an untracked worker may continue. `/symphony:status` reports policy and run state. `/symphony:help` is inert and documents usage.

Control commands carry machine-readable markers in their expanded prompt. Hooks process the marker before the root responds. Help and status never start a run.

### Capability routing uses the effective runtime catalog

The strong bootstrap detects effective skills and tools, not installation directories. It follows this precedence:

1. explicit user-requested skills;
2. repository instructions;
3. one primary workflow owner;
4. optional cross-cutting constraints;
5. documentation or structural-analysis tools when evidence requires them.

Superpowers, Compound Engineering, and Matt Pocock skills are competing workflow owners unless the selected skills have non-overlapping roles. Ponytail is a cross-cutting simplicity constraint. Context7 supplies current documentation. Codebase Memory supplies structural discovery.

When Codebase Memory is available, the root indexes or verifies the project, uses graph discovery before filesystem search, checks coverage for material paths, and passes graph findings to workers. A worker without MCP access uses the supplied evidence and never claims direct graph access.

Workers receive explicitly named skills in their assignment and return a startup capability receipt. Missing optional capabilities do not block work. A missing capability is suggested only when it would materially improve the current task, at most once per run and once per capability per project every 30 days. Installation is never automatic.

## Hook lifecycle

1. `SessionStart` reads project policy and active state. It injects recovery instructions for `resume`, `compact`, or an unfinished project run.
2. `UserPromptSubmit` handles control markers. For an enabled project with no active run, it creates a run and injects the exact strong-bootstrap packet.
3. `SubagentStart` records the agent id and injects the run's capability requirements.
4. `SubagentStop` removes the agent id and tells the root to integrate the result.
5. `Stop` waits up to 55 seconds for tracked agents. It blocks while agents remain, while the run lacks a completion receipt, or while graceful stopping has not reconciled children.
6. A matching completion receipt clears active state. A forced stop clears it immediately and preserves a warning for the next session.

Host overrides and explicit interrupts remain outside Symphony's control. The supported guarantee is that a tracked run does not silently complete during normal Stop processing and can be recovered after interruption.

## Files and interfaces

- `plugins/symphony/scripts/symphony_hook.py`: state store, control parser, lifecycle handlers, and CLI diagnostics.
- `plugins/symphony/hooks/hooks.json`: Claude lifecycle declarations.
- `plugins/symphony/hooks/codex.json`: Codex lifecycle declarations.
- `plugins/symphony/commands/*.md`: user command surfaces and machine-readable control markers.
- `plugins/symphony/skills/symphony/SKILL.md`: bootstrap, modes, capability routing, receipts, and recovery contract.
- `plugins/symphony/tests/test_symphony_hook.py`: deterministic state-machine tests.
- `.github/workflows/plugin-eval.yml`: lifecycle regression, plugin eval, and release checks.
- `README.md`: installation, prerequisites, persistent behavior, commands, capabilities, and limitations.

The hook script has one external interface: JSON event input on stdin and JSON/plain-text hook output on stdout. Its test-only/diagnostic CLI accepts an event JSON file or stdin and an explicit data directory.

## Failure behavior

- Invalid hook input produces a visible error and a non-zero exit.
- Corrupt state is quarantined and treated as recovery-required, never silently discarded.
- Concurrent updates use a lock directory and atomic replacement.
- A duplicate session cannot take ownership of an active project run without recovery or force-stop.
- Missing Python, untrusted hooks, or disabled host hooks are reported as loss of lifecycle protection.
- Missing companion plugins select a fallback and may produce a throttled suggestion.

## Verification

Unit tests cover project enable/disable, one-off start, automatic activation, duplicate invocation, resume/compaction context, subagent tracking, Stop blocking, graceful stop, force stop, completion receipts, corrupt state, and suggestion cooldowns.

Repository verification also parses both hook declarations and manifests, compiles the Python script, runs its unit tests, validates the Claude plugin, and runs the existing Claude eval suite when credentials permit. CI runs the lifecycle tests before the hosted eval and release check.
