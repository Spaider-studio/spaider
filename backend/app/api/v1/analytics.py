"""
Analytics API: ClickHouse-backed time-series and aggregate stats.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)
router = APIRouter()

_analytics_service = None


def _get_analytics() -> "AnalyticsService | None":  # noqa: F821
    global _analytics_service
    if _analytics_service is None:
        try:
            from app.services.analytics_service import AnalyticsService
            _analytics_service = AnalyticsService()
        except Exception as exc:
            logger.warning("AnalyticsService unavailable: %s", exc)
    return _analytics_service


def _svc():
    svc = _get_analytics()
    if svc is None or not svc._ready:
        raise HTTPException(
            status_code=503,
            detail="Analytics service unavailable. Is ClickHouse running?",
        )
    return svc


@router.get("/overview")
async def overview(
    agent_id: str = Query(default="default"),
):
    """Aggregate totals: ingests, nodes created, queries, latencies."""
    return await _svc().get_overview(agent_id)


@router.get("/ingest")
async def ingest_timeseries(
    agent_id: str = Query(default="default"),
    days: int = Query(default=7, ge=1, le=90),
):
    """Hourly ingest volume, node/edge counts, and avg latency for the last N days."""
    return {"timeseries": await _svc().get_ingest_timeseries(agent_id, days)}


@router.get("/query")
async def query_timeseries(
    agent_id: str = Query(default="default"),
    days: int = Query(default=7, ge=1, le=90),
):
    """Hourly query volume, avg latency, and nodes returned for the last N days."""
    return {"timeseries": await _svc().get_query_timeseries(agent_id, days)}


@router.get("/top-agents")
async def top_agents(
    limit: int = Query(default=10, ge=1, le=100),
):
    """Most active agents by ingest count."""
    return {"agents": await _svc().get_top_agents(limit)}
