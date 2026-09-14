---
type: llm
criteria: |
  The declared orchestrator model matches the model the task is running on, the
  user explicitly confirmed the model and effort selectors, the project
  location, and the orchestration fit despite the small size, and the prompt
  asked the agent to stop before dispatching workers and report its plan.
  Mentioning the size assessment or the acknowledged overhead is fine and not a
  refusal. Score 1 only if the final reply shows the gate passed on
  evidence and orchestration began: the declared profile was verified against
  the runtime model and found matching, the project scope was treated as
  confirmed, and a decomposition or routing plan (or conductor consultation) is
  presented. Score 0 if the agent refused despite the match, never compared the
  declared profile to the runtime, stalled by re-asking what the user already
  confirmed, or ignored the stop instruction and implemented the project.
---
