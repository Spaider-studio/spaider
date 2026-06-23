"""``spaider init`` — first-time install wizard.

Takes a fresh checkout from "git clone" to "working Studio + MCP-integrated
Claude Code" in one command. The interactive prompts can be skipped via
flags so the wizard is automation-friendly.

Order of operations:

1. Check Docker (CLI present + daemon running).
2. Prompt for LLM provider + API key; validate the key with a real probe.
3. Generate the secrets the user shouldn't have to type (JWT, connector key,
   Neo4j password).
4. Merge the values into the ``.env`` file (creating from ``.env.example`` if
   needed, preserving any user-set keys, backing up any prior version).
5. ``docker compose up -d backend-api``; wait for ``/health`` to go healthy.
6. Provision a dev agent named ``dev-${USER}`` (or rotate it if it already
   exists).
7. Install the MCP server entry + skill file into ``~/.claude/`` (skip with
   ``--skip-mcp``).
"""
from __future__ import annotations

import os
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from spaider_cli.lib import api as api_client
from spaider_cli.lib import docker as docker_lib
from spaider_cli.lib import env as env_lib
from spaider_cli.lib import llm as llm_lib
from spaider_cli.lib import mcp as mcp_lib

console = Console()
err_console = Console(stderr=True)


def run(
    repo: Path = typer.Option(
        Path.cwd(),
        "--repo",
        help="Path to the SpAIder checkout. Defaults to the current directory.",
    ),
    provider: str | None = typer.Option(
        None,
        "--provider",
        help="LLM provider: openai, anthropic, or ollama. Prompted if omitted.",
    ),
    llm_key: str | None = typer.Option(
        None,
        "--llm-key",
        help="API key for the chosen --provider. Not needed for ollama. Prompted if omitted.",
    ),
    llm_base_url: str | None = typer.Option(
        None,
        "--llm-base-url",
        help="Base URL for self-hosted providers (e.g. Ollama). Prompted for ollama if omitted.",
    ),
    skip_docker: bool = typer.Option(
        False,
        "--skip-docker",
        help="Do not run docker compose up. Useful when the stack is already running.",
    ),
    skip_mcp: bool = typer.Option(
        False,
        "--skip-mcp",
        help="Do not write to ~/.claude/. The agent + .env are still configured.",
    ),
    agent_name: str | None = typer.Option(
        None,
        "--agent",
        help="Override the auto-generated agent name (default: dev-${USER}).",
    ),
) -> None:
    """Run the first-time install wizard."""
    repo = repo.expanduser().resolve()
    console.print(
        Panel.fit(
            "[bold cyan]SpAIder init[/]\n"
            "Memory infrastructure for AI agents — let's get you set up.",
            border_style="cyan",
        )
    )

    # 1. Docker
    console.print("\n[bold][1/6] Checking Docker[/]")
    status = docker_lib.check_docker_available()
    if not status.cli_present:
        err_console.print(
            "[red]✗[/] docker CLI not found in PATH. Install Docker Desktop first: "
            "https://www.docker.com/products/docker-desktop/"
        )
        raise typer.Exit(code=1)
    if not status.daemon_running:
        err_console.print(
            f"[red]✗[/] Docker daemon not running. Start Docker Desktop and try again.\n"
            f"  Underlying error: {status.error}"
        )
        raise typer.Exit(code=1)
    console.print("  [green]✓[/] Docker CLI present + daemon running")

    # 2. LLM provider + credentials
    console.print("\n[bold][2/6] LLM provider[/]")
    provider_overrides = _configure_providers(
        provider_opt=provider, llm_key=llm_key, base_url_opt=llm_base_url,
    )

    # 3. Secrets
    # Re-run safety: NEVER regenerate a secret that already exists in .env.
    # The running Neo4j container is created with whatever NEO4J_PASSWORD was in
    # .env at first boot; rewriting it on a re-run would lock the backend out of
    # its own database (and silently invalidate JWTs / connector ciphertext).
    # So generate only the secrets that are missing/empty, and preserve the rest.
    console.print("\n[bold][3/6] Generating secrets[/]")
    env_path = repo / ".env"
    existing = (
        env_lib.parse_env_text(env_path.read_text(encoding="utf-8"))
        if env_path.exists()
        else {}
    )

    def _keep_or_generate(key: str, generator) -> tuple[str, bool]:
        current = existing.get(key, "").strip()
        return (current, True) if current else (generator(), False)

    jwt, jwt_kept = _keep_or_generate("JWT_SECRET", env_lib.generate_jwt_secret)
    connector_key, ck_kept = _keep_or_generate("CONNECTOR_SECRET_KEY", env_lib.generate_connector_key)
    neo4j_pw, neo_kept = _keep_or_generate("NEO4J_PASSWORD", env_lib.generate_neo4j_password)
    if jwt_kept or ck_kept or neo_kept:
        console.print("  [green]✓[/] Reused existing secrets where present (re-run safe)")
    else:
        console.print("  [green]✓[/] JWT signing secret, connector encryption key, Neo4j password")

    # 4. .env
    console.print("\n[bold][4/6] Writing .env[/]")
    overrides = {
        **provider_overrides,
        "JWT_SECRET": jwt,
        "CONNECTOR_SECRET_KEY": connector_key,
        "NEO4J_PASSWORD": neo4j_pw,
    }
    example_path = repo / ".env.example"
    try:
        backup = env_lib.write_env_file(
            target=env_path, example=example_path, overrides=overrides,
        )
    except FileNotFoundError as exc:
        err_console.print(f"  [red]✗[/] {exc}")
        raise typer.Exit(code=1)
    if backup:
        console.print(f"  [green]✓[/] {env_path} (backup: {backup})")
    else:
        console.print(f"  [green]✓[/] {env_path}")

    # 5. Stack up
    if skip_docker:
        console.print("\n[bold][5/6] Stack startup[/]  [dim](skipped via --skip-docker)[/]")
    else:
        console.print("\n[bold][5/6] Starting Docker stack[/]")
        compose = docker_lib.compose_up(repo)
        if not compose.ok:
            err_console.print(
                f"  [red]✗[/] docker compose up failed (rc={compose.returncode}):\n"
                f"  {compose.stderr.strip()[:400]}"
            )
            raise typer.Exit(code=1)
        console.print("  Containers starting…")
        if not docker_lib.wait_for_backend():
            err_console.print(
                "  [red]✗[/] backend-api did not become healthy within 120s. "
                "Run [bold]docker compose logs backend-api[/] to investigate."
            )
            raise typer.Exit(code=1)
        console.print("  [green]✓[/] backend-api healthy on :8000")

    # 6. Agent + MCP
    resolved_agent = agent_name or _default_agent_name()
    console.print(f"\n[bold][6/6] Provisioning agent '{resolved_agent}'[/]")
    try:
        existing = api_client.find_agent_by_name(resolved_agent)
        if existing is None:
            agent = api_client.create_agent(resolved_agent)
            agent_id = agent.id
            agent_key = agent.api_key or ""
        else:
            agent_id = existing.id
            agent_key = api_client.rotate_key(existing.id)
    except api_client.SpaiderApiError as exc:
        err_console.print(f"  [red]✗[/] agent provisioning failed: {exc}")
        raise typer.Exit(code=1)
    console.print(f"  [green]✓[/] agent ready (id={agent_id})")

    if skip_mcp:
        console.print("\n[dim]Skipping MCP install (--skip-mcp).[/]")
        _print_success_summary(agent_name=resolved_agent, agent_key=agent_key, mcp_done=False)
        return

    console.print("\n[bold]Installing MCP into Claude Code[/]")
    report = mcp_lib.install_for_claude_code(
        url="http://localhost:8000/api/v1/mcp",
        api_key=agent_key,
    )
    console.print(f"  [green]✓[/] {report.config_path}")
    console.print(f"  [green]✓[/] {report.skill_path}")

    _print_success_summary(
        agent_name=resolved_agent, agent_key=agent_key, mcp_done=True,
    )


