# Symphony Workflow Authority and Telemetry Design

## Goal

Keep Symphony in control of routing while an active run uses supporting workflow skills, and report only telemetry the host actually exposes without repeating unavailable token or duration values.

## Authority

Explicit user instructions and repository instructions remain authoritative. During an active Symphony run, Symphony exclusively owns assessment, execution mode, delegation, waiting, reassessment, and completion. Other workflow skills are bounded techniques selected by the assessor or execution lead; they do not start a second orchestration lifecycle or ask the user to choose an execution method already resolved by the accepted Symphony mode.

After a supporting planning skill finishes, the Symphony lead continues automatically:

- small: direct lead execution;
- medium: direct integration work plus bounded independent workers;
- large: dependency-aware worker waves.

Only unresolved product requirements, irreversible actions, security-sensitive decisions, or genuine blockers return to the user.

The hook reinjects a compact authority reminder at active root and child boundaries. It does not repeat the complete skill body.

## Telemetry

The existing project-scoped, locked agent ledger remains the only persisted telemetry store. Symphony does not install global collectors, parse private user transcripts, infer costs, or depend on undocumented local transcript schemas.

`/symphony:agents [--all]` reports lifecycle status, role, model, and effort when available. It reports observed wall duration and authoritative host usage only when those values exist. Missing token and duration fields are omitted instead of rendered as `not exposed by host`. Unknown model, effort, or role may retain the explicit unknown label because those fields identify the assignment rather than repeat absent measurements.

Terminal lifecycle state is not relabeled completed or failed unless the host exposes that outcome. Every usage value retains its source and scope.

The root's terminal response must remain useful even when earlier lifecycle messages collapse from view. It repeats the integrated deliverable, assessor and lead completion records, authoritative verification, selected mode, and completion receipt in one self-contained report.

## Verification

- A regression test proves an active Symphony lead treats Superpowers planning as a bounded technique and automatically follows the accepted mode.
- Agent-ledger tests prove missing token and duration fields are absent while available observations retain their labels and source.
- A live Codex scenario exercises the Superpowers-planning collision with a weak root and verifies that no execution-method question terminates the run.
- Existing lifecycle, command, memory, and release validation remains green.
