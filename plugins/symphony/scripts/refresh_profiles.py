#!/usr/bin/env python3
"""Keep the shipped capability profiles current without a maintainer in the loop.

Neither host lets a hook discover models, so the tier-to-model map is a build
artifact. This keeps that artifact honest: it reads each provider's own roster,
rewrites the profiles, and refuses to ship a map naming a model the provider
will not accept.

    refresh_profiles.py --verify   # reject any model the provider rejects

Codex is the only provider whose identifiers churn. Claude's tiers are aliases
that outlive model generations, so nothing here rewrites them.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import select
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import time

ROOT = Path(__file__).resolve().parents[1]
PROFILES = ROOT / "profiles.json"

# Ordered by the provider's stated price/capability positioning. Terra remains
# in the rank only to migrate old profiles; Symphony no longer selects it.
CODEX_RANK = ("gpt-5.5", "gpt-5.6-luna", "gpt-6-luna", "gpt-5.6-terra", "gpt-5.6-sol", "gpt-6-sol", "gpt-6-astra")
CODEX_RETIRED = {"gpt-5.6-terra"}
CODEX_EFFORTS = ("none", "low", "medium", "high", "xhigh", "max")
CLAUDE_EFFORTS = ("low", "medium", "high", "xhigh", "max")
sys.path.insert(0, str(ROOT))
from symphony.routing import Assessment, MATRIX as ROUTING_MATRIX, route_for  # noqa: E402

CELLS = tuple(f"{size}/{complexity}" for size, complexity in ROUTING_MATRIX)
TIER_CELLS = {
    "economy": "large/simple",
    "balanced": "medium/simple",
    "capable": "small/mixed",
    "strongest": "small/complex",
}


def _model_rank(provider: str, model: str) -> int | None:
    """Independent provider-family ordering; never trust an agent's ranking."""
    if provider == "codex":
        if model in CODEX_RANK:
            return CODEX_RANK.index(model)
        match = re.fullmatch(r"gpt-(\d+)(?:[.-].*)?", model)
        if match and int(match.group(1)) > 6:
            return len(CODEX_RANK) + int(match.group(1))
        return None
    lowered = model.lower()
    for family, rank in (("haiku", 0), ("sonnet", 1), ("opus", 2), ("fable", 3)):
        if family in lowered:
            return rank
    return None


def codex_roster(home: Path) -> list[dict]:
    """The models this account may select, including on a fresh CI home."""
    cache_path = home / "models_cache.json"
    if not cache_path.exists():
        try:
            return _app_server_roster(home)
        except (OSError, ValueError, RuntimeError) as error:
            raise SystemExit(f"::error::no readable Codex model roster at {home}: {error}")
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise SystemExit(f"::error::no readable Codex model roster at {home}: {error}")
    return [
        item
        for item in cache.get("models", ())
        if isinstance(item, dict) and item.get("visibility") == "list" and item.get("slug")
        and item["slug"] not in CODEX_RETIRED
    ]


