"""Finds or unpacks the compose stack, and runs docker compose against it."""
from __future__ import annotations

import shutil
import subprocess
from importlib import resources
from pathlib import Path

_FILES = ("docker-compose.yml", ".env.example", "gateway/kong.yml")


def stack_home() -> Path:
    return Path.home() / ".spaider" / "stack"


def _unpack(target: Path) -> None:
    root = resources.files("spaider_cli") / "stack"
    for name in _FILES:
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text((root / name).read_text(encoding="utf-8"), encoding="utf-8")


def locate(directory: Path | None = None) -> Path:
    """An explicit directory, then the current one, then the bundled copy."""
    if directory is not None:
        return directory
    if (Path.cwd() / "docker-compose.yml").exists():
        return Path.cwd()
    home = stack_home()
    _unpack(home)
    if not (home / ".env").exists():
        shutil.copyfile(home / ".env.example", home / ".env")
    return home


def compose(directory: Path, *arguments: str, profile: str | None = None, capture: bool = False):
    command = ["docker", "compose"]
    if profile:
        command += ["--profile", profile]
    return subprocess.run(
        [*command, *arguments], cwd=directory, check=False, text=True, capture_output=capture
    )
