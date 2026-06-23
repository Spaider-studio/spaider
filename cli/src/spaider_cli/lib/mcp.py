"""MCP client config + skill file management for ``spaider mcp install``.

Writes two things side-by-side for the chosen client:

1. The MCP server config (e.g. ``~/.claude/.mcp.json``) — non-destructive
   merge that preserves any other servers the user already has.
2. The skill file (e.g. ``~/.claude/skills/spaider.md``) — the agent-side
   instruction set lifted from this package's ``skills/claude_code.md``.

Both writes are atomic (temp file + rename) and any pre-existing file is
backed up with a timestamp suffix so the user can recover.
"""
from __future__ import annotations

import importlib.resources
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Client config locations
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClientPaths:
    """Resolved filesystem locations for one MCP client install."""

    name: str
    mcp_config: Path
    skill_file: Path | None  # None means no skill-file slot for this client


def claude_code_paths(
    *,
    scope: str = "user",
    home: Path | None = None,
    project_root: Path | None = None,
) -> ClientPaths:
    """Paths for the Claude Code MCP client.

    ``scope="user"`` (default) targets the global ``~/.claude`` config that
    every project sees — one agent memory everywhere. ``scope="project"``
    targets the current repo so it can use its own agent memory, overriding the
    global one: ``<root>/.mcp.json`` plus a project-level skill at
    ``<root>/.claude/skills/spaider.md``.
    """
    if scope == "project":
        root = project_root or Path.cwd()
        return ClientPaths(
            name="claude-code",
            mcp_config=root / ".mcp.json",
            skill_file=root / ".claude" / "skills" / "spaider.md",
        )
    home = home or Path.home()
    return ClientPaths(
        name="claude-code",
        mcp_config=home / ".claude" / ".mcp.json",
        skill_file=home / ".claude" / "skills" / "spaider.md",
    )


def opencode_paths(
    *,
    scope: str = "user",
    home: Path | None = None,
    project_root: Path | None = None,
) -> ClientPaths:
    """Paths for OpenCode.

    OpenCode reads MCP servers from ``opencode.json``: globally at
    ``~/.config/opencode/opencode.json`` (``scope="user"``, default) or
    per-project at ``<root>/opencode.json`` (``scope="project"``). Agent
    guidance goes in ``AGENTS.md`` (the project root for project scope, the
    global config dir otherwise).
    """
    if scope == "project":
        root = project_root or Path.cwd()
        return ClientPaths(
            name="opencode",
            mcp_config=root / "opencode.json",
            skill_file=root / "AGENTS.md",
        )
    home = home or Path.home()
    cfg_dir = home / ".config" / "opencode"
    return ClientPaths(
        name="opencode",
        mcp_config=cfg_dir / "opencode.json",
        skill_file=cfg_dir / "AGENTS.md",
    )


def cursor_paths(project_root: Path) -> ClientPaths:
    """Paths for Cursor (per-project ``.cursorrules``).

    Cursor's MCP support is project-scoped via ``.cursorrules``; we treat that
    file as both the "config" surface and the "skill" surface — they're not
    separate concepts in Cursor's mental model.
    """
    return ClientPaths(
        name="cursor",
        mcp_config=project_root / ".cursorrules",
        skill_file=None,  # the same file carries the rules; no second file
    )


# ---------------------------------------------------------------------------
# Skill content
# ---------------------------------------------------------------------------


def read_packaged_skill(filename: str = "claude_code.md") -> str:
    """Load a skill file shipped inside the wheel as package data."""
    skill = importlib.resources.files("spaider_cli.skills").joinpath(filename)
    return skill.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# MCP config merge
# ---------------------------------------------------------------------------


def _spaider_server_entry(url: str, api_key: str) -> dict[str, Any]:
    """The mcpServers block that we add for SpAIder.

    ``type: "http"`` selects the Streamable HTTP transport, the modern MCP
    transport SpAIder's backend serves at ``/api/v1/mcp``.
    """
    return {
        "type": "http",
        "url": url,
        "headers": {"Authorization": f"Bearer {api_key}"},
    }


def merge_mcp_server(
    existing: dict[str, Any] | None,
    *,
    server_name: str = "spaider",
    url: str,
    api_key: str,
) -> dict[str, Any]:
    """Return a new dict with the spaider mcpServers entry merged in.

    Pure function — does not touch the filesystem. Preserves every other
    server the user already has and any unrelated top-level keys.
    """
    merged: dict[str, Any] = dict(existing or {})
    servers = dict(merged.get("mcpServers") or {})
    servers[server_name] = _spaider_server_entry(url=url, api_key=api_key)
    merged["mcpServers"] = servers
    return merged


