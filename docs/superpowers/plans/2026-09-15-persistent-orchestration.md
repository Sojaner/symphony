# Persistent Symphony Orchestration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Symphony persistently enableable per project, recover active runs, keep weak roots alive through deterministic hooks, route three execution modes, and select available companion capabilities.

**Architecture:** A single Python standard-library hook module owns JSON state and lifecycle decisions behind the host hook interface. Claude and Codex use separate declarations but the same implementation. Markdown commands provide user-facing controls and machine-readable markers; the Symphony skill owns strong-lead routing and capability policy.

**Tech Stack:** Python 3 standard library, JSON hook declarations, Markdown plugin commands/skills, `unittest`, Claude Code and Codex plugin manifests.

**Spec:** `docs/superpowers/specs/2026-09-15-persistent-orchestration-design.md`

## Global Constraints

- Store user state only in the host-provided plugin data directory; never write Symphony state into the target repository.
- Use no new runtime dependency beyond Python 3.
- Keep small, medium, and large modes mutually exclusive.
- Never claim that hooks can prevent explicit interrupts or host override behavior.
- Never install a companion plugin automatically.
- Rate-limit each missing-capability suggestion to once per project every 30 days and emit at most one suggestion per run.
- Preserve existing worktree changes and plugin compatibility for Claude Code and Codex.

---

### Task 1: Lifecycle state module and regression tests

**Files:**
- Create: `plugins/symphony/scripts/symphony_hook.py`
- Create: `plugins/symphony/tests/test_symphony_hook.py`

**Interfaces:**
- Consumes: lifecycle JSON on stdin; `PLUGIN_DATA` or `CLAUDE_PLUGIN_DATA`; `PLUGIN_ROOT` or `CLAUDE_PLUGIN_ROOT`.
- Produces: `handle_event(payload, data_dir, now=None) -> HookResult`, JSON hook output, and atomic per-project policy/run records.

- [ ] **Step 1: Write failing lifecycle tests**

Cover enable, disable, start, automatic enabled-project activation, resume, duplicate ownership, SubagentStart/Stop, Stop blocking, exact completion receipt, force stop, corrupt state, and 30-day suggestion cooldown with `unittest` and temporary directories.

- [ ] **Step 2: Run the focused tests and confirm failure**

Run: `python3 -m unittest discover -s plugins/symphony/tests -v`

Expected: failure because `scripts/symphony_hook.py` does not exist.

- [ ] **Step 3: Implement the minimum state machine**

Use dataclasses only where they shorten validation. Resolve the project root with `git rev-parse --show-toplevel`, falling back to `Path(cwd).resolve()`. Hash the root for the filename, acquire a lock directory, write JSON to a sibling temporary file with mode `0600`, and replace atomically.

Recognize these exact markers in prompt input:

```text
SYMPHONY_CONTROL: enable
SYMPHONY_CONTROL: disable
SYMPHONY_CONTROL: start
SYMPHONY_CONTROL: stop
SYMPHONY_CONTROL: force-stop
SYMPHONY_CONTROL: status
SYMPHONY_CONTROL: help
```

Generate a random run id and exact receipt `SYMPHONY_RUN_COMPLETE:<run-id>`. Track agent ids as a sorted unique list. For Stop, poll active state for at most `SYMPHONY_STOP_WAIT_SECONDS` (default 55) and return `decision:block` with a concrete continuation instruction when unfinished.

- [ ] **Step 4: Run lifecycle tests**

Run: `python3 -m unittest discover -s plugins/symphony/tests -v`

Expected: all tests pass.

### Task 2: Host hooks and user commands

**Files:**
- Create: `plugins/symphony/hooks/hooks.json`
- Create: `plugins/symphony/hooks/codex.json`
- Create: `plugins/symphony/commands/enable.md`
- Create: `plugins/symphony/commands/disable.md`
- Modify: `plugins/symphony/commands/start.md`
- Create: `plugins/symphony/commands/status.md`
- Create: `plugins/symphony/commands/stop.md`
- Create: `plugins/symphony/commands/help.md`
- Modify: `plugins/symphony/.codex-plugin/plugin.json`

**Interfaces:**
- Consumes: the hook module's stdin/stdout interface and command markers from Task 1.
- Produces: Claude and Codex registration for SessionStart, UserPromptSubmit, SubagentStart, SubagentStop, Stop, and Interrupt where supported; six documented slash commands.

- [ ] **Step 1: Add hook-declaration validation tests**

Extend `test_symphony_hook.py` to load both JSON declarations, verify every command points through `${CLAUDE_PLUGIN_ROOT}/scripts/symphony_hook.py`, and confirm the Codex manifest declares `./hooks/codex.json`.

