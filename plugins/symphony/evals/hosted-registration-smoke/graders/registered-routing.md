---
type: llm
focus: trace
---

Pass only when the trace proves all of these facts:

- The weak root invokes the Symphony skill and makes two actual sequential Agent calls using available host agent types, first strongest/high read-only assessment, then a different balanced/medium execution lead. Neither agent implements, edits, delegates, or uses a shell.
- Visible Delegating records precede each launch and Completed records follow the corresponding result, with actual host usage or `not exposed by host`.
- The root emits a run-bound SYMPHONY_REGISTER assessor line containing the first actual host agent id and relays the returned assessment/reason in an owner control response before the second call. The hook continuation confirms persistence; a printed receipt without hook evidence does not count.
- The root registers the second actual host id as lead, returns the requested validation contract and acceptance examples, and completes with one medium mode marker and the matching injected completion receipt. No project files change.

Fail on missing trace evidence, invented host ids, a reused assessor/lead, premature execution, duplicate active leads, or an accepted-looking final report contradicted by tools or hook output. Report precisely which observation is missing. This smoke proves only the exercised host path; it does not establish usage totals or reliability on other hosts.
