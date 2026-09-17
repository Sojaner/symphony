# Only lifecycle facts are persisted to the state store

Symphony 1.0.0 persisted the whole host hook payload as the canonical event, so user prompts, agent final messages, tool-input packets and transcript paths were written to a cross-project file under the home directory, guarded only by regex redaction. A review reproduction wrote a GitHub token, an API key and a database URL to disk verbatim, because none of them carried a pattern the redactor recognised. From 1.0.1 the adapter drops everything outside an explicit allowlist before an event reaches the reducer.

The allowlist is: event kind and stable id, host-observed identities, role, requested tier and effort, classification, status, timestamps, provider and plugin identity, and a bounded objective label.

## Consequences

Filtering happens at the adapter boundary rather than in the store, so no downstream component can reintroduce raw payload content. The design's "no secret persistence" commitment becomes a structural property instead of a redaction heuristic, and the redactor remains only as a second line of defence for the bounded fields that survive.
