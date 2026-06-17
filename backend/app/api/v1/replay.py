"""
Replay API endpoints: fetch workflow event logs for deterministic replay and auditing.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class RecordEventRequest(BaseModel):
    workflow_id: str = Field(..., min_length=1)
    agent_id: Optional[str] = Field(default=None)
    event_type: str = Field(..., min_length=1)
    payload: dict = Field(default_factory=dict)
    graph_state_hash: Optional[str] = None


class RecordEventResponse(BaseModel):
    event_id: str
    workflow_id: str


class WorkflowEventsResponse(BaseModel):
    workflow_id: str
    events: list[dict]
    total: int


class WorkflowSummary(BaseModel):
    workflow_id: str
    agent_id: str
    first_event: str
    last_event: str
    event_count: int


class WorkflowListResponse(BaseModel):
    workflows: list[WorkflowSummary]
    total: int


# ---------------------------------------------------------------------------
# Lazy-initialised singleton (concurrency-safe)
# ---------------------------------------------------------------------------

_replay_service = None
_replay_service_lock = asyncio.Lock()
_replay_unavailable_until: float = 0.0   # monotonic epoch; 0 = never backed off
_REPLAY_RETRY_COOLDOWN = 60.0            # seconds between Kafka reconnect attempts


async def _get_replay_service():
    global _replay_service, _replay_unavailable_until
    async with _replay_service_lock:
        if _replay_service is not None:
            return _replay_service
        import time as _time
        if _time.monotonic() < _replay_unavailable_until:
            # Still within cooldown — skip silently to avoid connection spam
            return None
        try:
            from app.services.replay_service import ReplayService
            svc = ReplayService()
            await svc.start()
            _replay_service = svc
            _replay_unavailable_until = 0.0
            logger.info("Replay service initialised")
        except Exception as exc:
            _replay_unavailable_until = _time.monotonic() + _REPLAY_RETRY_COOLDOWN
            logger.debug(
                "Replay service unavailable (retry in %.0fs): %s",
                _REPLAY_RETRY_COOLDOWN, exc,
            )
    return _replay_service


async def shutdown_replay_service() -> None:
    """Stop the replay service producer. Called from app lifespan shutdown."""
    global _replay_service
    async with _replay_service_lock:
        if _replay_service is not None:
            await _replay_service.stop()
            _replay_service = None


async def get_replay_service():
    """Public accessor for the replay service singleton.

    Importable by other modules (ingest, query) so they can emit workflow
    events directly without going through the HTTP endpoint.  Returns None
    when Kafka is unavailable — callers must treat that as a no-op.
    """
    return await _get_replay_service()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/events", response_model=RecordEventResponse, status_code=201)
async def record_event(request: RecordEventRequest):
    """Record a workflow event for later replay/audit."""
    svc = await _get_replay_service()
    if svc is None:
        raise HTTPException(status_code=503, detail="Replay service unavailable.")

    effective_agent_id = request.agent_id or settings.kafka_agent_namespace
    try:
        event_id = await svc.record_event(
            workflow_id=request.workflow_id,
            agent_id=effective_agent_id,
            event_type=request.event_type,
            payload=request.payload,
            graph_state_hash=request.graph_state_hash,
        )
        return RecordEventResponse(event_id=event_id, workflow_id=request.workflow_id)
    except Exception as exc:
        logger.error("Failed to record event: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/workflows", response_model=WorkflowListResponse)
async def list_workflows(
    agent_id: Optional[str] = Query(None, description="Filter by agent ID"),
    limit: int = Query(50, ge=1, le=200),
):
    """List distinct workflow runs available for replay."""
    svc = await _get_replay_service()
    if svc is None:
        raise HTTPException(status_code=503, detail="Replay service unavailable.")

    try:
        workflows = await svc.list_workflows(agent_id=agent_id, limit=limit)
        return WorkflowListResponse(workflows=workflows, total=len(workflows))
    except Exception as exc:
        logger.error("Failed to list workflows: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/workflows/{workflow_id}/events", response_model=WorkflowEventsResponse)
async def get_workflow_events(
    workflow_id: str,
    start_time: Optional[datetime] = Query(None, description="Filter events after this ISO timestamp"),
    end_time: Optional[datetime] = Query(None, description="Filter events before this ISO timestamp"),
    event_types: Optional[str] = Query(None, description="Comma-separated event type filter"),
    limit: int = Query(1000, ge=1, le=10000),
):
    """Fetch the complete event log for a specific workflow run."""
    svc = await _get_replay_service()
    if svc is None:
        raise HTTPException(status_code=503, detail="Replay service unavailable.")

    type_filter = None
    if event_types:
        type_filter = [t.strip() for t in event_types.split(",") if t.strip()]

    try:
        events = await svc.get_workflow_events(
            workflow_id=workflow_id,
            start_time=start_time,
            end_time=end_time,
            event_types=type_filter,
            limit=limit,
        )
        return WorkflowEventsResponse(
            workflow_id=workflow_id,
            events=events,
            total=len(events),
        )
    except Exception as exc:
        logger.error("Failed to fetch workflow events: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
