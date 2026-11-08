"""Federation grants, Knowledge PRs and fine-tuning."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from spaider_cli.lib import client

console = Console()
grant = typer.Typer(help="Share one knowledge base with another.", no_args_is_help=True)
kpr = typer.Typer(help="Propose merging knowledge between KBs.", no_args_is_help=True)
finetune = typer.Typer(help="Train a model on a KB's data with an external provider.", no_args_is_help=True)


def _show(value) -> None:
    console.print_json(json.dumps(value))


@grant.command("request")
def grant_request(
    source: str = typer.Argument(..., help="KB being read."),
    target: str = typer.Argument(..., help="KB that will read it."),
    node_type: Optional[list[str]] = typer.Option(None, "--node-type"),
    relation: Optional[list[str]] = typer.Option(None, "--relation"),
    max_clearance: int = typer.Option(1, "--max-clearance"),
    expires_at: Optional[str] = typer.Option(None, "--expires-at", help="ISO 8601."),
) -> None:
    body = {
        "source_kb": source, "target_kb": target, "node_types": node_type or None,
        "relation_types": relation or None, "max_clearance": max_clearance, "expires_at": expires_at,
    }
    _show(client.request("POST", "/federation/grants", json=body))


@grant.command("list")
def grant_list() -> None:
    _show(client.request("GET", "/federation/grants"))


@grant.command("approve")
def grant_approve(grant_id: str) -> None:
    _show(client.request("POST", f"/federation/grants/{grant_id}/approve", json={}))


@grant.command("deny")
def grant_deny(grant_id: str) -> None:
    _show(client.request("POST", f"/federation/grants/{grant_id}/deny"))


@grant.command("revoke")
def grant_revoke(grant_id: str) -> None:
    _show(client.request("DELETE", f"/federation/grants/{grant_id}"))


@kpr.command("create")
def kpr_create(
    source: str,
    target: str,
    node_ids: list[str] = typer.Argument(...),
    title: str = typer.Option(..., "--title"),
) -> None:
    body = {"source_kb": source, "target_kb": target, "node_ids": node_ids, "title": title}
    _show(client.request("POST", "/federation/kpr", json=body))


@kpr.command("list")
def kpr_list(target: str) -> None:
    _show(client.request("GET", "/federation/kpr", params={"target_kb": target}))


@kpr.command("show")
def kpr_show(kpr_id: str) -> None:
    _show(client.request("GET", f"/federation/kpr/{kpr_id}"))


@kpr.command("approve")
def kpr_approve(kpr_id: str) -> None:
    _show(client.request("POST", f"/federation/kpr/{kpr_id}/approve"))


@kpr.command("reject")
def kpr_reject(kpr_id: str) -> None:
    _show(client.request("POST", f"/federation/kpr/{kpr_id}/reject"))


@kpr.command("revert")
def kpr_revert(kpr_id: str) -> None:
    _show(client.request("POST", f"/federation/kpr/{kpr_id}/revert"))


@finetune.command("create")
def finetune_create(
    agent: str,
    provider: str = typer.Option(..., "--provider", help="openai, together or tinker."),
    base_model: str = typer.Option(..., "--base-model"),
    data_clearance: int = typer.Option(1, "--data-clearance"),
) -> None:
    """Submit a job. Training data is sent to the provider."""
    body = {"agent_id": agent, "provider": provider, "base_model": base_model, "data_clearance": data_clearance}
    _show(client.request("POST", "/finetune/jobs", json=body, timeout=600))


@finetune.command("list")
def finetune_list(agent: str) -> None:
    _show(client.request("GET", "/finetune/jobs", params={"agent_id": agent}))


@finetune.command("status")
def finetune_status(version_id: str) -> None:
    _show(client.request("GET", f"/finetune/jobs/{version_id}"))


@finetune.command("evaluate")
def finetune_evaluate(
    version_id: str,
    items: Path = typer.Argument(..., exists=True, help='JSON list of {"question", "expected"}.'),
) -> None:
    body = {"items": json.loads(items.read_text(encoding="utf-8"))}
    _show(client.request("POST", f"/finetune/jobs/{version_id}/evaluate", json=body, timeout=3600))


@finetune.command("promote")
def finetune_promote(version_id: str) -> None:
    _show(client.request("POST", f"/finetune/jobs/{version_id}/promote"))


@finetune.command("rollback")
def finetune_rollback(agent: str) -> None:
    _show(client.request("POST", "/finetune/rollback", params={"agent_id": agent}))
