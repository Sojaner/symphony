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

Risk may elevate effort, require an independent check, or reserve consultation. It never changes the reported size or complexity. Reserved consultation means an available concurrency slot, not an idle agent.

## Capability resolution

Resolve abstract tiers `economy`, `balanced`, `capable`, and `strongest` against, in order:

1. live provider capabilities;
2. cached provider snapshot;
3. conservative shipped fallback.

A snapshot records models, supported efforts, relative tiers, source, provider version, and refresh time. At session start and reassessment, refresh snapshots older than 24 hours without blocking use of a valid cache. Use Context7 for current official model, effort, API, and library facts when available; record dated conclusions instead of repeating research. If no suitable assessor is available, disclose and use the conservative provider route—the root does not improvise an assessment.

Until a refresh succeeds, the shipped fallback is:

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
