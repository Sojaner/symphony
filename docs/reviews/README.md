# Reviews

Review artifacts for Symphony releases. Each review is report-only; nothing in this directory changes runtime behavior.

## 2026-09-17: Symphony 1.0.0 (v0.21.2 -> v1.0.0, commit eba877b)

Verdict: **Not ready.** 107 unit tests, 8 package smokes, and both plugin manifests pass, but tests and smokes send payload shapes the hosts never send, so they mask validated defects.

- `2026-09-17-symphony-1-0-code-review.md` -- compound-engineering multi-agent code review. 23 findings, 12 validated by reproduction. P0: the Stop hook livelocks on an enabled project whenever a run has no lead, and nothing reads `stop_hook_active`.
- `2026-09-17-symphony-1-0-actionable-findings.json` -- the 20 findings routed to the downstream resolver, with suggested fixes.
- `2026-09-17-symphony-1-0-ponytail-review.md` -- over-engineering review. About 420 removable lines, mostly dead schema (capability snapshots, memory helpers, token/duration plumbing).
- `2026-09-17-symphony-1-0-spec-review.md` -- compound-engineering document review of the 1.0 design spec. 15 proposed fixes and 4 decisions; nothing applied.

Suggested fix order: stop policy (`runtime.py` `_transition`, `reducer.py` `_stop_requested`) -> reducer/runtime one-liners (first lead after interrupt, control-event dedup, invalid-consultant marker) -> session-based reconciliation -> Codex context deferral -> faulted marker -> store fixes -> guard fidelity -> decide capability/memory schema -> structure.
