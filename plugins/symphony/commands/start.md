---
description: Start one guarded Symphony run without changing persistent project enablement
argument-hint: [--dry-run] <project description>
---

<!-- SYMPHONY_CONTROL: start -->
<!-- SYMPHONY_TASK: $ARGUMENTS -->

Invoke the Symphony skill and follow the lifecycle context injected by the hook. This is a one-off run: completing or stopping it must not change the project's persistent enablement policy.

`--dry-run` records an explicit validation-only run: do not spawn agents or write project files, and report only the planned assessor and separate execution lead. Without `--dry-run`, assessment completion remains mandatory. If no task was supplied, end with the injected `SYMPHONY_CONTROL_HANDLED` receipt instead of starting work.
