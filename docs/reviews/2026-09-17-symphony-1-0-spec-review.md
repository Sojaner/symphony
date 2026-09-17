Document review complete (non-interactive mode).

Document: docs/superpowers/specs/2026-09-16-symphony-1-0-clean-room-rewrite-design.md (classified requirements; origin none)
Reviewers: coherence, feasibility, product-lens, security-lens, scope-guardian, adversarial (all completed); cross-model judgment pass via Codex for adversarial, product-lens, security-lens and a whole-document sweep (all done, independence verified, served model unverified).

Applied 0 fixes:
- None. Every retained correction changes design meaning or requires a product choice; non-interactive mode grants no edit authority for those.

Proposed fixes (nothing here has landed; awaiting the same grouped confirmation):

[P1] Section: Canonical lifecycle - Ownership and recovery — Recovery depends on a host liveness list and an interrupt event that Claude Code never sends (feasibility, adversarial; confidence 100)
  Recommendation: Apply
  Consequence if unchanged: after a restart or an aborted turn, dead delegations stay active forever and normal completion is blocked until the user discovers the force-stop control.
  Change: replace "Resume first reconciles host-observed agents" and the Interrupt sentence with a recovery boundary defined on facts the hosts emit: a Codex Interrupt, a SessionStart while a run is active, or a UserPromptSubmit arriving while delegations are active; at that boundary delegations without a host-observed stop become interrupted, the run enters recovering, and the next registered lead takes the next owner generation. Note in Provider parity that Claude Code has no interrupt hook, and rename acceptance item 7's "interruption/resume" to "restart/resume recovery".
  Basis: neither host's SessionStart payload lists live agents, Claude Code has no Interrupt hook, and the shipped runtime only reconciles when a payload carries an agent-id list nobody sends.

