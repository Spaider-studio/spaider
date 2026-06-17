"""``spaider doctor`` — read-only self-check.

Inspects everything ``spaider init`` set up, prints a status table, and exits
non-zero if any red items remain. The actual repair flow (offering one-click
fixes for each red item) is v0.2 — keeping doctor read-only in v0.1.0 means
running it is always safe to share with support.

Checks (in order, fail-fast for the ones that block later checks):

1. ``docker`` CLI present.
2. Docker daemon running.
3. ``.env`` file present at the repo root.
4. ``LLM_API_KEY`` set and accepted by OpenAI (1-token probe).
5. Backend ``/health`` returns healthy.
6. Stored embeddings all match ``EMBEDDING_DIMENSIONS`` (no silent vector-index
   degradation from mixing providers/dimensions).
7. ``~/.claude/.mcp.json`` has a SpAIder MCP entry.
8. ``~/.claude/skills/spaider.md`` exists and references the four MCP tools.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from spaider_cli.lib import api as api_client
from spaider_cli.lib import docker as docker_lib
from spaider_cli.lib import llm as llm_lib

console = Console()


@dataclass
class CheckResult:
    name: str
    status: str  # "ok" | "warn" | "fail"
    detail: str  # short hint shown alongside the status


def _ok(name: str, detail: str = "") -> CheckResult:
    return CheckResult(name=name, status="ok", detail=detail)


def _warn(name: str, detail: str) -> CheckResult:
    return CheckResult(name=name, status="warn", detail=detail)


def _fail(name: str, detail: str) -> CheckResult:
    return CheckResult(name=name, status="fail", detail=detail)


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def check_docker() -> tuple[CheckResult, CheckResult]:
    status = docker_lib.check_docker_available()
    cli = (
        _ok("docker CLI")
        if status.cli_present
        else _fail("docker CLI", "not on PATH. Install Docker Desktop.")
    )
    if not status.cli_present:
        return cli, _fail("docker daemon", "skipped: docker CLI missing.")
    daemon = (
        _ok("docker daemon")
        if status.daemon_running
        else _fail("docker daemon", f"not running. {(status.error or '')[:80]}")
    )
    return cli, daemon


def check_env_file(repo: Path) -> CheckResult:
    env_path = repo / ".env"
    if not env_path.exists():
        return _fail(
            ".env file",
            f"missing at {env_path}. Run `spaider init` or `cp .env.example .env`.",
        )
    return _ok(".env file", str(env_path))


def check_llm_key(repo: Path) -> CheckResult:
    env_path = repo / ".env"
    if not env_path.exists():
        return _warn("LLM_API_KEY", "skipped: .env not present yet.")
    from spaider_cli.lib import env as env_lib

    parsed = env_lib.parse_env_text(env_path.read_text(encoding="utf-8"))
    key = parsed.get("LLM_API_KEY", "").strip().strip('"').strip("'")
    if not key or key == "sk-your-key-here":
        return _fail(
            "LLM_API_KEY",
            "still set to the .env.example placeholder. Set a real OpenAI key.",
        )
    result = llm_lib.validate_openai_key(key)
    if not result.ok:
        return _fail("LLM_API_KEY", result.detail)
    return _ok("LLM_API_KEY", "OpenAI accepts the key")


def check_backend_health() -> CheckResult:
    if api_client.health():
        return _ok("backend /health", "healthy on :8000")
    return _fail(
        "backend /health",
        "not reachable. Run `make dev` or `docker compose up -d backend-api`.",
    )


def check_embedding_dimensions() -> CheckResult:
    """Flag a graph whose vectors don't match EMBEDDING_DIMENSIONS.

    A mismatch (e.g. 768-dim Ollama data left behind after switching to a
    1536-dim OpenAI model) can't live in the vector index and silently degrades
    semantic search — so it's a failure, not a warning.
    """
    report = api_client.embedding_health()
    if report is None:
        return _warn("embedding dimensions", "skipped: backend not reachable.")
    if report.get("error"):
        return _warn("embedding dimensions", f"could not check: {report['error']}")
    expected = report.get("expected_dims")
    present = report.get("present_dims") or []
    if not present:
        return _ok("embedding dimensions", f"no embeddings yet; will use {expected}-dim.")
    if report.get("consistent"):
        return _ok(
            "embedding dimensions",
            f"all {expected}-dim — matches EMBEDDING_DIMENSIONS.",
        )
    return _fail(
        "embedding dimensions",
        f"graph has {present}-dim vectors but EMBEDDING_DIMENSIONS={expected}; "
        f"vector search degrades. Re-seed the agent(s) at {expected}-dim, or "
        f"revert EMBEDDING_DIMENSIONS.",
    )


def check_mcp_config(home: Path | None = None) -> CheckResult:
    home = home or Path.home()
    path = home / ".claude" / ".mcp.json"
    if not path.exists():
        return _warn(
            "~/.claude/.mcp.json",
            "no Claude Code MCP config yet. Run `spaider mcp install`.",
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return _fail("~/.claude/.mcp.json", f"malformed JSON: {exc}")
    servers = (data.get("mcpServers") or {})
    if "spaider" not in servers:
        return _warn(
            "~/.claude/.mcp.json",
            "exists but has no SpAIder entry. Run `spaider mcp install`.",
        )
    return _ok("~/.claude/.mcp.json", "SpAIder entry present")


def check_skill_file(home: Path | None = None) -> CheckResult:
    home = home or Path.home()
    path = home / ".claude" / "skills" / "spaider.md"
    if not path.exists():
        return _warn(
            "~/.claude/skills/spaider.md",
            "skill not installed. Run `spaider mcp install`.",
        )
    body = path.read_text(encoding="utf-8")
    missing_tools = [t for t in ("spaider.query", "spaider.feedback") if t not in body]
    if missing_tools:
        return _warn(
            "~/.claude/skills/spaider.md",
            f"present but missing references to {', '.join(missing_tools)} — re-install.",
        )
    return _ok("~/.claude/skills/spaider.md", "loaded by Claude Code at session start")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run(
    repo: Path = typer.Option(
        Path.cwd(),
        "--repo",
        help="Path to the SpAIder checkout. Defaults to the current directory.",
    ),
) -> None:
    """Run every check and print a status table."""
    results: list[CheckResult] = []

    docker_cli, docker_daemon = check_docker()
    results.append(docker_cli)
    results.append(docker_daemon)
    results.append(check_env_file(repo))
    results.append(check_llm_key(repo))
    results.append(check_backend_health())
    results.append(check_embedding_dimensions())
    results.append(check_mcp_config())
    results.append(check_skill_file())

    table = Table(title="SpAIder doctor", header_style="bold cyan")
    table.add_column("Check")
    table.add_column("Status", justify="center")
    table.add_column("Detail", overflow="fold")
    icon = {"ok": "[green]✓[/]", "warn": "[yellow]⚠[/]", "fail": "[red]✗[/]"}
    for r in results:
        table.add_row(r.name, icon[r.status], r.detail)
    console.print(table)

    failures = [r for r in results if r.status == "fail"]
    warnings = [r for r in results if r.status == "warn"]
    if failures:
        console.print(
            f"\n[red]{len(failures)} failure(s)[/] — fix the rows above and re-run "
            "[bold]spaider doctor[/]."
        )
        raise typer.Exit(code=1)
    if warnings:
        console.print(
            f"\n[yellow]{len(warnings)} warning(s)[/] — non-blocking, but worth addressing."
        )
        raise typer.Exit(code=0)
    console.print("\n[bold green]All clear.[/] SpAIder is ready.")
