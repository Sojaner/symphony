# A second session does not adopt a run whose owner is still reporting

Symphony treated any `SessionStart` carrying an unfamiliar session id as proof that the process owning the run had died. It then marked the live lead interrupted, told the root to spawn a replacement, and rewrote the run's owner to the newcomer. That inference is sound for a resumed session and wrong for a second terminal in the same project, and the two cases are indistinguishable from the payload alone. Two sessions would take the run from each other indefinitely, each declaring the other's healthy lead dead.

From 1.1.1 adoption requires evidence. The host saying `source: resume` or `compact` is exact, and Claude sends it. Absent that, the run's owner must have stopped reporting for thirty minutes, tracked in `RunState.owner_seen_at` and stamped on every event arriving from the owning session. A session that meets neither test is told the run belongs elsewhere and how to release it.

## Consequences

Recovery after a genuine crash is slower when the host does not report a resume, which today means Codex, where no captured payload exists to say whether it sends `source` at all. A user who restarts within the window is asked to release the run explicitly rather than having it taken silently. That is the deliberate trade: the cost of waiting is a prompt, and the cost of guessing wrong was killing work in progress.

The thirty-minute window is a heuristic and is marked as one in the code. A parent process is quiet for as long as its lead runs, so a long delegation can look idle; the window is sized to outlast that rather than to detect death quickly. Neither host exposes session liveness, and the day one does, this becomes an exact check.

This also settles where consent lives. An accepted route clamp is scoped to a session by design, but it was stored in a single slot per provider, so a heartbeat from any other session erased it and `/symphony:proceed` quietly stopped holding. Acceptances are now keyed by session.
