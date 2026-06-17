"""SpAIder CLI entry point.

Registers the top-level ``spaider`` command and its subcommands via Typer.
The actual implementation of each subcommand lives in ``spaider_cli.commands.*``
modules; this file only wires the app together so ``[project.scripts]``
resolution stays trivial.
"""
from __future__ import annotations

import typer

from spaider_cli import __version__
from spaider_cli.commands import agent as agent_cmd
from spaider_cli.commands import doctor as doctor_cmd
from spaider_cli.commands import init as init_cmd
from spaider_cli.commands import mcp as mcp_cmd

app = typer.Typer(
    name="spaider",
    help=(
        "SpAIder install wizard, agent management, and AI-agent skill "
        "installer. Run `spaider init` to set up a new SpAIder stack and "
        "wire it into Claude Code in under 5 minutes."
    ),
    rich_markup_mode="rich",
    no_args_is_help=True,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"spaider-cli {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        callback=_version_callback,
        is_eager=True,
        help="Print the spaider-cli version and exit.",
    ),
) -> None:
    """SpAIder CLI — durable AI memory in one command."""
    return None


# ---------------------------------------------------------------------------
# Top-level commands (single-purpose, no sub-tree).
# ---------------------------------------------------------------------------

app.command("init", help="First-time install wizard — sets up SpAIder end-to-end.")(
    init_cmd.run
)
app.command("doctor", help="Self-check current install + offer repairs.")(
    doctor_cmd.run
)

# ---------------------------------------------------------------------------
# Nested command groups.
# ---------------------------------------------------------------------------

app.add_typer(agent_cmd.app, name="agent", help="Create / list / rotate-key / delete agents.")
app.add_typer(mcp_cmd.app, name="mcp", help="Install SpAIder into Claude Code / Cursor as an MCP server.")


if __name__ == "__main__":
    app()
