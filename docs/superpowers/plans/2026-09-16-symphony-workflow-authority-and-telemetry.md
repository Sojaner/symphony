# Symphony Workflow Authority and Telemetry Implementation Plan

> **For agentic workers:** Symphony owns execution of this plan. Supporting skills are bounded techniques and must not offer a second execution-method choice. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Symphony authoritative over supporting workflow skills and omit unavailable token and duration telemetry.

**Architecture:** Strengthen the existing lifecycle-injected contract and capability-routing reference instead of adding a new orchestration layer. Reuse the existing locked agent ledger and render only present measurements.

**Tech Stack:** Python standard library, Codex/Claude plugin hooks, Markdown skills and commands, `unittest`.

**Spec:** `docs/superpowers/specs/2026-09-16-symphony-workflow-authority-and-telemetry-design.md`

## Global Constraints

- Explicit user and repository instructions remain above Symphony.
- An active Symphony run owns orchestration and completion.
- Supporting skills remain usable as bounded techniques.
- No global telemetry file, transcript parser, daemon, dependency, or inferred usage.
- Missing token and duration measurements are omitted.

---

### Task 1: Lock workflow authority

**Files:**
- Modify: `plugins/symphony/tests/test_symphony_hook.py`
- Modify: `plugins/symphony/scripts/symphony_hook.py`
- Modify: `plugins/symphony/skills/symphony/SKILL.md`
- Modify: `plugins/symphony/skills/symphony/references/capability-routing.md`

- [ ] Add failing tests for compact root/child authority context and the Superpowers planning boundary.
- [ ] Run the focused tests and confirm the expected failure.
- [ ] Add the minimum shared authority contract to lifecycle context and skill routing.
- [ ] Run the focused tests and confirm they pass.

### Task 2: Omit unavailable measurements

**Files:**
- Modify: `plugins/symphony/tests/test_symphony_hook.py`
- Modify: `plugins/symphony/scripts/symphony_hook.py`
- Modify: `plugins/symphony/commands/agents.md`
- Modify: `plugins/symphony/commands/help.md`
- Modify: `README.md`

- [ ] Add failing tests proving unavailable token and duration fields are omitted.
- [ ] Run the focused tests and confirm the expected failure.
- [ ] Render optional measurement columns only when at least one included row exposes them.
- [ ] Update user-facing documentation and rerun focused tests.

### Task 3: Validate and release

**Files:**
- Modify: `plugins/symphony/evals/*` only if the existing matrix lacks the collision scenario.
- Modify: plugin manifests and version assertions.

- [ ] Add or update the smallest hosted/live regression scenario.
- [ ] Run focused, full, validation, and live weak-root checks.
- [ ] Review the final diff with a strongest/high reviewer and resolve findings.
- [ ] Bump the release version, commit, push, observe CI and release publication, verify a fresh install, and notify the user.
