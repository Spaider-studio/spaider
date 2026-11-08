#!/usr/bin/env python3
"""Copy the compose stack into the CLI package. --check fails if it has drifted."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = ("docker-compose.yml", ".env.example", "gateway/kong.yml")
TARGET = ROOT / "cli/src/spaider_cli/stack"


def main() -> int:
    check = "--check" in sys.argv
    stale = []
    for name in FILES:
        source, destination = ROOT / name, TARGET / name
        if not destination.exists() or source.read_bytes() != destination.read_bytes():
            stale.append(name)
            if not check:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
    if check and stale:
        print(f"Out of sync: {', '.join(stale)}. Run scripts/sync_cli_stack.py.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
