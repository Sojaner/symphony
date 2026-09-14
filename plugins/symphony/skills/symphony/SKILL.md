---
name: symphony
description: Orchestrate complicated projects from a user-confirmed low-cost root through a reusable routing consultant and narrowly scoped parallel subagents. Use for multi-domain, high-risk, or multi-part work where model and reasoning-effort choices materially affect cost, speed, or quality; skip ordinary tasks one agent can finish directly.
---

# Symphony

Make a cheap root agent an effective project controller. The root owns scope, integration, verification, and the user relationship. Subagents own bounded reasoning or implementation units. A reusable **conductor** advises on decomposition, model choice, effort, scheduling, and review; it never becomes the project owner.

## Activate

Use Symphony when the work has at least one of these properties:

- several independent implementation units;
- architecture or reasoning whose mistakes would propagate widely;
- multiple specialties, repositories, or verification surfaces;
- a long execution path where routing decisions materially affect cost or quality.

Handle a small, sequential task directly. Delegation overhead is real work.

## Require the orchestrator profile

Complete this gate before reading project files, consulting the conductor, spawning workers, or starting project work.

The user selects the current task's orchestrator model and effort in the host (Codex or Claude Code) and declares that choice when invoking Symphony:

```text
Orchestrator: <exact model id>
Effort: <low|medium>
```

The declared profile is a request to verify, not evidence. It never satisfies the gate by itself. Verify it in order:

1. Determine the model and effort the current task is actually running on, from runtime metadata the host exposes to the agent: system context that names the active model, host status commands, or equivalent. The live model catalog only lists available choices; it does not identify the active orchestrator and cannot satisfy this step. Do not infer or guess.
2. The gate passes only when the runtime-reported model id and effort exactly match the declared profile, the model supports spawning subagents with model overrides, and the effort is `low` or `medium`.
3. On any mismatch, stop before all project work. Report both values plainly — "You declared `<declared>`, but this task is running on `<actual>`" — and ask the user to either start a new task with the declared model at `low` or `medium` and re-invoke Symphony, or explicitly accept the actual runtime profile if it is also a valid low-cost orchestrator. State that a skill cannot change the model or effort of its already-running task. Never continue on the wrong model by default.
4. When the runtime exposes neither model nor effort, do not proceed on the declaration alone. Ask the user one direct question — "Is this task's model selector currently set to exactly `<id>` at `<effort>`?" — and continue only after an explicit yes. Treat any other answer as a mismatch.

When the profile is missing, unverifiable, unsupported, or uses another effort, stop before project work. Ask the user to start a new task with the cheapest available model that supports subagent model overrides, at `low` or `medium`, then invoke Symphony with the two-line profile above.

## Bootstrap the conductor

1. Compare the confirmed orchestrator profile with the host's live subagent tool schema — Codex collaboration tools, or the Claude Code agent tool — then inventory available worker models, efforts, and concurrency. Treat the live schema as authoritative; model names in examples or cached documentation may be stale.
2. Read [references/model-routing.md](references/model-routing.md). Refresh its working facts from the official vendor documentation it links only when its freshness rule fires. Do not rewrite the installed plugin during a project run.
3. Spawn `symphony_conductor` with no inherited turns. Prefer the highest-capability available general reasoning model at `high` — for example `gpt-6-astra` on a current Codex catalog, or the strongest Opus- or Fable-tier model on Claude Code. If unavailable, use the strongest listed general model at `high`, or its highest supported effort below `high`.
4. Give it only:
   - the project outcome and acceptance criteria;
   - material constraints and known risks;
   - the exact live model/effort catalog and concurrency limit;
   - the absolute path to `references/model-routing.md`;
   - the decision currently needed.

The conductor returns this compact record:

```text
decision: <route or next step>
assignments: <unit -> model / effort>
schedule: <parallel waves and dependencies>
why: <one sentence per assignment>
evidence: <checks required before integration>
reconsult_when: <observable triggers>
```

Keep its agent id. When it is idle, send the next consultation to the same agent — a follow-up task in Codex, a follow-up message in Claude Code — instead of spawning another conductor. An idle agent spends no inference tokens. If the host cannot resume an idle agent, spawn a fresh conductor with the same bootstrap packet plus a one-paragraph summary of decisions so far.

## Consult at decision gates

Consult the conductor for every material orchestration decision:

- initial decomposition and the first worker wave;
- each model/effort assignment or reassignment;
- parallel versus serial scheduling when dependencies are uncertain;
- replanning after a failed, conflicting, or surprising result;
- reviewer selection and whether the evidence is sufficient to finish.

Bundle related choices into one consultation. Local tool calls, obvious next commands, and implementation details within an approved unit are not orchestration decisions.

The root may reject advice that conflicts with user instructions, live constraints, repository evidence, or safety boundaries. Record the replacement decision in one sentence and continue.

## Dispatch bounded work

Give every worker a packet with:

```text
objective: one independently verifiable result
ownership: exact files, modules, or research question
context: only facts and paths needed for this unit
constraints: interfaces and decisions it must preserve
done: observable acceptance checks
return: conclusions, changed files, checks run, blockers
```

Use full history only when the unit genuinely depends on it. Prefer a narrow recent-turn fork or no fork plus the packet. Parallelize units only when their writes and decisions do not overlap. The root resolves integration and runs authoritative checks; workers do not commit, publish, or broaden scope unless the user explicitly requested that action.

Use a stronger reasoning model for a narrow hard question before spending that model on a broad implementation. Use cheaper coding agents for settled units. Select a reviewer different from the primary implementer when the live catalog and capacity allow it.

## Run the project

1. Ask the conductor for a decomposition, routes, waves, and evidence.
2. Dispatch the first independent wave within the live concurrency limit.
3. Inspect worker artifacts and results rather than trusting summaries alone.
4. Reconsult only at the gates above, passing deltas instead of replaying the project.
5. Integrate the smallest coherent change and run the checks named in the accepted route.
6. Ask the conductor to select a focused final reviewer. Address findings or document why they do not apply.
7. Finish only when the user outcome and acceptance criteria are met.

If subagent spawning or model overrides are unavailable, keep the same decomposition and evidence discipline but execute sequentially with the current agent. State the limitation once.

## Cost discipline

- Default to `low` for deterministic lookup or mechanical work, `medium` for bounded multi-step work, and `high` for genuinely difficult reasoning.
- Use the host's top effort tiers (`xhigh`, `max`, or `ultra`, where offered) only when the conductor identifies the specific uncertainty that lower effort is unlikely to resolve.
- Escalate the smallest unit, not the whole project.
- Stop a worker when its acceptance check is satisfied. Reuse its conclusion; do not make the root solve the same problem again.
- Prefer diverse review over duplicate implementation.
