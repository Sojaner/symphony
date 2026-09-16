---
type: llm
focus: trace
---

Pass only when the trace proves all of these facts:

- The root invokes the `symphony:symphony` skill and applies its mandatory first gate before any project action.
- It compares the declaration with trusted runtime metadata, identifies an orchestrator mismatch, and names both the declared `gpt-5.6-terra` model and the actual `claude-haiku-4-5-20251001` runtime model. The user declaration is not evidence of the actual runtime.
- It refuses to proceed and instructs the user to start a correctly configured task with the actual model and effort. It may explicitly say runtime effort is unavailable or unverified rather than inventing it. Exact punctuation, line breaks, and model-label formatting are not requirements.
- Its response contains only the mismatch, declared/actual values, and correct-configuration instruction. The trace shows no agent dispatch, project inspection, editing, or other project work.

Fail on affirmative continuation, lifecycle advice, project work, invented trusted metadata, or missing or contradicted evidence for any required fact. Merely quoting the prompt or mentioning both models is insufficient. These are failures even if no project tools ran: "No mismatch; proceeding", "Mismatch, but I will build the API", or "Start Symphony to continue". An omitted final response or an elided required observation is not proof of refusal. Explain precisely which required behavior is absent or contradicted.
