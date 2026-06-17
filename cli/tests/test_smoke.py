"""Smoke tests for the CLI scaffold.

These confirm the package imports cleanly, the typer app is wired up, and the
shipped skill file is reachable as package data. The real subcommand logic is
tested in dedicated files (test_init_flow.py, test_mcp_merge.py, etc.)
once those subcommands land.
"""
from __future__ import annotations

import importlib.resources

from typer.testing import CliRunner

from spaider_cli.main import app

runner = CliRunner()


def test_app_imports():
    """The Typer app must be importable without side effects."""
    assert app is not None
    assert app.info.name == "spaider"


def test_help_command():
    """`spaider --help` must run and list every top-level command."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("init", "doctor", "agent", "mcp"):
        assert cmd in result.stdout, f"command '{cmd}' missing from --help output"


def test_version_flag():
    """`spaider --version` returns the package version and exits 0."""
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "spaider-cli" in result.stdout


def test_init_command_registered():
    """`spaider init --help` confirms the command is wired up.

    Real wizard orchestration (with everything mocked) lives in test_init.py.
    """
    result = runner.invoke(app, ["init", "--help"])
    assert result.exit_code == 0
    assert "install" in result.output.lower() or "wizard" in result.output.lower()


def test_doctor_command_registered():
    """`spaider doctor --help` confirms the command is wired up.

    Real doctor checks (with everything mocked) live in test_doctor.py.
    """
    result = runner.invoke(app, ["doctor", "--help"])
    assert result.exit_code == 0
    assert "check" in result.output.lower() or "doctor" in result.output.lower()


def test_agent_create_argparse():
    """`spaider agent create` must require the name argument.

    Real subcommand behaviour (with mocked httpx) is covered in test_agent.py.
    """
    result = runner.invoke(app, ["agent", "create"])
    # Missing required argument → typer exits non-zero with usage info.
    assert result.exit_code != 0
    assert "name" in result.output.lower() or "missing" in result.output.lower()


def test_mcp_install_command_registered():
    """`spaider mcp install --help` confirms the command is wired up.

    Real install behaviour (filesystem mocked) is covered in test_mcp.py.
    """
    result = runner.invoke(app, ["mcp", "install", "--help"])
    assert result.exit_code == 0
    assert "install" in result.output.lower()


def test_skill_file_is_packaged():
    """The Claude Code skill markdown must be reachable via importlib.resources.

    This guards against accidental removal of the package_data declaration
    in pyproject.toml — without it, the wheel ships without the skill and
    `spaider mcp install` would silently fail at runtime.
    """
    skill = importlib.resources.files("spaider_cli.skills").joinpath("claude_code.md")
    assert skill.is_file()
    content = skill.read_text(encoding="utf-8")
    assert "name: spaider" in content
    assert "spaider.query" in content
    assert "spaider.feedback" in content
