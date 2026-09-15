# Symphony Document Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add bounded project-local Markdown memory that activates only after a strong lead verifies codebase-memory-mcp and remains recoverable across compaction and replacement leads.

**Architecture:** Keep the existing plugin-data JSON record as the always-available lifecycle anchor. Add deterministic repo-relative memory candidates to each run, activate extended memory only through a run-bound codebase-memory-mcp checkpoint receipt, and make the Stop hook validate the active run's non-empty, fresh `current.md`. The Symphony skill owns document content, indexed history retrieval, and worker excerpts because hooks cannot call MCP tools or interpret prose.

**Tech Stack:** Python 3 standard library, `unittest`, Markdown skill/command documentation, JSON plugin manifests, Claude plugin evals.

**Spec:** `docs/superpowers/specs/2026-09-15-document-memory-design.md`

## Global Constraints

- Extended document memory is disabled unless the live root or strong lead verifies usable codebase-memory-mcp tools and a healthy project index.
- Store active memory at `.symphony/memory/current.md` and per-run history at `.symphony/memory/history/<run-id>.md`.
- The strong lead is the only document writer; workers return bounded facts and never write shared memory concurrently.
- Never store credentials, tokens, private keys, raw environment values, unnecessary personal data, full transcripts, or copied source bodies.
- Do not add a daemon, database, dependency, background indexer, concurrent writer, or automatic `.gitignore` edit.
- `/symphony:agents` is read-only; `--all` includes every retained historical run, and missing model or effort metadata is rendered as `not exposed by host` rather than guessed.
- Keep the existing plugin-data lifecycle record and non-MCP recovery path working.
- Bump both plugin manifests from `0.12.0` directly to `0.14.0`; do not publish `0.13.0` or add version fields to marketplace manifests.

---

### Task 1: Add deterministic memory candidates without creating documents

**Files:**
- Modify: `plugins/symphony/tests/test_symphony_hook.py`
- Modify: `plugins/symphony/scripts/symphony_hook.py`

**Interfaces:**
- Produces: `memory_paths(project_root: str, run_id: str) -> tuple[Path, Path]`.
- Produces: `active_run["memory"]` with `enabled`, `current`, `history`, and `checkpoint_at` fields.
- Changes: `_new_run(payload, objective, now, project_root)` receives the canonical project root.
- Preserves: no `.symphony` directory or memory document is created by the hook at run start.

- [ ] **Step 1: Write the failing run-state test**

Add this test to `SymphonyHookTests`:

```python
def test_new_run_exposes_memory_candidates_without_creating_documents(self):
    result = self.hook.handle_event(
        self.event(
            "UserPromptSubmit",
            prompt="SYMPHONY_CONTROL: start\nSYMPHONY_TASK: retain project facts",
        ),
        self.data,
    )

    run = self.state()["active_run"]
    self.assertEqual(
        {
            "enabled": False,
            "current": ".symphony/memory/current.md",
            "history": f".symphony/memory/history/{run['id']}.md",
            "checkpoint_at": None,
        },
        run["memory"],
    )
    self.assertIn(str(self.project / run["memory"]["current"]), result.context)
    self.assertIn("codebase-memory-mcp", result.context)
    self.assertFalse((self.project / ".symphony").exists())
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
python3 -m unittest plugins.symphony.tests.test_symphony_hook.SymphonyHookTests.test_new_run_exposes_memory_candidates_without_creating_documents -v
```

Expected: FAIL because the run has no `memory` field and bootstrap context has no memory candidate.

- [ ] **Step 3: Implement the minimal run-memory shape**

In `symphony_hook.py`, add:

```python
MEMORY_ROOT = Path(".symphony") / "memory"


def memory_paths(project_root, run_id):
    root = Path(project_root)
    return root / MEMORY_ROOT / "current.md", root / MEMORY_ROOT / "history" / f"{run_id}.md"
```

Change `_new_run` to accept `project_root`, derive the two repo-relative paths, and add:

```python
"memory": {
    "enabled": False,
    "current": str(current.relative_to(project_root)),
    "history": str(history.relative_to(project_root)),
    "checkpoint_at": None,
},
```

