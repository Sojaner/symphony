---
name: Read-only hosted assessor registration smoke
description: Real sequential Agent calls and owner-root registration/assessment relays in the evaluator's isolated empty workspace; no project writes.
tags: [orchestrator, lifecycle, smoke]
runs: 1
max_turns: 16
timeout_seconds: 300
allowed_tools: [Read, Glob, Grep, Skill, Agent]
---

Orchestrator: claude-haiku-4-5-20251001
Effort: medium

SYMPHONY_CONTROL: start
SYMPHONY_TASK: read-only design of a CSV-to-JSON validation contract and independently verifiable acceptance cases

Use the symphony skill in this isolated eval workspace. This is a real, bounded, read-only orchestration smoke, not a dry run. Spawn exactly two sequential agents using types actually available from this host: first the strongest/high read-only assessor, then a separate capable/medium execution lead for this small task. Neither agent may edit files, implement code, delegate, or use a shell. The lead's deliverable is two short paragraphs: a CSV-to-JSON validation contract and three independently verifiable acceptance examples. Task facts are all provided here; no repository scan or document memory writes are needed. Input is UTF-8 CSV with a header; output is a JSON array of objects; reject duplicate headers and rows whose field counts differ from the header.

Visibly announce each delegation before its actual Agent call and its completion afterward, using the skill's full records and honest host usage fields. Use synchronous Agent calls. After the assessor returns, register its actual host id and relay its run-bound assessment and reason in an owning-root control response without a run-completion receipt. Continue after the lifecycle hook accepts that assessment. Then spawn the separate lead, collect its result, and register its actual id. Never claim a registration or receipt was accepted without hook evidence; report any missing capability or evidence plainly.

Keep the whole run compact so lifecycle evidence is retained. The final response must be self-contained and repeat, in order: both distinct actual agent ids and roles; both `Completed:` records with honest usage; the hook-confirmed small assessment mode; the two-paragraph deliverable; exactly `<!-- SYMPHONY_MODE:small -->`; and the exact injected completion receipt. Do not use a prose `mode: small` substitute.
