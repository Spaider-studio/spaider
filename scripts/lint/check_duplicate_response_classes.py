#!/usr/bin/env python3
"""
Lint hook: flag duplicate ``*Response`` / ``*Request`` Pydantic class names
inside ``backend/app/``.

Why
---
Python's "last-definition-wins" semantics silently shadow earlier definitions
when the same class name is declared twice in different modules. During a
rebase merge, two ``RotateKeyResponse`` classes ended up coexisting
(one with the `success` field defaulted, one without). The shadow caused
HTTP 422 ``message: Field required`` at runtime — debugged manually.

This hook makes that whole class of bug a fail-closed gate.

Usage
-----
Pre-commit:
    pre-commit run check-duplicate-response-classes

Direct:
    python scripts/lint/check_duplicate_response_classes.py

Exit codes
----------
0 — no duplicates.
1 — duplicates found; details written to stderr.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_ROOT = REPO_ROOT / "backend" / "app"

# Match Pydantic-style schema names. Only Request/Response suffixes — keeps
# false-positives down (we don't care if two different modules each declare
# a `Counter` class, only the API-surface duplicates).
_PATTERN = re.compile(r"^[A-Z][A-Za-z0-9]+(?:Request|Response)$")


def main() -> int:
    seen: dict[str, list[str]] = {}
    for py_file in SCAN_ROOT.rglob("*.py"):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except SyntaxError:
            # Don't fail the whole hook on an in-progress edit.
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and _PATTERN.match(node.name):
                rel = py_file.relative_to(REPO_ROOT)
                seen.setdefault(node.name, []).append(f"{rel}:{node.lineno}")

    duplicates = {name: paths for name, paths in seen.items() if len(paths) > 1}
    if not duplicates:
        return 0

    print(
        "Duplicate Pydantic Request/Response class names detected.",
        file=sys.stderr,
    )
    print(
        "Python keeps only the last definition, silently shadowing the others.",
        file=sys.stderr,
    )
    print("Rename, consolidate, or remove the redundant declaration:", file=sys.stderr)
    print("", file=sys.stderr)
    for name, paths in sorted(duplicates.items()):
        print(f"  {name}:", file=sys.stderr)
        for p in paths:
            print(f"    - {p}", file=sys.stderr)
    print("", file=sys.stderr)
    print(
        "Reference: `RotateKeyResponse` was duplicated and "
        "the wrong shape won at import time, causing HTTP 422 at runtime.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
