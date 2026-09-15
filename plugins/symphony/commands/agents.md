---
description: List Symphony subagents for the active run or retained run history
argument-hint: [--all]
---

<!-- SYMPHONY_CONTROL: agents -->
<!-- SYMPHONY_ARGS: $ARGUMENTS -->

Report the injected Symphony agent ledger as a compact table with run, id, status, role, model, effort, final-request total/input/output/cache-creation/cache-read tokens, run duration/tool uses, and usage source/token scope. Claude Agent `PostToolUse` token values describe only the final request, not the whole agent; duration and tool uses describe the whole agent run. When the host exposes a live agent-listing tool, reconcile live status and metadata before reporting. Preserve `not exposed by host`; never infer model, effort, usage, or cost.

The default includes every observed subagent in the active run, including terminal agents. `--all` also includes every retained historical run. Prefer live host values and use lifecycle metadata when live fields are unavailable. This command is read-only: do not enable Symphony, start a run, spawn a lead, or update agent status. Do not collect or retain prompts, transcripts, reasoning, or worker output.

End with the exact injected `SYMPHONY_CONTROL_HANDLED` receipt on its own final line. It is single-use and bound to this run, session, and turn; its matching Stop ends this control without waiting for agents or changing lifecycle state. Do not reuse it or emit a run-completion receipt.