# ---------------------------------------------------------------------------
# Prompts + final summary
# ---------------------------------------------------------------------------


# Per-provider defaults written to .env. The backend (LiteLLM) derives the
# provider prefix from LLM_PROVIDER + LLM_BASE_URL, so these only need the bare
# model id. EMBEDDING_DIMENSIONS MUST match the embedding model's real output —
# a wrong value silently breaks the Neo4j vector index.
_PROVIDERS = ("openai", "anthropic", "ollama")
_LLM_DEFAULT_MODEL = {
    "openai": "gpt-4o",
    "anthropic": "claude-sonnet-4-5",
    "ollama": "llama3.1",
}
_OLLAMA_DEFAULT_URL = "http://host.docker.internal:11434"
_EMBED_OPENAI = {"model": "text-embedding-3-small", "dims": "1536"}
_EMBED_OLLAMA = {"model": "embeddinggemma:300m", "dims": "768"}
_OPENAI_KEY_URL = "https://platform.openai.com/api-keys"
_ANTHROPIC_KEY_URL = "https://console.anthropic.com/"


def _configure_providers(
    *, provider_opt: str | None, llm_key: str | None,
    base_url_opt: str | None = None,
) -> dict[str, str]:
    """Resolve the LLM + embedding provider config and return .env overrides.

    Prompts (and validates) interactively unless the relevant flags are
    supplied (``--provider`` / ``--llm-key`` / ``--llm-base-url``).
    """
    provider = (provider_opt or _prompt_provider_choice()).lower().strip()
    if provider not in _PROVIDERS:
        err_console.print(
            f"  [red]✗[/] unknown provider '{provider}'. "
            f"Choose one of: {', '.join(_PROVIDERS)}."
        )
        raise typer.Exit(code=1)

    llm_api_key = ""
    llm_base_url = ""

    if provider == "openai":
        key = llm_key or _prompt_api_key("OpenAI", _OPENAI_KEY_URL)
        llm_api_key = _validate_openai_loop(key)
        embed = _embed_openai(llm_api_key)
    elif provider == "anthropic":
        key = llm_key or _prompt_api_key("Anthropic", _ANTHROPIC_KEY_URL)
        llm_api_key = _validate_anthropic_loop(key)
        # Anthropic has no embedding API — pick a separate embedding backend.
        embed = _configure_embeddings_for_anthropic()
    else:  # ollama — local, no key
        base_url = base_url_opt or _prompt_base_url(default=_OLLAMA_DEFAULT_URL)
        llm_base_url = _validate_ollama_loop(base_url)
        embed = _embed_ollama(llm_base_url)

    model = _LLM_DEFAULT_MODEL[provider]
    console.print(
        f"  [green]✓[/] LLM=[bold]{provider}[/] ({model}) · "
        f"embeddings=[bold]{embed['EMBEDDING_PROVIDER']}[/] "
        f"({embed['EMBEDDING_MODEL']}, {embed['EMBEDDING_DIMENSIONS']}d)"
    )
    if provider != "openai":
        console.print("  [dim]Edit LLM_MODEL in .env if you want a different model.[/]")

    return {
        "LLM_PROVIDER": provider,
        "LLM_MODEL": model,
        "LLM_API_KEY": llm_api_key,
        "LLM_BASE_URL": llm_base_url,
        **embed,
    }


