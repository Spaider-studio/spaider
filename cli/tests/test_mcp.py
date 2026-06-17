"""Unit tests for ``spaider mcp install`` + the underlying mcp_lib helpers.

Filesystem operations use ``tmp_path`` so nothing touches the real
``~/.claude/.mcp.json``. The API client is mocked at its module boundary.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from spaider_cli.lib import mcp as mcp_lib
from spaider_cli.lib.api import Agent
from spaider_cli.main import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Pure-function tests on the merge / atomic-write helpers
# ---------------------------------------------------------------------------


class TestMergeMcpServer:
    def test_empty_existing_creates_servers_block(self):
        merged = mcp_lib.merge_mcp_server(
            existing={},
            url="http://localhost:8000/api/v1/mcp/sse",
            api_key="sk-test",
        )
        assert "mcpServers" in merged
        assert "spaider" in merged["mcpServers"]
        assert merged["mcpServers"]["spaider"]["url"].endswith("/mcp/sse")
        assert merged["mcpServers"]["spaider"]["headers"]["Authorization"] == "Bearer sk-test"

    def test_preserves_other_servers(self):
        existing = {
            "mcpServers": {
                "filesystem": {"command": "fs-server"},
                "github": {"command": "gh-server"},
            }
        }
        merged = mcp_lib.merge_mcp_server(
            existing=existing,
            url="http://localhost:8000/api/v1/mcp/sse",
            api_key="sk-test",
        )
        assert set(merged["mcpServers"]) == {"filesystem", "github", "spaider"}

    def test_overwrites_existing_spaider_entry(self):
        existing = {"mcpServers": {"spaider": {"url": "stale", "headers": {}}}}
        merged = mcp_lib.merge_mcp_server(
            existing=existing,
            url="http://localhost:8000/api/v1/mcp/sse",
            api_key="sk-new",
        )
        assert merged["mcpServers"]["spaider"]["url"].endswith("/mcp/sse")
        assert merged["mcpServers"]["spaider"]["headers"]["Authorization"] == "Bearer sk-new"

    def test_preserves_top_level_keys(self):
        existing = {"someUnrelatedKey": True, "mcpServers": {}}
        merged = mcp_lib.merge_mcp_server(
            existing=existing,
            url="http://localhost:8000/api/v1/mcp/sse",
            api_key="sk-test",
        )
        assert merged["someUnrelatedKey"] is True


class TestAtomicWrite:
    def test_atomic_write_text(self, tmp_path: Path):
        target = tmp_path / "config" / "file.txt"
        mcp_lib.atomic_write_text(target, "hello")
        assert target.read_text() == "hello"

    def test_atomic_write_json(self, tmp_path: Path):
        target = tmp_path / "config.json"
        mcp_lib.atomic_write_json(target, {"a": 1, "b": "two"})
        loaded = json.loads(target.read_text())
        assert loaded == {"a": 1, "b": "two"}

    def test_backup_if_exists(self, tmp_path: Path):
        target = tmp_path / "original.json"
        target.write_text("v1-content")
        backup = mcp_lib.backup_if_exists(target)
        assert backup is not None
        assert backup.read_text() == "v1-content"
        assert backup.name.startswith("original.json.")
        assert backup.name.endswith(".bak")

    def test_backup_returns_none_when_missing(self, tmp_path: Path):
        assert mcp_lib.backup_if_exists(tmp_path / "does-not-exist") is None


class TestReadMcpConfig:
    def test_missing_returns_empty_dict(self, tmp_path: Path):
        assert mcp_lib.read_mcp_config(tmp_path / "ghost.json") == {}

    def test_valid_json_parses(self, tmp_path: Path):
        target = tmp_path / "ok.json"
        target.write_text('{"mcpServers": {}}')
        assert mcp_lib.read_mcp_config(target) == {"mcpServers": {}}

    def test_malformed_raises(self, tmp_path: Path):
        target = tmp_path / "bad.json"
        target.write_text("{not json")
        with pytest.raises(mcp_lib.MalformedConfigError):
            mcp_lib.read_mcp_config(target)


# ---------------------------------------------------------------------------
# install_for_claude_code orchestration
# ---------------------------------------------------------------------------


class TestInstallForClaudeCode:
    def test_fresh_install_creates_files(self, tmp_path: Path):
        report = mcp_lib.install_for_claude_code(
            url="http://localhost:8000/api/v1/mcp/sse",
            api_key="sk-fresh",
            home=tmp_path,
        )
        assert report.config_backup is None
        assert report.skill_backup is None
        # Config file with spaider entry.
        data = json.loads(report.config_path.read_text())
        assert data["mcpServers"]["spaider"]["headers"]["Authorization"] == "Bearer sk-fresh"
        # Skill file with expected marker content.
        skill_text = report.skill_path.read_text()
        assert "spaider.query" in skill_text
        assert "feedback" in skill_text

    def test_reinstall_preserves_other_servers_and_backs_up(self, tmp_path: Path):
        # Prime with an existing config + skill.
        config = tmp_path / ".claude" / ".mcp.json"
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
        skill = tmp_path / ".claude" / "skills" / "spaider.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("OLD SKILL CONTENT")

        report = mcp_lib.install_for_claude_code(
            url="http://localhost:8000/api/v1/mcp/sse",
            api_key="sk-second",
            home=tmp_path,
        )
        assert report.config_backup is not None
        assert report.skill_backup is not None
        # Other server preserved.
        data = json.loads(report.config_path.read_text())
        assert "other" in data["mcpServers"]
        assert "spaider" in data["mcpServers"]
        # Skill file overwritten with packaged content.
        assert "spaider.query" in report.skill_path.read_text()
        # Backups have the previous content.
        assert report.skill_backup.read_text() == "OLD SKILL CONTENT"


class TestClaudeCodeScope:
    def test_user_scope_targets_home(self, tmp_path: Path):
        paths = mcp_lib.claude_code_paths(scope="user", home=tmp_path)
        assert paths.mcp_config == tmp_path / ".claude" / ".mcp.json"
        assert paths.skill_file == tmp_path / ".claude" / "skills" / "spaider.md"

    def test_project_scope_targets_repo_root(self, tmp_path: Path):
        paths = mcp_lib.claude_code_paths(scope="project", project_root=tmp_path)
        assert paths.mcp_config == tmp_path / ".mcp.json"
        assert paths.skill_file == tmp_path / ".claude" / "skills" / "spaider.md"

    def test_install_project_scope_writes_repo_files(self, tmp_path: Path):
        report = mcp_lib.install_for_claude_code(
            url="http://localhost:8000/api/v1/mcp/sse",
            api_key="sk-proj",
            scope="project",
            project_root=tmp_path,
        )
        # MCP config lands at the repo root, not under ~/.claude.
        assert report.config_path == tmp_path / ".mcp.json"
        data = json.loads(report.config_path.read_text())
        assert data["mcpServers"]["spaider"]["headers"]["Authorization"] == "Bearer sk-proj"
        # Project-level skill.
        assert report.skill_path == tmp_path / ".claude" / "skills" / "spaider.md"
        assert "spaider.query" in report.skill_path.read_text()


# ---------------------------------------------------------------------------
# install_for_cursor orchestration
# ---------------------------------------------------------------------------


class TestInstallForCursor:
    def test_fresh_install_writes_cursorrules(self, tmp_path: Path):
        report = mcp_lib.install_for_cursor(
            project_root=tmp_path,
            url="http://localhost:8000/api/v1/mcp/sse",
            api_key="sk-cursor",
        )
        assert report.config_backup is None
        content = report.config_path.read_text()
        assert "spaider" in content.lower()
        assert "sk-cursor" in content

    def test_appends_when_existing_unrelated_rules(self, tmp_path: Path):
        existing_rules = "# my project rules\nuse snake_case for variables\n"
        (tmp_path / ".cursorrules").write_text(existing_rules)

        report = mcp_lib.install_for_cursor(
            project_root=tmp_path,
            url="http://localhost:8000/api/v1/mcp/sse",
            api_key="sk-cursor",
        )
        content = report.config_path.read_text()
        assert "my project rules" in content       # preserved
        assert "use snake_case" in content
        assert "spaider" in content.lower()
        assert report.config_backup is not None    # backup created


# ---------------------------------------------------------------------------
# CLI integration — `spaider mcp install`
# ---------------------------------------------------------------------------


class TestMcpInstallCommand:
    def test_install_with_explicit_api_key(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        result = runner.invoke(
            app,
            ["mcp", "install", "--api-key", "sk-explicit"],
        )
        assert result.exit_code == 0, result.output
        config = tmp_path / ".claude" / ".mcp.json"
        data = json.loads(config.read_text())
        assert data["mcpServers"]["spaider"]["headers"]["Authorization"] == "Bearer sk-explicit"
        assert "Restart Claude Code" in result.output

    def test_install_resolves_agent_via_api(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        agent = Agent(id="aaa-111", name="dev-mycl", api_key=None)
        with patch(
            "spaider_cli.commands.mcp.api_client.find_agent_by_name",
            return_value=agent,
        ), patch(
            "spaider_cli.commands.mcp.api_client.rotate_key",
            return_value="sk-rotated-cli",
        ):
            result = runner.invoke(
                app,
                ["mcp", "install", "--agent", "dev-mycl"],
            )
        assert result.exit_code == 0, result.output
        config = tmp_path / ".claude" / ".mcp.json"
        data = json.loads(config.read_text())
        assert data["mcpServers"]["spaider"]["headers"]["Authorization"] == "Bearer sk-rotated-cli"

    def test_install_handles_api_unreachable(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        from spaider_cli.lib.api import SpaiderApiError
        with patch(
            "spaider_cli.commands.mcp.api_client.find_agent_by_name",
            side_effect=SpaiderApiError("connection refused"),
        ):
            result = runner.invoke(app, ["mcp", "install"])
        assert result.exit_code == 1
        assert "connection refused" in result.output or "could not resolve" in result.output

    def test_install_for_unsupported_client_fails(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        result = runner.invoke(
            app,
            ["mcp", "install", "--api-key", "sk-x", "--for", "windsurf"],
        )
        assert result.exit_code == 2
        assert "unsupported" in result.output.lower()

    def test_install_project_scope_writes_local_mcp_json(self, tmp_path: Path, monkeypatch):
        # chdir so Path.cwd() inside the command resolves to the temp repo.
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(
            app,
            ["mcp", "install", "--api-key", "sk-proj", "--scope", "project"],
        )
        assert result.exit_code == 0, result.output
        local = tmp_path / ".mcp.json"
        assert local.exists()
        data = json.loads(local.read_text())
        assert data["mcpServers"]["spaider"]["headers"]["Authorization"] == "Bearer sk-proj"
        # Warns the user to gitignore the secret.
        assert ".gitignore" in result.output

    def test_install_rejects_bad_scope(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        result = runner.invoke(
            app,
            ["mcp", "install", "--api-key", "sk-x", "--scope", "galaxy"],
        )
        assert result.exit_code == 2
        assert "scope" in result.output.lower()
