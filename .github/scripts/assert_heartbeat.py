#!/usr/bin/env python3
"""Assert that an installed Symphony hook actually executed in a real session.

The model's reply is not evidence: it paraphrases, so grepping its prose either
passes on almost anything or fails on wording. The heartbeat is the contract's
own proof of guarded execution, so that is what the real-provider smoke checks.
"""

import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from plugins.symphony.symphony import HOOK_SCHEMA_VERSION


def main() -> int:
    provider = sys.argv[1]
    root = pathlib.Path(os.environ["SYMPHONY_STATE_DIR"])
    expected = json.loads(
        pathlib.Path("plugins/symphony/.claude-plugin/plugin.json").read_text()
    )["version"]

    documents = []
    for path in sorted(root.rglob("*.json")):
        try:
            documents.append(json.loads(path.read_text()))
        except (OSError, json.JSONDecodeError):
            continue

    for document in documents:
        activation = document.get("activation", {}).get(provider, {})
        if activation.get("state") != "guarded":
            continue
        if activation.get("plugin_version") != expected:
            print(
                f"::error::{provider} heartbeat reports "
                f"{activation.get('plugin_version')!r}, expected {expected!r}"
            )
            return 1
        if not activation.get("session_id"):
            print(f"::error::{provider} heartbeat has no session ID")
            return 1
        if activation.get("hook_schema_version") != HOOK_SCHEMA_VERSION:
            print(f"::error::{provider} heartbeat has an unexpected hook schema")
            return 1
        expected_root = os.environ.get("SYMPHONY_EXPECTED_PLUGIN_ROOT")
        if expected_root and pathlib.Path(activation.get("plugin_root", "")).resolve() != pathlib.Path(expected_root).resolve():
            print(f"::error::{provider} heartbeat did not come from the installed plugin root")
            return 1
        print(f"{provider}: installed hook executed and recorded a guarded heartbeat")
        return 0

    listing = [str(path) for path in root.rglob("*")] or ["<empty>"]
    print(f"::error::no guarded {provider} heartbeat under {root}: {listing}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
