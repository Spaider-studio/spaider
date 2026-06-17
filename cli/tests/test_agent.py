"""Unit tests for ``spaider agent ...`` subcommands.

httpx is mocked at the boundary so these tests don't need a running backend.
"""
from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from spaider_cli.lib.api import Agent, SpaiderApiError
from spaider_cli.main import app

runner = CliRunner()


def _make_agent(name: str, agent_id: str = "agent-1", api_key: str | None = None) -> Agent:
    return Agent(id=agent_id, name=name, api_key=api_key, tenant_id="default", clearance_level=1)


class TestAgentCreate:
    def test_create_new_agent_prints_key(self):
        new_agent = _make_agent("dev-mycl", api_key="sk-newly-created")
        with patch(
            "spaider_cli.commands.agent.api_client.find_agent_by_name",
            return_value=None,
        ), patch(
            "spaider_cli.commands.agent.api_client.create_agent",
            return_value=new_agent,
        ):
            result = runner.invoke(app, ["agent", "create", "dev-mycl"])
        assert result.exit_code == 0
        assert "sk-newly-created" in result.stdout
        assert "dev-mycl" in result.stdout

    def test_create_existing_agent_rotates_key(self):
        existing = _make_agent("dev-mycl", api_key=None)
        with patch(
            "spaider_cli.commands.agent.api_client.find_agent_by_name",
            return_value=existing,
        ), patch(
            "spaider_cli.commands.agent.api_client.rotate_key",
            return_value="sk-rotated-key",
        ):
            result = runner.invoke(app, ["agent", "create", "dev-mycl"])
        assert result.exit_code == 0
        assert "sk-rotated-key" in result.stdout
        assert "rotating" in result.stdout.lower()

    def test_create_existing_agent_no_rotate_fails(self):
        existing = _make_agent("dev-mycl")
        with patch(
            "spaider_cli.commands.agent.api_client.find_agent_by_name",
            return_value=existing,
        ):
            result = runner.invoke(app, ["agent", "create", "dev-mycl", "--no-rotate"])
        assert result.exit_code == 1
        # CliRunner merges stdout + stderr into result.output by default.
        assert "already exists" in result.output

    def test_create_api_error_exits_nonzero(self):
        with patch(
            "spaider_cli.commands.agent.api_client.find_agent_by_name",
            side_effect=SpaiderApiError("backend unreachable"),
        ):
            result = runner.invoke(app, ["agent", "create", "dev-mycl"])
        assert result.exit_code == 1


class TestAgentList:
    def test_list_empty(self):
        with patch(
            "spaider_cli.commands.agent.api_client.list_agents",
            return_value=[],
        ):
            result = runner.invoke(app, ["agent", "list"])
        assert result.exit_code == 0
        assert "No agents found" in result.stdout

    def test_list_renders_table(self):
        agents = [
            _make_agent("dev-mycl", agent_id="aaa-111"),
            _make_agent("bench-acmeai", agent_id="bbb-222"),
        ]
        with patch(
            "spaider_cli.commands.agent.api_client.list_agents",
            return_value=agents,
        ):
            result = runner.invoke(app, ["agent", "list"])
        assert result.exit_code == 0
        assert "dev-mycl" in result.stdout
        assert "bench-acmeai" in result.stdout


class TestAgentRotateKey:
    def test_rotate_known_agent(self):
        existing = _make_agent("dev-mycl")
        with patch(
            "spaider_cli.commands.agent.api_client.find_agent_by_name",
            return_value=existing,
        ), patch(
            "spaider_cli.commands.agent.api_client.rotate_key",
            return_value="sk-fresh",
        ):
            result = runner.invoke(app, ["agent", "rotate-key", "dev-mycl"])
        assert result.exit_code == 0
        assert "sk-fresh" in result.stdout

    def test_rotate_unknown_agent_fails(self):
        with patch(
            "spaider_cli.commands.agent.api_client.find_agent_by_name",
            return_value=None,
        ):
            result = runner.invoke(app, ["agent", "rotate-key", "ghost"])
        assert result.exit_code == 1


class TestAgentDelete:
    def test_delete_with_yes_flag_skips_prompt(self):
        existing = _make_agent("dev-mycl")
        with patch(
            "spaider_cli.commands.agent.api_client.find_agent_by_name",
            return_value=existing,
        ), patch(
            "spaider_cli.commands.agent.api_client.delete_agent",
            return_value=None,
        ) as delete_mock:
            result = runner.invoke(app, ["agent", "delete", "dev-mycl", "--yes"])
        assert result.exit_code == 0
        delete_mock.assert_called_once_with(existing.id)

    def test_delete_unknown_agent_fails(self):
        with patch(
            "spaider_cli.commands.agent.api_client.find_agent_by_name",
            return_value=None,
        ):
            result = runner.invoke(app, ["agent", "delete", "ghost", "--yes"])
        assert result.exit_code == 1

    def test_delete_declined_at_prompt(self):
        existing = _make_agent("dev-mycl")
        with patch(
            "spaider_cli.commands.agent.api_client.find_agent_by_name",
            return_value=existing,
        ):
            # input 'n\n' declines the typer.confirm prompt
            result = runner.invoke(app, ["agent", "delete", "dev-mycl"], input="n\n")
        assert result.exit_code == 0
        assert "Aborted" in result.stdout
