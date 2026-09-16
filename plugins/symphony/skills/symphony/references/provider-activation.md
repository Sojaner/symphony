# Provider activation

Guarded means a Symphony hook executed successfully in the current provider session and durably wrote a matching heartbeat containing provider session identity, plugin version/root identity, hook-schema version, and observation time. Installation, discovery, configuration, or trust alone is not proof.

## Activation states

| State | Meaning | Response |
|---|---|---|
| `not_discovered` | Provider has not discovered Symphony hooks | Check the installed plugin and provider registration view |
| `needs_review` | Codex requires review/trust for this hook hash | Open `/hooks`, review Symphony, then submit another prompt |
| `pending_reload` | Installed version is not loaded in this session | Reload or restart using the provider path below |
| `active_unverified` | Hooks are registered but no matching heartbeat exists | Submit another prompt, then run status |
| `guarded` | A matching current-session heartbeat is stored | Managed completion protection is active |
| `policy_blocked` | Managed provider policy prevents the hooks | Report the policy boundary and offer explicit unguarded one-shot execution |
| `faulted` | A hook command or packaged file failed | Report the event and source once; treat it as an execution or packaging fault |

Missing heartbeat is **pending verification**, not “unarmed.” Show the recovery notice only in `status` or the first explicit managed-run attempt in that session. Never silently downgrade a requested guarded run.

## Codex

Native syntax is `$symphony:symphony <task-or-control>`.

- An install performed through the running Codex plugin flow may refresh hooks. Open `/hooks`, review/trust Symphony, and submit another prompt to create the heartbeat.
- After an external `codex plugin add` or marketplace upgrade, start a new Codex session, review Symphony in `/hooks`, then submit a prompt or run `$symphony:symphony status`.
- A changed hook definition can require renewed review because trust is bound to the exact hook hash.
- `--dangerously-bypass-hook-trust` is for already-vetted automation, never normal onboarding.

## Claude Code

Native controls are `/symphony:<control>`; one-shot work is `/symphony:start <task>`.

- Marketplace installation is the plugin trust decision; workspace and managed hook policies remain separate.
- After an install or external update, run `/reload-plugins`. If normal reload is declined, use `/reload-plugins --force`; restart only when reload cannot recover.
- Submit another prompt after reload to create the heartbeat. Reload need not re-fire `SessionStart`.
- Use `/hooks` to inspect registration. Managed-only or globally disabled hooks are `policy_blocked`, not trust failures.

In both providers, preserve the old loaded version until reload/restart succeeds and distinguish a missing executable or nonzero exit from trust state.
