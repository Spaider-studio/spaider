"""Profiles (contract v1.3): list, create, rename, delete, activate (switch)."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ..profiles import ProfileError, ProfileManager
from .errors import ApiError

router = APIRouter(prefix="/profiles", tags=["profiles"])


def get_manager(request: Request) -> Optional[ProfileManager]:
    return getattr(request.app.state, "manager", None)


class ProfileBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)


def _require(mgr: Optional[ProfileManager]) -> ProfileManager:
    if mgr is None:
        raise ApiError(409, "unsupported", "Profile sind in diesem Betriebsmodus nicht verfügbar.")
    return mgr


@router.get("")
async def list_profiles(mgr: Optional[ProfileManager] = Depends(get_manager)):
    return _require(mgr).status()


@router.post("", status_code=201)
async def create_profile(body: ProfileBody, mgr: Optional[ProfileManager] = Depends(get_manager)):
    m = _require(mgr)
    entry = m.registry.create(body.name)
    return {"profile": entry, **m.status()}


@router.patch("/{profile_id}")
async def rename_profile(profile_id: str, body: ProfileBody, mgr: Optional[ProfileManager] = Depends(get_manager)):
    m = _require(mgr)
    try:
        return {"profile": m.registry.rename(profile_id, body.name)}
    except ProfileError as exc:
        raise ApiError(404, exc.code, exc.detail)


@router.delete("/{profile_id}")
async def delete_profile(profile_id: str, mgr: Optional[ProfileManager] = Depends(get_manager)):
    m = _require(mgr)
    try:
        m.registry.delete(profile_id, active=m.active_id)
    except ProfileError as exc:
        raise ApiError(409 if exc.code == "active" else 404, exc.code, exc.detail)
    return {"ok": True, **m.status()}


@router.post("/{profile_id}/activate")
async def activate_profile(profile_id: str, mgr: Optional[ProfileManager] = Depends(get_manager)):
    m = _require(mgr)
    try:
        return await m.switch(profile_id)
    except ProfileError as exc:
        raise ApiError(404, exc.code, exc.detail)
