"""Smoke tests for ``spaider init`` orchestration.

Every external boundary is mocked: Docker, OpenAI HTTP, SpAIder API, MCP
filesystem. We're asserting the orchestration calls each layer in the right
order with the right inputs — not the layers themselves (those have their own
unit tests in test_env / test_mcp / etc.).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from spaider_cli.lib.api import Agent
from spaider_cli.lib.docker import ComposeResult, DockerStatus
from spaider_cli.lib.env import parse_env_text
from spaider_cli.lib.llm import KeyCheckResult
from spaider_cli.lib.mcp import InstallReport
from spaider_cli.main import app

runner = CliRunner()


def _good_docker():
    return DockerStatus(cli_present=True, daemon_running=True)


def _ok_compose():
    return ComposeResult(ok=True, stdout="", stderr="", returncode=0)


def _prime_env_example(repo: Path) -> Path:
    (repo / ".env.example").write_text(
        "LLM_API_KEY=sk-your-key-here\n"
        "EMBEDDING_API_KEY=\n"
        "JWT_SECRET=change-me\n"
        "CONNECTOR_SECRET_KEY=\n"
        "NEO4J_PASSWORD=spaider-dev-2024\n"
    )
    return repo


def test_init_full_happy_path(tmp_path: Path, monkeypatch):
    repo = _prime_env_example(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
    (tmp_path / "fake-home").mkdir()
    monkeypatch.setenv("USER", "tester")

    with patch("spaider_cli.commands.init.docker_lib.check_docker_available",
               return_value=_good_docker()), \
         patch("spaider_cli.commands.init.llm_lib.validate_openai_key",
               return_value=KeyCheckResult(ok=True, detail="")), \
         patch("spaider_cli.commands.init.docker_lib.compose_up",
               return_value=_ok_compose()), \
         patch("spaider_cli.commands.init.docker_lib.wait_for_backend",
               return_value=True), \
         patch("spaider_cli.commands.init.api_client.find_agent_by_name",
               return_value=None), \
         patch("spaider_cli.commands.init.api_client.create_agent",
               return_value=Agent(id="aaa-111", name="dev-tester", api_key="sk-init-key")), \
         patch("spaider_cli.commands.init.mcp_lib.install_for_claude_code",
               return_value=InstallReport(
                   config_path=tmp_path / "fake-home" / ".claude" / ".mcp.json",
                   config_backup=None,
                   skill_path=tmp_path / "fake-home" / ".claude" / "skills" / "spaider.md",
                   skill_backup=None,
               )):
        result = runner.invoke(
            app,
            ["init", "--repo", str(repo), "--provider", "openai", "--llm-key", "sk-input-key"],
        )

    assert result.exit_code == 0, result.output
    # .env written with our overrides
    env_body = (repo / ".env").read_text()
    assert "LLM_API_KEY=sk-input-key" in env_body
    assert "JWT_SECRET=" in env_body
    assert "CONNECTOR_SECRET_KEY=" in env_body
    # Final summary references the agent + key
    assert "dev-tester" in result.output
    assert "sk-init-key" in result.output
    assert "Restart Claude Code" in result.output


def test_init_rerun_preserves_existing_secrets(tmp_path: Path, monkeypatch):
    """Re-running ``init`` must NOT regenerate secrets already in .env.

    The running Neo4j container is created with whatever NEO4J_PASSWORD was in
    .env at first boot; rewriting it on a re-run would lock the backend out of
    its own database. Same hazard for JWT (invalidates tokens) and the
    connector key (orphans encrypted credentials).
    """
    repo = _prime_env_example(tmp_path)
    monkeypatch.setenv("USER", "tester")

    def _run():
        with patch("spaider_cli.commands.init.docker_lib.check_docker_available",
                   return_value=_good_docker()), \
             patch("spaider_cli.commands.init.llm_lib.validate_openai_key",
                   return_value=KeyCheckResult(ok=True, detail="")), \
             patch("spaider_cli.commands.init.api_client.find_agent_by_name",
                   return_value=None), \
             patch("spaider_cli.commands.init.api_client.create_agent",
                   return_value=Agent(id="r1", name="dev-tester", api_key="sk-x")):
            return runner.invoke(
                app,
                ["init", "--repo", str(repo), "--provider", "openai",
                 "--llm-key", "sk-input-key", "--skip-docker", "--skip-mcp"],
            )

    r1 = _run()
    assert r1.exit_code == 0, r1.output
    env1 = parse_env_text((repo / ".env").read_text())
    # first run replaced the .env.example placeholders with real generated secrets
    assert env1["NEO4J_PASSWORD"] != "spaider-dev-2024"
    assert env1["JWT_SECRET"] != "change-me"

    r2 = _run()
    assert r2.exit_code == 0, r2.output
    body2 = (repo / ".env").read_text()
    env2 = parse_env_text(body2)
    # the whole point: secrets are identical across the re-run
    assert env2["NEO4J_PASSWORD"] == env1["NEO4J_PASSWORD"]
    assert env2["JWT_SECRET"] == env1["JWT_SECRET"]
    assert env2["CONNECTOR_SECRET_KEY"] == env1["CONNECTOR_SECRET_KEY"]
    # and not duplicated
    assert body2.count("NEO4J_PASSWORD=") == 1


def test_init_openai_writes_provider_metadata(tmp_path: Path, monkeypatch):
    repo = _prime_env_example(tmp_path)
    monkeypatch.setenv("USER", "tester")
    with patch("spaider_cli.commands.init.docker_lib.check_docker_available",
               return_value=_good_docker()), \
         patch("spaider_cli.commands.init.llm_lib.validate_openai_key",
               return_value=KeyCheckResult(ok=True, detail="")), \
         patch("spaider_cli.commands.init.api_client.find_agent_by_name",
               return_value=None), \
         patch("spaider_cli.commands.init.api_client.create_agent",
               return_value=Agent(id="ccc", name="dev-tester", api_key="sk-key3")):
        result = runner.invoke(
            app,
            ["init", "--repo", str(repo), "--provider", "openai", "--llm-key", "sk-input-key",
             "--skip-docker", "--skip-mcp"],
        )
    assert result.exit_code == 0, result.output
    env_body = (repo / ".env").read_text()
    assert "LLM_PROVIDER=openai" in env_body
    assert "EMBEDDING_PROVIDER=openai" in env_body
    assert "EMBEDDING_DIMENSIONS=1536" in env_body
    assert "EMBEDDING_API_KEY=sk-input-key" in env_body


def test_init_ollama_provider_non_interactive(tmp_path: Path, monkeypatch):
    repo = _prime_env_example(tmp_path)
    monkeypatch.setenv("USER", "tester")
    with patch("spaider_cli.commands.init.docker_lib.check_docker_available",
               return_value=_good_docker()), \
         patch("spaider_cli.commands.init.llm_lib.validate_ollama_local",
               return_value=KeyCheckResult(ok=True, detail="")), \
         patch("spaider_cli.commands.init.api_client.find_agent_by_name",
               return_value=None), \
         patch("spaider_cli.commands.init.api_client.create_agent",
               return_value=Agent(id="ddd", name="dev-tester", api_key="sk-key4")):
        result = runner.invoke(
            app,
            ["init", "--repo", str(repo), "--provider", "ollama",
             "--llm-base-url", "http://host.docker.internal:11434",
             "--skip-docker", "--skip-mcp"],
        )
    assert result.exit_code == 0, result.output
    env_body = (repo / ".env").read_text()
    assert "LLM_PROVIDER=ollama" in env_body
    assert "EMBEDDING_PROVIDER=ollama" in env_body
    assert "EMBEDDING_DIMENSIONS=768" in env_body
    assert "EMBEDDING_BASE_URL=http://host.docker.internal:11434" in env_body
    # Ollama is keyless.
    assert "LLM_API_KEY=\n" in env_body or env_body.rstrip().endswith("LLM_API_KEY=")


def test_init_anthropic_provider_with_openai_embeddings(tmp_path: Path, monkeypatch):
    repo = _prime_env_example(tmp_path)
    monkeypatch.setenv("USER", "tester")
    with patch("spaider_cli.commands.init.docker_lib.check_docker_available",
               return_value=_good_docker()), \
         patch("spaider_cli.commands.init.llm_lib.validate_anthropic_key",
               return_value=KeyCheckResult(ok=True, detail="")), \
         patch("spaider_cli.commands.init.llm_lib.validate_openai_key",
               return_value=KeyCheckResult(ok=True, detail="")), \
         patch("spaider_cli.commands.init.api_client.find_agent_by_name",
               return_value=None), \
         patch("spaider_cli.commands.init.api_client.create_agent",
               return_value=Agent(id="eee", name="dev-tester", api_key="sk-key5")):
        result = runner.invoke(
            app,
            ["init", "--repo", str(repo), "--provider", "anthropic",
             "--llm-key", "sk-ant-test", "--skip-docker", "--skip-mcp"],
            # embedding-provider choice (default openai), then OpenAI embedding key
            input="\nsk-embed-key\n",
        )
    assert result.exit_code == 0, result.output
    env_body = (repo / ".env").read_text()
    assert "LLM_PROVIDER=anthropic" in env_body
    assert "LLM_API_KEY=sk-ant-test" in env_body
    assert "EMBEDDING_PROVIDER=openai" in env_body
    assert "EMBEDDING_DIMENSIONS=1536" in env_body
    assert "EMBEDDING_API_KEY=sk-embed-key" in env_body


def test_init_fails_loud_without_docker(tmp_path: Path):
    _prime_env_example(tmp_path)
    with patch(
        "spaider_cli.commands.init.docker_lib.check_docker_available",
        return_value=DockerStatus(cli_present=False, daemon_running=False),
    ):
        result = runner.invoke(
            app,
            ["init", "--repo", str(tmp_path), "--provider", "openai", "--llm-key", "sk-input-key"],
        )
    assert result.exit_code == 1
    assert "docker cli not found" in result.output.lower()


def test_init_fails_loud_when_daemon_off(tmp_path: Path):
    _prime_env_example(tmp_path)
    with patch(
        "spaider_cli.commands.init.docker_lib.check_docker_available",
        return_value=DockerStatus(cli_present=True, daemon_running=False, error="boom"),
    ):
        result = runner.invoke(
            app,
            ["init", "--repo", str(tmp_path), "--provider", "openai", "--llm-key", "sk-input-key"],
        )
    assert result.exit_code == 1
    assert "daemon not running" in result.output.lower()


def test_init_skip_docker_and_skip_mcp(tmp_path: Path, monkeypatch):
    _prime_env_example(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
    (tmp_path / "fake-home").mkdir()
    monkeypatch.setenv("USER", "tester")
    with patch("spaider_cli.commands.init.docker_lib.check_docker_available",
               return_value=_good_docker()), \
         patch("spaider_cli.commands.init.llm_lib.validate_openai_key",
               return_value=KeyCheckResult(ok=True, detail="")), \
         patch("spaider_cli.commands.init.api_client.find_agent_by_name",
               return_value=None), \
         patch("spaider_cli.commands.init.api_client.create_agent",
               return_value=Agent(id="bbb", name="dev-tester", api_key="sk-key2")):
        result = runner.invoke(
            app,
            [
                "init",
                "--repo", str(tmp_path),
                "--provider", "openai", "--llm-key", "sk-input-key",
                "--skip-docker",
                "--skip-mcp",
            ],
        )
    assert result.exit_code == 0, result.output
    assert "skipped via --skip-docker" in result.output
    assert "Skipping MCP install" in result.output


def test_init_compose_failure_exits_nonzero(tmp_path: Path):
    _prime_env_example(tmp_path)
    with patch("spaider_cli.commands.init.docker_lib.check_docker_available",
               return_value=_good_docker()), \
         patch("spaider_cli.commands.init.llm_lib.validate_openai_key",
               return_value=KeyCheckResult(ok=True, detail="")), \
         patch("spaider_cli.commands.init.docker_lib.compose_up",
               return_value=ComposeResult(
                   ok=False, stdout="", stderr="port already in use", returncode=1,
               )):
        result = runner.invoke(
            app,
            ["init", "--repo", str(tmp_path), "--provider", "openai", "--llm-key", "sk-input-key"],
        )
    assert result.exit_code == 1
    assert "docker compose up failed" in result.output.lower()


def test_init_health_timeout_exits_nonzero(tmp_path: Path):
    _prime_env_example(tmp_path)
    with patch("spaider_cli.commands.init.docker_lib.check_docker_available",
               return_value=_good_docker()), \
         patch("spaider_cli.commands.init.llm_lib.validate_openai_key",
               return_value=KeyCheckResult(ok=True, detail="")), \
         patch("spaider_cli.commands.init.docker_lib.compose_up",
               return_value=_ok_compose()), \
         patch("spaider_cli.commands.init.docker_lib.wait_for_backend",
               return_value=False):
        result = runner.invoke(
            app,
            ["init", "--repo", str(tmp_path), "--provider", "openai", "--llm-key", "sk-input-key"],
        )
    assert result.exit_code == 1
    assert "did not become healthy" in result.output.lower()
