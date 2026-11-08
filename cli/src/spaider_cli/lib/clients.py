"""Writes one MCP entry per client. Each client gets its own key, so it can be revoked alone."""
from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from spaider_cli.lib import mcp as mcp_lib


@dataclass(frozen=True)
class Client:
    path: Callable[[Path], Path]
    key: str
    entry: Callable[[str, str], dict[str, Any]]


def _http(url: str, key: str) -> dict[str, Any]:
    return {"type": "http", "url": url, "headers": {"Authorization": f"Bearer {key}"}}


def _windsurf(url: str, key: str) -> dict[str, Any]:
    return {"serverUrl": url, "headers": {"Authorization": f"Bearer {key}"}}


def _bridge(url: str, key: str) -> dict[str, Any]:
    """For clients that only start local processes."""
    return {"command": "npx", "args": ["-y", "mcp-remote", url, "--header", f"Authorization: Bearer {key}"]}


def _zed(url: str, key: str) -> dict[str, Any]:
    return {"source": "custom", **_bridge(url, key)}


def _opencode(url: str, key: str) -> dict[str, Any]:
    return {"type": "remote", "url": url, "headers": {"Authorization": f"Bearer {key}"}, "enabled": True}


def _claude_desktop_path(_cwd: Path) -> Path:
    home = Path.home()
    system = platform.system()
    if system == "Darwin":
        return home / "Library/Application Support/Claude/claude_desktop_config.json"
    if system == "Windows":
        return Path(os.environ.get("APPDATA", home)) / "Claude/claude_desktop_config.json"
    return home / ".config/Claude/claude_desktop_config.json"


CLIENTS: dict[str, Client] = {
    "claude-code": Client(lambda cwd: Path.home() / ".claude/.mcp.json", "mcpServers", _http),
    "claude-desktop": Client(_claude_desktop_path, "mcpServers", _bridge),
    "cursor": Client(lambda cwd: cwd / ".cursor/mcp.json", "mcpServers", _http),
    "windsurf": Client(lambda cwd: Path.home() / ".codeium/windsurf/mcp_config.json", "mcpServers", _windsurf),
    "vscode": Client(lambda cwd: cwd / ".vscode/mcp.json", "servers", _http),
    "zed": Client(lambda cwd: Path.home() / ".config/zed/settings.json", "context_servers", _zed),
    "opencode": Client(lambda cwd: cwd / "opencode.json", "mcp", _opencode),
}
CODEX = "codex"
SUPPORTED = sorted([*CLIENTS, CODEX])

_CODEX_START = "# spaider:start (managed by `spaider mcp add`)"
_CODEX_END = "# spaider:end"


def _install_codex(url: str, key: str) -> Path:
    path = Path.home() / ".codex/config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if _CODEX_START in existing:
        before = existing.split(_CODEX_START)[0]
        after = existing.split(_CODEX_END, 1)[1] if _CODEX_END in existing else ""
        existing = before + after.lstrip("\n")
    block = (
        f'{_CODEX_START}\n[mcp_servers.spaider]\ncommand = "npx"\n'
        f'args = ["-y", "mcp-remote", "{url}", "--header", "Authorization: Bearer {key}"]\n{_CODEX_END}\n'
    )
    mcp_lib.backup_if_exists(path)
    mcp_lib.atomic_write_text(path, (existing.rstrip("\n") + "\n\n" if existing.strip() else "") + block)
    os.chmod(path, 0o600)
    return path


def install(name: str, url: str, key: str, *, cwd: Path | None = None) -> Path:
    if name == CODEX:
        return _install_codex(url, key)
    client = CLIENTS[name]
    path = client.path(cwd or Path.cwd())
    config = mcp_lib.read_mcp_config(path) if path.exists() else {}
    config.setdefault(client.key, {})["spaider"] = client.entry(url, key)
    mcp_lib.backup_if_exists(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    mcp_lib.atomic_write_json(path, config)
    # The file holds a credential.
    os.chmod(path, 0o600)
    return path
