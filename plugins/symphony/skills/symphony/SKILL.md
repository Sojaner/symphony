---
name: symphony
description: Use when a user invokes Symphony controls or a Symphony-enabled project receives substantive work that needs assessed routing, delegation, lifecycle recovery, or managed completion.
---

# Symphony

Symphony is the sole authority for topology, delegation, lifecycle, reassessment, and completion. Keep the root thin.

**Invocation:** Codex uses `$symphony:symphony ...`; Claude Code uses `/symphony:<command>`. Codex does not support `/symphony:*`.

## Root loop

1. **Check activation.** Call a run guarded only when this provider session has a matching heartbeat for the loaded plugin and hook schema. If verification is pending, explain the native recovery step once and offer an explicitly unguarded one-shot route. Read [provider activation](references/provider-activation.md) for installation, trust, reload, policy, or fault branches.
2. **Classify.** Treat controls as inert. Send substantive or uncertain work to a bounded `strongest/high` assessor with an explicit model and effort. The run begins when that assessor is spawned: a prompt on its own opens nothing, so answering directly leaves the turn ungoverned rather than blocked. Every managed spawn follows [role contracts](references/role-contracts.md). Claude rejects an invalid spawn before launch; Codex validates native lifecycle facts and withholds completion when required result markers are missing.
3. **Resolve.** Use the fixed matrix and the freshest available capability snapshot. Read [capability routing](references/capability-routing.md) when a snapshot is stale, a route is unsupported, or a supporting workflow is needed.
4. **Delegate.** Announce the route, spawn and register one lead with an explicit model and effort, relay the bounded packet, then wait through the host's blocking wait/result primitive. Name each spawned role `symphony_<role>_<model>_<effort>` when the host supports names, and announce `Delegating: <role> [<model>/<effort>] — <identity> — <objective>` from observed spawn facts. Repository inspection, implementation, specialist judgment, integration, and verification belong below the root.
5. **Reconcile.** Record host-observed lifecycle facts. Reassess only at a material evidence boundary; preserve active ownership and replace a lead only through a safe recorded transition.
6. **Complete.** Return the lead's integrated outcome only after it has returned and no tracked work remains active. A stop is blocked at most once per turn; a repeated stop releases the session and records the run as abandoned with whatever was never reconciled. Report only host-observed model, effort, lifecycle, token, and duration facts; omit unavailable metrics.

On Claude Code, model and effort come from the packaged `symphony-<role>-<model>-<effort>` agent type; never use a generic Agent because its call cannot pin effort. On Codex, pass model and reasoning effort explicitly and use the equivalent underscore-separated task name.
