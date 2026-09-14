---
name: Orchestrator match proceeds
description: The preflight gate must pass and orchestration must begin when the declared profile matches the runtime model. Run this suite with --model claude-haiku-4-5-20251001 so the declaration below matches the sandbox model.
tags: [orchestrator, gating]
runs: 3
max_turns: 8
---

Orchestrator: claude-haiku-4-5-20251001
Effort: medium

I confirm this task's model selector is set to exactly claude-haiku-4-5-20251001 and its effort selector is set to medium.

Use the symphony skill to orchestrate this complex project: in the current working directory, build a small CLI tool that converts CSV files to JSON, with a README and one test. This is a greenfield project; the directory is empty, and I confirm it is the correct location.

I know this project is small and I acknowledge the orchestration overhead; I want Symphony orchestration regardless, so treat the fit as confirmed.

For this dry run, stop before dispatching any worker or writing any project file: end your reply with the verified orchestrator profile, the confirmed project scope, and your planned decomposition and routing.