- [ ] **Step 2: Run tests and confirm the declaration test fails**

Run: `python3 -m unittest discover -s plugins/symphony/tests -v`

Expected: failure because hook declarations are absent.

- [ ] **Step 3: Add host declarations and commands**

Use `/usr/bin/env python3` and the compatibility root variable. Keep `help` and `status` inert. Put one exact control marker and `$ARGUMENTS` in each command. Explain that `stop` preserves project enablement and `disable` removes it.

- [ ] **Step 4: Run tests and validate JSON**

Run: `python3 -m unittest discover -s plugins/symphony/tests -v`

Expected: all tests pass.

### Task 3: Strong lead, modes, and capability routing

**Files:**
- Modify: `plugins/symphony/skills/symphony/SKILL.md`
- Create: `plugins/symphony/skills/symphony/references/capability-routing.md`
- Modify: `plugins/symphony/skills/symphony/agents/openai.yaml`
- Modify: `plugins/symphony/evals/match-proceeds/prompt.md`
- Modify: `plugins/symphony/evals/match-proceeds/graders/proceeds-after-verification.md`
- Modify: `plugins/symphony/evals/mismatch-refusal/prompt.md`
- Modify: `plugins/symphony/evals/mismatch-refusal/graders/no-project-work.md`

**Interfaces:**
- Consumes: hook bootstrap packet, live model/effort/skill/tool catalog, codebase-memory graph evidence, and command semantics.
- Produces: one strong execution lead, a mutually exclusive mode, capability assignments, worker receipts, recovery behavior, and final completion receipt.

- [ ] **Step 1: Rewrite bootstrap and lifecycle instructions**

Allow weak roots. Require a strongest-available high-effort bootstrap with no inherited turns. Make that agent the execution lead. Specify small/direct, medium/mixed, and large/orchestrated selection; thin-root responsibilities; recovery; completion receipts; and Stop behavior.

- [ ] **Step 2: Add capability routing reference**

Define precedence and exact discovery signals for `ponytail:*`, `superpowers:*`, `compound-engineering:*`, `mattpocock-skills:*`, Context7 MCP tools, and Codebase Memory MCP tools. Select one primary workflow owner, with Ponytail and evidence tools as optional additions.

- [ ] **Step 3: Update evals for the new root contract**

Replace the old weak-root rejection expectation with strong-bootstrap routing. Keep a mismatch case only for an explicit declared runtime profile that contradicts observed metadata. Require the dry-run receipt to show mode and capability routing without performing project writes.

- [ ] **Step 4: Validate skill structure**

Run: `python3 /home/rojan/.codex/skills/.system/skill-creator/scripts/quick_validate.py plugins/symphony/skills/symphony`

Expected: validation succeeds.

### Task 4: Documentation, version, and full verification

**Files:**
- Modify: `.github/workflows/plugin-eval.yml`
- Modify: `README.md`
- Modify: `plugins/symphony/.claude-plugin/plugin.json`
- Modify: `plugins/symphony/.codex-plugin/plugin.json`

**Interfaces:**
- Consumes: all shipped commands, lifecycle guarantees, runtime prerequisite, and capability routes.
- Produces: complete user documentation and matching semantic versions.

- [ ] **Step 1: Document installation trust and usage**

Document Python 3, hook trust, persistent enablement, all commands including `/symphony:help`, modes, recovery, companion capabilities including Codebase Memory, suggestion throttling, and the interrupt limitation.

Add the lifecycle unit test command to CI before plugin validation and hosted evals.

- [ ] **Step 2: Bump both manifests from `0.11.5` to `0.12.0`**

Use a minor release because persistent activation, hooks, commands, and routing modes are new public behavior.

- [ ] **Step 3: Run focused and repository checks**

Run:

```bash
python3 -m py_compile plugins/symphony/scripts/symphony_hook.py
python3 -m unittest discover -s plugins/symphony/tests -v
python3 /home/rojan/.codex/skills/.system/skill-creator/scripts/quick_validate.py plugins/symphony/skills/symphony
claude plugin validate ./plugins/symphony
git diff --check
```

Expected: every command exits zero.

- [ ] **Step 4: Review the complete diff**

Check hook compatibility, state safety, command semantics, accidental files, manifest parity, documentation accuracy, and whether every requirement in the spec has implementation and test coverage.

- [ ] **Step 5: Commit and push**

```bash
git add README.md docs plugins/symphony
git commit -m "feat: make Symphony orchestration persistent and recoverable"
git push
```

- [ ] **Step 6: Observe CI**

Use the repository's GitHub workflow status until the pushed commit's `Plugin evals` workflow reaches a terminal state. If it fails for an in-scope cause, inspect logs, fix, reverify, commit, push, and observe again.