def _app_server_roster(home: Path) -> list[dict]:
    """Ask Codex for its live picker roster; `codex exec` need not write a cache."""
    process = subprocess.Popen(
        ["codex", "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, env={**os.environ, "CODEX_HOME": str(home)},
    )
    try:
        def send(message: dict) -> None:
            process.stdin.write((json.dumps(message) + "\n").encode())
            process.stdin.flush()

        send({"method": "initialize", "id": 1, "params": {
            "clientInfo": {"name": "symphony_refresh", "title": "Symphony Refresh", "version": "1.0.0"}
        }})
        send({"method": "initialized", "params": {}})
        send({"method": "model/list", "id": 2, "params": {"limit": 100, "includeHidden": False}})
        deadline = time.monotonic() + 30
        pending = b""
        models = []
        while time.monotonic() < deadline:
            if not select.select([process.stdout], [], [], max(0, deadline - time.monotonic()))[0]:
                break
            chunk = os.read(process.stdout.fileno(), 65536)
            if not chunk:
                break
            pending += chunk
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                response = json.loads(line)
                if response.get("id") != 2:
                    continue
                if "error" in response:
                    raise RuntimeError(response["error"].get("message", "model/list failed"))
                result = response["result"]
                models.extend(result["data"])
                cursor = result.get("nextCursor")
                if cursor:
                    send({"method": "model/list", "id": 2, "params": {
                        "limit": 100, "includeHidden": False, "cursor": cursor
                    }})
                    continue
                return [
                    {
                        "slug": item["model"], "visibility": "list",
                        "supported_reasoning_levels": [
                            {"effort": level["reasoningEffort"]}
                            for level in item.get("supportedReasoningEfforts", ())
                        ],
                    }
                    for item in models
                    if not item.get("hidden") and item.get("model")
                    and item["model"] not in CODEX_RETIRED
                ]
        raise RuntimeError("model/list did not return a complete roster within 30 seconds")
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def efforts_of(entry: dict) -> list[str]:
    levels = entry.get("supported_reasoning_levels") or ()
    found = [
        str(level.get("effort"))
        for level in levels
        if isinstance(level, dict) and level.get("effort")
    ]
    # Preserve provider order while dropping preview-only values.
    return [effort for effort in CODEX_EFFORTS if effort in found]


def claude_roster() -> list[dict]:
    """Read the model IDs this Anthropic API key can select."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise SystemExit("::error::ANTHROPIC_API_KEY is required to read the model roster")
    url = "https://api.anthropic.com/v1/models?limit=100"
    models = []
    while url:
        request = urllib.request.Request(url, headers={
            "x-api-key": key, "anthropic-version": "2023-06-01",
        })
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                page = json.load(response)
        except (OSError, ValueError) as error:
            raise SystemExit(f"::error::could not read Claude model roster: {error}")
        models.extend(page.get("data", ()))
        last_id = page.get("last_id")
        url = (
            "https://api.anthropic.com/v1/models?limit=100&after_id="
            + urllib.parse.quote(str(last_id), safe="")
            if page.get("has_more") and last_id else ""
        )
    return [item for item in models if isinstance(item, dict) and item.get("id")]


def _agent_prompt(provider: str, current: list[dict], roster: list[dict]) -> str:
    floor = current[-1].get("matrix", {})
    return """Choose this provider's model/effort grid for Symphony. Optimize outcome quality against token and latency cost: use the cheapest adequate model and effort for routine cells, reserve stronger models and higher effort for work whose size/complexity benefits from them. The grid must be monotonic: complexity never lowers model capability or effort; increasing task size never raises model cost or effort. For the full entitlement profile, each cell must be at least as capable and effortful as its fallback profile.

The last profile is also the runtime safety floor for users whose entitlements cannot be detected. Do not promote any fallback cell above the model capability/family of its currently shipped floor; those users may not have access to gated models. You may still choose each fallback cell's effort semantically. Current fallback model selections: """ + json.dumps({
        cell: choice.get("model")
        for cell, choice in floor.items()
        if isinstance(choice, dict) and "model" in choice
    }) + """

Do not change the profile IDs or entitlement gates. Use only model IDs in the supplied roster. For every selected model, list its supported effort levels in provider-supported order. Return exactly one JSON object and no markdown or extra text with this shape:
{"model_order":["least costly model", "...", "most capable model"],"model_efforts":{"model-id":["low","medium"]},"profiles":[{"id":"full","matrix":{"small/simple":{"model":"model-id","effort":"medium"},...}}],"rationale":"brief basis for the tradeoffs"}

Every profile must contain all nine matrix cells: small/simple, small/mixed, small/complex, medium/simple, medium/mixed, medium/complex, large/simple, large/mixed, large/complex. Every effort must be listed in that model's model_efforts entry. Return only this provider's decision; do not edit files or run commands.

Provider: """ + provider + "\nCurrent profile IDs/gates: " + json.dumps([
        {key: profile[key] for key in ("id", "requires_all", "requires_any") if key in profile}
        for profile in current
    ]) + "\nCurrent routing matrix: " + json.dumps(list(CELLS)) + "\nProvider model roster: " + json.dumps(roster)


