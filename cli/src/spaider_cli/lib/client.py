"""Authenticated calls to the SpAIder API."""
from __future__ import annotations

from typing import Any

import httpx
import typer
from rich.console import Console

from spaider_cli.lib import config

_err = Console(stderr=True)


def headers(key: str | None = None) -> dict[str, str]:
    token = key or config.api_key()
    return {"X-Api-Key": token} if token else {}


def request(
    method: str,
    path: str,
    *,
    json: Any = None,
    params: dict | None = None,
    base: str | None = None,
    key: str | None = None,
    timeout: float = 120.0,
) -> Any:
    """Make a call, printing a readable message and exiting on failure."""
    try:
        response = httpx.request(
            method, (base or config.api_base()) + path, json=json, params=params, headers=headers(key), timeout=timeout
        )
    except httpx.HTTPError as exc:
        _err.print(f"[red]✗[/] Could not reach the API: {exc}")
        raise typer.Exit(code=1)
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        _err.print(f"[red]✗[/] {response.status_code}: {detail}")
        raise typer.Exit(code=1)
    return response.json() if response.content else None


def healthy() -> bool:
    root = config.api_base().rsplit("/api/", 1)[0]
    try:
        return httpx.get(root + "/health", timeout=5).status_code == 200
    except httpx.HTTPError:
        return False


def agent_or_exit(agent: str | None) -> str:
    resolved = agent or config.default_agent()
    if not resolved:
        _err.print("[red]✗[/] No knowledge base given. Use --agent or log in with one.")
        raise typer.Exit(code=1)
    return resolved
