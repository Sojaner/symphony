# Capability routing

## Fixed matrix

| Size / complexity | Lead | Execution | Consultation |
|---|---|---|---|
| small / simple | capable/medium | direct | none |
| small / mixed | capable/high | direct | optional narrow |
| small / complex | strongest/high | direct | optional independent check |
| medium / simple | balanced/medium | direct plus mechanical delegation | none |
| medium / mixed | balanced/high | selective delegation | optional narrow |
| medium / complex | capable/high | selective delegation | reserve one slot |
| large / simple | economy/low | administrative delegation | none |
| large / mixed | economy/medium | administrative delegation | reserve one slot |
| large / complex | economy/medium | administrative delegation | strongest/high bounded decisions |

Risk is `normal` or `high`. `high` raises a lead effort of `low` to `medium` and requires an independent check. Risk never changes the reported size or complexity. Reserved consultation means an available concurrency slot, not an idle agent.

The fixed matrix and these risk rules are authoritative for topology; an assessor's recommended topology is advisory input.

## Capability resolution

Resolve abstract tiers `economy`, `balanced`, `capable`, and `strongest` against the capability map shipped with the installed version. Neither host gives a hook a model inventory and neither CLI lists available models, so there is no runtime discovery to attempt; the map is maintained at release time by a scheduled workflow that verifies every model against the provider before shipping it.

If no suitable assessor is available, disclose and use the conservative shipped route—the root does not improvise an assessment.

The shipped map is:

| Provider | economy | balanced | capable | strongest |
|---|---|---|---|---|
| Codex | `gpt-5.6-luna` | `gpt-5.6-terra` | `gpt-5.6-sol` | `gpt-6-astra` |
| Claude Code | `haiku` | `sonnet` | `opus` | `opus` |

Recommend a useful missing capability at most once per project per Symphony version. Never install it automatically.

## Workflow authority

Symphony retains topology and lifecycle ownership. Supporting workflows operate inside the selected route and never ask the user to choose a second topology.

| Phase | Primary capability | Constraint |
|---|---|---|
| Requirements and architecture | Superpowers brainstorming | Use grilling only for ambiguous or contested decisions |
| Current external facts | Context7 | Prefer official sources and dated conclusions |
| Implementation planning | Compound Engineering `ce-plan` | Ponytail prunes speculative work |
| Code execution | Compound Engineering `ce-work` | Superpowers TDD covers behavioral changes |
| Review | Compound Engineering `ce-code-review` | Superpowers verification gates completion |
| Commit, release, monitoring | Relevant Compound Engineering shipping workflow | Symphony retains lifecycle ownership |
| Every design and code phase | Ponytail | Choose the smallest correct native solution |

When a supporting capability is unavailable, use the closest native process without delaying the run.

## Optional indexed memory

Enable `.symphony/context.md` only after Codebase Memory reports healthy indexing and usable coverage. Store curated goals, decisions and sources, constraints, stable architecture facts, verified outcomes, unresolved risks, and concise continuation state. Exclude transcripts, secrets, raw tool output, inferred telemetry, and routine progress. If indexing is absent or unhealthy, continue with compact lifecycle state only.
