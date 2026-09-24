# Provider activation

Guarded means a Symphony hook executed successfully in the current provider session and durably wrote a matching heartbeat containing provider session identity, plugin version/root identity, hook-schema version, and observation time. Installation, discovery, configuration, or trust alone is not proof.

## Activation states

Symphony can record only two states, because a hook is the only Symphony code the host runs and it cannot execute in any of the others:

| Recorded state | Meaning | Response |
|---|---|---|
| `guarded` | A matching current-session heartbeat is stored | Managed completion protection is active |
| pending verification | No matching heartbeat exists | Apply the provider recovery path below, then run status |

The remaining conditions are diagnoses for the user, not values Symphony stores. When the heartbeat stays absent, name the likely cause from the provider's own views:

| Diagnosis | Signal | Response |
|---|---|---|
| Not discovered | The plugin is absent from the provider's registration view | Check the installed plugin |
| Needs review | Codex requires review/trust for this hook hash | Open `/hooks`, review Symphony, then submit another prompt |
| Pending reload | The installed version is not loaded in this session | Reload or restart using the provider path below |
| Policy blocked | Managed-only or globally disabled hooks | Report the policy boundary and offer explicit unguarded one-shot execution |
| Faulted | A hook command or packaged file failed | Reported once in the next status from the durable fault record |

Missing heartbeat is **pending verification**, not “unarmed.” Show the recovery notice only in `status` or the first explicit managed-run attempt in that session. Never silently downgrade a requested guarded run.

## Codex

Native syntax is `$symphony:symphony <task-or-control>`.

- An install performed through the running Codex plugin flow may refresh hooks. Open `/hooks`, review/trust Symphony, and submit another prompt to create the heartbeat.
- After an external `codex plugin add` or marketplace upgrade, start a new Codex session, review Symphony in `/hooks`, then submit a prompt or run `$symphony:symphony status`.
- A changed hook definition can require renewed review because trust is bound to the exact hook hash.
- On Windows, if `/hooks` shows a trusted hook but this project's heartbeat is absent, check `where.exe python` and `python --version` in the environment that launches Codex. Put a working Python on `PATH`, restart Codex, review the changed hook, and submit a prompt; an interpreter launch failure is a hook fault.
- `--dangerously-bypass-hook-trust` is for already-vetted automation, never normal onboarding.

PR #2 introduced `py -3`; its test only asserted manifest text, while CI ran on Linux. The 1.4.2 change kept that launcher and quoted the script path, but [Codex wraps Windows hook commands in outer quotes](https://github.com/openai/codex/blob/main/codex-rs/hooks/src/engine/command_runner.rs), which can break embedded quotes. WindowsApps `py.exe` can also fail before Symphony writes any state. Before claiming Windows activation works, execute all six packaged `commandWindows` entries on Windows with an unusable `py`, a plugin path containing spaces, and a matching guarded heartbeat.

## Claude Code

Native controls are `/symphony:<control>`; one-shot work is `/symphony:start <task>`.

- Marketplace installation is the plugin trust decision; workspace and managed hook policies remain separate.
- After an install or external update, run `/reload-plugins`. If normal reload is declined, use `/reload-plugins --force`; restart only when reload cannot recover.
- Submit another prompt after reload to create the heartbeat. Reload need not re-fire `SessionStart`.
- Use `/hooks` to inspect registration. Managed-only or globally disabled hooks are `policy_blocked`, not trust failures.

In both providers, preserve the old loaded version until reload/restart succeeds and distinguish a missing executable or nonzero exit from trust state.
