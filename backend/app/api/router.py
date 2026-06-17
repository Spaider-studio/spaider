"""
Central API router: aggregates all v1 sub-routers.
"""
from fastapi import APIRouter

from app.api.v1 import (
    agents,
    analytics,
    connectors,
    delete,
    feedback,
    graph,
    ingest,
    query,
    replay,
    swarm,
    synthesize,
    system_settings,
)

api_router = APIRouter()

api_router.include_router(ingest.router, prefix="/ingest", tags=["ingest"])
api_router.include_router(query.router, prefix="/query", tags=["query"])
api_router.include_router(graph.router, prefix="/graph", tags=["graph"])
api_router.include_router(delete.router, prefix="/node", tags=["node"])
api_router.include_router(synthesize.router, prefix="/synthesize", tags=["synthesize"])
api_router.include_router(agents.router, prefix="/agents", tags=["agents"])
api_router.include_router(swarm.router, prefix="/swarm", tags=["swarm"])
api_router.include_router(analytics.router, prefix="/analytics", tags=["analytics"])
api_router.include_router(system_settings.router, prefix="/system", tags=["system"])
api_router.include_router(feedback.router, prefix="/system", tags=["system"])
api_router.include_router(replay.router, prefix="/replay", tags=["replay"])
api_router.include_router(connectors.router, prefix="/connectors", tags=["connectors"])
# NOTE: the MCP sub-app is mounted DIRECTLY on the FastAPI app in
# app/main.py (`app.mount("/api/v1/mcp", mcp_app)`), not here.
# `APIRouter.include_router(prefix=...)` does not propagate Starlette
# `Mount` nodes; including the mount here silently 404s.
