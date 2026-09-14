# Symphony

Symphony is a Codex plugin for complicated projects. It lets a lightweight root model coordinate stronger specialist models without asking the root model to solve every hard problem itself.

The root agent remains responsible for scope, integration, verification, and communication. Symphony adds a reusable **conductor** subagent that recommends:

- how to split the project into independent units;
- which available model and reasoning effort should handle each unit;
- which units can run in parallel;
- when a result requires replanning or escalation;
- which model should perform the final review.

Workers receive narrow objectives, explicit ownership, constraints, acceptance checks, and a required return format. Expensive reasoning is applied to the smallest unresolved question; settled implementation work goes to cheaper coding agents.

## Install in Codex

Add this repository as a Git marketplace:

```bash
codex plugin marketplace add Sojaner/symphony
```

Install the plugin:

```bash
codex plugin add symphony@symphony
```

Start a new Codex task so its skill catalog includes the plugin, then ask:

```text
Use Symphony to orchestrate this complex project: <your project>
```

Symphony requires Codex collaboration tools with subagent support. It reads the live model and reasoning-effort catalog instead of assuming every Codex host offers the same models.

## Update

Refresh the marketplace and reinstall the plugin:

```bash
codex plugin marketplace upgrade symphony
codex plugin add symphony@symphony
```

Start a new task after updating.

## How routing works

For each complex project, Symphony creates one reusable conductor using the strongest suitable general reasoning model available. The conductor stays idle between decision gates and is consulted again through follow-up tasks. Independent workers can then run in parallel using different models and effort levels.

The bundled [model-routing index](plugins/symphony/skills/symphony/references/model-routing.md) summarizes current routing principles and links to official OpenAI guidance. The live Codex tool schema remains authoritative for model availability and supported effort levels.

Symphony uses Codex's native collaboration tools. It has no MCP server, background service, credentials, or external runtime.

## Repository layout

```text
.agents/plugins/marketplace.json       Git marketplace manifest
plugins/symphony/.codex-plugin/        Plugin manifest
plugins/symphony/skills/symphony/      Orchestration skill and routing index
```

## References

- [OpenAI model guidance](https://developers.openai.com/api/docs/guides/latest-model)
- [OpenAI reasoning guide](https://developers.openai.com/api/docs/guides/reasoning)
