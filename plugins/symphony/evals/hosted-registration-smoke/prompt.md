---
name: Read-only hosted assessor registration smoke
description: Real sequential Agent calls and owner-root registration/assessment relays in the evaluator's isolated empty workspace; no project writes.
tags: [orchestrator, lifecycle, smoke]
runs: 1
max_turns: 24
timeout_seconds: 300
allowed_tools: [Read, Glob, Grep, Skill, Agent, Task, TaskOutput, TaskStop]
---

Orchestrator: claude-haiku-4-5-20251001
Effort: medium

SYMPHONY_CONTROL: start
SYMPHONY_TASK: read-only design of a CSV-to-JSON validation contract and independently verifiable acceptance cases

Invoke the `symphony:symphony` skill in this isolated eval workspace before dispatching. This is a real, bounded, read-only orchestration smoke, not a dry run. Spawn exactly two sequential agents using a type actually available from this host, such as `general-purpose`: first the strongest/high read-only assessor, then a separate capable/medium execution lead for this small task. `symphony_assessor` and `symphony_lead` are assignments, not host agent types. Neither agent may edit files, implement code, delegate, or use a shell. The lead's deliverable is two short paragraphs: a CSV-to-JSON validation contract and three independently verifiable acceptance examples. Task facts are all provided here; no repository scan or document memory writes are needed. Input is UTF-8 CSV with a header; output is a JSON array of objects; reject duplicate headers and rows whose field counts differ from the header.

Visibly announce each delegation before its actual Agent/Task call and its completion afterward, using the skill's full records and honest host usage fields. Request synchronous calls (`run_in_background: false` when exposed). For a background assessor, collect its terminal result before registration. Use the blocking result tool (`TaskOutput` with `block: true` when exposed) for that same id, then perform the combined registration and assessment relay below. Register a background lead immediately as described below, before collecting its result. Do not launch another child to collect a result. Agent and Task are host aliases; neither a denied attempt nor a retry counts as a successful launch.

Give the first child the explicit assignment `symphony_assessor`, the injected run id, objective, acceptance criteria, project profile, lifecycle status, and the skill's absolute routing-reference paths. Require its concise assessment to end with exactly `SYMPHONY_ASSESSMENT:<run-id>:<project-profile>:<run-mode>` and `SYMPHONY_ASSESSMENT_REASON:<single bounded line>`, substituting the actual run id and assessed sizes. After it returns, relay those actual lines with `SYMPHONY_REGISTER:<run-id>:assessor:<agent-id>` in an owning-root final-channel control response, without a run-completion receipt. End that response and wait for the hook's accepted-assessment acknowledgment before the second launch. Do not invent an assessment receipt in the root.

Give the separate `symphony_lead` the accepted assessment and bounded deliverable. As soon as its host id is available, end a final-channel response with `SYMPHONY_REGISTER:<run-id>:lead:<agent-id>` and no run-completion receipt. After the hook acknowledges registration, collect that same lead's terminal result, using the blocking result tool if needed. Never claim a registration or receipt was accepted without hook evidence; report any missing capability or evidence plainly.

Keep the whole run compact so lifecycle evidence is retained. The final response must be self-contained and repeat, in order: both distinct actual agent ids and roles; both `Completed:` records with honest usage; the hook-confirmed small assessment mode; the two-paragraph deliverable; exactly `<!-- SYMPHONY_MODE:small -->`; and the exact injected completion receipt. Do not use a prose `mode: small` substitute.
