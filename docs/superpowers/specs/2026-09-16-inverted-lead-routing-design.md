# Inverted Lead Routing Design

## Goal

Reduce orchestration cost without weakening decisions. Symphony will spend its strongest reasoning on bounded assessment and narrow decisions, while the most talkative role uses the cheapest route suitable for its execution mode.

## Routing model

The assessor remains a read-only strongest/high agent. It classifies the run and selects an explicit lead route from the live provider catalog.

| Run | Consulting strategy | Lead responsibility | Lead route | Delegation |
|---|---|---|---|---|
| Small/simple | None by default | Execute the straightforward task directly | Capable executor at medium or high | Only long-running mechanical work |
| Medium/mixed | Occasional | Execute quick and integration-sensitive work, make decisions when consultation is unavailable, and coordinate bounded units | Capable balanced agent at medium | Workers and narrow consultants when useful |
| Large/complex | Consultant-heavy | Administer, schedule, communicate, integrate, and verify | Cheapest reliable coordinator at low or medium | Workers implement; consultants decide bounded hard questions |

This inversion is deliberate: the lead produces the most conversation and therefore becomes cheaper as orchestration volume rises. A large-run lead does not inherit or reuse the assessor route.

## Enforced assessment handoff

The assessor returns its existing assessment receipt plus a machine-readable lead route containing the exact provider model, effort, and consulting strategy selected from the live catalog. The hook persists that route and accepts the owner lead spawn only when its explicit model and effort match. Missing, malformed, unavailable, or mismatched routes fail closed and request a corrected assessment or spawn.

The route contract makes the assessor's choice deterministic for a weak root. Provider labels continue to expose the actual role, model, and effort.

## Narrow consultants

A consultant receives one bounded decision area with only the evidence needed to resolve it. It does not implement, orchestrate, broadly explore, or inherit the parent conversation. The lead supplies the applicable constraints and the worker-facing choices.

An actionable consultant response may contain one decision or split the area into a small ordered set of narrower decisions. It ends with a machine-readable decision packet:

```text
SYMPHONY_CONSULTATION:<run-id>
SYMPHONY_DECISION_COUNT:<count>
SYMPHONY_DECISION:<index>:<small|medium|large>:<low|medium|high>:<precise decision>
SYMPHONY_ACTION:<index>:<mechanical action or mapping>
```

Every actionable decision carries its own size and complexity. These values describe that decision, not the whole project, and give the lead the routing input for its implementation or delegation. Every index from one through `count` appears exactly once in both fields, and index order is execution order. The lead applies the actions mechanically in that order. If the response exposes a broader issue or would change the run mode, the lead requests reassessment instead of expanding the consultation.

Consultants use the cheapest route suitable for the decision. Strongest/high is reserved for genuinely hard, risky, or irreversible decisions and final review; it is not the default consultant route.

## Consultant capacity reservation

The assessor marks runs as consultant-heavy when repeated hard decisions are foreseeable. In that strategy, the lead reserves one available child concurrency slot for consultants: worker waves may use only the remaining child capacity. The reservation is capacity, not an idle long-lived agent, so it consumes no agent context while unused.

The lead dispatches consultations before dependent worker work and keeps unrelated workers moving. If no consultant is needed, the slot remains unused rather than being filled speculatively. If the host exposes no spare child capacity, the lead runs the consultant before the dependent worker wave.

An occasional-consulting strategy does not reserve a slot. Its lead must be capable enough to decide the bounded question itself when consultation capacity is unavailable. A consultant-heavy lead does not improvise a hard decision after consultant failure; it serializes the consultation, replaces a terminal failed consultant, or triggers reassessment.

## Adaptive reassessment

The existing reassessment hooks, events, recovery records, and mode-revision history remain the control loop for this routing model. Owner prompts, planning boundaries, completed worker waves, interrupts, resumes, and material evidence that changes scope or risk make reassessment due. These recurring boundaries provide periodic reevaluation during long runs without adding a separate timer or daemon.

Reassessment evaluates current size, complexity, consulting frequency, lead route, worker plan, and verification surface from accumulated evidence. It may move in either direction among direct, mixed, and administrative execution; change the lead model or effort; or switch between none, occasional, and consultant-heavy strategies. A route or strategy change increments the existing mode revision and starts a fresh matching lead after the current lead and its children are terminal. Unchanged results reuse current evidence and avoid a broad project rescan.

A proposed mode, route, or consulting-strategy change that exceeds the current lead's authority returns to a strongest/high assessor. A cheap periodic check that finds no material change remains with the current capable lead.

## Hook behavior

- Codex `Interrupt` declares the host maximum timeout of three seconds.
- Assessment parsing persists the exact lead model, effort, execution strategy, and consulting strategy.
- Owner lead spawns must match the accepted route.
- Large administrative leads cannot use the assessor's exact model/effort pair.
- Consultant labels expose role/model/effort on both providers.
- Consultant-heavy worker waves preserve one child slot, or serialize consultation before dependent work when no spare slot exists.
- Occasional-consulting leads remain capable of resolving their own bounded decisions when no consultant slot is available.
- Completion rejects actionable consultant results when any indexed decision lacks size or complexity, or when decision count, indexed decisions, and indexed actions do not form a complete matching set.

## Failure and recovery

An invalid assessor route leaves the strong-assessment requirement active. An invalid consultant packet leaves its dependent decision unresolved but does not invalidate unrelated completed work. Recovery reconstructs the accepted route, consulting strategy, reserved capacity, mode revision, terminal agents, and unresolved decisions from the lifecycle record; it does not repeat repository-wide assessment without evidence of drift.

## Verification

Regression coverage will prove:

- the Interrupt declaration no longer triggers timeout clamping;
- assessor and lead routes cannot be accidentally conflated;
- each mode accepts its intended lead class and effort range;
- both Codex and Claude reject a lead route that differs from the accepted assessor selection;
- large consultant-heavy schedules preserve decision capacity;
- occasional-consulting leads can proceed when no consultant slot is available;
- every actionable consultant decision includes its own size and complexity plus a matching indexed action;
- reassessment can change mode, lead route, and consulting strategy without discarding accumulated evidence;
- small direct execution and medium mixed execution retain their existing behavior;
- existing lifecycle, visibility, recovery, and completion suites remain green.
