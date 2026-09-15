---
description: Reassess Symphony or set the persistent project profile
argument-hint: [small|medium|large|auto]
---

<!-- SYMPHONY_CONTROL: assess -->
<!-- SYMPHONY_ARGS: $ARGUMENTS -->

Request an evidence-based reassessment for the active run. `small`, `medium`, or `large` sets a persistent project profile without forcing the run mode; `auto` clears that override.

End with the exact injected `SYMPHONY_CONTROL_HANDLED` receipt on its own final line. Its matching Stop ends only this control response; do not resume project work or emit a run-completion receipt.
