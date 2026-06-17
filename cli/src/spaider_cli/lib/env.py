"""``.env`` file management for ``spaider init``.

Two responsibilities:

1. **Merge** values into an existing ``.env`` (or create from ``.env.example``)
   without disturbing keys we don't touch. Preserves comments and ordering.
2. **Generate** the secrets the user shouldn't have to type — JWT signing
   key, connector encryption key, Neo4j password — with the same recipes
   the README documents (so values round-trip with manual setup).

Atomic writes via ``spaider_cli.lib.mcp.atomic_write_text`` so partial state
never lands on disk.
"""
from __future__ import annotations

import base64
import re
import secrets
from pathlib import Path

from spaider_cli.lib.mcp import atomic_write_text, backup_if_exists

# ---------------------------------------------------------------------------
# Secret generation — match the README recipes exactly
# ---------------------------------------------------------------------------


def generate_jwt_secret() -> str:
    """64 hex chars (matches ``python -c "import secrets; print(secrets.token_hex(32))"``)."""
    return secrets.token_hex(32)


def generate_connector_key() -> str:
    """Base64-encoded 32-byte AES key.

    Matches the README recipe::

        python -c "import secrets,base64; print(base64.b64encode(secrets.token_bytes(32)).decode())"
    """
    return base64.b64encode(secrets.token_bytes(32)).decode()


def generate_neo4j_password() -> str:
    """A friendly password — 24 url-safe characters."""
    return secrets.token_urlsafe(18)


# ---------------------------------------------------------------------------
# .env parsing / writing
# ---------------------------------------------------------------------------


# Matches ``KEY=value`` (anchored to the start of a line, allowing whitespace).
# Captures the key in group 1 for in-place substitution.
_ASSIGN = re.compile(r"^([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$")


def parse_env_text(text: str) -> dict[str, str]:
    """Parse a ``.env`` style text blob into a dict of ``KEY: value`` pairs.

    Comments and blank lines are ignored. The trailing value text is taken
    verbatim — no shell-style quoting is interpreted, which matches what
    ``pydantic-settings`` does at runtime.
    """
    out: dict[str, str] = {}
    for raw_line in text.splitlines():
        stripped = raw_line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _ASSIGN.match(stripped)
        if match is None:
            continue
        key, value = match.group(1), match.group(2)
        out[key] = value
    return out


def update_env_text(template: str, overrides: dict[str, str]) -> str:
    """Return a new .env body with each ``overrides[key]`` substituted in.

    Preserves comments, blank lines, and the original ordering. Keys that
    appear in ``overrides`` but not in ``template`` are appended at the end
    in insertion order (so the output is deterministic).
    """
    lines = template.splitlines(keepends=True)
    seen: set[str] = set()
    new_lines: list[str] = []
    for line in lines:
        match = _ASSIGN.match(line.lstrip())
        if match is None:
            new_lines.append(line)
            continue
        key = match.group(1)
        if key in overrides:
            seen.add(key)
            newline = "\n" if line.endswith("\n") else ""
            new_lines.append(f"{key}={overrides[key]}{newline}")
        else:
            new_lines.append(line)

    extras = [k for k in overrides if k not in seen]
    if extras:
        # Ensure trailing newline before appending
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines[-1] = new_lines[-1] + "\n"
        new_lines.append("\n# ── added by `spaider init` ─────────────────────\n")
        for key in extras:
            new_lines.append(f"{key}={overrides[key]}\n")
    return "".join(new_lines)


def write_env_file(
    *,
    target: Path,
    example: Path,
    overrides: dict[str, str],
) -> Path | None:
    """Write ``target`` based on ``example`` with ``overrides`` applied.

    If ``target`` already exists, it's read in (preserving any custom values)
    and ``overrides`` are merged on top. The previous version is backed up.

    Returns the path of the backup file (or ``None`` if no backup was made).
    """
    if not example.exists():
        raise FileNotFoundError(f"missing .env.example at {example}")
    if target.exists():
        template = target.read_text(encoding="utf-8")
    else:
        template = example.read_text(encoding="utf-8")

    backup = backup_if_exists(target)
    new_body = update_env_text(template, overrides)
    atomic_write_text(target, new_body)
    return backup