def _run_provider_agent(provider: str, current: list[dict], roster: list[dict]) -> dict:
    prompt = _agent_prompt(provider, current, roster)
    if provider == "codex":
        available = {entry["slug"]: entry for entry in roster}
        ranked = sorted(
            (name for name in available if name not in CODEX_RETIRED and _model_rank(provider, name) is not None),
            key=lambda name: _model_rank(provider, name),
        )
        if not ranked:
            raise SystemExit("::error::no supported Codex model can run the refresh agent")
        model = ranked[-1]
        effort = next((item for item in reversed(CODEX_EFFORTS) if item in efforts_of(available[model])), None)
        if not effort:
            raise SystemExit(f"::error::no supported reasoning effort for Codex agent model {model}")
        argv = ["codex", "exec", "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only",
                "--model", model, "-c", f'model_reasoning_effort="{effort}"', "-"]
        completed = subprocess.run(argv, input=prompt, capture_output=True, text=True, timeout=900)
        print(f"Codex matrix agent: {model} at {effort} effort")
    else:
        argv = ["claude", "--bare", "--print", "--no-session-persistence", "--tools", "",
                "--model", "fable", "--effort", "max", "--output-format", "text", prompt]
        completed = subprocess.run(argv, capture_output=True, text=True, timeout=900)
        print("Claude matrix agent: fable at max effort")
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip().splitlines()
        raise SystemExit(f"::error::{provider} matrix agent failed: {detail[-1][:300] if detail else completed.returncode}")
    try:
        result = json.loads(completed.stdout)
    except (TypeError, ValueError) as error:
        raise SystemExit(f"::error::{provider} matrix agent did not return plain JSON: {error}")
    return validate_matrix(provider, result, current, roster)


def validate_matrix(provider: str, result: dict, current: list[dict], roster: list[dict]) -> dict:
    """Reject provider-agent output unless every route is complete and safe."""
    models = ({entry["slug"]: set(efforts_of(entry)) for entry in roster
               if entry["slug"] not in CODEX_RETIRED}
              if provider == "codex" else {entry["id"]: set(CLAUDE_EFFORTS) for entry in roster})
    expected_ids = [profile["id"] for profile in current]
    if not isinstance(result, dict) or not isinstance(result.get("profiles"), list):
        raise SystemExit(f"::error::{provider} matrix output must contain a profile list")
    if any(not isinstance(profile, dict) for profile in result["profiles"]):
        raise SystemExit(f"::error::{provider} matrix profiles must be objects")
    if [p.get("id") for p in result["profiles"]] != expected_ids:
        raise SystemExit(f"::error::{provider} matrix changed profile ordering or IDs")
    model_order = result.get("model_order")
    if (not isinstance(model_order, list) or not model_order
            or any(not isinstance(model, str) for model in model_order)
            or len(set(model_order)) != len(model_order)):
        raise SystemExit(f"::error::{provider} matrix has invalid model ordering")
    if set(model_order) - set(models):
        raise SystemExit(f"::error::{provider} matrix names models outside its roster: {set(model_order) - set(models)}")
    rank = {model: _model_rank(provider, model) for model in models}
    unknown = {model for model in model_order if rank[model] is None}
    if unknown:
        raise SystemExit(f"::error::{provider} matrix uses models without a verified capability order: {sorted(unknown)}")
    declared_efforts = result.get("model_efforts", {})
    if not isinstance(declared_efforts, dict):
        raise SystemExit(f"::error::{provider} matrix effort support must be an object")
    if provider == "codex":
        # The roster is the authority for Codex effort support; the semantic
        # agent must choose a route, not restate mutable provider metadata.
        declared_efforts = {
            model: [effort for effort in CODEX_EFFORTS if effort in models[model]]
            for model in models
        }
    elif not declared_efforts:
        raise SystemExit("::error::Claude matrix must include provider-checked effort support")
    normalized_profiles = []
    used_models = set()
    for profile in result["profiles"]:
        matrix = profile.get("matrix")
        if not isinstance(matrix, dict) or set(matrix) != set(CELLS):
            raise SystemExit(f"::error::{provider} profile {profile['id']} must define exactly nine matrix cells")
        normalized = {}
        for cell in CELLS:
            choice = matrix[cell]
            if not isinstance(choice, dict):
                raise SystemExit(f"::error::{provider} {cell} choice must be an object")
            model, effort = choice.get("model"), choice.get("effort")
            if not isinstance(model, str) or not isinstance(effort, str):
                raise SystemExit(f"::error::{provider} {cell} must name a string model and effort")
            if model not in models or rank.get(model) is None:
                raise SystemExit(f"::error::{provider} {cell} model is absent from its roster/order: {model!r}")
            if effort not in models[model] or effort not in declared_efforts.get(model, ()):
                raise SystemExit(f"::error::{provider} unsupported effort {effort!r} for {model}")
            size, complexity = cell.split("/")
            risk_floor = route_for(Assessment(size, complexity, risk="high")).lead_effort
            if risk_floor not in declared_efforts.get(model, ()):
                raise SystemExit(f"::error::{provider} {model} cannot preserve the high-risk {risk_floor} effort floor at {cell}")
            used_models.add(model)
            normalized[cell] = {"model": model, "effort": effort}
        for size in ("small", "medium", "large"):
            cells = [normalized[f"{size}/{complexity}"] for complexity in ("simple", "mixed", "complex")]
            if [rank[item["model"]] for item in cells] != sorted(rank[item["model"]] for item in cells):
                raise SystemExit(f"::error::{provider} model capability must not fall as complexity increases")
            if [CODEX_EFFORTS.index(item["effort"]) for item in cells] != sorted(CODEX_EFFORTS.index(item["effort"]) for item in cells):
                raise SystemExit(f"::error::{provider} effort must not fall as complexity increases")
        for complexity in ("simple", "mixed", "complex"):
            cells = [normalized[f"{size}/{complexity}"] for size in ("small", "medium", "large")]
            if [rank[item["model"]] for item in cells] != sorted((rank[item["model"]] for item in cells), reverse=True):
                raise SystemExit(f"::error::{provider} model cost must not rise as task size increases")
            if [CODEX_EFFORTS.index(item["effort"]) for item in cells] != sorted((CODEX_EFFORTS.index(item["effort"]) for item in cells), reverse=True):
                raise SystemExit(f"::error::{provider} effort must not rise as task size increases")
        normalized_profiles.append({"id": profile["id"], "matrix": normalized})
    if provider == "codex":
        declared_efforts = {model: declared_efforts[model] for model in used_models}
    if used_models != set(model_order) or set(declared_efforts) != used_models:
        raise SystemExit(f"::error::{provider} matrix model order/efforts must cover exactly the selected models")
    trusted_order = sorted(used_models, key=lambda model: (rank[model], model))
    for model, levels in declared_efforts.items():
        if (model not in models or not isinstance(levels, list) or not levels
                or any(not isinstance(level, str) for level in levels)
                or len(set(levels)) != len(levels) or not set(levels) <= models[model]):
            raise SystemExit(f"::error::{provider} has invalid supported efforts for {model}")
        if levels != [effort for effort in CODEX_EFFORTS if effort in levels]:
            raise SystemExit(f"::error::{provider} effort support is not in provider order for {model}")
    if len(normalized_profiles) > 1:
        full, fallback = normalized_profiles[0]["matrix"], normalized_profiles[-1]["matrix"]
        shipped_fallback = current[-1].get("matrix", {})
        improved = False
        for cell in CELLS:
            if rank[full[cell]["model"]] < rank[fallback[cell]["model"]]:
                raise SystemExit(f"::error::{provider} full profile cannot use a weaker model than fallback at {cell}")
            if CODEX_EFFORTS.index(full[cell]["effort"]) < CODEX_EFFORTS.index(fallback[cell]["effort"]):
                raise SystemExit(f"::error::{provider} full profile cannot use lower effort than fallback at {cell}")
            prior_floor = shipped_fallback.get(cell)
            if prior_floor and rank[fallback[cell]["model"]] > _model_rank(provider, prior_floor["model"]):
                raise SystemExit(f"::error::{provider} fallback cannot use a more gated model at {cell}")
            improved |= rank[full[cell]["model"]] > rank[fallback[cell]["model"]] or full[cell]["effort"] != fallback[cell]["effort"]
        if not improved:
            raise SystemExit(f"::error::{provider} full profile must improve at least one cell over the fallback")
    return {"profiles": normalized_profiles, "model_order": trusted_order, "model_efforts": declared_efforts,
            "rationale": str(result.get("rationale", ""))[:600]}


