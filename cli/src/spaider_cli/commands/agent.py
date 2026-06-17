"""``spaider agent ...`` — REST wrapper around SpAIder's /api/v1/agents.

Replaces ``scripts/dev/setup_mcp_dev_agent.sh`` and ``setup_bench_agent.sh``
with proper subcommands. The bash scripts now shell out to these.

Idempotent semantics on ``create``:

- If an agent with the requested name already exists, the command rotates its
  key by default (matches the legacy bash-script behaviour). Pass
  ``--no-rotate`` to fail loudly instead.
"""
from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from spaider_cli.lib import api as api_client

app = typer.Typer(no_args_is_help=True)
console = Console()
err_console = Console(stderr=True)


@app.command("create")
def create(
    name: str = typer.Argument(..., help="Agent name (e.g. dev-mycl, bench-acmeai)"),
    description: str | None = typer.Option(
        None, "--description", "-d", help="Optional description for the agent."
    ),
    tenant: str = typer.Option(
        "default", "--tenant", help="Tenant ID for multi-tenant deployments."
    ),
    clearance: int = typer.Option(
        1,
        "--clearance",
        min=1,
        max=5,
        help="Diplomat Protocol clearance level (1=Public/Guest … 5=Admin).",
    ),
    no_rotate: bool = typer.Option(
        False,
        "--no-rotate",
        help="If the agent already exists, fail instead of rotating its key.",
    ),
) -> None:
    """Create an agent (idempotent: rotates the key if name already exists)."""
    try:
        existing = api_client.find_agent_by_name(name)
    except api_client.SpaiderApiError as exc:
        err_console.print(f"[red]✗[/] {exc}")
        raise typer.Exit(code=1)

    if existing is not None:
        if no_rotate:
            err_console.print(
                f"[red]✗[/] agent '{name}' already exists (id={existing.id}); "
                "pass without --no-rotate to rotate its key instead."
            )
            raise typer.Exit(code=1)
        console.print(
            f"[yellow]![/] agent '{name}' already exists (id={existing.id}); "
            "rotating its API key…"
        )
        try:
            new_key = api_client.rotate_key(existing.id)
        except api_client.SpaiderApiError as exc:
            err_console.print(f"[red]✗[/] {exc}")
            raise typer.Exit(code=1)
        _print_credentials(name=name, agent_id=existing.id, api_key=new_key)
        return

    try:
        agent = api_client.create_agent(
            name,
            description=description,
            tenant_id=tenant,
            clearance_level=clearance,
        )
    except api_client.SpaiderApiError as exc:
        err_console.print(f"[red]✗[/] {exc}")
        raise typer.Exit(code=1)

    if not agent.api_key:
        err_console.print(
            "[red]✗[/] create returned no api_key — backend may be misconfigured."
        )
        raise typer.Exit(code=1)
    _print_credentials(name=agent.name, agent_id=agent.id, api_key=agent.api_key)


@app.command("list")
def list_agents() -> None:
    """List every agent registered against this SpAIder instance."""
    try:
        agents = api_client.list_agents()
    except api_client.SpaiderApiError as exc:
        err_console.print(f"[red]✗[/] {exc}")
        raise typer.Exit(code=1)

    if not agents:
        console.print("[yellow]No agents found.[/] Create one with: spaider agent create <name>")
        return

    table = Table(title="SpAIder agents", header_style="bold cyan")
    table.add_column("Name", style="bold")
    table.add_column("ID", overflow="fold")
    table.add_column("Tenant", style="dim")
    table.add_column("Clearance", justify="right")
    for a in agents:
        table.add_row(a.name, a.id, a.tenant_id, str(a.clearance_level))
    console.print(table)


@app.command("rotate-key")
def rotate(
    name: str = typer.Argument(..., help="Agent name whose key should be rotated."),
) -> None:
    """Rotate an agent's API key — shows the new value once."""
    try:
        existing = api_client.find_agent_by_name(name)
        if not existing:
            err_console.print(f"[red]✗[/] agent '{name}' not found.")
            raise typer.Exit(code=1)
        new_key = api_client.rotate_key(existing.id)
    except api_client.SpaiderApiError as exc:
        err_console.print(f"[red]✗[/] {exc}")
        raise typer.Exit(code=1)
    _print_credentials(name=name, agent_id=existing.id, api_key=new_key)


@app.command("delete")
def delete(
    name: str = typer.Argument(..., help="Agent name to delete."),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the confirmation prompt."
    ),
) -> None:
    """Delete an agent and all its graph data (destructive — confirms by default)."""
    try:
        existing = api_client.find_agent_by_name(name)
    except api_client.SpaiderApiError as exc:
        err_console.print(f"[red]✗[/] {exc}")
        raise typer.Exit(code=1)

    if not existing:
        err_console.print(f"[red]✗[/] agent '{name}' not found.")
        raise typer.Exit(code=1)

    if not yes:
        confirmed = typer.confirm(
            f"Delete agent '{name}' (id={existing.id}) and all its graph data?",
            default=False,
        )
        if not confirmed:
            console.print("[yellow]Aborted.[/]")
            raise typer.Exit(code=0)

    try:
        api_client.delete_agent(existing.id)
    except api_client.SpaiderApiError as exc:
        err_console.print(f"[red]✗[/] {exc}")
        raise typer.Exit(code=1)
    console.print(f"[green]✓[/] deleted agent '{name}' (id={existing.id}).")


def _print_credentials(*, name: str, agent_id: str, api_key: str) -> None:
    """Pretty-print the agent + key block so the user can copy it once."""
    console.print()
    console.print("[bold green]✓[/] agent ready")
    console.print(f"  name    : [bold]{name}[/]")
    console.print(f"  id      : [dim]{agent_id}[/]")
    console.print(f"  api key : [bold yellow]{api_key}[/]")
    console.print()
    console.print(
        "[dim]The api key is shown once. Store it safely or rotate later with "
        "[bold]spaider agent rotate-key " + name + "[/].[/]"
    )
