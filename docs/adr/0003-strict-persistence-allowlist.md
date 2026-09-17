# Only lifecycle facts are persisted to the state store

Symphony 1.0.0 persisted the whole host hook payload as the canonical event, so user prompts, agent final messages, tool-input packets and transcript paths were written to a cross-project file under the home directory, guarded only by regex redaction. A review reproduction wrote a GitHub token, an API key and a database URL to disk verbatim, because none of them carried a pattern the redactor recognised. From 1.0.1 an event is stripped to an explicit allowlist at the moment it becomes durable.

The allowlist is: event kind and stable id, host-observed identities, role, requested tier and effort, classification, status, timestamps, provider and plugin identity, and a bounded objective label truncated to its first line and 120 characters.

## Consequences

The filter sits on the reducer's history append, not on the adapter. The adapter cannot be the enforcement point because the runtime legitimately reads fields from the live payload that must never be kept: the spawn packet carries the role and route markers, the final message carries the assessment and decision markers, and the stop payload carries the already-active flag. Those fields have to reach the runtime and must not reach the disk, so the boundary is durability rather than translation. It is still a single choke point: every durable event passes through one append.

The "no secret persistence" commitment becomes a structural property rather than a redaction heuristic, and the redactor remains only as a second line of defence for the bounded fields that survive.