def agent_probe(home: Path) -> bool:
    """Run both provider agents concurrently, then merge only validated JSON."""
    document = json.loads(PROFILES.read_text(encoding="utf-8"))
    current = document["providers"]
    codex_models = codex_roster(home)
    claude_models = claude_roster()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {
            "codex": pool.submit(_run_provider_agent, "codex", current["codex"]["profiles"], codex_models),
            "claude": pool.submit(_run_provider_agent, "claude", current["claude"]["profiles"], claude_models),
        }
        decisions = {provider: future.result() for provider, future in futures.items()}
    updated = json.loads(json.dumps(document))
    for provider, decision in decisions.items():
        profiles = {profile["id"]: profile for profile in decision["profiles"]}
        for profile in updated["providers"][provider]["profiles"]:
            matrix = profiles[profile["id"]]["matrix"]
            profile["matrix"] = matrix
            profile["tiers"] = {tier: matrix[cell]["model"] for tier, cell in TIER_CELLS.items()}
            profile["efforts"] = decision["model_efforts"]
            if profile.get("requires_all") is not None and (
                provider == "claude" or profile["id"] == "full"
            ):
                profile["requires_all"] = sorted({choice["model"] for choice in matrix.values()})
        print(f"{provider} matrix rationale: {decision['rationale'] or '(not supplied)'}")
    changed = document["providers"] != updated["providers"]
    if changed:
        updated["generated_at"] = os.environ.get("REFRESH_DATE") or _today()
        temporary = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=PROFILES.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(json.dumps(updated, indent=2) + "\n")
            temporary.replace(PROFILES)
        finally:
            if temporary and temporary.exists():
                temporary.unlink()
    print("provider matrices updated" if changed else "provider matrices already match")
    return changed


