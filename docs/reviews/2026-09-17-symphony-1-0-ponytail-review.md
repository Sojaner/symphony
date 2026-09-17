# Ponytail over-engineering review: Symphony 1.0 (v0.21.2 -> v1.0.0)

Scope: over-engineering and complexity only. Correctness findings live in `2026-09-17-symphony-1-0-code-review.md`. Paths are relative to `plugins/symphony/`.

`symphony/memory.py:L13-21,L43-76: delete: ALLOWED_CONTEXT_SECTIONS, context_update_path, validated_context_sections, capability_refresh_due, record_missing_capability_suggestion. Zero production callers; only tests import them. Keep redact_secrets. ~45 lines.`

`symphony/model.py:L48-64,L72,L77-78: delete: CapabilitySnapshot persistence, MemoryStatus, ProjectState.capabilities/memory/capability_suggestions. Nothing ever writes them; resolve_tier always gets fallback_snapshot(). Keep the dataclass only as fallback_snapshot's return type. ~15 lines here, ~70 in store.py serializers (L147-182, L198-205, L219-226, L242-253).`

`symphony/model.py:L30-31, reducer.py:L161-164, runtime.py:L258-263: delete: Delegation.tokens/duration_seconds plumbing. No host payload carries either key; format_delegation branches are dead. ~15 lines.`

`symphony/routing.py:L124-132 + memory.py:L61-62: yagni: snapshot_is_stale wrapped by capability_refresh_due, both with test-only callers. Delete both. ~12 lines.`

`symphony/adapters.py:L32-38: delete: detect_provider heuristic. Both hook manifests always set SYMPHONY_PROVIDER; runtime.py:L24 becomes environ["SYMPHONY_PROVIDER"]. 7 lines.`

`symphony/adapters.py:L26-29: yagni: HookResult.stderr/exit_code never set non-default; render() returns stdout only. HookResult -> str. ~6 lines + main() L938-942 shrinks to one print.`

`symphony/store.py:L26-27,L291-295,L301-304: yagni: threading.Lock fallback for fcntl-less platforms. Hooks invoke python3 via env, no Windows target is claimed anywhere. Delete; let ImportError surface. ~12 lines.`

`symphony/store.py:L322-330: delete: StateStore.load/save. Only tests call them; runtime uses update(). Tests can use update(). 9 lines.`

`symphony/store.py:L44-50: shrink: legacy_project_key is project_key()[:24] with one caller. Inline at L362. 3 lines.`

`symphony/store.py:L53-59,L72-83,L107-119: stdlib: hand-rolled _event_to_dict/_delegation_to_dict/_run_to_dict. dataclasses.asdict handles nested frozen dataclasses; keep the _from_dict validators. ~30 lines.`

`symphony/model.py:L68 + store.py:L186-187: yagni: schema_version on the in-memory ProjectState guarded by a "cannot save" check. Only the file needs the version; write SCHEMA_VERSION at L189, drop the field and check. 4 lines.`

`symphony/routing.py:L31-34: yagni: ResolvedRoute subclass of Route. route_for returns Route, resolve_tier returns ResolvedRoute, runtime.py:L515-533 immediately flattens both into a dict. Return the dict from resolve_tier. ~8 lines.`

`symphony/runtime.py:L470-473 + L522-525: shrink: identical snapshot lookup twice. One _snapshot(state, provider) helper, or compute once in _prepare_delegation and pass to _accept_assessment. 4 lines.`

`symphony/runtime.py:L474-489 + L329-331: shrink: required lead model/effort derived twice with different guard code. One _required_route(assessment) -> (model, effort). ~10 lines.`

`symphony/runtime.py:L573-575: yagni: _decision_marker wraps _decision_markers for one caller (L500). Inline the truth test. 3 lines.`

`symphony/runtime.py:L545-551: yagni: _route_marker/_assessment_marker are one-line aliases of _assessment_from_marker. Call it directly with the marker string. 6 lines.`

`symphony/runtime.py:L780-792: shrink: _prompt_stop_actions rewrites block_stop/permit_stop into inject_context. Have _render_actions (L795) handle both kinds for prompt-originated stops instead of a second pass. ~10 lines.`

`symphony/runtime.py:L920-928 + commands/help.md: delete: _help() duplicates commands/help.md prose in a second wording; /symphony:help already expands to the command file. Point Claude at the command file text and keep one Codex line. ~6 lines.`

`symphony/reducer.py:L94-98 + runtime.py:L661-777: yagni: three lifecycle queues (_pending_delegations, _pending_parent_actions, _invalid_consultants) smuggled into assessment under underscore keys, each with its own copy/pop/replace boilerplate and an explicit preservation step in _assessment_accepted. Three first-class RunState tuple fields drop the preservation block and ~40 lines of dict juggling.`

`scripts/package_smoke.py:L223-240: shrink: recursive _contains/_has_active_run over arbitrary JSON. State files are one known shape: doc.get("active_run") is not None and version in json.dumps(doc). ~15 lines.`

`scripts/package_smoke.py:L50-64: yagni: upgrade scenario rewrites PLUGIN_VERSION via regex in a copied package. Set SYMPHONY_PLUGIN_VERSION env (already exported at L184) and read it in runtime.py's heartbeat instead of patching source. ~12 lines.`

`.github/workflows/ci.yml:L44-62: delete: real Codex smoke step that can never run (codex CLI never installed). Either install it or drop the dead step. ~19 lines.`

`tests/test_memory.py, tests/test_routing.py:L85-92: delete: tests for the deleted memory/staleness helpers above. ~80 lines.`

`agents/*.md (15 files): keep. Claude pins model/effort only through agent type; one file per cell is the platform-native minimum.`

`net: -420 lines possible.`
