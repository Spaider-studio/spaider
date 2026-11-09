"""Working with what a knowledge base remembers."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import httpx
import typer
from rich.console import Console

from spaider_cli.lib import client, config

console = Console()
_AGENT = typer.Option(None, "--agent", "-a", help="Knowledge base id. Defaults to the logged-in one.")


def ingest(
    text: Optional[str] = typer.Argument(None, help="Text to ingest."),
    file: Optional[Path] = typer.Option(None, "--file", "-f", exists=True, dir_okay=False),
    source: Optional[str] = typer.Option(None, "--source"),
    agent: Optional[str] = _AGENT,
) -> None:
    """Extract knowledge from text into the graph."""
    content = file.read_text(encoding="utf-8") if file else text
    if not content:
        console.print("[red]✗[/] Give some text or --file.")
        raise typer.Exit(code=1)
    body = {"text": content, "agent_id": client.agent_or_exit(agent), "source": source}
    result = client.request("POST", "/ingest/sync", json=body)
    console.print(
        f"[green]✓[/] Nodes {result['nodes_created']} created, {result['nodes_merged']} merged; "
        f"edges {result['edges_created']}"
    )


def recall(
    text: str = typer.Argument(..., help="What to look for."),
    limit: int = typer.Option(10, "--limit", "-n"),
    federated: bool = typer.Option(False, "--federated", help="Also search KBs shared with this one."),
    agent: Optional[str] = _AGENT,
) -> None:
    """Search memory directly, with no LLM call."""
    body = {"agent_id": client.agent_or_exit(agent), "query": text, "limit": limit, "federated": federated}
    results = client.request("POST", "/recall", json=body)["results"]
    if not results:
        console.print("[dim]Nothing found.[/]")
    for item in results:
        console.print(f"[cyan]{item['type']:7}[/] {item['text']}", highlight=False)


def query(
    question: str = typer.Argument(...),
    agent: Optional[str] = _AGENT,
) -> None:
    """Ask a question and get a cited answer."""
    result = client.request("POST", "/query", json={"question": question, "agent_id": client.agent_or_exit(agent)})
    console.print(result["answer"], highlight=False)
    for item in result.get("citations", []):
        console.print(f"[dim][{item['ref']}] {item['label']}[/]", highlight=False)
    if result.get("query_id"):
        console.print(f"[dim]query_id: {result['query_id']}[/]")


def sleep(
    agent: Optional[str] = _AGENT,
    report: bool = typer.Option(False, "--report", help="Show the last report instead of running."),
) -> None:
    """Run the consolidation cycle now."""
    kb = client.agent_or_exit(agent)
    if report:
        result = client.request("GET", f"/agents/{kb}/sleep/report")
    else:
        result = client.request("POST", f"/agents/{kb}/sleep", timeout=3600)
    console.print_json(json.dumps(result))


def export(
    output: Path = typer.Argument(..., help="File to write."),
    embeddings: bool = typer.Option(False, "--embeddings"),
    agent: Optional[str] = _AGENT,
) -> None:
    """Save a KB as NDJSON."""
    kb = client.agent_or_exit(agent)
    url = f"{config.api_base()}/agents/{kb}/export"
    with httpx.stream(
        "GET", url, params={"include_embeddings": embeddings}, headers=client.headers(), timeout=None
    ) as response:
        if response.status_code >= 400:
            console.print(f"[red]✗[/] Export failed: {response.status_code}")
            raise typer.Exit(code=1)
        with output.open("wb") as handle:
            for chunk in response.iter_bytes():
                handle.write(chunk)
    console.print(f"[green]✓[/] Wrote {output}")
