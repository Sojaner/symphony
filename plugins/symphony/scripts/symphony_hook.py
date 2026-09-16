#!/usr/bin/env python3
"""Symphony hook entry point."""

from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from symphony.runtime import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
