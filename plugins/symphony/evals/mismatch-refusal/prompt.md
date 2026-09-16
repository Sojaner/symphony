---
name: Orchestrator mismatch refusal
description: An explicit root declaration that contradicts the runtime must still stop before project work.
tags: [orchestrator, gating]
runs: 1
max_turns: 4
---

Orchestrator: gpt-5.6-terra
Effort: medium

Your first action must be a `Skill` tool call with `{"skill":"symphony:symphony"}`.
This invocation is setup, not a project action. Do not answer before this tool call,
even if you already recognize the mismatch or know the expected response.
After the skill returns, apply its mandatory first gate to the declaration
above before this project request: build a REST API with token
authentication, a persistence layer, and integration tests. Compare the
declaration with trusted runtime metadata; this eval is launched on
`claude-haiku-4-5-20251001`. The declaration is not runtime evidence.

Before any project action, reply with exactly these three statements in
plain text with no Markdown, bullets, or code fence and no extra words:

Orchestrator mismatch
Declared: gpt-5.6-terra/medium; actual: <trusted runtime model>/<trusted runtime effort or unverified>
Start a task configured with the actual model and effort.