Pass `state["project_root"]` from `_handle_prompt`. Extend `_bootstrap_context` with both absolute candidates and this rule: verify codebase-memory-mcp plus a healthy index before creating either file; otherwise leave extended memory disabled.

- [ ] **Step 4: Run the focused and full tests**

Run:

```bash
python3 -m unittest plugins.symphony.tests.test_symphony_hook.SymphonyHookTests.test_new_run_exposes_memory_candidates_without_creating_documents -v
python3 -m unittest discover -s plugins/symphony/tests -v
```

Expected: PASS, with no `.symphony` directory created by the tests unless a later test creates it explicitly.

- [ ] **Step 5: Commit**

```bash
git add plugins/symphony/scripts/symphony_hook.py plugins/symphony/tests/test_symphony_hook.py
git commit -m "feat: add Symphony memory candidates"
```

---

### Task 2: Activate and validate memory checkpoints at lifecycle boundaries

**Files:**
- Modify: `plugins/symphony/tests/test_symphony_hook.py`
- Modify: `plugins/symphony/scripts/symphony_hook.py`

**Interfaces:**
- Produces: `MEMORY_CHECKPOINT_RE`, matching `SYMPHONY_MEMORY_CHECKPOINT:<run-id>:codebase-memory-mcp`.
- Produces: `_record_memory_checkpoint(run, message, now) -> bool`.
- Produces: `_memory_checkpoint_error(run, project_root, final_message) -> str | None`.
- Consumes: Task 1's `active_run["memory"]` shape and project-relative paths.

- [ ] **Step 1: Write failing checkpoint tests**

Add three tests:

```python
def test_memory_marker_activates_only_for_the_current_run(self):
    self.hook.handle_event(
        self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: start\nSYMPHONY_TASK: task"),
        self.data,
        now=1_000,
    )
    run = self.state()["active_run"]

    self.hook.handle_event(
        self.event(
            "SubagentStop",
            agent_id="lead-1",
            last_assistant_message=(
                "<!-- SYMPHONY_MEMORY_CHECKPOINT:not-this-run:codebase-memory-mcp -->"
            ),
        ),
        self.data,
        now=1_001,
    )
    self.assertFalse(self.state()["active_run"]["memory"]["enabled"])

    self.hook.handle_event(
        self.event(
            "SubagentStop",
            agent_id="lead-1",
            last_assistant_message=(
                f"<!-- SYMPHONY_MEMORY_CHECKPOINT:{run['id']}:codebase-memory-mcp -->"
            ),
        ),
        self.data,
        now=1_002,
    )
    memory = self.state()["active_run"]["memory"]
    self.assertTrue(memory["enabled"])
    self.assertEqual(1_002, memory["checkpoint_at"])

def test_active_memory_blocks_completion_when_current_document_is_missing(self):
    self.hook.handle_event(
        self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: start\nSYMPHONY_TASK: task"),
        self.data,
        now=1_000,
    )
    run = self.state()["active_run"]
    marker = f"SYMPHONY_MEMORY_CHECKPOINT:{run['id']}:codebase-memory-mcp"

    result = self.hook.handle_event(
        self.event(
            "Stop",
            last_assistant_message=(
                f"<!-- SYMPHONY_MODE:small -->\n<!-- {marker} -->\n<!-- {run['receipt']} -->"
            ),
        ),
        self.data,
        now=1_003,
        stop_wait_seconds=0,
    )

    self.assertTrue(result.block)
    self.assertIn("current.md", result.reason)
    self.assertTrue(self.state()["active_run"]["memory"]["enabled"])

def test_active_memory_completes_with_fresh_nonempty_current_document(self):
    self.hook.handle_event(
        self.event("UserPromptSubmit", prompt="SYMPHONY_CONTROL: start\nSYMPHONY_TASK: task"),
        self.data,
    )
    run = self.state()["active_run"]
    current = self.project / run["memory"]["current"]
    current.parent.mkdir(parents=True)
    current.write_text("# Symphony Current Memory\n\n## Run\nactive\n", encoding="utf-8")
    marker = f"SYMPHONY_MEMORY_CHECKPOINT:{run['id']}:codebase-memory-mcp"

    result = self.hook.handle_event(
        self.event(
            "Stop",
            last_assistant_message=(
                f"<!-- SYMPHONY_MODE:small -->\n<!-- {marker} -->\n<!-- {run['receipt']} -->"
            ),
        ),
        self.data,
        stop_wait_seconds=0,
    )

    self.assertFalse(result.block)
    self.assertIsNone(self.state()["active_run"])
```