def _embed_openai(api_key: str) -> dict[str, str]:
    return {
        "EMBEDDING_PROVIDER": "openai",
        "EMBEDDING_MODEL": _EMBED_OPENAI["model"],
        "EMBEDDING_API_KEY": api_key,
        "EMBEDDING_BASE_URL": "",
        "EMBEDDING_DIMENSIONS": _EMBED_OPENAI["dims"],
    }


def _embed_ollama(base_url: str) -> dict[str, str]:
    return {
        "EMBEDDING_PROVIDER": "ollama",
        "EMBEDDING_MODEL": _EMBED_OLLAMA["model"],
        "EMBEDDING_API_KEY": "",
        "EMBEDDING_BASE_URL": base_url,
        "EMBEDDING_DIMENSIONS": _EMBED_OLLAMA["dims"],
    }


def _configure_embeddings_for_anthropic() -> dict[str, str]:
    console.print(
        "  [dim]Anthropic has no embedding API — choose a separate embedding "
        "provider.[/]"
    )
    choice = Prompt.ask(
        "  Embedding provider", choices=["openai", "ollama"], default="openai",
    )
    if choice == "openai":
        key = _validate_openai_loop(
            _prompt_api_key("OpenAI (for embeddings)", _OPENAI_KEY_URL)
        )
        return _embed_openai(key)
    return _embed_ollama(_validate_ollama_loop(_prompt_base_url(default=_OLLAMA_DEFAULT_URL)))


