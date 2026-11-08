"""Stack lifecycle and login."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from spaider_cli.lib import client, config, stack

console = Console()
_PROFILE = typer.Option("lite", "--profile", help="lite (Neo4j and API) or full.")
_DIRECTORY = typer.Option(None, "--dir", help="Directory holding docker-compose.yml.")


def up(profile: str = _PROFILE, directory: Optional[Path] = _DIRECTORY) -> None:
    """Start the stack."""
    root = stack.locate(directory)
    result = stack.compose(root, "up", "-d", "--wait", profile=profile)
    if result.returncode:
        raise typer.Exit(code=result.returncode)
    console.print(f"[green]✓[/] Stack up ({profile}) from {root}")


def down(profile: str = _PROFILE, directory: Optional[Path] = _DIRECTORY) -> None:
    """Stop the stack. Data volumes are kept."""
    result = stack.compose(stack.locate(directory), "down", profile=profile)
    raise typer.Exit(code=result.returncode)


def status() -> None:
    """Show whether the API answers and who you are logged in as."""
    base = config.api_base()
    if not client.healthy():
        console.print(f"[red]✗[/] No API at {base}")
        raise typer.Exit(code=1)
    console.print(f"[green]✓[/] API healthy at {base}")
    if not config.api_key():
        console.print("[yellow]![/] Not logged in. Run [bold]spaider login[/].")
        return
    who = client.request("GET", "/auth/whoami")
    console.print(f"[green]✓[/] {who.get('name') or who.get('id')} ({who.get('org_role')})")


def logs(service: Optional[str] = typer.Argument(None), directory: Optional[Path] = _DIRECTORY) -> None:
    """Follow container logs."""
    arguments = ["logs", "-f", "--tail", "100"] + ([service] if service else [])
    raise typer.Exit(code=stack.compose(stack.locate(directory), *arguments).returncode)


def login(
    api_key: Optional[str] = typer.Option(None, "--api-key", help="Omit to be prompted without echo."),
    url: str = typer.Option(config.DEFAULT_API_BASE, "--url"),
    agent: Optional[str] = typer.Option(None, "--agent", help="Default knowledge base for commands."),
) -> None:
    """Save and verify an API key."""
    key = api_key or typer.prompt("API key", hide_input=True)
    who = client.request("GET", "/auth/whoami", base=url.rstrip("/"), key=key)
    path = config.save(api_base=url.rstrip("/"), api_key=key, agent_id=agent or who.get("default_kb"))
    console.print(f"[green]✓[/] Logged in as {who.get('name') or who.get('id')}. Saved to {path}")