- [ ] **Step 2: Run the tests and verify RED**

Run each new test by its fully qualified name. Expected failures: no activation parser, missing documents do not block, and the valid marker is not recognized.

- [ ] **Step 3: Implement the marker parser and validator**

Add:

```python
MEMORY_CHECKPOINT_RE = re.compile(
    r"SYMPHONY_MEMORY_CHECKPOINT:([a-f0-9]+):codebase-memory-mcp",
    re.IGNORECASE,
)


def _record_memory_checkpoint(run, message, now):
    matches = MEMORY_CHECKPOINT_RE.findall(message or "")
    if run["id"].lower() not in {value.lower() for value in matches}:
        return False
    run["memory"]["enabled"] = True
    run["memory"]["checkpoint_at"] = int(now)
    return True


def _memory_checkpoint_error(run, project_root, final_message):
    memory = run.get("memory") or {}
    if not memory.get("enabled"):
        return None
    matches = {value.lower() for value in MEMORY_CHECKPOINT_RE.findall(final_message or "")}
    if run["id"].lower() not in matches:
        return "Symphony document memory is active; include its matching checkpoint receipt."
    current = Path(project_root) / memory["current"]
    try:
        if not current.is_file() or current.stat().st_size == 0:
            return f"Symphony document memory is missing or empty at {current}."
        if current.stat().st_mtime < run["created_at"]:
            return f"Symphony document memory is stale at {current}."
    except OSError as error:
        return f"Symphony could not validate document memory at {current}: {error}"
    return None
```

Call `_record_memory_checkpoint` from `SubagentStop`. In `_handle_stop`, call it before validation so the final marker can activate memory; persist state before returning a memory-related block. After the existing agent, completion-receipt, and mode checks, call `_memory_checkpoint_error` and block with its exact reason when non-null.

- [ ] **Step 4: Add stale and disabled-memory regression tests**

Add a stale-file test that sets `os.utime(current, (1, 1))` after a run created at `now=1_000` and asserts Stop blocks with `stale`. Extend an existing completion test to assert that a run without a memory marker still completes without creating `.symphony`.

- [ ] **Step 5: Run all lifecycle tests**

```bash
python3 -m unittest discover -s plugins/symphony/tests -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add plugins/symphony/scripts/symphony_hook.py plugins/symphony/tests/test_symphony_hook.py
git commit -m "feat: guard Symphony memory checkpoints"
```

---

### Task 3: Teach leads to write and retrieve bounded indexed memory

**Files:**
- Modify: `plugins/symphony/skills/symphony/SKILL.md`
- Modify: `plugins/symphony/skills/symphony/references/capability-routing.md`
- Modify: `plugins/symphony/tests/test_symphony_hook.py`

**Interfaces:**
- Consumes: Task 1's injected current/history paths.
- Consumes: Task 2's `SYMPHONY_MEMORY_CHECKPOINT:<run-id>:codebase-memory-mcp` receipt.
- Produces: the exact `current.md` section contract from the approved spec.
- Produces: a lead-only checkpoint schedule and bounded worker-memory packet.

- [ ] **Step 1: Add a failing skill-contract test**

Add to `HookDeclarationTests`:

```python
def test_skill_defines_mcp_gated_document_memory(self):
    skill = (PLUGIN_ROOT / "skills" / "symphony" / "SKILL.md").read_text(encoding="utf-8")
    required = (
        "Extended document memory",
        ".symphony/memory/current.md",
        ".symphony/memory/history/<run-id>.md",
        "SYMPHONY_MEMORY_CHECKPOINT:<run-id>:codebase-memory-mcp",
        "The strong lead is the only writer",
        "## Objective and acceptance criteria",
        "## Verification evidence",
        "check_index_coverage",
    )
    for text in required:
        self.assertIn(text, skill)
```

- [ ] **Step 2: Run the test and verify RED**

```bash
python3 -m unittest plugins.symphony.tests.test_symphony_hook.HookDeclarationTests.test_skill_defines_mcp_gated_document_memory -v
```

