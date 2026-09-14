---
name: symphony
description: Orchestrate complicated projects from a runtime-verified low-cost root through a reusable routing consultant and narrowly scoped parallel subagents. Use for multi-domain, high-risk, or multi-part work where model and reasoning-effort choices materially affect cost, speed, or quality; skip ordinary tasks one agent can finish directly. When the user explicitly asks for Symphony or orchestration, invoke this skill immediately and before asking any clarifying question — Symphony runs its own preflight verification and questionnaires.
---

# Symphony

Make a cheap root agent an effective project controller. The root owns scope, integration, verification, and the user relationship. Subagents own bounded reasoning or implementation units. A reusable **conductor** advises on decomposition, model choice, effort, scheduling, and review; it never becomes the project owner.

## Activate

Use Symphony when the work has at least one of these properties:

- several independent implementation units;
- architecture or reasoning whose mistakes would propagate widely;
- multiple specialties, repositories, or verification surfaces;
- a long execution path where routing decisions materially affect cost or quality.

Handle a small, sequential task directly. Delegation overhead is real work: bootstrapping the conductor and dispatching workers costs minutes of latency before any project work starts. As a sizing rule, orchestrate only when the project decomposes into three or more independently dispatchable units, or a single agent would need well over fifteen minutes of work — below that, the token savings cannot repay the coordination time, and one capable agent finishes faster at similar quality.

## Verify the orchestrator

Complete this gate before reading project files, consulting the conductor, spawning workers, or starting project work.

The user does not need to declare any model id, effort, or profile in the prompt. Symphony gathers and verifies the orchestrator itself and interacts with the user only for what it cannot determine:

1. Determine the model and effort the current task is actually running on — yourself, before involving the user. Check every runtime source the host exposes to the agent, in order:
   - the injected system or session context that names the active model (both Codex and Claude Code provide one);
   - the shell environment of the agent's own subprocesses — Claude Code exports the effort and session id directly (`CLAUDE_EFFORT`, `CLAUDE_CODE_SESSION_ID`);
   - the host's own session records — in Claude Code, the transcript at `~/.claude/projects/<project-slug>/<session-id>.jsonl` (the exact file named by the session id from the environment) records the model and effort on its recent assistant entries; in Codex, the newest session under `~/.codex/sessions/` records the session's model (and effort where present) in its metadata, and its transcript must contain this conversation's own recent text before the record is trusted as this session;
   - the host's status facility or session header, and any other runtime metadata.

   The live model catalog only lists available choices; it does not identify the active orchestrator and cannot satisfy this step. Do not infer or guess, and do not ask the user anything this step can answer.
2. Read the live catalog and identify the cheapest suitable orchestrator: a model that supports spawning subagents with model overrides, at `medium` or `high` effort. Low effort is not accepted for the orchestrator — the gate, the fit assessment, and integration are judgment work, and a root running at low effort demonstrably skips them. Workers may still run at `low`.
3. When the runtime profile is verified and suitable, state it in one line — "Orchestrator verified: `<id>` at `<effort>`" — and continue. When it is suitable but a different model or effort than the cheapest suitable recommendation, name the recommendation and ask one question to confirm the current selection is intentional.
4. When the runtime profile is unsuitable — low effort, or no subagent support — stop before project work and run the profile questionnaire below.
5. Only when every source in step 1 has been checked and none exposes the model or effort, ask the user to read the host's model selector and report it, through the structured question tool, offering the catalog's suitable candidates as options. Before asking, read the host's stored model configuration where the agent can reach it — `~/.codex/config.toml` in Codex; the `model` key in `.claude/settings.json` or `~/.claude/settings.json` in Claude Code — and offer its model and effort as the preselected default option: a config value is a strong hint to confirm with one yes/no, never verification by itself, since the session selector can override it. Precede the question with a one-line report of the failed checks — "Runtime metadata does not name the active model or effort (checked session context and status)" — so the attempt is auditable; asking without that report is skipping step 1, not verification. Their explicit answer is the verified profile: a suitable answer passes, an unsuitable one goes to the questionnaire. The question must be about the selector's actual current value, ask nothing else, and never be bundled with other confirmations; a vague question ("a Symphony-capable model at a suitable effort?") or a compound one is not verification. Never proceed on a guess.

A declared profile in the invocation (`Orchestrator: <id>` / `Effort: <medium|high>`) remains supported but is a request to verify, never evidence. Verify it against the runtime exactly; on any mismatch, stop before all project work, report both values plainly — "You declared `<declared>`, but this task is running on `<actual>`" — and run the profile questionnaire. Never continue on the wrong model by default.

Re-run this verification from step 1 whenever the project resumes: a new session, a restored checkpoint, a compacted conversation, or a handoff. A profile remembered from earlier turns, a checkpoint, or phrasing like "previously selected" is never evidence.

