"""
Central API router: aggregates all v1 sub-routers.
"""
from fastapi import APIRouter, Depends

from app.api.v1 import (
    agents,
    analytics,
    connectors,
    delete,
    federation,
    feedback,
    finetune,
    graph,
    identity,
    ingest,
    memory,
    query,
    replay,
    swarm,
    synthesize,
    system_settings,
)
from app.core.authz import get_principal  # noqa: E402

api_router = APIRouter()
# Deny by default: a route added here is authenticated even if its handler
# forgets to ask. /auth/bootstrap is the one deliberate exception.
_authenticated = [Depends(get_principal)]
api_router.include_router(identity.public_router, tags=["identity"])
api_router.include_router(identity.router, tags=["identity"], dependencies=_authenticated)

api_router.include_router(ingest.router, prefix="/ingest", tags=["ingest"], dependencies=_authenticated)
api_router.include_router(query.router, prefix="/query", tags=["query"], dependencies=_authenticated)
api_router.include_router(graph.router, prefix="/graph", tags=["graph"], dependencies=_authenticated)
api_router.include_router(delete.router, prefix="/node", tags=["node"], dependencies=_authenticated)
api_router.include_router(synthesize.router, prefix="/synthesize", tags=["synthesize"], dependencies=_authenticated)
api_router.include_router(agents.router, prefix="/agents", tags=["agents"], dependencies=_authenticated)
api_router.include_router(finetune.router, prefix="/finetune", tags=["finetune"], dependencies=_authenticated)
api_router.include_router(memory.router, tags=["memory"], dependencies=_authenticated)
api_router.include_router(federation.router, prefix="/federation", tags=["federation"], dependencies=_authenticated)
api_router.include_router(swarm.router, prefix="/swarm", tags=["swarm"], dependencies=_authenticated)
api_router.include_router(analytics.router, prefix="/analytics", tags=["analytics"], dependencies=_authenticated)
api_router.include_router(system_settings.router, prefix="/system", tags=["system"], dependencies=_authenticated)
api_router.include_router(feedback.router, prefix="/system", tags=["system"], dependencies=_authenticated)
api_router.include_router(replay.router, prefix="/replay", tags=["replay"], dependencies=_authenticated)
api_router.include_router(connectors.router, prefix="/connectors", tags=["connectors"], dependencies=_authenticated)
# NOTE: the MCP sub-app is mounted DIRECTLY on the FastAPI app in
# app/main.py (`app.mount("/api/v1/mcp", mcp_app)`), not here.
# `APIRouter.include_router(prefix=...)` does not propagate Starlette
# `Mount` nodes; including the mount here silently 404s.
