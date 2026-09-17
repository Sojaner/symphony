# A run begins at assessor spawn, not at the prompt

Symphony 1.0.0 opened a run for every non-control prompt in an enabled project, before any agent existed. Because a run with no lead has no outcome, the Stop hook blocked completion forever, and nothing read the host's already-active flag, so the session could only be freed by a force-stop control the block message never named. From 1.1.0 a run begins when the assessor is actually spawned, and a run with zero delegations never blocks Stop.

## Consequences

A root agent that ignores the injected assessment guidance produces an ungoverned turn rather than a blocked session. This is deliberate: Symphony gates observed work, and it cannot force a root to obey. The alternative considered and rejected was to keep opening runs at the prompt and define a "deterministically trivial" pattern list to decide which prompts skip assessment. Any such list is a heuristic over free text, which is the prose-as-protocol failure mode the 1.0 rewrite existed to remove.

This also removes the need to persist the prompt text: the assessment guidance is now composed from the live payload rather than rebuilt later from stored state, which is what makes the strict persistence allowlist in ADR 0003 reachable.
