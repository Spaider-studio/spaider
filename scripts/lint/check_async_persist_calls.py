#!/usr/bin/env python3
"""
Lint hook: any ``async def`` handler in ``backend/app/api/v1/ingest.py``
that calls ``_get_run_state(...)`` must also call ``_save_run_state(...)``.

Why
---
Connector handlers mutate ``run_state`` while yielding records (URL stores
ETags, MCP stores content hashes, SQL advances cursors). If the handler
forgets the matching ``await _save_run_state(...)`` before returning, those
mutations are dropped — the next request hits a cold cache and re-fetches
everything. The Phase 2a review caught one of these manually; this hook
turns the next one into a pre-commit failure.

Scope is intentionally limited to ``ingest.py`` because that's where the
``_get_run_state`` / ``_save_run_state`` pair lives. If the helpers ever
move, update the path below.

Usage
-----
Pre-commit:
    pre-commit run check-async-persist-paired

Direct:
    python scripts/lint/check_async_persist_calls.py

Exit codes
----------
0 — every handler is paired correctly.
1 — at least one handler misses ``_save_run_state``; details on stderr.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TARGET = REPO_ROOT / "backend" / "app" / "api" / "v1" / "ingest.py"


class _CallNameVisitor(ast.NodeVisitor):
    """Collect every callable name that appears in the function body —
    plain calls (``foo()``) and attribute calls (``self.foo()``)."""

    def __init__(self) -> None:
        self.names: set[str] = set()

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802 — ast API
        target = node.func
        if isinstance(target, ast.Name):
            self.names.add(target.id)
        elif isinstance(target, ast.Attribute):
            self.names.add(target.attr)
        self.generic_visit(node)


def main() -> int:
    if not TARGET.exists():
        # File was renamed/deleted — let CI surface that elsewhere.
        return 0

    tree = ast.parse(TARGET.read_text(encoding="utf-8"))
    offenders: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        visitor = _CallNameVisitor()
        visitor.visit(node)
        if "_get_run_state" in visitor.names and "_save_run_state" not in visitor.names:
            rel = TARGET.relative_to(REPO_ROOT)
            offenders.append(f"  {rel}:{node.lineno}  async def {node.name}(...)")

    if not offenders:
        return 0

    print(
        "Endpoint handlers call _get_run_state but never _save_run_state.",
        file=sys.stderr,
    )
    print(
        "Mutations to RunState (ETags, cursors, hashes) are dropped on return:",
        file=sys.stderr,
    )
    print("", file=sys.stderr)
    for line in offenders:
        print(line, file=sys.stderr)
    print("", file=sys.stderr)
    print(
        "Fix: add `await _save_run_state(connector_id, agent_id, run_state)` "
        "before each return path.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