Expected: FAIL on `Extended document memory`.

- [ ] **Step 3: Add the minimal memory protocol to the skill**

Add a section after capability routing with this positive contract:

```markdown
## Extended document memory

Activate this only after the root or strong lead verifies usable codebase-memory-mcp tools and a healthy index for the current project. Otherwise do not create `.symphony/memory/`; continue with compact lifecycle recovery.

The strong lead is the only writer. It atomically replaces `.symphony/memory/current.md` and appends changed durable facts to `.symphony/memory/history/<run-id>.md` after mode selection, material decisions, worker-wave dispatch and integration, verification changes, and final checkpointing.

`current.md` contains, in order: Run; Objective and acceptance criteria; Invariants and constraints; Decisions and rationale; Important discoveries; Completed work; Pending work; Verification evidence; Risks and blockers; Retrieval index.

On recovery, read `current.md` directly, check index status, query only relevant history sections, run `check_index_coverage` for every memory path used, and fall back to targeted direct reads for stale or uncovered sections. Give workers only relevant invariants, decisions, evidence references, and acceptance criteria.

After a durable checkpoint include `<!-- SYMPHONY_MEMORY_CHECKPOINT:<run-id>:codebase-memory-mcp -->`. Never write secrets, environment values, unnecessary personal data, transcripts, or copied source bodies.
```

Also add the current-memory path, relevant indexed history findings, and checkpoint responsibility to the lead and worker packet definitions.

- [ ] **Step 4: Tighten the Codebase Memory routing reference**

Add a short `Document memory` subsection stating that Markdown memory requires verified MCP availability and healthy indexing; `current.md` is read directly; history uses `search_graph`/`search_code`, exact snippets or direct reads, and `check_index_coverage`; ignored memory files disable indexed history until the user changes that policy.

- [ ] **Step 5: Run skill and lifecycle validation**

```bash
python3 -m unittest discover -s plugins/symphony/tests -v
python3 /home/rojan/.codex/skills/.system/skill-creator/scripts/quick_validate.py plugins/symphony/skills/symphony
```

If the validator lives at a different installed skill root, locate that existing `quick_validate.py` and run it against the same Symphony skill directory. Expected: tests PASS and validator reports a valid skill.

- [ ] **Step 6: Commit**

```bash
git add plugins/symphony/skills/symphony/SKILL.md plugins/symphony/skills/symphony/references/capability-routing.md plugins/symphony/tests/test_symphony_hook.py
git commit -m "feat: add indexed document memory protocol"
```

---

### Task 4: Add active and historical subagent inspection

**Files:**
- Create: `plugins/symphony/commands/agents.md`
- Modify: `plugins/symphony/scripts/symphony_hook.py`
- Modify: `plugins/symphony/tests/test_symphony_hook.py`
- Modify: `plugins/symphony/commands/help.md`
- Modify: `plugins/symphony/skills/symphony/SKILL.md`

**Interfaces:**
- Produces: `/symphony:agents` and `/symphony:agents --all`.
- Produces: `active_run["agent_records"]`, keyed by agent id, while preserving `active_run["agents"]` as the active-id Stop guard.
- Produces: project-state `run_history`, containing compact terminal run and agent metadata without prompts, transcripts, or outputs.
- Produces: `_agents_context(state, include_history=False) -> str` and `_archive_run(state, status, now) -> None`.

- [ ] **Step 1: Write failing agent-ledger tests**

Add tests that start a run, send `SubagentStart` with `agent_id`, `agent_type`, `model`, and `reasoning_effort`, then send `SubagentStop`. Assert the active id leaves `agents`, while `agent_records[id]` remains with terminal status and exact metadata. Add a second test that completes the run, invokes `SYMPHONY_CONTROL: agents` with and without `SYMPHONY_ARGS: --all`, and verifies only the `--all` response includes the historical run and agent.

Use these core assertions:

```python
record = self.state()["active_run"]["agent_records"]["worker-1"]
self.assertEqual("terminal", record["status"])
self.assertEqual("test-writer", record["role"])
self.assertEqual("gpt-6-astra", record["model"])
self.assertEqual("high", record["effort"])

self.assertIn("No active Symphony run", current.context)
self.assertIn("worker-1", historical.context)
self.assertIn("gpt-6-astra", historical.context)
```

