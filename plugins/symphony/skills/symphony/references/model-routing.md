# Model-routing index

Last checked: 2026-09-14.

This file is a compact bootstrap cache for the conductor. The live `spawn_agent` schema is the source of truth for models, supported efforts, and concurrency on the current host.

## Stable routing rules

| Work | Starting route | Raise effort when |
|---|---|---|
| Orchestration consultation | strongest general reasoning model, `high` | the decision is hard to reverse or spans several uncertain systems |
| Architecture, algorithms, difficult diagnosis | strongest reasoning model, `high` | competing hypotheses survive evidence; use the smallest `max`/`ultra` unit available |
| Difficult implementation | capable coding or agentic workhorse, `high` | acceptance checks expose unresolved reasoning |
| Settled multi-file implementation | balanced coding model, `medium` | interfaces or migrations remain ambiguous |
| Mechanical patch, search, inventory, or focused test | fastest suitable model, `low` or `medium` | the task stops being mechanical |
| Final review | strong model different from the implementer, `high` | security, data loss, money, or irreversible migration is involved |

The current Codex catalog may describe models such as `gpt-6-astra` for the most demanding reasoning, `gpt-5.6-sol` as a reliable agentic workhorse, `gpt-5.6-terra` for balanced coding, `gpt-5.6-luna` for fast affordable work, and `gpt-5.3-codex-spark` for ultra-fast coding. Use only exact ids and efforts exposed by the live tool.

Effort is a budget, not a quality rank. Start at the cheapest plausible level. Escalate only the narrow unit containing the unresolved uncertainty.

## Official OpenAI sources

- [Current model guidance](https://developers.openai.com/api/docs/guides/latest-model) covers reasoning-effort tuning, prompt design, and explicit subagent-delegation guidance.
- [GPT-5.3-Codex model page](https://developers.openai.com/api/docs/models/gpt-5.3-codex) documents its coding specialization and supported API reasoning efforts.
- [Reasoning models](https://developers.openai.com/api/docs/guides/reasoning) explains reasoning controls and when additional reasoning is useful.

The current guidance recommends `low` for efficient reasoning, `medium` as a balanced starting point, and higher effort for complex work where latency and cost matter less. It also recommends explicitly defining when and how subagents should be used.

## Freshness rule

Refresh the working facts with the OpenAI Docs skill when either condition holds:

- the live catalog contains a model or effort relevant to the decision that this index does not recognize; or
- this index is more than 30 days old and the decision depends on current model capabilities.

Search and open the relevant official page. Give the conductor only the changed facts and source links. Availability on this host still comes from the live collaboration-tool schema.
