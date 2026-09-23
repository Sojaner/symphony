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

Resolve each cell through the selected shipped profile's model and effort choices. Profiles without cell choices fall back to the abstract tiers `economy`, `balanced`, `capable`, and `strongest`. Neither host gives a hook a model inventory, so there is no runtime discovery to attempt; the map is maintained at release time by a scheduled workflow that verifies every model against the provider before shipping it.

If no suitable assessor is available, disclose and use the conservative shipped route—the root does not improvise an assessment.

## Shipped resolved routes

<!-- generated routes: start -->
The tables below are generated from `profiles.json` with the runtime resolver. The last profile for each provider is the fallback when entitlement is unknown.

### Codex: `full`

| Size / complexity | Normal risk | High risk |
|---|---|---|
| small / simple | `gpt-5.5/low` | `gpt-5.5/medium` |
| small / mixed | `gpt-6-sol/medium` | `gpt-6-sol/high` |
| small / complex | `gpt-6-astra/high` | `gpt-6-astra/high` |
| medium / simple | `gpt-5.5/low` | `gpt-5.5/medium` |
| medium / mixed | `gpt-6-sol/medium` | `gpt-6-sol/high` |
| medium / complex | `gpt-6-sol/high` | `gpt-6-sol/high` |
| large / simple | `gpt-5.5/low` | `gpt-5.5/medium` |
| large / mixed | `gpt-5.5/medium` | `gpt-5.5/medium` |
| large / complex | `gpt-6-sol/high` | `gpt-6-sol/high` |

### Codex: `base` (fallback)

| Size / complexity | Normal risk | High risk |
|---|---|---|
| small / simple | `gpt-5.5/low` | `gpt-5.5/medium` |
| small / mixed | `gpt-5.5/medium` | `gpt-5.5/high` |
| small / complex | `gpt-5.5/high` | `gpt-5.5/high` |
| medium / simple | `gpt-5.5/low` | `gpt-5.5/medium` |
| medium / mixed | `gpt-5.5/medium` | `gpt-5.5/high` |
| medium / complex | `gpt-5.5/high` | `gpt-5.5/high` |
| large / simple | `gpt-5.5/low` | `gpt-5.5/medium` |
| large / mixed | `gpt-5.5/medium` | `gpt-5.5/medium` |
| large / complex | `gpt-5.5/high` | `gpt-5.5/high` |

### Claude Code: `opus`

| Size / complexity | Normal risk | High risk |
|---|---|---|
| small / simple | `claude-sonnet-5/low` | `claude-sonnet-5/medium` |
| small / mixed | `claude-sonnet-5/medium` | `claude-sonnet-5/high` |
| small / complex | `claude-opus-5-5/xhigh` | `claude-opus-5-5/xhigh` |
| medium / simple | `claude-sonnet-5/low` | `claude-sonnet-5/medium` |
| medium / mixed | `claude-sonnet-5/medium` | `claude-sonnet-5/high` |
| medium / complex | `claude-opus-5-5/high` | `claude-opus-5-5/high` |
| large / simple | `claude-sonnet-5/low` | `claude-sonnet-5/medium` |
| large / mixed | `claude-sonnet-5/medium` | `claude-sonnet-5/medium` |
| large / complex | `claude-opus-5-5/high` | `claude-opus-5-5/high` |

### Claude Code: `sonnet` (fallback)

| Size / complexity | Normal risk | High risk |
|---|---|---|
| small / simple | `claude-sonnet-5/low` | `claude-sonnet-5/medium` |
| small / mixed | `claude-sonnet-5/medium` | `claude-sonnet-5/high` |
| small / complex | `claude-sonnet-5/xhigh` | `claude-sonnet-5/xhigh` |
| medium / simple | `claude-sonnet-5/low` | `claude-sonnet-5/medium` |
| medium / mixed | `claude-sonnet-5/medium` | `claude-sonnet-5/high` |
| medium / complex | `claude-sonnet-5/high` | `claude-sonnet-5/high` |
| large / simple | `claude-sonnet-5/low` | `claude-sonnet-5/medium` |
| large / mixed | `claude-sonnet-5/medium` | `claude-sonnet-5/medium` |
| large / complex | `claude-sonnet-5/high` | `claude-sonnet-5/high` |
<!-- generated routes: end -->

## Entitlement clamps

Each provider ships several profiles: an account routes through the best one it is entitled to, and through the conservative floor when entitlement cannot be read. A clamp is measured against the best profile, not the applied one.

A **tier clamp** means a weaker model does the work. It blocks the lead spawn and waits: the user accepts it with `/symphony:proceed` (Codex: `$symphony:symphony proceed`), which holds for the rest of that provider session and is asked again in the next one. An **effort clamp** on the same model is announced and the run continues, because effort is the dimension the matrix already trades away under risk.

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
