# Inverted Lead Routing Design

## Goal

Reduce orchestration cost without weakening decisions. Symphony will spend its strongest reasoning on bounded assessment and narrow decisions, while the most talkative role uses the cheapest route suitable for its execution mode.

## Routing model

The assessor remains a read-only strongest/high agent. It classifies the run and selects an explicit lead route from the live provider catalog.

| Run | Lead responsibility | Lead route | Delegation |
|---|---|---|---|
| Small/simple | Execute the straightforward task directly | Capable executor at medium or high | Only long-running mechanical work |
| Medium/mixed | Execute quick and integration-sensitive work; coordinate bounded units | Balanced agent at medium | Workers and narrow consultants when useful |
| Large/complex | Administer, schedule, communicate, integrate, and verify | Cheapest reliable coordinator at low or medium | Workers implement; consultants decide narrow hard questions |

This inversion is deliberate: the lead produces the most conversation and therefore becomes cheaper as orchestration volume rises. A large-run lead does not inherit or reuse the assessor route.

## Enforced assessment handoff

The assessor returns its existing assessment receipt plus a machine-readable lead route containing the exact provider model and effort selected from the live catalog. The hook persists that route and accepts the owner lead spawn only when its explicit model and effort match. Missing, malformed, unavailable, or mismatched routes fail closed and request a corrected assessment or spawn.

The route contract makes the assessor's choice deterministic for a weak root. Provider labels continue to expose the actual role, model, and effort.

## Narrow consultants

A consultant receives one decision question with only the evidence needed to decide it. It does not implement, orchestrate, broadly explore, or inherit the parent conversation. The lead supplies the applicable constraints and the worker-facing choices.

An actionable consultant response ends with a machine-readable decision packet:

```text
SYMPHONY_CONSULTATION:<run-id>:<size>:<complexity>
SYMPHONY_DECISION:<single precise decision>
SYMPHONY_ACTION:<single mechanical action or mapping>
```

`size` and `complexity` describe the decided unit, not the whole project. The lead applies `SYMPHONY_ACTION` mechanically. If the response exposes a broader issue or would change the run mode, the lead requests reassessment instead of expanding the consultation.

Consultants use the cheapest route suitable for the decision. Strongest/high is reserved for genuinely hard, risky, or irreversible decisions and final review; it is not the default consultant route.

## Consultant capacity reservation

The assessor marks large runs as consultant-heavy when repeated hard decisions are foreseeable. In that strategy, the lead reserves one available child concurrency slot for consultants: worker waves may use only the remaining child capacity. The reservation is capacity, not an idle long-lived agent, so it consumes no agent context while unused.

The lead dispatches consultations before dependent worker work and keeps unrelated workers moving. If no consultant is needed, the slot remains unused rather than being filled speculatively. If the host exposes no spare child capacity, the lead runs the consultant before the dependent worker wave.

## Hook behavior

- Codex `Interrupt` declares the host maximum timeout of three seconds.
- Assessment parsing persists the exact lead model, effort, and execution strategy.
- Owner lead spawns must match the accepted route.
- Large administrative leads cannot use the assessor's exact model/effort pair.
- Consultant labels expose role/model/effort on both providers.
- Consultant-heavy worker waves preserve one child slot, or serialize consultation before dependent work when no spare slot exists.
- Completion rejects actionable consultant results missing size, complexity, decision, or action fields.

## Failure and recovery

An invalid assessor route leaves the strong-assessment requirement active. An invalid consultant packet leaves its dependent decision unresolved but does not invalidate unrelated completed work. Recovery reconstructs the accepted route, reserved capacity strategy, terminal agents, and unresolved decisions from the lifecycle record; it does not repeat repository-wide assessment without evidence of drift.

## Verification

Regression coverage will prove:

- the Interrupt declaration no longer triggers timeout clamping;
- assessor and lead routes cannot be accidentally conflated;
- each mode accepts its intended lead class and effort range;
- both Codex and Claude reject a lead route that differs from the accepted assessor selection;
- large consultant-heavy schedules preserve decision capacity;
- actionable consultant packets include unit size, complexity, decision, and action;
- small direct execution and medium mixed execution retain their existing behavior;
- existing lifecycle, visibility, recovery, and completion suites remain green.
