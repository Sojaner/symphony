#!/usr/bin/env python3
"""Symphony hook entry point."""

from pathlib import Path
import io
import os
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from symphony.runtime import main  # noqa: E402


if __name__ == "__main__":
    if diagnostic := os.environ.get("SYMPHONY_DIAGNOSTIC_INPUT"):
        payload_bytes = sys.stdin.buffer.read()
        Path(diagnostic).write_text(
            f"{len(payload_bytes)} {payload_bytes[:16].hex()} {payload_bytes[-16:].hex()}",
            encoding="ascii",
        )
        sys.stdin = io.TextIOWrapper(io.BytesIO(payload_bytes), encoding="utf-8")
    raise SystemExit(main())
