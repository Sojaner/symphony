---
description: Stop the active Symphony run while preserving project enablement
argument-hint: [--force]
---

<!-- SYMPHONY_CONTROL: stop -->
<!-- SYMPHONY_ARGS: $ARGUMENTS -->

Stop only the active Symphony run. Without `--force`, follow the injected graceful-stop instructions. With `--force`, report that lifecycle protection was released and that an agent may still be running. Never revert or delete workspace changes.
