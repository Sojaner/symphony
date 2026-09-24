#!/usr/bin/env python3
"""Symphony hook entry point."""

from pathlib import Path
import io
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from symphony.runtime import main  # noqa: E402


if __name__ == "__main__":
    # Windows PowerShell's redirected child stdin prepends a UTF-8 BOM.
    sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8-sig")
    raise SystemExit(main())
