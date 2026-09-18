<p align="center">
  <img src="plugins/symphony/assets/logo.png" alt="Symphony logo" width="220">
</p>

# Symphony

Symphony is a Codex and Claude Code plugin that keeps the root agent thin, selects a task-sized execution lead, and preserves lifecycle state across long-running work.

## Install

### Codex

```bash
codex plugin marketplace add Sojaner/symphony
codex plugin add symphony@symphony
```

Open `/hooks`, review and trust Symphony's hooks, then send another prompt. An install or update performed outside the running Codex process requires a new session before the hooks can execute.

### Claude Code

```text
/plugin marketplace add Sojaner/symphony
/plugin install symphony@symphony
/reload-plugins
```

If normal reload is declined, use `/reload-plugins --force` or restart Claude Code. Send another prompt after reload so Symphony can record the current-session heartbeat. `/hooks` shows whether workspace or managed policy blocks hook execution.

## Use

Codex exposes one skill entry point:

```text
$symphony:symphony enable
$symphony:symphony start <task>
$symphony:symphony bypass <task>
$symphony:symphony status
$symphony:symphony agents [--all]
$symphony:symphony reassess
$symphony:symphony proceed
$symphony:symphony stop [--force]
$symphony:symphony disable
$symphony:symphony version
$symphony:symphony help
```

Claude Code exposes native slash commands:

```text
/symphony:enable
/symphony:start <task>
/symphony:bypass <task>
/symphony:status
/symphony:agents [--all]
/symphony:reassess
/symphony:proceed
/symphony:stop [--force]
/symphony:disable
/symphony:version
/symphony:help
```

Do not use `/symphony:*` in Codex. `$symphony:symphony start <task>` and `/symphony:start <task>` each run one managed task without changing project enablement. A leading control word counts as a control only when nothing but a documented flag follows it, so `$symphony:symphony help me fix the login bug` is treated as a task.

Symphony assumes the session root runs at the economy tier: it exists to route work to a right-sized lead, so a root that is already the strongest model pays for an assessor and a lead on top of itself. Set the root model to the cheapest capable option before enabling.

## Persistent enablement

`enable` governs future substantive prompts in the current project, including new sessions and resumes, until `disable` is used. Controls and deterministic trivial tasks do not trigger assessment. `bypass` runs one task outside Symphony without changing enablement or mutating an active run.

`stop` ends the active run but keeps project enablement. `stop --force` records an explicit interruption when a safe stop cannot be completed. `disable` gracefully stops and archives active recovery context, then disables future automatic governance. Uninstalling removes plugin execution but does not rewrite the project or silently finish active work; disable first when possible.

## Guarded execution

A run is **guarded** only after the loaded Symphony hook has written a matching heartbeat for the current provider session. Installation, discovery, or trust alone is not proof of execution.

When the heartbeat is absent, `status` reports pending verification and gives the provider-native recovery step. A missing packaged executable or nonzero hook exit is reported as a fault, not as a trust problem. Symphony never silently labels an unverified run guarded; an unguarded one-shot route must be explicit.

Normal completion is blocked while host-observed tracked work remains active. A stop is blocked at most once per turn: when the host reports that the stop hook is already active, Symphony releases the session, records the run as abandoned with the identities it never reconciled, and shows that in `status`. A prompt that never spawned an assessor opens no run and can never hold a session open.

User interruption and host-enforced overrides remain authoritative, so interrupted work is recovered from durable lifecycle state rather than described as uninterruptible. Neither host reports which agents are still alive, so recovery keys on the provider session: a heartbeat from a new session marks unreconciled delegations interrupted before new ownership is created.

## Routing

Assessment treats task size and complexity as separate axes. The fixed route is resolved against the capability map shipped with the installed version. Hooks are given no model inventory by either host, so the map is maintained at release time rather than discovered at runtime.

Symphony ships several profiles per provider and routes through the best one your plan is entitled to, falling back to a conservative floor when entitlement cannot be read. When your plan clamps a task to a weaker model, the lead spawn stops and waits for `proceed`, so quality never degrades silently; a reduced effort on the same model is announced and continues.

| Size / complexity | Simple | Mixed | Complex |
|---|---|---|---|
| Small | capable/medium, direct | capable/high, direct with optional consultation | strongest/high, direct with independent review |
| Medium | balanced/medium, mixed | balanced/high, mixed with optional consultation | capable/high, mixed with reserved consultation |
| Large | economy/low, delegated | economy/medium, delegated with reserved consultation | economy/medium, delegated with strongest consultation |

