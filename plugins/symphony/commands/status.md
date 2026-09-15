---
description: Show Symphony enablement and active-run status for this project
---

<!-- SYMPHONY_CONTROL: status -->

Report the Symphony project policy and active-run status injected by the hook, including observed final-request tokens and the number of agents whose final-request total is `not exposed by host`. These are partial observations, never complete agent totals. Do not estimate missing usage or present a partial total as complete. Do not start, stop, recover, or modify a run.

End this inspection response with the exact injected `SYMPHONY_AGENTS_INSPECTED` receipt on its own final line. The hook issues a random, single-use authorization bound to the run, session, and host turn; a matching Stop consumes it without changing lifecycle state. Do not reuse it for later project work or emit a run-completion receipt for this command.
