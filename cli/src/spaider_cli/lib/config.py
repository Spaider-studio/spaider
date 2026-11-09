"""Where the CLI finds its server and key.

Order: the SPAIDER_API_KEY and SPAIDER_API_BASE environment variables, then
~/.spaider/config.json, which only the current user can read.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULT_API_BASE = "http://localhost:8000/api/v1"


def config_path() -> Path:
    return Path(os.environ.get("SPAIDER_CONFIG", Path.home() / ".spaider" / "config.json"))


def load() -> dict[str, Any]:
    path = config_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save(**values: Any) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {**load(), **values}
    # Created unreadable to others before any secret is written.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
    os.chmod(path, 0o600)
    return path


def api_base() -> str:
    return (os.environ.get("SPAIDER_API_BASE") or load().get("api_base") or DEFAULT_API_BASE).rstrip("/")


def api_key() -> str | None:
    return os.environ.get("SPAIDER_API_KEY") or load().get("api_key")


def default_agent() -> str | None:
    return os.environ.get("SPAIDER_AGENT_ID") or load().get("agent_id")
