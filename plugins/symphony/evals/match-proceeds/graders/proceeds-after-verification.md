---
type: llm
criteria: |
  The task runs on the declared Haiku root, but the new Symphony contract allows
  a weak root because a strongest-available high-effort child becomes the
  execution lead. The prompt explicitly requests a dry run without spawning or
  writing. Score 1 only if the final reply identifies the actual root, names a
  strongest/high lead from the live catalog, selects exactly one of small,
  medium, or large, identifies applicable capability routing, performs no
  project work, and repeats the exact SYMPHONY_RUN_COMPLETE receipt injected by
  the lifecycle hook. Score 0 if it rejects Haiku merely for being weak, makes
  the root the integrator, selects overlapping modes, omits the receipt, spawns
  an agent, or writes project files.
---
