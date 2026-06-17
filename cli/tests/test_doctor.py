"""Tests for ``spaider doctor``.

Every external boundary mocked: Docker, OpenAI key probe, backend HTTP,
filesystem (Claude Code config / skill file).
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from spaider_cli.lib.docker import DockerStatus
from spaider_cli.lib.llm import KeyCheckResult
from spaider_cli.main import app

runner = CliRunner()


def _good_docker():
    return DockerStatus(cli_present=True, daemon_running=True)


def _prime_env(tmp_path: Path, *, llm_key: str = "sk-good") -> Path:
    (tmp_path / ".env").write_text(
        f"LLM_API_KEY={llm_key}\n"
        "JWT_SECRET=ignore-me\n"
        "CONNECTOR_SECRET_KEY=also-ignore\n"
    )
    return tmp_path


def _prime_claude_dir(home: Path, *, with_spaider: bool = True, with_skill: bool = True) -> None:
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    mcp_config = home / ".claude" / ".mcp.json"
    if with_spaider:
        mcp_config.write_text(json.dumps({"mcpServers": {"spaider": {}}}))
    else:
        mcp_config.write_text(json.dumps({"mcpServers": {}}))
    if with_skill:
        skill_dir = home / ".claude" / "skills"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "spaider.md").write_text(
            "name: spaider\nspaider.query\nspaider.feedback\n"
        )


class TestDoctorAllGreen:
    def test_all_green_exits_zero(self, tmp_path: Path, monkeypatch):
        repo = _prime_env(tmp_path)
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        _prime_claude_dir(home)

        with patch(
            "spaider_cli.commands.doctor.docker_lib.check_docker_available",
            return_value=_good_docker(),
        ), patch(
            "spaider_cli.commands.doctor.llm_lib.validate_openai_key",
            return_value=KeyCheckResult(ok=True, detail=""),
        ), patch(
            "spaider_cli.commands.doctor.api_client.health",
            return_value=True,
        ), patch(
            "spaider_cli.commands.doctor.api_client.embedding_health",
            return_value={
                "expected_dims": 1536, "present_dims": [1536],
                "embedded_nodes": 5, "consistent": True,
            },
        ):
            result = runner.invoke(app, ["doctor", "--repo", str(repo)])

        assert result.exit_code == 0, result.output
        assert "All clear" in result.output


class TestDoctorRedItems:
    def test_no_docker_fails(self, tmp_path: Path, monkeypatch):
        repo = _prime_env(tmp_path)
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))

        with patch(
            "spaider_cli.commands.doctor.docker_lib.check_docker_available",
            return_value=DockerStatus(cli_present=False, daemon_running=False),
        ), patch(
            "spaider_cli.commands.doctor.llm_lib.validate_openai_key",
            return_value=KeyCheckResult(ok=True, detail=""),
        ), patch(
            "spaider_cli.commands.doctor.api_client.health",
            return_value=False,
        ):
            result = runner.invoke(app, ["doctor", "--repo", str(repo)])

        assert result.exit_code == 1
        assert "docker CLI" in result.output
        assert "failure" in result.output.lower()

    def test_placeholder_llm_key_fails(self, tmp_path: Path, monkeypatch):
        repo = _prime_env(tmp_path, llm_key="sk-your-key-here")
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        _prime_claude_dir(home)

        with patch(
            "spaider_cli.commands.doctor.docker_lib.check_docker_available",
            return_value=_good_docker(),
        ), patch(
            "spaider_cli.commands.doctor.api_client.health",
            return_value=True,
        ):
            result = runner.invoke(app, ["doctor", "--repo", str(repo)])

        assert result.exit_code == 1
        assert "placeholder" in result.output.lower()

    def test_missing_env_fails(self, tmp_path: Path, monkeypatch):
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))

        with patch(
            "spaider_cli.commands.doctor.docker_lib.check_docker_available",
            return_value=_good_docker(),
        ), patch(
            "spaider_cli.commands.doctor.api_client.health",
            return_value=False,
        ):
            result = runner.invoke(app, ["doctor", "--repo", str(tmp_path)])

        assert result.exit_code == 1
        assert ".env" in result.output


class TestDoctorEmbeddingDimensions:
    def test_dimension_mismatch_fails(self, tmp_path: Path, monkeypatch):
        repo = _prime_env(tmp_path)
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        _prime_claude_dir(home)

        with patch(
            "spaider_cli.commands.doctor.docker_lib.check_docker_available",
            return_value=_good_docker(),
        ), patch(
            "spaider_cli.commands.doctor.llm_lib.validate_openai_key",
            return_value=KeyCheckResult(ok=True, detail=""),
        ), patch(
            "spaider_cli.commands.doctor.api_client.health",
            return_value=True,
        ), patch(
            "spaider_cli.commands.doctor.api_client.embedding_health",
            return_value={
                "expected_dims": 1536, "present_dims": [768],
                "embedded_nodes": 21751, "consistent": False,
            },
        ):
            result = runner.invoke(app, ["doctor", "--repo", str(repo)])

        assert result.exit_code == 1
        assert "768" in result.output and "1536" in result.output

    def test_no_embeddings_is_ok(self, tmp_path: Path, monkeypatch):
        repo = _prime_env(tmp_path)
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        _prime_claude_dir(home)

        with patch(
            "spaider_cli.commands.doctor.docker_lib.check_docker_available",
            return_value=_good_docker(),
        ), patch(
            "spaider_cli.commands.doctor.llm_lib.validate_openai_key",
            return_value=KeyCheckResult(ok=True, detail=""),
        ), patch(
            "spaider_cli.commands.doctor.api_client.health",
            return_value=True,
        ), patch(
            "spaider_cli.commands.doctor.api_client.embedding_health",
            return_value={
                "expected_dims": 1536, "present_dims": [],
                "embedded_nodes": 0, "consistent": True,
            },
        ):
            result = runner.invoke(app, ["doctor", "--repo", str(repo)])

        assert result.exit_code == 0, result.output
        assert "All clear" in result.output


class TestDoctorWarnings:
    def test_missing_mcp_is_warn_not_fail(self, tmp_path: Path, monkeypatch):
        """No ~/.claude/.mcp.json should warn — not block the install."""
        repo = _prime_env(tmp_path)
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        # Deliberately no claude dir.

        with patch(
            "spaider_cli.commands.doctor.docker_lib.check_docker_available",
            return_value=_good_docker(),
        ), patch(
            "spaider_cli.commands.doctor.llm_lib.validate_openai_key",
            return_value=KeyCheckResult(ok=True, detail=""),
        ), patch(
            "spaider_cli.commands.doctor.api_client.health",
            return_value=True,
        ):
            result = runner.invoke(app, ["doctor", "--repo", str(repo)])

        assert result.exit_code == 0
        assert "warning" in result.output.lower()

    def test_mcp_without_spaider_entry_is_warn(self, tmp_path: Path, monkeypatch):
        repo = _prime_env(tmp_path)
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        _prime_claude_dir(home, with_spaider=False)

        with patch(
            "spaider_cli.commands.doctor.docker_lib.check_docker_available",
            return_value=_good_docker(),
        ), patch(
            "spaider_cli.commands.doctor.llm_lib.validate_openai_key",
            return_value=KeyCheckResult(ok=True, detail=""),
        ), patch(
            "spaider_cli.commands.doctor.api_client.health",
            return_value=True,
        ):
            result = runner.invoke(app, ["doctor", "--repo", str(repo)])

        assert result.exit_code == 0
        assert "warning" in result.output.lower()
