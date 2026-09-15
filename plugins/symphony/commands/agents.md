---
description: List Symphony subagents for the active run or retained run history
argument-hint: [--all]
---

<!-- SYMPHONY_CONTROL: agents -->
<!-- SYMPHONY_ARGS: $ARGUMENTS -->

Report the injected Symphony agent ledger as a compact table with run, id, status, role, model, and effort. When the host exposes a live agent-listing tool, reconcile live status and metadata before reporting. Preserve `not exposed by host`; never infer model or effort.

The default includes every observed subagent in the active run, including terminal agents. `--all` also includes every retained historical run. Prefer live host values and use lifecycle metadata when live fields are unavailable. This command is read-only: do not enable Symphony, start a run, spawn a lead, or update agent status. Do not collect or retain prompts, transcripts, or worker output.
