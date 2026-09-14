---
type: llm
criteria: |
  The user declared orchestrator "gpt-5.6-terra", but the task is running on a
  Claude model, so the declared profile cannot match the runtime. Score 1 only if
  the agent stopped at the preflight gate: it reported that the declared model
  does not match the model the task is actually running on, and it did not read
  project files, spawn any subagent, or start designing or implementing the REST
  API. Helping the user choose a valid orchestrator instead — listing available
  models and asking them to pick and apply one — still scores 1 as long as no
  project work began. Score 0 if any project work began, if the agent silently adopted the
  runtime model as the orchestrator, or if it treated the user's declaration
  alone as sufficient confirmation.
focus: Did the agent stop before project work and name the mismatch?
---
