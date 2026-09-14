<p align="center">
  <img src="plugins/symphony/assets/logo.png" alt="Symphony logo" width="220">
</p>

# Symphony

Symphony is a plugin for Codex and Claude Code aimed at complicated projects. It runs a project the way a capable administrator does: the coordinator does not need deep knowledge of every area of the project. It consults an expert about what needs to be done and who should do it, then delegates the right task to the right model.

A lightweight root agent stays in charge of scope, integration, verification, and communication. A reusable **conductor** subagent acts as its senior consultant and recommends:

- how to split the project into independent units;
- which available model and reasoning effort should handle each unit;
- which units can run in parallel;
- when a result requires replanning or escalation;
- which model should perform the final review.

Workers receive narrow objectives, explicit ownership, constraints, acceptance checks, and a required return format.

## Why this shape works

Sending each task to the model that fits it changes how the whole project behaves, not just what it costs:

- **Less overthinking.** Senior reasoning models are consulted only on the narrow, hard questions — decomposition, routing, diagnosis, review. Their deep reasoning is never burned on mechanical work, and cheap models are never left to wrestle with problems above their weight.
- **Less overdoing.** Every worker gets a bounded objective with acceptance checks, so a settled unit ends when its check passes instead of growing extra scope.
- **Steadier progress.** The project advances through explicit decision gates and independently verifiable units. Long runs stay on course logically instead of drifting, because replanning happens at the gates rather than mid-task.
- **Right economics as a consequence.** Expensive reasoning is spent on the smallest unresolved question, while the heavy lifting goes to hardworking workhorse models whose longer runs justify their time. The token savings fall out of correct delegation, not corner-cutting.

## Install in Codex

Add this repository as a Git marketplace:

```bash
codex plugin marketplace add Sojaner/symphony
```

Install the plugin:

```bash
codex plugin add symphony@symphony
```

## Install in Claude Code

Add this repository as a plugin marketplace:

```text
/plugin marketplace add Sojaner/symphony
```

Install the plugin:

```text
/plugin install symphony@symphony
```

## Use

Start a new task so the skill catalog includes the plugin. Select the task's model and effort in the host UI first: use the cheapest available model that supports spawning subagents with model overrides, at `low` effort for straightforward routing or `medium` when the root must coordinate a longer or less settled project. Then ask:

```text
Orchestrator: <exact model id>
Effort: <low|medium>

Use Symphony to orchestrate this complex project: <your project>
```

Symphony enforces this as a preflight gate. The declared profile is treated as a request to verify, not as evidence: Symphony checks it against the model and effort the task is actually running on and stops on any mismatch — it will not silently continue on a different model than the one you declared. A skill cannot change the model or effort of its already-running task, so an invalid configuration requires a new task.

Symphony reads the live model and reasoning-effort catalog of the current host instead of assuming every host offers the same models.

## Update

Codex:

```bash
codex plugin marketplace upgrade symphony
codex plugin add symphony@symphony
```

Claude Code:

```text
/plugin marketplace update symphony
```

Start a new task after updating.

## How routing works

After the orchestrator preflight passes, Symphony creates one reusable conductor using the strongest suitable general reasoning model available. The conductor stays idle between decision gates and is consulted again through follow-up tasks. Independent workers can then run in parallel using different models and effort levels.

The bundled [model-routing index](plugins/symphony/skills/symphony/references/model-routing.md) summarizes current routing principles and links to official OpenAI and Anthropic guidance. The live subagent tool schema of the current host remains authoritative for model availability and supported effort levels.

Symphony uses the host's native subagent tools — Codex collaboration tools or the Claude Code agent tool. It has no MCP server, background service, credentials, or external runtime.

## Testing

The plugin ships an eval suite for Claude Code's `claude plugin eval`. It pins the preflight gate: one case proves Symphony refuses when the declared orchestrator cannot match the runtime model, another proves it proceeds when the profile matches. Run it locally with:

```bash
claude plugin eval ./plugins/symphony --model claude-haiku-4-5-20251001
```

The `--model` value must match the orchestrator declared in `plugins/symphony/evals/match-proceeds/prompt.md`. CI runs the same suite on every push and pull request (`.github/workflows/plugin-eval.yml`; requires an `ANTHROPIC_API_KEY` repository secret).

## Repository layout

```text
.agents/plugins/marketplace.json       Codex Git marketplace manifest
.claude-plugin/marketplace.json        Claude Code marketplace manifest
plugins/symphony/.codex-plugin/        Codex plugin manifest
plugins/symphony/.claude-plugin/       Claude Code plugin manifest
plugins/symphony/assets/               Logo and icon assets
plugins/symphony/evals/                Plugin eval suite for the preflight gate
plugins/symphony/skills/symphony/      Orchestration skill and routing index (shared by both hosts)
```

## References

- [OpenAI model guidance](https://developers.openai.com/api/docs/guides/latest-model)
- [OpenAI reasoning guide](https://developers.openai.com/api/docs/guides/reasoning)
- [Anthropic models overview](https://docs.claude.com/en/docs/about-claude/models/overview)
- [Claude Code subagents](https://code.claude.com/docs/en/sub-agents)
