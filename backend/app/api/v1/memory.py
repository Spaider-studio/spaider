"""Fast memory paths: write episodes and recall without any LLM call."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

from app.core.authz import Action, Principal, authorize, get_principal, resolve_kb
from app.services.episode_store import EpisodeInput, EpisodeStore, Location
from app.services.graph_service import get_graph_service
from app.services.recall_service import RecallQuery, RecallService

router = APIRouter()

MAX_BATCH = 1000


class LocationBody(BaseModel):
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)
    x: Optional[float] = None
    y: Optional[float] = None
    z: Optional[float] = None

    @model_validator(mode="after")
    def _complete(self) -> "LocationBody":
        if (self.latitude is None or self.longitude is None) and (self.x is None or self.y is None):
            raise ValueError("A location needs latitude and longitude, or x and y.")
        return self

    def to_location(self) -> Location:
        return Location(**self.model_dump())


class EpisodeBody(BaseModel):
    content: str = Field(min_length=1, max_length=100_000)
    kind: str = Field(default="observation", max_length=64)
    idempotency_key: Optional[str] = Field(default=None, max_length=256)
    session_id: Optional[str] = Field(default=None, max_length=256)
    source: Optional[str] = Field(default=None, max_length=256)
    occurred_at: Optional[datetime] = None
    location: Optional[LocationBody] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    clearance_level: int = Field(default=1, ge=1, le=5)

    @model_validator(mode="after")
    def _reserved_kinds(self) -> "EpisodeBody":
        if self.kind in {"query", "answer", "compaction"}:
            raise ValueError(f"kind '{self.kind}' is reserved")
        return self


class EpisodeBatch(BaseModel):
    agent_id: Optional[str] = None
    episodes: list[EpisodeBody] = Field(min_length=1, max_length=MAX_BATCH)


class RecallBody(BaseModel):
    agent_id: Optional[str] = None
    query: str = Field(default="", max_length=10_000)
    limit: int = Field(default=10, ge=1, le=100)
    kinds: Optional[list[str]] = None
    session_id: Optional[str] = None
    since: Optional[datetime] = None
    until: Optional[datetime] = None
    near: Optional[LocationBody] = None
    radius: float = Field(default=100.0, gt=0)
    federated: bool = False
    as_of: Optional[datetime] = None


def _driver():
    return get_graph_service()._driver


@router.post("/episodes", status_code=201)
async def write_episodes(body: EpisodeBatch, auth: Principal = Depends(get_principal)):
    """Store observations. No LLM call. Retrying with the same idempotency key is safe."""
    agent_id = resolve_kb(auth, body.agent_id)
    await authorize(auth, agent_id, Action.WRITE)
    if max(item.clearance_level for item in body.episodes) > auth.clearance_level:
        raise HTTPException(status_code=403, detail="Cannot write above your own clearance.")
    episodes = [
        EpisodeInput(
            content=item.content,
            kind=item.kind,
            idempotency_key=item.idempotency_key,
            session_id=item.session_id,
            source=item.source,
            occurred_at=item.occurred_at,
            location=item.location.to_location() if item.location else None,
            metadata=item.metadata,
            clearance_level=item.clearance_level,
        )
        for item in body.episodes
    ]
    return {"agent_id": agent_id, "episodes": await EpisodeStore(_driver()).write_batch(agent_id, episodes)}


@router.get("/episodes")
async def list_episodes(
    agent_id: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=200),
    auth: Principal = Depends(get_principal),
):
    agent_id = resolve_kb(auth, agent_id)
    await authorize(auth, agent_id, Action.READ)
    return {
        "agent_id": agent_id,
        "episodes": await EpisodeStore(_driver()).recent(agent_id, limit=limit, max_clearance=auth.clearance_level),
    }


@router.post("/recall")
async def recall(body: RecallBody, auth: Principal = Depends(get_principal)):
    """Search episodes and nodes. No LLM or embedding call, for sub-100 ms use."""
    agent_id = resolve_kb(auth, body.agent_id)
    await authorize(auth, agent_id, Action.READ)
    results = await RecallService(_driver()).recall(
        RecallQuery(
            agent_id=agent_id,
            text=body.query,
            limit=body.limit,
            clearance=auth.clearance_level,
            kinds=body.kinds,
            session_id=body.session_id,
            since=body.since,
            until=body.until,
            near=body.near.to_location() if body.near else None,
            radius=body.radius,
            federated=body.federated,
            as_of=body.as_of,
        )
    )
    return {"agent_id": agent_id, "results": results}
