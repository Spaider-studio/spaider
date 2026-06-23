"""
Minimal FastAPI app exposing **only** the MCP server sub-app.

Why this exists
---------------
The full ``spaider-backend-api`` container also speaks MCP, but it's
restarted every time we rebuild the backend image. That breaks any
long-lived Claude Code session that's using SpAIder as durable memory:
mid-task tool calls fail, and `spaider.ingest_fact` writes get lost during
the rebuild window.

This standalone module runs the MCP sub-app as a host-side process on a
different port (default 8001), pointing at the **same** Redis and Neo4j
that compose is already running. So `docker compose build backend-api`
no longer kills the brain.

Run::

    make mcp-server-host

(or directly:: ``uvicorn app.mcp_standalone:app --port 8001``)

The compose data layer must be up — typically `make dev` or
`docker compose up -d redis neo4j postgres`.

Implementation
--------------
We deliberately import only the MCP router and the routes that resolve
auth (which itself only needs Redis). No Kafka producer, no graph_service
warm-up at boot, no clickhouse analytics — those would all want the full
lifespan from ``app.main``. The lazy-init pattern in QueryService /
AuthService means the first MCP tool call constructs whatever it needs
on-demand from env vars.

Configuration
-------------
Reads the same ``.env`` as the main app via ``app.config.settings``:

  REDIS_URL          (required for auth + RunState lookup)
  NEO4J_URI / USER / PASSWORD  (required for QueryService)
  LLM_API_KEY etc.   (required for spaider.query — list_recent works without)
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1.mcp_server import mcp_app

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Initialise the GraphService singleton so MCP can do semantic search.

    Without this, ``mcp_server._get_graph_service`` falls back to a bare
    instance whose ``initialize()`` is never awaited — meaning
    ``vector_index_available`` stays False forever and ``spaider.query``
    permanently returns "vector index unavailable" even when the index
    is healthy. Mirrors the relevant slice of ``app.main.lifespan``.
    """
    from app import main as _app_main
    from app.api.v1.mcp_server import mcp_session_manager
    from app.services.graph_service import GraphService

    try:
        _app_main._graph_service = GraphService()
        await _app_main._graph_service.initialize()
        logger.info(
            "standalone: GraphService initialised "
            "(vector_index_available=%s)",
            _app_main._graph_service.vector_index_available,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("standalone: GraphService init failed: %s", exc)

    # The Streamable HTTP session manager must run for the app's lifetime, same
    # as in app.main — without it `mcp_app` has no manager to hand requests to.
    async with mcp_session_manager.run():
        logger.info("standalone: MCP Streamable HTTP session manager started")
        try:
            yield
        finally:
            if _app_main._graph_service is not None:
                try:
                    await _app_main._graph_service.close()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("standalone: GraphService close error: %s", exc)


app = FastAPI(
    title="SpAIder MCP (standalone)",
    version="0.1.0",
    docs_url="/docs",
    lifespan=lifespan,
)

# Mount the Starlette sub-app under the same path the full app uses, so a
# single `.mcp.json` entry can target either deployment by switching the port.
app.mount("/api/v1/mcp", mcp_app)


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    """Lightweight liveness probe. Does not exercise Redis/Neo4j."""
    return {"status": "ok", "mode": "standalone"}
