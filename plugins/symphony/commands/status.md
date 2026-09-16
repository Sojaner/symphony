---
description: Show Symphony enablement and active-run status for this project
---

<!-- SYMPHONY_CONTROL: status -->

Report the injected project profile, profile source, assessment revision, active-run mode, mode revision, and reassessment-due state, including when no run is active. Include observed final-request tokens only when at least one total is exposed. These are partial observations, never complete agent totals. Omit missing token and duration measurements; do not estimate them or present a partial total as complete. Do not start, stop, recover, or modify a run.

End with the exact injected `SYMPHONY_CONTROL_HANDLED` receipt on its own final line. The receipt is bound to the run, session, and host turn; a matching Stop consumes it without changing lifecycle state or waiting for agents. Do not reuse it or emit a run-completion receipt.