def _opencode_server_entry(url: str, api_key: str) -> dict[str, Any]:
    """The ``opencode.json`` ``mcp.<name>`` block for SpAIder.

    OpenCode uses ``type: "remote"`` for HTTP/Streamable-HTTP MCP servers
    (vs ``"local"`` stdio servers); ``enabled: true`` registers it.
    """
    return {
        "type": "remote",
        "url": url,
        "enabled": True,
        "headers": {"Authorization": f"Bearer {api_key}"},
    }


def merge_opencode_server(
    existing: dict[str, Any] | None,
    *,
    server_name: str = "spaider",
    url: str,
    api_key: str,
) -> dict[str, Any]:
    """Merge the SpAIder entry into an ``opencode.json`` dict (top-level ``mcp``
    key), preserving every other server and unrelated keys. Pure function."""
    merged: dict[str, Any] = dict(existing or {})
    servers = dict(merged.get("mcp") or {})
    servers[server_name] = _opencode_server_entry(url=url, api_key=api_key)
    merged["mcp"] = servers
    return merged


# ---------------------------------------------------------------------------
# Atomic write + backup
# ---------------------------------------------------------------------------


def _timestamp_suffix() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def backup_if_exists(path: Path) -> Path | None:
    """If ``path`` exists, copy it to ``path.<timestamp>.bak``. Returns the backup path."""
    if not path.exists():
        return None
    backup = path.with_name(f"{path.name}.{_timestamp_suffix()}.bak")
    shutil.copy2(path, backup)
    return backup