[P1] Section: Architecture - Agent instructions and Retained failure lessons — Routing depends on the final-answer markers the spec forbids, and no sanctioned result channel is named (feasibility, product-lens, adversarial; confidence 100)
  Recommendation: Apply
  Consequence if unchanged: implementers read the shipped marker parsing as a violation of the approved design, and the completion criterion "no longer relies on receipt parsing" cannot be tested.
  Change: state that role results travel as exactly one machine-validated marker line per result in host-observable channels (the spawn packet and the child's final message), that a missing or malformed line makes the result non-actionable, and that completion, ownership, and child state come only from host lifecycle events. Narrow the failure lesson to "structured role results are validated markers, never free-form receipts".
  Basis: the hosts offer no structured return channel other than the final message and the spawn input, so the ban as written cannot hold.

[P1] Section: Product contract - Honest guarantees — Managed runs can enter an unbounded Stop loop (adversarial; confidence 75)
  Recommendation: Apply
  Consequence if unchanged: a run whose children died without a host event, or a root that ignores injected guidance, is blocked on every Stop with no exit the agent can reach.
  Change: add "A Stop is blocked at most once per turn. When the host reports the Stop hook is already active, the reducer permits completion, marks the run degraded with the unreconciled identities, and reports this in status."
  Basis: the spec bounds Stop loops only for controls; Claude Code re-fires Stop with a flag the design never consults.

[P1] Section: Architecture - State store and Failure handling — Persisted content, retention, and permissions are undefined, so raw prompts and agent messages get stored (security-lens, security-lens via Codex (+1 anchor); confidence 100)
  Recommendation: Apply
  Consequence if unchanged: the "no transcript mirroring" and "no secret persistence" commitments are unenforceable; the shipped store already writes full prompts, final messages, and tool inputs under the home directory behind best-effort regex redaction.
  Change: add a "Persisted data" subsection with a field allowlist (event kind and id, identities, role, tier and effort, classification, status, timestamps, provider and plugin identity, a bounded objective label), forbid raw prompts, final messages, packet bodies, transcript paths and tool output in the store, summaries, or archives, bound event history, require 0600 files in a 0700 directory, and have disable report where state lives.
  Basis: the state store section names "bounded canonical event history" without saying what an event may contain.

[P2] Section: Hook packaging - Activation states — Six of the seven listed states are not observable by a hook, and "needs_review or pending_reload" is ambiguous (feasibility, coherence; confidence 100)
  Recommendation: Apply
  Consequence if unchanged: readers expect status to distinguish undiscovered, untrusted, unloaded, policy-blocked and faulted hooks, but no Symphony code runs in any of those states.
  Change: state that the store records two facts, guarded and pending verification; describe the other labels as diagnostics the user resolves through provider views; attribute the faulted classification to release smoke verification rather than runtime; and split the "or" into the Codex and Claude Code variants.
  Basis: the reducer only ever writes guarded on heartbeat; the other names appear only in the smoke harness.

[P2] Section: Acceptance evidence — Fixtures are invented rather than captured, and no real-host oracle exists (adversarial, adversarial via Codex (+1 anchor); confidence 100)
  Recommendation: Apply
  Consequence if unchanged: acceptance passes against payload shapes the hosts never send, so a real-host regression ships green.
  Change: require provider hook fixtures captured from real installed sessions of the pinned provider versions, require every payload field the reducer depends on to appear in a captured fixture, and run the current stable release as a black-box oracle for each retained failure lesson before deleting active artifacts.
  Basis: the smoke harness already relies on fields such as an active-agent list and a subagent status that neither host emits.

[P2] Section: Product contract - Provider parity — Codex cannot prevent a mis-routed spawn, only detect it afterwards (adversarial; confidence 75)
  Recommendation: Apply
  Consequence if unchanged: users and acceptance tests credit Codex with protection it lacks; the expensive route is already paid for by the time the mismatch is noticed.
  Change: add "Pre-launch route enforcement is available only where the host emits a pre-spawn event (Claude Code PreToolUse). On Codex, route violations are detected at SubagentStart or SubagentStop and reported, not prevented."
  Basis: Codex collaboration spawns emit no pre-spawn hook, which the shipped role contract already concedes.

[P2] Section: Commands and visibility — The Codex bare-tail grammar swallows tasks that start with a control word (product-lens; confidence 75)
  Recommendation: Apply
  Consequence if unchanged: "help me fix the login bug" prints help and "stop the server from crashing" stops the active run.
  Change: make the Codex managed-task form "start <task>" and rule that a leading control word is honored only when the remainder is empty or a documented flag; otherwise the whole tail is a task.
  Basis: the command table defines task and control on the same bare tail with nothing separating them.

[P2] Section: Objective and Enablement — The cheap-root precondition is never stated to users (product-lens; confidence 75)
  Recommendation: Apply
  Consequence if unchanged: a user on a strongest-tier root pays a strongest assessor plus a lead on top, inverting the product rule into pure overhead without any notice.
  Change: add to Enablement that Symphony assumes an economy-tier root, that enablement guidance and the README state the provider-native way to set it, and that status reports the root model where the host exposes it and flags when it exceeds the economy tier.
  Basis: the objective assumes "a weak or inexpensive root agent" but no requirement makes that visible.

[P2] Section: Ownership and recovery — "Safe boundary" is used twice and never defined (coherence; confidence 100)
  Recommendation: Apply
  Consequence if unchanged: implementers diverge on when a lead may be replaced.
  Change: after the reassessment boundary list, define safe boundaries as those lifecycle points (worker wave completion, explicit request, new task, approved plan, resume or compaction, interruption, reported drift) and reference that definition from the replacement rule.
  Basis: the reassessment list already enumerates the candidate points without naming them.

[P2] Section: Honest guarantees and Thin-root boundary — Structured results are not bound to the registered role, and untrusted content has no stated boundary (security-lens, security-lens via Codex; confidence 75)
  Recommendation: Apply
  Consequence if unchanged: a marker line planted in a repository file or echoed by a worker could re-route the run or satisfy a consultant gate.
  Change: state that a result marker is accepted only from the host-observed terminal event of the agent registered for that role in the current run, that markers anywhere else are inert, and that repository content, tool output, and delegated results are untrusted data that never change lifecycle policy or scope; add a reducer acceptance case for a valid marker from the wrong identity.
  Basis: the spec says "the assessor returns the assessment contract" without saying who may return it.

[P2] Section: Objective and Acceptance evidence — The cost and outcome goals have no acceptance evidence (product-lens, product-lens via Codex, adversarial via Codex (+1 anchor); confidence 75)
  Recommendation: Apply
  Consequence if unchanged: nobody can tell whether an enabled project finishes work more cheaply than running the task directly, so the matrix can never be tuned from evidence.
  Change: add acceptance item 9 requiring the end-to-end run on each provider to record host-observed token totals per role next to a direct-root baseline for the same task, published in the release notes.
  Basis: every acceptance item is functional while the objective commits to cost.

[P2] Section: Migration and cutover — The migration contract is undefined while the clean-room rule forbids reading the old code (whole-document sweep via Codex; confidence 100)
  Recommendation: Apply
  Consequence if unchanged: implementers guess the legacy schema; the shipped importer already keys legacy files by the current directory where the old hook keyed them by the git toplevel, so enablement is lost from subdirectories.
  Change: add a migration contract naming the legacy state location (provider data directory, projects folder, file named by the first 24 hex characters of the sha256 of the git toplevel path), the preserved fields (enabled, configuration), explicit secret exclusion, and a compatibility fixture.
  Basis: the section promises preservation of enablement and configuration without saying where they live.

[P2] Section: Assessment and routing — Risk vocabulary and its escalation effect are undefined (whole-document sweep via Codex; confidence 100)
  Recommendation: Apply
  Consequence if unchanged: identical assessments can produce different execution strategies and the nine-cell test never covers risk.
  Change: define risk as normal or high; high raises a low lead effort to medium and requires an independent check; add routing acceptance cases for each mapping.
  Basis: "Risk may elevate effort, require an independent check, or reserve consultation" names three possible effects with no rule.

[P2] Section: Assessment and routing — Topology authority between the assessor and the matrix is ambiguous (whole-document sweep via Codex; confidence 75)
  Recommendation: Apply
  Consequence if unchanged: the same task is delegated differently depending on which source an implementer treats as authoritative.
  Change: state that the fixed matrix and risk rules are authoritative and that the assessor's recommended topology is advisory input whose rejection is recorded.
  Basis: the assessment contract returns "recommended topology" while the matrix also fixes an execution pattern.

Decisions (requires user judgment):

[P1] Section: Enablement and Canonical lifecycle step 3 — "Deterministically trivial" is undefined, so every prompt in an enabled project pays a strongest assessment and opens a run (product-lens, adversarial (+1 anchor); confidence 100)
  Recommendation: Defer
  Consequence if unchanged: "thanks" or "what does this function do" each open a run and demand an assessor, inverting the cost rule and, with the shipped Stop gating, locking the session.
  Change: either define an adapter-owned deterministic pattern set (how broad it is decides governance versus cost) or drop the promise and state that only controls, empty prompts, and prompts during an active run skip assessment.
  Basis: a hook can only decide deterministically whether a prompt is a control; anything else is a heuristic or a model call.

[P2] Section: Assessment and routing - Capability resolution — No actor can perform the capability refresh the resolution order promises (feasibility, adversarial, adversarial via Codex; confidence 100)
  Recommendation: Defer
  Consequence if unchanged: "live provider capabilities" and the 24-hour refresh describe a cache nothing can fill; every route resolves through the shipped fallback while status implies freshness.
  Change: either replace the resolution order with a release-time shipped map revised from official documentation, or assign refresh to the assessor packet and add invalidation when provider or plugin identity changes.
  Basis: hooks have no model inventory or MCP access, and the thin-root rule forbids the root from doing capability research.

[P2] Section: Workflow capability routing — The phase-to-plugin table serves no stated objective and binds Symphony's identity to six third-party plugins (scope-guardian, product-lens; confidence 75)
  Recommendation: Defer
  Consequence if unchanged: a recommendation subsystem is maintained for an SDLC-process concern outside reliability, cost, and observability, and users on other stacks read it as required.
  Change: either remove the table from the contract, or keep it prefixed as optional defaults with one consolidated notice per project per version.
  Basis: the objective names three goals and the table connects to none of them.

[P2] Section: Capability resolution and Lead behavior — The conservative fallback when no assessor or required consultant is available is undefined (adversarial via Codex, whole-document sweep via Codex; confidence 100)
  Recommendation: Defer
  Consequence if unchanged: implementers cannot produce the same safe route twice, and the shipped runtime has no fallback at all.
  Change: name the deterministic fallback cell, effort, disclosure text, and completion behavior for both cases.
  Basis: the spec says Symphony "selects and discloses a conservative provider route" without naming one.

FYI observations (2):
- Assessor and consultant roles carry no tool-permission boundary, so the first agent to read untrusted task content at the strongest tier can edit and execute (security-lens, confidence 50).
- The project-level context file is read back as settled decisions with no stated trust level or commit/ignore guidance (security-lens, confidence 50).

Residual concerns (2):
- Nonzero hook exits have no runtime path: the hook swallows every exception and exits 0, so its own faults never surface as the packaging or execution faults the spec describes (feasibility).
- Large or complex work is owned by an economy-tier lead that also integrates and verifies; nothing detects a lead silently absorbing decisions it should escalate (product-lens, adversarial).

Deferred questions (6):
- Does Codex emit an Interrupt hook at the pinned baseline, and does Claude Code emit SubagentStop for children killed by an interrupted turn? (adversarial)
- Should the project context file be committed or ignored by default? (security-lens)
- What makes a live lead "materially incapable", and what timeout or progress evidence authorizes replacement? (adversarial via Codex)
- Which Codex and Claude Code versions does 1.0.0 support, and which provider change invalidates installed-hook evidence? (adversarial via Codex)
- Can a project have more than one active managed run across sessions, and what happens to a second substantive task? (whole-document sweep via Codex)
- What activation burden and per-task assessment overhead will users accept before bypass or disable becomes the default? (product-lens via Codex)

Restated: 6 (residual/deferred items suppressed as duplicates of actionable findings)

Review complete
