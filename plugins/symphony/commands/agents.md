---
description: List Symphony subagents for the active run or retained run history
argument-hint: [--all]
---

<!-- SYMPHONY_CONTROL: agents -->
<!-- SYMPHONY_ARGS: $ARGUMENTS -->

Report the injected Symphony agent ledger as a compact table with run, id, status, role, model, effort, final-request total/input/output/cache-creation/cache-read tokens, final-request duration/tool uses, and usage source/scope. Claude Agent `PostToolUse` values describe only the final request, not the whole agent; never call them agent totals. When the host exposes a live agent-listing tool, reconcile live status and metadata before reporting. Preserve `not exposed by host`; never infer model, effort, usage, or cost.

The default includes every observed subagent in the active run, including terminal agents. `--all` also includes every retained historical run. Prefer live host values and use lifecycle metadata when live fields are unavailable. This command is read-only: do not enable Symphony, start a run, spawn a lead, or update agent status. Do not collect or retain prompts, transcripts, reasoning, or worker output.

End this inspection response with the exact injected `SYMPHONY_AGENTS_INSPECTED` receipt on its own final line. The hook issues a random, single-use authorization for this run, session, and turn when exposed by the host; it stores only that pending authorization in a private `.inspection.<session-hash>.json` file alongside the plugin-data lifecycle record. A matching Stop consumes it without changing lifecycle state, and any later prompt in that session invalidates it. Other sessions keep their own pending authorizations. Do not reuse it for subsequent project work, and do not emit a run-completion receipt for this command.
