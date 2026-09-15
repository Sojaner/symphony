# Model-routing index

Last checked: 2026-09-14.

This file is a compact bootstrap cache for the conductor. The host's live subagent tool schema — Codex collaboration tools (`spawn_agent`), or the Claude Code agent tool — is the source of truth for models, supported efforts, and concurrency on the current host.

## Stable routing rules

## Symphony assessment and execution

| Stage | Route |
|---|---|
| Assessment | strongest available general reasoning model, `high`, one read-only `symphony_assessor` with no inherited turns; it returns exactly one concise result and never implements. |
| Small execution | capable direct executor, `medium`; no workers merely to justify Symphony. |
| Medium execution | balanced agentic lead, `medium`; at most two workers concurrently. |
| Large execution | capable coordinator, `medium` or `high` by risk; dependency-aware delegated waves. |
| Narrow hard decision or high-risk final review | strongest general reasoning model, `high`. |

Assessment is not execution: wait for its authorized receipt, then start the separate mode-appropriate lead. On resume or compaction, start a fresh lead from bounded lifecycle/document memory after reconciling workers.

| Work | Starting route | Raise effort when |
|---|---|---|
| Orchestration consultation | strongest general reasoning model, `high` | the decision is hard to reverse or spans several uncertain systems |
| Architecture, algorithms, difficult diagnosis | strongest reasoning model, `high` | competing hypotheses survive evidence; use the smallest `max`/`ultra` unit available |
| Difficult implementation | capable coding or agentic workhorse, `high` | acceptance checks expose unresolved reasoning |
| Settled multi-file implementation | balanced coding model, `medium` | interfaces or migrations remain ambiguous |
| Mechanical patch, search, inventory, or focused test | fastest suitable model, `low` or `medium` | the task stops being mechanical |
| Final review | strong model different from the implementer, `high` | security, data loss, money, or irreversible migration is involved |

A current Codex catalog may describe models such as `gpt-6-astra` for the most demanding reasoning, `gpt-5.6-sol` as a reliable agentic workhorse, `gpt-5.6-terra` for balanced coding, `gpt-5.6-luna` for fast affordable work, and `gpt-5.3-codex-spark` for ultra-fast coding. Efforts commonly run `low` through `high` with `xhigh`/`ultra` tiers on some models.

A current Claude Code catalog may describe models such as `claude-fable-5` or `claude-opus-5` for the most demanding reasoning, `claude-sonnet-5` for balanced coding and agentic work, and `claude-haiku-4-5` for fast affordable work; subagent overrides are often exposed as tiers (`opus`, `sonnet`, `haiku`). Efforts run `low`, `medium`, `high`, `xhigh`, `max`.

Use only exact ids and efforts exposed by the live tool on the current host.

Effort is a budget, not a quality rank. Start at the cheapest plausible level. Escalate only the narrow unit containing the unresolved uncertainty.

## Official vendor sources

OpenAI:

- [Current model guidance](https://developers.openai.com/api/docs/guides/latest-model) covers reasoning-effort tuning, prompt design, and explicit subagent-delegation guidance.
- [GPT-5.3-Codex model page](https://developers.openai.com/api/docs/models/gpt-5.3-codex) documents its coding specialization and supported API reasoning efforts.
- [Reasoning models](https://developers.openai.com/api/docs/guides/reasoning) explains reasoning controls and when additional reasoning is useful.

Anthropic:

- [Models overview](https://docs.claude.com/en/docs/about-claude/models/overview) lists current Claude models, context windows, and capability tiers.
- [Claude Code subagents](https://code.claude.com/docs/en/sub-agents) documents subagent spawning and per-agent model overrides.

Both vendors recommend `low` effort for efficient mechanical work, `medium` as a balanced starting point, and higher effort only for complex work where latency and cost matter less, and recommend explicitly defining when and how subagents should be used.

## Freshness rule

Refresh the working facts from the official vendor sources above (web search or fetch) when either condition holds:

- the live catalog contains a model or effort relevant to the decision that this index does not recognize; or
- this index is more than 30 days old and the decision depends on current model capabilities.

Search and open the relevant official page. Give the conductor only the changed facts and source links. Availability on this host still comes from the live subagent tool schema.