def _today() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).date().isoformat()


def verify() -> int:
    """Reject any shipped model/effort selection the provider will not accept."""
    document = json.loads(PROFILES.read_text(encoding="utf-8"))
    failures: list[str] = []
    for provider, block in document["providers"].items():
        models = {choice["model"] for profile in block["profiles"] for choice in profile.get("matrix", {}).values()}
        declared = {model: efforts_by_model(block["profiles"], model) for model in models}
        if provider == "codex":
            roster = {entry["slug"]: efforts_of(entry) for entry in codex_roster(Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex"))}
            supported = {model: roster.get(model, []) for model in models}
            for model in models:
                if supported[model] != declared[model]:
                    failures.append(f"Codex {model}: declared effort support differs from provider roster")
        else:
            supported = {}
            for model in sorted(models):
                supported[model] = []
                for effort in CLAUDE_EFFORTS:
                    accepted, detail = _accepts(provider, model, effort)
                    if accepted:
                        supported[model].append(effort)
                    print(f"{provider} {model} {effort}: {'ok' if accepted else 'unsupported'} {detail}".rstrip())
                if supported[model] != declared[model]:
                    failures.append(f"Claude {model}: declared effort support differs from provider checks")
        for profile in block["profiles"]:
            for cell, choice in profile.get("matrix", {}).items():
                if choice["effort"] not in supported.get(choice["model"], []):
                    failures.append(f"{provider} {cell}: selected effort is unsupported for {choice['model']}")
        for model in sorted(models):
            if provider == "codex":
                accepted, detail = _accepts(provider, model)
                print(f"{provider} {model}: {'ok' if accepted else 'REJECTED'} {detail}".rstrip())
                if not accepted:
                    failures.append(f"{provider} {model}: {detail}")
    if failures:
        for failure in failures:
            print(f"::error::shipped profile names a model the provider rejects: {failure}")
        return 1
    print(f"every model in the shipped profiles is accepted by its provider")
    return 0


def efforts_by_model(profiles: list[dict], model: str) -> list[str]:
    return next((profile.get("efforts", {}).get(model, []) for profile in profiles if model in profile.get("efforts", {})), [])


def _accepts(provider: str, model: str, effort: str = "") -> tuple[bool, str]:
    """A minimal provider call verifies the model and selected effort."""
    if provider == "codex":
        argv = ["codex", "exec", "--ephemeral", "--model", model, "--skip-git-repo-check"]
        if effort:
            argv.extend(["-c", f'model_reasoning_effort="{effort}"'])
        argv.append("reply with ok")
    else:
        argv = ["claude", "--bare", "--print", "--no-session-persistence", "--tools", "",
                "--model", model, "--output-format", "text"]
        if effort:
            argv.extend(["--effort", effort])
        argv.append("reply with ok")
    try:
        completed = subprocess.run(argv, capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.SubprocessError) as error:
        return False, f"could not run {argv[0]}: {error}"
    if completed.returncode == 0:
        return True, ""
    detail = (completed.stderr or completed.stdout).strip().splitlines()
    return False, detail[-1][:200] if detail else f"exit {completed.returncode}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-probe", action="store_true", help="run provider matrix agents in parallel")
    parser.add_argument("--verify", action="store_true", help="check every shipped model resolves")
    parser.add_argument(
        "--codex-home",
        type=Path,
        default=Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex"),
    )
    args = parser.parse_args()
    if args.agent_probe:
        changed = agent_probe(args.codex_home)
        output = os.environ.get("GITHUB_OUTPUT")
        if output:
            with Path(output).open("a", encoding="utf-8") as stream:
                stream.write(f"changed={'true' if changed else 'false'}\n")
    if args.verify:
        return verify()
    return 0


if __name__ == "__main__":
    sys.exit(main())
