---
description: Disable Symphony for this project and gracefully stop any active run
---

<!-- SYMPHONY_CONTROL: disable -->

Confirm that Symphony is disabled for future work in this working tree. If a run is active, follow the injected graceful-stop instructions and report any incomplete integration or verification.

End with the exact injected `SYMPHONY_CONTROL_HANDLED` receipt on its own final line so this control response terminates without entering the run's Stop wait path.
