"""Fine-tune jobs, the eval gate and promotion."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.core.authz import Principal, get_principal
from app.services.finetune.gate import CONFIDENCE_FLOOR, MIN_DELTA_FLOOR, MIN_ITEMS_FLOOR, GateParams
from app.services.finetune.service import FineTuneService
from app.services.graph_service import get_graph_service
from app.services.query_service import QueryService

router = APIRouter()


def _service() -> FineTuneService:
    return FineTuneService(get_graph_service())


class CreateJob(BaseModel):
    agent_id: str
    provider: str
    base_model: str
    data_clearance: int = Field(default=1, ge=1, le=5, description="Highest clearance exported to the provider.")
    max_examples: int = Field(default=500, ge=1, le=50000)
    hyperparameters: Optional[dict] = None


class EvalItem(BaseModel):
    question: str
    expected: str


class Evaluate(BaseModel):
    items: list[EvalItem] = Field(min_length=1, max_length=500)
    confidence: float = CONFIDENCE_FLOOR
    min_delta: float = MIN_DELTA_FLOOR
    memory: bool = True
    min_items: int = MIN_ITEMS_FLOOR


@router.post("/jobs", status_code=201)
async def create_job(body: CreateJob, auth: Principal = Depends(get_principal)):
    return await _service().create_job(auth, **body.model_dump())


@router.get("/jobs")
async def list_jobs(agent_id: str = Query(...), auth: Principal = Depends(get_principal)):
    return await _service().list_for(auth, agent_id)


@router.get("/jobs/{version_id}")
async def get_job(version_id: str, auth: Principal = Depends(get_principal)):
    return await _service().refresh(auth, version_id)


@router.post("/jobs/{version_id}/cancel")
async def cancel_job(version_id: str, auth: Principal = Depends(get_principal)):
    return await _service().cancel(auth, version_id)


@router.post("/jobs/{version_id}/evaluate")
async def evaluate_job(version_id: str, body: Evaluate, auth: Principal = Depends(get_principal)):
    service = _service()
    version = await service.get(auth, version_id)
    queries = QueryService(get_graph_service())

    async def answer(question: str, override: Optional[dict]) -> str:
        result = await queries.query_nl(
            question, version["agent_id"], max_clearance=auth.clearance_level, answer_model=override, use_cache=False
        )
        return result.answer

    params = GateParams(
        confidence=body.confidence, min_delta=body.min_delta, memory=body.memory, min_items=body.min_items
    )
    return await service.evaluate(auth, version_id, [item.model_dump() for item in body.items], params, answer)


@router.post("/jobs/{version_id}/promote")
async def promote_job(version_id: str, auth: Principal = Depends(get_principal)):
    return await _service().promote(auth, version_id)


@router.post("/rollback")
async def rollback(agent_id: str = Query(...), auth: Principal = Depends(get_principal)):
    return await _service().rollback(auth, agent_id)