# --- validation loops -------------------------------------------------------


def _validate_openai_loop(key: str) -> str:
    console.print("  Validating key with OpenAI…")
    result = llm_lib.validate_openai_key(key)
    while not result.ok:
        err_console.print(f"  [red]✗[/] {result.detail}")
        key = _prompt_api_key("OpenAI", _OPENAI_KEY_URL, retry=True)
        console.print("  Validating key with OpenAI…")
        result = llm_lib.validate_openai_key(key)
    console.print("  [green]✓[/] OpenAI key works")
    return key


def _validate_anthropic_loop(key: str) -> str:
    result = llm_lib.validate_anthropic_key(key)
    while not result.ok:
        err_console.print(f"  [red]✗[/] {result.detail}")
        key = _prompt_api_key("Anthropic", _ANTHROPIC_KEY_URL, retry=True)
        result = llm_lib.validate_anthropic_key(key)
    console.print("  [green]✓[/] Anthropic key accepted")
    return key


def _validate_ollama_loop(base_url: str) -> str:
    console.print(f"  Checking Ollama at {base_url}…")
    result = llm_lib.validate_ollama_local(base_url)
    while not result.ok:
        err_console.print(f"  [red]✗[/] {result.detail}")
        base_url = _prompt_base_url(default=base_url, retry=True)
        console.print(f"  Checking Ollama at {base_url}…")
        result = llm_lib.validate_ollama_local(base_url)
    console.print("  [green]✓[/] Ollama reachable")
    return base_url


# --- prompts ----------------------------------------------------------------


def _prompt_provider_choice() -> str:
    console.print("  [dim]Which LLM provider should SpAIder use?[/]")
    return Prompt.ask("  Provider", choices=list(_PROVIDERS), default="openai")


def _prompt_api_key(label: str, url: str, *, retry: bool = False) -> str:
    prefix = "Try again — your key wasn't accepted" if retry else "Get a key at"
    console.print(f"  [dim]{prefix} {'' if retry else url}[/]")
    return Prompt.ask(f"  {label} API key", password=True).strip()


def _prompt_base_url(*, default: str, retry: bool = False) -> str:
    if retry:
        console.print("  [dim]Make sure Ollama is running (`ollama serve`).[/]")
    return Prompt.ask("  Ollama base URL", default=default).strip()


def _default_agent_name() -> str:
    user = os.environ.get("USER") or os.environ.get("USERNAME") or "user"
    return f"dev-{user}"


def _print_success_summary(*, agent_name: str, agent_key: str, mcp_done: bool) -> None:
    body = (
        f"\n[bold green]🎉 SpAIder is ready.[/]\n\n"
        f"  Studio    : [link=http://localhost:3000]http://localhost:3000[/link]\n"
        f"  Agent     : [bold]{agent_name}[/]\n"
        f"  API key   : [bold yellow]{agent_key}[/]\n"
    )
    if mcp_done:
        body += (
            "\n  [yellow]Restart Claude Code[/] so it picks up the new MCP server "
            "and skill file.\n"
            "  After restart, ask Claude something like \"what do you remember about "
            "this project?\" — it should reach for [bold]spaider.list_recent[/] automatically.\n"
        )
    else:
        body += (
            "\n  [dim]Run [bold]spaider mcp install[/] when you're ready to wire "
            "SpAIder into Claude Code.[/]\n"
        )
    console.print(body)