Pre-launch route enforcement needs a pre-spawn event, which only Claude Code provides; on Codex a mis-routed spawn is detected once the child starts and reported, not prevented.

The assessor is bounded, read-only, and separate from the lead. The lead route never inherits the assessor's expensive model or effort. Large-task leads administer dependency-aware work and reserve capacity for narrow consultant decisions. Small-task leads do straightforward work directly and delegate only genuinely independent or mechanical units.

`reassess` updates subsequent work at a safe evidence boundary. It does not duplicate an active lead or rewrite completed work.

## Visibility

`status` reports project enablement, activation state, guarded/degraded state, assessment cell, topology, lead identity, and up to five current delegation records. Each agent has one latest record, prioritized as failed, active/waiting, then recently completed.

```text
Working: worker [capable/medium] — <identity> — <bounded objective>
Failed: consultant [strongest/high] — <identity> — <bounded decision>
Completed: worker [balanced/medium] — <identity> — <bounded objective>
```

`agents --all` includes the latest state for every delegation in the current run and the retained 20-run history. Token and duration fields are omitted when the provider does not expose them.

## Optional capabilities

Symphony uses available supporting workflows only for bounded jobs. Superpowers, Compound Engineering, Matt Pocock skills, and Ponytail may supply a suitable technique; Context7 may provide current official documentation; Codebase Memory may provide structural discovery and indexed document memory. Missing optional capabilities use native source inspection and repository documents without delaying startup.

Document memory is enabled only when Codebase Memory is available and its project index is healthy. It stores curated decisions and continuation state in `.symphony/context.md`, never secrets, transcripts, or copied source bodies.

## Upgrade and migration

```bash
codex plugin marketplace upgrade symphony
codex plugin add symphony@symphony
```

```text
/plugin marketplace update symphony
/reload-plugins
```

Codex updates loaded outside the current process require a new session and renewed `/hooks` review when the hook hash changes. Claude updates require reload or restart. In both providers, the next prompt confirms the loaded version through its heartbeat.

Symphony 1.0 imports project enablement and user configuration only. Incompatible active-run state is archived and the next managed task receives a fresh assessment. Pre-1.0 state is located by hashing the repository's git toplevel, with the working directory as a fallback, so an import still succeeds from a subdirectory.

Symphony stores only lifecycle facts: identities, roles, requested tier and effort, classification, status and timestamps, plus a short objective label. Prompts, agent messages, spawn packets and transcript paths are never written to disk.

## Develop and verify

```bash
python3 -m unittest discover -s plugins/symphony/tests -v
claude plugin validate ./plugins/symphony
python3 plugins/symphony/scripts/package_smoke.py --provider codex --candidate . --scenario activation
python3 plugins/symphony/scripts/package_smoke.py --provider claude --candidate . --scenario activation
git diff --check
```

The package smoke supports `activation`, `managed-run`, `unmarked-spawn`, `interrupt-resume`, and `upgrade`.

`scripts/capture_fixtures.py --provider claude` records real hook payloads from an installed session into `tests/fixtures/captured/`, sanitising paths and identifiers first. The conformance test holds the runtime to those shapes and lists the events nobody has captured yet, so an unobserved payload is never quietly relied upon. It installs the candidate in an isolated fake-provider home, executes only materialized hook paths, and emits one JSON result.

## References

- [Codex hooks](https://learn.chatgpt.com/docs/hooks)
- [Claude Code hooks](https://code.claude.com/docs/en/hooks)
- [Claude Code plugins](https://code.claude.com/docs/en/discover-plugins)

### Receiving capability updates

Symphony ships its tier-to-model map inside the release, and a scheduled workflow republishes that map whenever a provider retires a model. Installing does not subscribe you to those releases: both hosts leave a third-party plugin at the version you installed until you ask for a newer one.

```bash
claude plugin update symphony
```

On Codex the equivalent refreshes the marketplace snapshot:

```bash
codex plugin marketplace upgrade symphony
```

Claude Code can also do this for you at startup. Open `/plugin`, select this marketplace and enable auto-update. Be deliberate about that choice: it lets the map that decides which model your work runs on change between sessions. Symphony will tell you when it notices, and will stop and ask before letting an assessment you already accepted run on something weaker, but the update itself will be silent.
