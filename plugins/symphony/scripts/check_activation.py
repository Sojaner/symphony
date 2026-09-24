#!/usr/bin/env python3
"""Read-only proof that this Codex session ran the loaded Symphony hook."""

import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from symphony import HOOK_SCHEMA_VERSION, PLUGIN_VERSION  # noqa: E402
from symphony.store import project_key  # noqa: E402


def main() -> int:
    session = os.environ.get("CODEX_SESSION_ID")
    if not session:
        print("pending verification: Codex session ID is unavailable")
        return 1

    root = Path(__file__).resolve().parents[1]
    state_dir = Path(os.environ.get("SYMPHONY_STATE_DIR", Path.home() / ".symphony" / "state"))
    path = state_dir / f"{project_key(Path.cwd())}.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        activation = document["activation"]["codex"]
    except (OSError, ValueError, KeyError, TypeError):
        print("pending verification: no readable Codex heartbeat for this project")
        return 1

    def matches(record):
        return (
            isinstance(record, dict)
            and record.get("session_id") == session
            and record.get("plugin_version") == PLUGIN_VERSION
            and record.get("plugin_root") == str(root)
            and record.get("hook_schema_version") == HOOK_SCHEMA_VERSION
            and bool(record.get("observed_at"))
        )

    # Another live session can replace the provider's latest activation slot.
    # ponytail: history keeps 200 events; persist per-session heartbeats if collisions outlive it.
    historical = (
        {**event["payload"], "observed_at": event.get("observed_at")}
        for event in document.get("event_history", ())
        if isinstance(event, dict) and event.get("kind") == "session_heartbeat"
        and isinstance(event.get("payload"), dict)
        and event["payload"].get("provider") == "codex"
    )
    if (isinstance(activation, dict) and activation.get("state") == "guarded" and matches(activation)) or any(map(matches, historical)):
        print("guarded: matching current-session heartbeat")
        return 0

    print("pending verification: heartbeat does not match this session and loaded plugin")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
