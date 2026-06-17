"""``spaider mcp install`` — write SpAIder into MCP client configs.

Two things land for the chosen client:

1. The MCP server entry (URL + Authorization Bearer header).
2. The skill file — the agent-side usage protocol that converts "MCP server
   present" into "LLM reflexively reaches for SpAIder when context warrants".

The skill is the higher-leverage half: without it Claude Code sees the
``spaider.*`` tools as just-another-tool and rarely invokes them. The skill
file teaches the LLM when to call each tool, the feedback protocol, and the
failure-mode etiquette.
"""
from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from spaider_cli.lib import api as api_client
from spaider_cli.lib import mcp as mcp_lib

app = typer.Typer(no_args_is_help=True)
console = Console()
err_console = Console(stderr=True)


DEFAULT_MCP_URL = "http://localhost:8000/api/v1/mcp/sse"


@app.command("install")
def install(
    agent: str | None = typer.Option(
        None,
        "--agent",
        help=(
            "Agent name to use for the MCP credential. Looked up via the API; "
            "if not found, the command prompts to create it. "
            "Defaults to dev-$USER."
        ),
    ),
    api_key: str | None = typer.Option(
        None,
        "--api-key",
        help=(
            "Bearer token to embed in the MCP config. If omitted, the CLI "
            "rotates the agent's key (or creates the agent + new key) so a "
            "valid credential is always written. Pass an existing key to skip rotation."
        ),
    ),
    url: str = typer.Option(
        DEFAULT_MCP_URL,
        "--url",
        help="MCP server URL. Override if you run the standalone MCP host on :8001.",
    ),
    client: str = typer.Option(
        "claude-code",
        "--for",
        help="Which MCP client to install for: claude-code (default) or cursor.",
    ),
    scope: str = typer.Option(
        "user",
        "--scope",
        help=(
            "claude-code only. user = global ~/.claude (default; same agent "
            "memory in every repo). project = write ./.mcp.json so THIS repo "
            "uses its own agent memory, overriding the global one."
        ),
    ),
) -> None:
    """Install the SpAIder MCP server + skill into your AI coding tool's config.

    Idempotent: re-running upgrades the skill, refreshes the credential, and
    backs up any prior config. Non-destructive — other MCP servers in your
    config are preserved.
    """
    # Resolve the credential we'll embed.
    resolved_key: str
    resolved_agent: str
    if api_key:
        resolved_key = api_key
        resolved_agent = agent or "unspecified"
    else:
        resolved_agent = agent or _default_agent_name()
        try:
            resolved_key = _ensure_agent_key(resolved_agent)
        except api_client.SpaiderApiError as exc:
            err_console.print(
                f"[red]✗[/] could not resolve credentials for agent "
                f"'{resolved_agent}': {exc}"
            )
            err_console.print(
                "  Pass [bold]--api-key sk-...[/] to skip the API lookup, "
                "or start the SpAIder backend first (make dev)."
            )
            raise typer.Exit(code=1)

    # Dispatch to the right client.
    if client == "claude-code":
        if scope not in ("user", "project"):
            err_console.print(
                f"[red]✗[/] unsupported --scope '{scope}'. Supported: user, project."
            )
            raise typer.Exit(code=2)
        report = mcp_lib.install_for_claude_code(
            url=url, api_key=resolved_key, scope=scope, project_root=Path.cwd(),
        )
        _report_claude_code(report=report, agent=resolved_agent, scope=scope)
    elif client == "cursor":
        report = mcp_lib.install_for_cursor(
            project_root=Path.cwd(),
            url=url,
            api_key=resolved_key,
        )
        _report_cursor(report=report, agent=resolved_agent)
    else:
        err_console.print(
            f"[red]✗[/] unsupported --for value '{client}'. "
            "Supported: claude-code, cursor."
        )
        raise typer.Exit(code=2)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _default_agent_name() -> str:
    """Return ``dev-${USER}`` (falling back to ``dev-user`` on no $USER)."""
    import os
    user = os.environ.get("USER") or os.environ.get("USERNAME") or "user"
    return f"dev-{user}"


def _ensure_agent_key(name: str) -> str:
    """Find or create an agent named ``name`` and return a fresh API key.

    - If the agent exists: rotate its key (matches setup_mcp_dev_agent.sh).
    - If not: create it and return the just-issued key.
    """
    existing = api_client.find_agent_by_name(name)
    if existing is not None:
        return api_client.rotate_key(existing.id)
    created = api_client.create_agent(name)
    if not created.api_key:
        raise api_client.SpaiderApiError(
            "create_agent returned no api_key — backend may be misconfigured."
        )
    return created.api_key


def _report_claude_code(
    *, report: mcp_lib.InstallReport, agent: str, scope: str = "user",
) -> None:
    console.print()
    console.print("[bold green]✓[/] SpAIder MCP installed for Claude Code")
    console.print(f"  agent     : [bold]{agent}[/]")
    console.print(f"  scope     : [bold]{scope}[/]" + (
        "  [dim](this repo only)[/]" if scope == "project" else "  [dim](global)[/]"
    ))
    console.print(f"  config    : {report.config_path}")
    if report.config_backup:
        console.print(f"  backup of : [dim]{report.config_backup}[/]")
    if report.skill_path:
        console.print(f"  skill     : {report.skill_path}")
    if report.skill_backup:
        console.print(f"  backup of : [dim]{report.skill_backup}[/]")
    if scope == "project":
        console.print()
        console.print(
            "[yellow]![/] [bold].mcp.json[/] holds a secret Bearer token — add it "
            "to this repo's [bold].gitignore[/] so it is never committed."
        )
    console.print()
    console.print(
        "[yellow]Restart Claude Code[/] so it picks up the new MCP server "
        "and skill file."
    )


def _report_cursor(*, report: mcp_lib.InstallReport, agent: str) -> None:
    console.print()
    console.print("[bold green]✓[/] SpAIder rules installed for Cursor")
    console.print(f"  agent     : [bold]{agent}[/]")
    console.print(f"  rules     : {report.config_path}")
    if report.config_backup:
        console.print(f"  backup of : [dim]{report.config_backup}[/]")
    console.print()
    console.print(
        "[yellow]Reload the Cursor window[/] so it picks up the updated .cursorrules."
    )
