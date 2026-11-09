"""
MCP over stdio, for clients that launch a local process.

The process talks straight to Neo4j, so it needs the same NEO4J_* settings as the
API, plus SPAIDER_API_KEY. Stdout carries the protocol and nothing else, so all
logging goes to stderr.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys


async def run() -> int:
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING, force=True)

    from mcp.server.stdio import stdio_server

    from app.api.v1 import mcp_server as server
    from app.core.authz import resolve_token
    from app.services.graph_service import get_graph_service

    graph = get_graph_service()
    await graph.initialize()
    principal = await resolve_token(os.environ.get("SPAIDER_API_KEY"))
    if principal is None:
        print("SPAIDER_API_KEY is missing or invalid.", file=sys.stderr)
        await graph.close()
        return 1
    agent_id = os.environ.get("SPAIDER_AGENT_ID") or principal.default_kb
    if agent_id is None:
        print("This key has no default knowledge base; set SPAIDER_AGENT_ID.", file=sys.stderr)
        await graph.close()
        return 1
    server._AGENT_ID.set(agent_id)
    server._PRINCIPAL.set(principal)
    try:
        async with stdio_server() as (read, write):
            await server.mcp_server.run(read, write, server.mcp_server.create_initialization_options())
    finally:
        await graph.close()
    return 0


def main() -> None:
    sys.exit(asyncio.run(run()))


if __name__ == "__main__":
    main()
