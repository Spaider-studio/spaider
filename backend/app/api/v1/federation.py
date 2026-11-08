"""Federation grants and Knowledge Pull Requests."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.core.authz import Principal, accessible_kbs, get_principal
from app.services.federation import FederationService
from app.services.graph_service import get_graph_service
from app.services.knowledge_pr import KnowledgePRService

router = APIRouter()


def _grants() -> FederationService:
    return FederationService(get_graph_service()._driver)


def _kprs() -> KnowledgePRService:
    return KnowledgePRService(get_graph_service()._driver)


class GrantRequest(BaseModel):
    source_kb: str = Field(description="The KB whose knowledge would be shared.")
    target_kb: str = Field(description="The KB that would be able to read it.")
    node_types: Optional[list[str]] = None
    relation_types: Optional[list[str]] = None
    max_clearance: int = Field(default=1, ge=1, le=5)
    expires_at: Optional[str] = None
    reason: Optional[str] = None


class GrantDecision(BaseModel):
    node_types: Optional[list[str]] = None
    relation_types: Optional[list[str]] = None
    max_clearance: Optional[int] = Field(default=None, ge=1, le=5)
    expires_at: Optional[str] = None


class KPRCreate(BaseModel):
    source_kb: str
    target_kb: str
    node_ids: list[str]
    title: str = Field(min_length=1, max_length=200)


@router.post("/grants", status_code=201)
async def request_grant(body: GrantRequest, auth: Principal = Depends(get_principal)):
    return await _grants().request(auth, **body.model_dump())


@router.get("/grants")
async def list_grants(auth: Principal = Depends(get_principal)):
    return await _grants().list_for(await accessible_kbs(auth))


@router.post("/grants/{grant_id}/approve")
async def approve_grant(grant_id: str, body: GrantDecision, auth: Principal = Depends(get_principal)):
    return await _grants().approve(auth, grant_id, **body.model_dump())


@router.post("/grants/{grant_id}/deny")
async def deny_grant(grant_id: str, auth: Principal = Depends(get_principal)):
    return await _grants().deny(auth, grant_id)


@router.delete("/grants/{grant_id}")
async def revoke_grant(grant_id: str, auth: Principal = Depends(get_principal)):
    return await _grants().revoke(auth, grant_id)


@router.post("/kpr", status_code=201)
async def create_kpr(body: KPRCreate, auth: Principal = Depends(get_principal)):
    return await _kprs().create(auth, body.source_kb, body.target_kb, body.node_ids, body.title)


@router.get("/kpr")
async def list_kprs(target_kb: str = Query(...), auth: Principal = Depends(get_principal)):
    return await _kprs().list_for(auth, target_kb)


@router.get("/kpr/{kpr_id}")
async def get_kpr(kpr_id: str, auth: Principal = Depends(get_principal)):
    return await _kprs().get(auth, kpr_id)


@router.post("/kpr/{kpr_id}/approve")
async def approve_kpr(kpr_id: str, auth: Principal = Depends(get_principal)):
    return await _kprs().approve(auth, kpr_id)


@router.post("/kpr/{kpr_id}/reject")
async def reject_kpr(kpr_id: str, auth: Principal = Depends(get_principal)):
    return await _kprs().reject(auth, kpr_id)


@router.post("/kpr/{kpr_id}/revert")
async def revert_kpr(kpr_id: str, auth: Principal = Depends(get_principal)):
    return await _kprs().revert(auth, kpr_id)
