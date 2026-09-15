---
description: Enable Symphony persistently for this project and optionally start a task
argument-hint: [project task]
---

<!-- SYMPHONY_CONTROL: enable -->
<!-- SYMPHONY_TASK: $ARGUMENTS -->

Symphony is now enabled for this working tree. If a task was supplied, follow the lifecycle context injected by the hook and start it now. If no task was supplied, confirm enablement without starting a run.

When no task was supplied, end with the exact injected `SYMPHONY_CONTROL_HANDLED` receipt on its own final line. A task-bearing enable starts project work and has no control receipt.
