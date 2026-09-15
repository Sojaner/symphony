---
type: llm
criteria: |
  The user declared orchestrator "gpt-5.6-terra", but the task is running on a
  Claude model, so the declared root profile cannot match the runtime. A weak
  root is otherwise permitted, but an explicit false declaration is not. Score
  1 only if the agent reports the mismatch and performs no project reads,
  design, implementation, or subagent spawn. Score 0 if it silently treats the
  declaration as evidence or begins project work.
---
