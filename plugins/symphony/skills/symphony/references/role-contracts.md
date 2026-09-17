# Role contracts

Roles exchange explicit packets. Lifecycle changes come from reducer state and host events, never magic prose or completion receipts.

Every managed spawn must request an explicit model and effort; never inherit the assessor's settings. Put exactly one of these first-class lines in the task packet so the lifecycle hook can classify the host event even when the provider reports a generic agent type:

```text
SYMPHONY_ROLE: assessor
SYMPHONY_ROLE: lead
SYMPHONY_ROLE: worker
SYMPHONY_ROLE: consultant
```

Claude blocks unmarked spawns before launch. Codex validates the observed task name, model, effort, and final lifecycle result, then withholds managed completion when required result markers are missing. An assessor must use high effort or above. A lead must use the matrix-selected effort, and workers and consultants cannot start until a lead is registered.

Provider binding is mechanical:

- Claude Code: select a packaged `symphony-<role>-<model>-<effort>` agent type. The agent definition pins both settings because Claude's Agent call does not expose per-call effort.
- Codex: pass `model` and `reasoning_effort`, use `fork_turns="none"`, and use `symphony_<role>_<model>_<effort>` as the task name. Relay a bounded packet explicitly; never fork the root history into an assessor or lead.

Compact status shows at most five latest delegation records, ordered failed, active/waiting, then recently completed. `agents --all` shows every retained latest record, not every transition.

Use the host role name `symphony_<role>_<model>_<effort>` when custom names are supported. Every visible delegation line includes `role [model/effort]`, the host-observed identity, and its bounded objective. Omit model, effort, tokens, or duration when the host does not expose them; never infer them.

## Assessment

Run substantive or uncertain work as a bounded `strongest/high` assessment. The assessor chooses needs, not provider model names, and does not become the lead implicitly.

Required fields:

```yaml
size: small | medium | large
complexity: simple | mixed | complex
risk: <classification and material concerns>
rationale: <concise evidence-based reason>
topology: <direct | selective delegation | administrative delegation>
abstract_role_routes:
  lead: <tier/effort>
  workers: <tier/effort requirements or none>
  consultants: <tier/effort requirements, capacity, or none>
```

The assessor's final response must include the same fields on one exact machine-readable line so Codex can accept the route from its native `SubagentStop` event:

```text
SYMPHONY_ASSESSMENT: {"size":"medium","complexity":"mixed","risk":"normal","rationale":"...","topology":"selective delegation"}
```

Before spawning the selected lead, put its role and the accepted fields on exact first-class lines in the lead task so the lifecycle hook can register the route before the host starts it:

```text
SYMPHONY_ROLE: lead
SYMPHONY_ROUTE: {"size":"medium","complexity":"mixed","risk":"normal","rationale":"...","topology":"mixed"}
```

The JSON values must use the matrix vocabulary above. Claude can reject malformed spawn markers before launch. Codex collaboration spawns do not emit `PreToolUse`, so Symphony correlates their task name and settings from the native child transcript and accepts the route from the assessor's final lifecycle message.

## Actionable work packet

Every lead, worker, and consultant receives only the bounded context needed for its assignment. Every packet has these exact fields:

```yaml
objective: <one bounded outcome or decision>
ownership: <files, subsystem, or decision authority>
evidence: <relevant facts and sources>
constraints: <safety, compatibility, and process limits>
acceptance_check: <observable done condition>
return_contract: <result and evidence to return>
size: small | medium | large
complexity: simple | mixed | complex
```

`size` and `complexity` are local to the packet. Each consultant decision is classified separately; a consultant may split one question into multiple packets.

## Lead

- Own execution, integration, verification, and user communication for the task.
- A small lead works directly except for long-running mechanical work.
- A medium lead integrates and handles quick work while delegating bounded units selectively.
- A large lead is an inexpensive administrator: delegate project work and reserve consultant capacity for narrow decisions.
- Preserve active ownership across reassessment. Replace the lead only at a safe boundary or when unavailable or materially incapable.
- When required consultation is unavailable, a large lead uses the disclosed conservative fallback instead of absorbing specialist reasoning silently.

## Worker

- Own only the packet objective and acceptance check.
- Work directly; agent depth ends at worker/consultant.
- Return the requested result and evidence to the lead. Do not claim lifecycle completion.

## Consultant

- Own a bounded decision, not implementation or orchestration.
- Return the recommendation, rationale, evidence, uncertainty, and consequences requested by the packet.
- Classify each decision with decision-local size and complexity.
- Put one `SYMPHONY_DECISION` line per actionable decision in the final result; also include it in the Claude spawn packet so that provider can reject a missing classification before launch:

```text
SYMPHONY_ROLE: consultant
SYMPHONY_DECISION: {"size":"small","complexity":"mixed"}
```

## Reassessment boundaries

Reassess on an explicit request, new task, approved plan, completed worker wave, resume or compaction, interruption, or reported scope/risk drift. Skip an unchanged evidence fingerprint. Route changes apply to subsequent work and never duplicate active work.