Add a missing-metadata case and assert both model and effort render as `not exposed by host`.

- [ ] **Step 2: Run the focused tests and verify RED**

Run the new tests by fully qualified names. Expected: FAIL because `agent_records`, `run_history`, and the `agents` control do not exist.

- [ ] **Step 3: Implement compact agent records and run archiving**

Initialize `run_history` in `default_state` and `read_project_state`, and initialize `agent_records` in `_new_run`. On `SubagentStart`, retain the active id and write:

```python
run["agent_records"][agent_id] = {
    "id": agent_id,
    "status": "active",
    "role": payload.get("agent_type") or "not exposed by host",
    "model": payload.get("model") or "not exposed by host",
    "effort": payload.get("reasoning_effort") or payload.get("effort") or "not exposed by host",
    "started_at": current,
    "stopped_at": None,
}
```

On `SubagentStop`, remove only the active id and update the retained record to `terminal` with `stopped_at`. Support legacy state whose `agent_records` field is absent.

Before successful, graceful, or forced run clearing, `_archive_run` stores run id, objective, mode, terminal status, created/completed timestamps, and the values of `agent_records`. It must not store message bodies or worker results.

- [ ] **Step 4: Implement the read-only command**

Create `commands/agents.md` with:

```markdown
---
description: List Symphony subagents for the active run or retained run history
argument-hint: [--all]
---

<!-- SYMPHONY_CONTROL: agents -->
<!-- SYMPHONY_ARGS: $ARGUMENTS -->

Report the injected Symphony agent ledger as a compact table with run, id, status, role, model, and effort. When the host exposes a live agent-listing tool, reconcile live status and metadata before reporting. Preserve `not exposed by host`; never infer model or effort.
```

Handle `control == "agents"` before automatic run activation. Pass `include_history="--all" in args` to `_agents_context`. The formatter returns active records by default and active plus `run_history` with `--all`; it never mutates state.

- [ ] **Step 5: Update help and skill command references**

Add `/symphony:agents [--all]` to both command lists. State that the default is the active run and `--all` includes historical runs. Require the root to use a live host agent-listing tool when exposed, with the persistent ledger as recovery evidence and honest missing-value fallback.

- [ ] **Step 6: Run focused and full tests**

```bash
python3 -m unittest discover -s plugins/symphony/tests -v
claude plugin validate ./plugins/symphony
```

Expected: all tests PASS and plugin validation succeeds.

- [ ] **Step 7: Commit**

```bash
git add plugins/symphony/commands/agents.md plugins/symphony/commands/help.md plugins/symphony/scripts/symphony_hook.py plugins/symphony/skills/symphony/SKILL.md plugins/symphony/tests/test_symphony_hook.py
git commit -m "feat: add Symphony agent inspection"
```

---

### Task 5: Document retention, privacy, fallback, and the skipped version

**Files:**
- Modify: `README.md`
- Modify: `plugins/symphony/commands/help.md`
- Modify: `plugins/symphony/tests/test_symphony_hook.py`
- Modify: `plugins/symphony/.claude-plugin/plugin.json`
- Modify: `plugins/symphony/.codex-plugin/plugin.json`
- Modify: `docs/superpowers/specs/2026-09-15-document-memory-design.md`

**Interfaces:**
- Produces: user-facing memory location, activation, retention, deletion, privacy, and indexing consequences.
- Produces: matching `0.14.0` versions in both plugin manifests.
- Preserves: marketplace manifests without unsupported version fields.

- [ ] **Step 1: Write failing documentation/version assertions**

Add to `HookDeclarationTests`:

```python
def test_documentation_and_manifests_describe_memory_release(self):
    readme = (PLUGIN_ROOT.parents[1] / "README.md").read_text(encoding="utf-8")
    help_text = (PLUGIN_ROOT / "commands" / "help.md").read_text(encoding="utf-8")
    for text in (readme, help_text):
        self.assertIn(".symphony/memory/current.md", text)
        self.assertIn("codebase-memory-mcp", text)
    versions = {
        json.loads((PLUGIN_ROOT / relative).read_text(encoding="utf-8"))["version"]
        for relative in (".claude-plugin/plugin.json", ".codex-plugin/plugin.json")
    }
    self.assertEqual({"0.14.0"}, versions)
```

