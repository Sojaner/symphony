# Capability-routing reference

Use the effective runtime catalog for the current root or child. Installed files, marketplace presence, and the parent's catalog are not proof that a capability is usable by a particular agent.

## Precedence

1. Explicit user-requested skills.
2. Repository instructions such as `AGENTS.md` or `CLAUDE.md`.
3. One primary workflow owner for the unit.
4. Cross-cutting constraints that do not duplicate the workflow.
5. Evidence tools required by unresolved facts.

## Known capability families

| Signal in the effective catalog | Role | Use when |
|---|---|---|
| `superpowers:*` | Primary workflow | Its design approval, planning, debugging, TDD, subagent execution, or verification workflow best fits the unit. |
| `compound-engineering:*` | Primary workflow | Its end-to-end work, planning, review, POV, PR, or long-running workflow best fits the unit. |
| `mattpocock-skills:*` | Primary workflow | Focused codebase design, diagnosis, TDD, review, domain modeling, research, or agent-document work fits the unit. |
| `ponytail:*` | Cross-cutting constraint | Coding or design benefits from the smallest correct implementation. Never simplify away validation, safety, accessibility, or required verification. |
| Context7 query/resolve tools | Evidence tool | Current official library, framework, Codex, or Claude behavior materially affects a decision. |
| Codebase Memory graph tools | Evidence tool | Existing-code structure, callers, dependencies, data flow, blast radius, or architecture must be discovered. |

Do not run overlapping Superpowers, Compound Engineering, and Matt Pocock planning/delivery ceremonies on the same unit. A narrow specialist skill may support another workflow only when their responsibilities do not overlap.

## Codebase Memory route

Detect it by usable tools, not only by plugin name. Structural discovery normally needs `search_graph`, `trace_path`, `get_code_snippet`, and `check_index_coverage`; indexing and broader analysis may also use `list_projects`, `index_repository`, `index_status`, `query_graph`, `get_architecture`, or `detect_changes`.

Use this order:

1. List projects and index only when the repository is not indexed.
2. Use graph search for definitions and structural relationships.
3. Trace callers, callees, or data flow where the decision depends on them.
4. Read exact symbol snippets for material claims.
5. Check index coverage for every cited or operated-on path.
6. Fall back to targeted source search for literals, configuration, non-code files, or reported coverage gaps.

The assessor or execution lead performs this grounding; the root only relays its bounded result. A worker packet includes the project id, index generation/freshness, bounded scope, queries and pagination state, qualified symbols, trace findings, coverage results, source fallback, and unresolved questions. A worker without graph tools must not claim direct MCP evidence.

### Document memory

Small runs skip optional memory probing. Only after the assessor is terminal and its assessment is accepted may a medium or large execution lead dispatch at most one disposable memory-probe worker. That probe requires trusted configuration proving a verified host tool-timeout or cancellation path will make the worker terminal inside the bound. If that path cannot be verified, skip optional memory. After a missing capability or any failure, timeout, or hang, ensure the memory-probe worker is terminal before continuing and use the fallback to repository documents and source inspection; it never blocks project completion or triggers another probe.

Markdown memory additionally requires healthy indexing. Read `current.md` directly; retrieve history with `search_graph` or `search_code`, then inspect exact snippets or use targeted direct reads and run `check_index_coverage` for every memory path used. If memory files are ignored, indexed history is disabled until the user changes that policy.

## Missing-capability suggestions

A missing capability never blocks work unless the user requested its guarantee and no valid fallback exists. Suggest only when it would have materially improved the run.

- Emit no more than one suggestion per run.
- Do not repeat a capability for the same project within 30 days.
- Put the suggestion in the final handoff, not during active work.
- State the concrete lost benefit and the fallback used.
- Include `<!-- SYMPHONY_SUGGESTED:<capability-id> -->` so the lifecycle hook records the cooldown.
- Never install or enable a plugin automatically.
