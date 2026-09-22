#!/usr/bin/env python3
"""Keep the shipped capability profiles current without a maintainer in the loop.

Neither host lets a hook discover models, so the tier-to-model map is a build
artifact. This keeps that artifact honest: it reads each provider's own roster,
rewrites the profiles, and refuses to ship a map naming a model the provider
will not accept.

    refresh_profiles.py --probe    # rewrite profiles.json from the local roster
    refresh_profiles.py --verify   # reject any model the provider rejects

Codex is the only provider whose identifiers churn. Claude's tiers are aliases
that outlive model generations, so nothing here rewrites them.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
PROFILES = ROOT / "profiles.json"

# Ordered weakest to strongest. A roster entry not named here is not routed to:
# a new model is a deliberate decision, not something a cron job makes.
CODEX_RANK = ("gpt-5.5", "gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol", "gpt-6-astra")
CODEX_EFFORTS = ("none", "low", "medium", "high", "xhigh", "max")


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
                    for item in models if not item.get("hidden") and item.get("model")
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
    # The CLI roster has exposed preview-only values which the provider rejects
    # for normal Codex calls. Ship only the published API vocabulary.
    return [effort for effort in found if effort in CODEX_EFFORTS] or ["low", "medium", "high"]


def codex_profiles(roster: list[dict], current: list[dict]) -> list[dict]:
    """Keep the curated tier assignments, substituting only what vanished.

    Which model belongs at which tier is a cost-versus-capability judgement, so
    this never reassigns a tier whose model the account can still see. It reacts
    to one provider fact only: a model the profiles name is no longer offered.
    Substitution walks down the rank first, because quietly promoting a tier
    would raise what the user pays without anyone deciding to.
    """
    available = {entry["slug"]: entry for entry in roster}
    ranked = [slug for slug in CODEX_RANK if slug in available]
    if not ranked:
        raise SystemExit(
            f"::error::Codex roster names none of the models Symphony knows: {sorted(available)}"
        )

    def substitute(preferred: str) -> str:
        if preferred in available:
            return preferred
        if preferred not in CODEX_RANK:
            return ranked[-1]
        position = CODEX_RANK.index(preferred)
        weaker = [slug for slug in ranked if CODEX_RANK.index(slug) < position]
        return weaker[-1] if weaker else ranked[0]

    profiles = []
    for profile in current:
        tiers = {tier: substitute(model) for tier, model in profile["tiers"].items()}
        resolved = {
            "id": profile["id"],
            "tiers": tiers,
            "efforts": {
                model: efforts_of(available[model])
                for model in dict.fromkeys(tiers.values())
                if model in available
            },
        }
        if profile.get("requires_all") is not None:
            resolved["requires_all"] = sorted(set(tiers.values())) if profile["requires_all"] else []
        if profile.get("requires_any") is not None:
            resolved["requires_any"] = list(profile["requires_any"])
        profiles.append(resolved)
    return profiles


def probe(home: Path) -> bool:
    document = json.loads(PROFILES.read_text(encoding="utf-8"))
    current = document["providers"]["codex"]["profiles"]
    updated = codex_profiles(codex_roster(home), current)
    if document["providers"]["codex"]["profiles"] == updated:
        print("profiles already match the provider roster")
        return False
    document["providers"]["codex"]["profiles"] = updated
    document["generated_at"] = os.environ.get("REFRESH_DATE") or _today()
    PROFILES.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print("profiles updated from the provider roster")
    return True


def _today() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).date().isoformat()


def verify() -> int:
    """Reject any model in the shipped map that its provider will not accept."""
    document = json.loads(PROFILES.read_text(encoding="utf-8"))
    failures: list[str] = []
    for provider, block in document["providers"].items():
        models = sorted(
            {model for profile in block["profiles"] for model in profile["tiers"].values()}
        )
        for model in models:
            accepted, detail = _accepts(provider, model)
            status = "ok" if accepted else "REJECTED"
            print(f"{provider} {model}: {status} {detail}".rstrip())
            if not accepted:
                failures.append(f"{provider} {model}: {detail}")
    if failures:
        for failure in failures:
            print(f"::error::shipped profile names a model the provider rejects: {failure}")
        return 1
    print(f"every model in the shipped profiles is accepted by its provider")
    return 0


def _accepts(provider: str, model: str) -> tuple[bool, str]:
    """One minimal call whose only question is whether the name resolves."""
    if provider == "codex":
        argv = ["codex", "exec", "--model", model, "--skip-git-repo-check", "reply with ok"]
    else:
        argv = ["claude", "--print", "--model", model, "--output-format", "text", "reply with ok"]
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
    parser.add_argument("--probe", action="store_true", help="rewrite profiles from the roster")
    parser.add_argument("--verify", action="store_true", help="check every shipped model resolves")
    parser.add_argument(
        "--codex-home",
        type=Path,
        default=Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex"),
    )
    args = parser.parse_args()
    if args.probe:
        changed = probe(args.codex_home)
        output = os.environ.get("GITHUB_OUTPUT")
        if output:
            Path(output).open("a").write(f"changed={'true' if changed else 'false'}\n")
    if args.verify:
        return verify()
    return 0


if __name__ == "__main__":
    sys.exit(main())
