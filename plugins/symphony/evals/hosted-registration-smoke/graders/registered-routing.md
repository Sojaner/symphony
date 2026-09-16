---
type: llm
focus: trace
---

Pass only when the trace proves all of these facts:

- The weak root invokes the Symphony skill and makes exactly two actual sequential Agent/Task launches using available host agent types, first strongest/high read-only assessment, then a different capable/medium execution lead for the small task. If the host backgrounds a child, registration and blocking result collection for the same id are required before proceeding. Neither agent implements, edits, delegates, or uses a shell.
- Visible Delegating records precede each launch and Completed records follow the corresponding result, with actual host usage or `not exposed by host`.
- The root emits a run-bound SYMPHONY_REGISTER assessor line containing the first actual host agent id and relays the returned assessment/reason in an owner final-channel control response before the second launch. The hook must acknowledge the accepted assessment before execution. A request for a missing final mode marker does not prove assessment acceptance. A printed receipt without subsequent hook acknowledgment does not count.
- The root registers the second actual host id as lead in a separate final-channel control response without run completion, obtains the hook's registration acknowledgment, returns the requested validation contract and acceptance examples, and completes with one small mode marker and the matching injected completion receipt. No project files change.

The trace viewer may elide middle messages. An elision is not evidence that a required event happened; identify any required acknowledgment that cannot be verified. Fail on invented host ids, a reused assessor/lead, premature execution, duplicate active leads, max-turn exhaustion, or a final report contradicted by visible tools or hook output. Report precisely which observation is missing. This smoke proves only the exercised host path; it does not establish usage totals or reliability on other hosts.
