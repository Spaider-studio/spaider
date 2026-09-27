"""
Provision the first admin agent + API key.

Agent creation over the REST API deliberately refuses to grant the ``admin``
permission to non-admin callers, so the initial admin key cannot be minted
through the API. Run this script once, out-of-band, to create it. The raw key
is printed exactly once and never stored in plaintext.

Usage (from the backend/ directory, with the app's env, e.g. REDIS_URL set):

    python -m scripts.bootstrap_admin --name "ops-admin"

The key it prints authenticates as an admin: it can manage every agent and run
the admin-only routes (raw Cypher, imports, global settings). Keep it secret;
rotate it with POST /agents/{id}/rotate-key if it leaks.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone

from app.api.v1.agents import _save_agent
from app.models.schemas import Agent
from app.services.auth_service import AuthService


async def _bootstrap(name: str, tenant_id: str) -> None:
    import uuid

    agent_id = str(uuid.uuid4())
    auth = AuthService()

    permissions = ["read", "write", "admin"]
    raw_key, hashed_key = await auth.generate_and_store_api_key(
        agent_id=agent_id,
        tenant_id=tenant_id,
        permissions=permissions,
    )

    agent = Agent(
        id=agent_id,
        name=name,
        description="Bootstrap admin agent (provisioned via scripts/bootstrap_admin.py)",
        tenant_id=tenant_id,
        permissions=permissions,
        api_key_hash=hashed_key,
        created_at=datetime.now(timezone.utc),
    )
    await _save_agent(agent)

    # Best-effort SystemAgent node so the admin shows up in graph views. Never
    # fatal: the key is already valid without it.
    try:
        from app.api.v1.agents import _get_graph_service

        graph = _get_graph_service()
        await graph.create_agent_node(agent_id=agent_id, name=name)
    except Exception as exc:  # noqa: BLE001
        print(f"(warning) could not create SystemAgent node: {exc}")

    await auth.close()

    print("Admin agent provisioned.")
    print(f"  agent_id : {agent_id}")
    print(f"  name     : {name}")
    print(f"  API key  : {raw_key}")
    print("Store this key now. It will not be shown again.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Provision the first admin agent + key.")
    parser.add_argument("--name", default="admin", help="Human-readable agent name.")
    parser.add_argument("--tenant-id", default="default", help="Tenant namespace.")
    args = parser.parse_args()
    asyncio.run(_bootstrap(args.name, args.tenant_id))


if __name__ == "__main__":
    main()