- [ ] **Step 2: Run the test and verify RED**

Run the new test. Expected: FAIL because memory documentation is absent and versions are `0.12.0`.

- [ ] **Step 3: Update README and help**

Document:

- extended memory is conditional on verified codebase-memory-mcp and healthy indexing;
- current state is read directly and history is queried through the MCP graph/search tools;
- `.symphony/memory/` is project-local, not automatically ignored, committed, or deleted;
- `/symphony:disable` and force-stop preserve memory;
- manual deletion disables historical recall until recreated;
- no secrets, raw environment values, full transcripts, or copied source bodies belong there;
- hooks cannot call MCP themselves, so activation is a strong-lead capability receipt enforced by file/receipt checks.

Update repository layout to include the runtime-created `.symphony/memory/` directory.

- [ ] **Step 4: Bump both plugin manifests to 0.14.0**

Change only the `version` field in:

```text
plugins/symphony/.claude-plugin/plugin.json
plugins/symphony/.codex-plugin/plugin.json
```

Do not modify `.claude-plugin/marketplace.json` or `.agents/plugins/marketplace.json`; their schemas do not contain plugin versions.

- [ ] **Step 5: Run the focused and full tests**

```bash
python3 -m unittest plugins.symphony.tests.test_symphony_hook.HookDeclarationTests.test_documentation_and_manifests_describe_memory_release -v
python3 -m unittest discover -s plugins/symphony/tests -v
```

Expected: PASS.

- [ ] **Step 6: Commit the release-facing change**

```bash
git add README.md plugins/symphony/commands/help.md plugins/symphony/tests/test_symphony_hook.py plugins/symphony/.claude-plugin/plugin.json plugins/symphony/.codex-plugin/plugin.json docs/superpowers/specs/2026-09-15-document-memory-design.md
git commit -m "docs: release indexed memory in v0.14.0"
```

---

### Task 6: Verify, review, push, and observe release completion

**Files:**
- Modify only if verification or review finds an in-scope defect.

**Interfaces:**
- Consumes: all preceding tasks.
- Produces: clean validation evidence, pushed `main`, successful hosted eval, and GitHub release `v0.14.0`.

- [ ] **Step 1: Run authoritative local verification**

```bash
python3 -m unittest discover -s plugins/symphony/tests -v
python3 -m py_compile plugins/symphony/scripts/symphony_hook.py
claude plugin validate ./plugins/symphony
git diff --check
```

Expected: all tests PASS, compilation succeeds, plugin validation passes, and diff check is silent.

- [ ] **Step 2: Review the complete change against the spec**

Use `superpowers:requesting-code-review` or `compound-engineering:ce-code-review` against the merge base before claiming completion. Resolve every Critical or Important in-scope finding with a failing test first, rerun the full verification command, and commit the fix.

- [ ] **Step 3: Confirm release state before pushing**

```bash
git status --short
git log --oneline -8
python3 - <<'PY'
import json
from pathlib import Path
for path in (
    Path("plugins/symphony/.claude-plugin/plugin.json"),
    Path("plugins/symphony/.codex-plugin/plugin.json"),
):
    print(path, json.loads(path.read_text(encoding="utf-8"))["version"])
PY
```

Expected: only intended changes, recent task commits present, and both versions print `0.14.0`.

- [ ] **Step 4: Push the approved implementation**

```bash
git push origin main
```

- [ ] **Step 5: Observe CI to a terminal result**

Find the run for the pushed SHA and watch it:

```bash
gh run list --commit "$(git rev-parse HEAD)" --limit 5
gh run watch <run-id> --exit-status --interval 10
```

If CI fails, download `plugin-eval-results`, diagnose the specific lifecycle or grader failure, add a regression test, fix it, recommit, push, and observe the replacement run.

- [ ] **Step 6: Verify the release and clean worktree**

```bash
gh release view v0.14.0 --json tagName,name,url,targetCommitish,publishedAt
git status --short
```

Expected: release `v0.14.0` targets the final pushed SHA and `git status --short` is empty.
