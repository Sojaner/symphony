#!/usr/bin/env python3
"""Symphony hook entry point; runtime behavior is added in a later unit."""

import json
import sys


def main() -> int:
    try:
        json.load(sys.stdin)
    except (OSError, ValueError):
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
