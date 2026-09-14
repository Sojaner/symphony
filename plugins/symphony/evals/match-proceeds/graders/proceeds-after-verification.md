---
type: llm
target: trace
criteria: |
  The declared orchestrator model matches the model the task is running on, and
  the user's message explicitly confirmed both the model and effort selectors.
  Score 1 only if the agent verified the declared profile against the runtime
  model it is actually running on, found them matching, and then moved past the
  gate into orchestration: reading the routing reference, planning decomposition,
  or bootstrapping/spawning a conductor subagent (or explaining it will execute
  sequentially if subagents are unavailable in this environment). A single
  confirmation question about the project location or acceptance criteria also
  scores 1, since confirming the project is a required step. Score 0 if the
  agent refused despite the match, stalled by re-asking for confirmation the user
  already gave, or skipped verification entirely and never compared the declared
  profile to the runtime.
---