def atomic_write_text(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` via temp-file + rename so readers never see partial state."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Atomic JSON write with stable 2-space indent + trailing newline."""
    atomic_write_text(path, json.dumps(data, indent=2, sort_keys=False) + "\n")


# ---------------------------------------------------------------------------
# Read existing config (forgiving)
# ---------------------------------------------------------------------------


class MalformedConfigError(RuntimeError):
    """Raised when an existing .mcp.json file can't be parsed."""


def read_mcp_config(path: Path) -> dict[str, Any]:
    """Read an existing .mcp.json. Returns ``{}`` if missing; raises if malformed."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MalformedConfigError(
            f"existing {path} is not valid JSON: {exc}. "
            "Move it aside (or delete it) and re-run."
        ) from exc


# ---------------------------------------------------------------------------
# High-level orchestration — used by the mcp.install command
# ---------------------------------------------------------------------------


@dataclass
class InstallReport:
    """What ``install_for_claude_code`` actually did (for UI / tests)."""
    config_path: Path
    config_backup: Path | None
    skill_path: Path | None
    skill_backup: Path | None


def install_for_claude_code(
    *,
    url: str,
    api_key: str,
    scope: str = "user",
    home: Path | None = None,
    project_root: Path | None = None,
) -> InstallReport:
    """Idempotently install SpAIder into Claude Code on this machine.

    Steps (paths depend on ``scope`` — see :func:`claude_code_paths`):
    1. Read the target ``.mcp.json`` (treat missing as empty).
    2. Merge a ``mcpServers.spaider`` entry pointing at ``url`` with the
       Bearer ``api_key`` header.
    3. Back up the existing file (if any), then atomic-write the merged JSON.
    4. Back up + atomic-write the bundled skill file alongside it.
    """
    paths = claude_code_paths(scope=scope, home=home, project_root=project_root)

    existing = read_mcp_config(paths.mcp_config)
    merged = merge_mcp_server(existing, url=url, api_key=api_key)

    config_backup = backup_if_exists(paths.mcp_config)
    atomic_write_json(paths.mcp_config, merged)

    assert paths.skill_file is not None  # claude-code always has a skill slot
    skill_backup = backup_if_exists(paths.skill_file)
    atomic_write_text(paths.skill_file, read_packaged_skill("claude_code.md"))

    return InstallReport(
        config_path=paths.mcp_config,
        config_backup=config_backup,
        skill_path=paths.skill_file,
        skill_backup=skill_backup,
    )


def install_for_cursor(
    *,
    project_root: Path,
    url: str,
    api_key: str,
) -> InstallReport:
    """Install SpAIder's usage rules into a Cursor project as .cursorrules.

    Cursor's MCP support is per-project: the rules + server entry live in
    ``.cursorrules`` at the project root. If a rules file already exists, we
    append a clearly-delimited SpAIder block (preserving the user's existing
    rules) rather than overwriting.
    """
    paths = cursor_paths(project_root)
    skill_content = read_packaged_skill("claude_code.md")  # same source for now

    # Compose: a banner with the MCP server URL + key, followed by the skill.
    banner = (
        "# ── SpAIder MCP — auto-installed by `spaider mcp install --for cursor` ──\n"
        f"# MCP endpoint: {url}\n"
        f"# Bearer token: {api_key}\n"
        "# Edit above if needed; the skill content below is portable.\n\n"
    )
    payload = banner + skill_content

    if paths.mcp_config.exists():
        existing = paths.mcp_config.read_text(encoding="utf-8")
        if "spaider mcp install" in existing:
            # Already installed — back up and replace the spaider block.
            config_backup = backup_if_exists(paths.mcp_config)
            atomic_write_text(paths.mcp_config, payload)
        else:
            # Append, preserving user's prior rules.
            config_backup = backup_if_exists(paths.mcp_config)
            atomic_write_text(paths.mcp_config, existing.rstrip() + "\n\n" + payload)
    else:
        config_backup = None
        atomic_write_text(paths.mcp_config, payload)

    return InstallReport(
        config_path=paths.mcp_config,
        config_backup=config_backup,
        skill_path=None,
        skill_backup=None,
    )


# ---------------------------------------------------------------------------
# OpenCode
# ---------------------------------------------------------------------------

_SPAIDER_BLOCK_START = "<!-- spaider:start (managed by `spaider mcp install`) -->"
_SPAIDER_BLOCK_END = "<!-- spaider:end -->"


def upsert_marked_block(path: Path, inner: str) -> Path | None:
    """Idempotently write a SpAIder-delimited block into a markdown file.

    Missing file → create with just the block. Existing file with our markers →
    replace what's between them (preserving the user's surrounding content).
    Existing file without markers → append. Returns the backup path (or None).
    """
    block = f"{_SPAIDER_BLOCK_START}\n{inner.rstrip()}\n{_SPAIDER_BLOCK_END}\n"
    if not path.exists():
        atomic_write_text(path, block)
        return None
    existing = path.read_text(encoding="utf-8")
    backup = backup_if_exists(path)
    if _SPAIDER_BLOCK_START in existing and _SPAIDER_BLOCK_END in existing:
        pre = existing.split(_SPAIDER_BLOCK_START, 1)[0].rstrip()
        post = existing.split(_SPAIDER_BLOCK_END, 1)[1].lstrip("\n")
        new = block
        if pre:
            new = pre + "\n\n" + new
        if post.strip():
            new = new + "\n" + post
    else:
        new = existing.rstrip() + "\n\n" + block
    atomic_write_text(path, new)
    return backup


def install_for_opencode(
    *,
    url: str,
    api_key: str,
    scope: str = "user",
    home: Path | None = None,
    project_root: Path | None = None,
) -> InstallReport:
    """Idempotently install SpAIder into OpenCode.

    Writes two things (see :func:`opencode_paths`):
    1. An ``mcp.spaider`` entry in ``opencode.json`` (Streamable HTTP, Bearer
       auth), non-destructively merged so other servers survive.
    2. A SpAIder guidance block in ``AGENTS.md``, delimited by stable markers so
       re-running replaces it in place rather than duplicating.
    """
    paths = opencode_paths(scope=scope, home=home, project_root=project_root)

    existing = read_mcp_config(paths.mcp_config)
    merged = merge_opencode_server(existing, url=url, api_key=api_key)
    config_backup = backup_if_exists(paths.mcp_config)
    atomic_write_json(paths.mcp_config, merged)

    assert paths.skill_file is not None  # opencode always has an AGENTS.md slot
    guidance = (
        "# SpAIder memory\n\n"
        "SpAIder is wired in as the `spaider` MCP server. Use its tools for "
        "durable, queryable memory across sessions.\n\n"
        + read_packaged_skill("claude_code.md").rstrip()
    )
    skill_backup = upsert_marked_block(paths.skill_file, guidance)

    return InstallReport(
        config_path=paths.mcp_config,
        config_backup=config_backup,
        skill_path=paths.skill_file,
        skill_backup=skill_backup,
    )
