#!/usr/bin/env python3
"""Check that the docs mention only routes and CLI commands that exist.

--require-cli also loads the CLI and checks `spaider <command>` mentions; without
it those are skipped, for environments that do not have the CLI installed.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "sdk/python/contract/openapi.json"
_ROUTE = re.compile(r"`(?:GET |POST |PUT |PATCH |DELETE )?(/api/v1/[A-Za-z0-9_{}\-/.:<>]*)`")
_COMMAND = re.compile(r"`spaider ([a-z][a-z0-9-]*)")
_SKIP = {"docs/api.md", "CHANGELOG.md"}
_NOT_COMMANDS = {"cli", "edge", "ros2", "client"}


def documents() -> list[Path]:
    found = [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]
    tracked = [path for path in found if "cto-onboarding" not in path.parts and "internal" not in path.parts]
    return [path for path in tracked if str(path.relative_to(ROOT)) not in _SKIP and path.exists()]


def _pattern(route: str) -> re.Pattern:
    return re.compile("^" + re.sub(r"\{[^}]+\}", r"[^/]+", route.rstrip("/")) + "/?$")


def _known(mention: str, patterns: list[re.Pattern]) -> bool:
    candidate = re.sub(r"<[^>]+>", "x", mention).rstrip("/")
    candidate = re.sub(r"\{[^}]+\}", "x", candidate)
    if candidate.startswith("/api/v1/mcp"):
        return True
    return any(pattern.match(candidate) for pattern in patterns)


def cli_commands() -> set[str]:
    sys.path.insert(0, str(ROOT / "cli/src"))
    import typer.main

    from spaider_cli.main import app

    group = typer.main.get_command(app)
    return set(group.commands)


def main() -> int:
    routes = json.loads(SNAPSHOT.read_text(encoding="utf-8"))["paths"]
    patterns = [_pattern(route) for route in routes]
    commands = cli_commands() if "--require-cli" in sys.argv else None
    problems = []
    for path in documents():
        text = path.read_text(encoding="utf-8")
        name = path.relative_to(ROOT)
        problems += [f"{name}: unknown route {m}" for m in _ROUTE.findall(text) if not _known(m, patterns)]
        if commands is not None:
            problems += [
                f"{name}: unknown command spaider {m}"
                for m in _COMMAND.findall(text)
                if m not in commands and m not in _NOT_COMMANDS
            ]
    for problem in sorted(set(problems)):
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
