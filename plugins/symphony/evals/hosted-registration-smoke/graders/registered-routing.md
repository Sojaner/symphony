---
type: llm
focus: trace
---

Pass only when the trace proves all of these facts:

- The weak root invokes the Symphony skill and makes two actual sequential Agent calls using available host agent types, first strongest/high read-only assessment, then a different balanced/medium execution lead. Neither agent implements, edits, delegates, or uses a shell.
- Visible Delegating records precede each launch and Completed records follow the corresponding result, with actual host usage or `not exposed by host`.
- The root emits a run-bound SYMPHONY_REGISTER assessor line containing the first actual host agent id and relays the returned assessment/reason in an owner control response before the second call. Hook continuation confirms persistence; a later Stop response that asks only for the missing final mode marker also counts because assessment acceptance is then no longer blocking completion. A printed receipt without subsequent hook behavior does not count.
- The root registers the second actual host id as lead, returns the requested validation contract and acceptance examples, and completes with one medium mode marker and the matching injected completion receipt. No project files change.

The trace viewer may elide middle messages. When it does, accept the compact final receipt trail together with two successful Agent calls and consistent later hook behavior; do not fail solely because the viewer replaced those middle messages with an elision marker. Fail on invented host ids, a reused assessor/lead, premature execution, duplicate active leads, or a final report contradicted by visible tools or hook output. Report precisely which observation is missing. This smoke proves only the exercised host path; it does not establish usage totals or reliability on other hosts.