### Profile questionnaire

Help the user choose a valid profile instead of refusing outright:

1. Read the live catalog from the host's subagent tool schema — and the host's model selector list where it is exposed — and shortlist two to four orchestrator candidates: the cheapest models that support spawning subagents with model overrides, cheapest first, one line of reasoning each.
2. Ask the user to pick a model and an effort — `medium` for routine coordination, `high` for long or unsettled projects — recommending the cheapest suitable option. Use the host's structured question tool when it exists (AskUserQuestion in Claude Code, the user-input request facility in Codex); otherwise ask as a plain numbered question in chat.
3. Tell the user to apply the choice in the host — the model and effort selectors (for example `/model` in Claude Code, the task's model picker in Codex) — or to start a new task with that selection, since a skill cannot change the model or effort of its already-running task.
4. After the user says they applied it, re-run the verification above from step 1. Every pass through the questionnaire ends back at verification; it never leads directly into project work.

## Confirm the project

The user has often already started the agent inside the project. When the invocation does not name a location, assume the current working directory is the project — but confirm that assumption before acting on it; never treat it as settled silently.

Ask once, through the same question facility as the profile questionnaire — and in the same round when both are still open:

- the project location: the current working directory, another path or repository, or a greenfield project with no existing code yet;
- the outcome and acceptance criteria, when the invocation left them unclear.

Do not read project files or spawn workers before the location is confirmed. One explicit confirmation is enough; do not re-ask at later gates.

## Assess the fit

Before bootstrapping the conductor, size the project against the Activate criteria — the conductor is itself overhead, so this assessment is the root's own work:

1. Make a shallow pass only: the request, the plan or requirements the user pointed to, and at most a directory listing or file tree. Do not deep-read the codebase to decide whether to orchestrate.
2. Estimate the independently dispatchable units and the single-agent effort. State the estimate in two or three lines.
3. When the project meets the sizing rule (three or more independent units, or well over fifteen minutes of single-agent work), say so in one line and proceed to the conductor.
4. When it does not, recommend direct execution: report that orchestration overhead would exceed its savings, and ask through the same question facility whether to proceed with Symphony anyway, have this agent do the work directly, or stop. The user's explicit choice is final — including choosing orchestration despite the recommendation.

Skip the questionnaire in step 4 when the user has already acknowledged the overhead and asked for orchestration regardless; note their confirmation and proceed.

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
2. Dispatch the first independent wave within the live concurrency limit. Dispatch independent units as parallel workers; routing every unit to one worker serializes the project and forfeits the speed of orchestration.
3. Wait for every dispatched worker to return before proceeding or concluding. Never end the root turn while any worker is outstanding — an unfinished wave is unfinished project work, and "waiting for completion" is not a final state. Where workers run in the background, immediately call the host's blocking wait or result-retrieval tool on each outstanding worker id in the same turn (for example Claude Code's task-output tool, or a Codex follow-up wait); dispatching a wave and then emitting a status message like "dispatched, awaiting completion" IS ending the turn and abandons the wave — keep calling wait tools until every worker in the wave has returned.
4. Keep a wave ledger the moment more than one worker exists: worker id, unit, dispatch time, status, written where it survives the turn (a scratch file, or restated in the conversation at each dispatch). The host may still suspend the root during a long wait or deliver a worker's return while it sleeps; on every resume — a worker notification, a user message, a restored or compacted session — reconcile the ledger first and collect every returned result before any new decision. A suspended wave is resumed from the ledger, never abandoned; if a worker's status cannot be determined after a wake, query it or redispatch its unit rather than assuming it finished.
5. Inspect worker artifacts and results rather than trusting summaries alone.
6. Reconsult only at the gates above, passing deltas instead of replaying the project.
7. Integrate the smallest coherent change and run the checks named in the accepted route yourself — a worker's claim of success is not evidence.
8. Ask the conductor to select a focused final reviewer. Address findings or document why they do not apply.
9. Finish only when the user outcome and acceptance criteria are met and the root has verified them in this session.

If subagent spawning or model overrides are unavailable, keep the same decomposition and evidence discipline but execute sequentially with the current agent. State the limitation once.

## Cost discipline

- Default to `low` for deterministic lookup or mechanical work, `medium` for bounded multi-step work, and `high` for genuinely difficult reasoning.
- Use the host's top effort tiers (`xhigh`, `max`, or `ultra`, where offered) only when the conductor identifies the specific uncertainty that lower effort is unlikely to resolve.
- Escalate the smallest unit, not the whole project.
- Stop a worker when its acceptance check is satisfied. Reuse its conclusion; do not make the root solve the same problem again.
- Prefer diverse review over duplicate implementation.
