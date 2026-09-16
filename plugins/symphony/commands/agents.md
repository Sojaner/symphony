---
description: List Symphony subagents for the active run or retained run history
argument-hint: [--all]
---

<!-- SYMPHONY_CONTROL: agents -->
<!-- SYMPHONY_ARGS: $ARGUMENTS -->

Report the injected Symphony agent ledger as a compact table. Always include run, id, status, role, model, and effort. Include a token, duration, tool-use, or source/scope column only when at least one included row exposes that measurement; leave another row's unavailable measurement cell empty. Claude Agent `PostToolUse` token values describe only the final request, not the whole agent; host duration and tool uses describe the whole agent run, while lifecycle duration is observed wall time. When the host exposes a live agent-listing tool, reconcile live status and metadata before reporting. Never infer model, effort, usage, or cost, and never print unavailable token or duration placeholders.

The default includes every observed subagent in the active run, including terminal agents. `--all` also includes every retained historical run. Prefer live host values and use lifecycle metadata when live fields are unavailable. This command is read-only: do not enable Symphony, start a run, spawn a lead, or update agent status. Do not collect or retain prompts, transcripts, reasoning, or worker output.

End with the exact injected `SYMPHONY_CONTROL_HANDLED` receipt on its own final line. It is single-use and bound to this run, session, and turn; its matching Stop ends this control without waiting for agents or changing lifecycle state. Do not reuse it or emit a run-completion receipt.
