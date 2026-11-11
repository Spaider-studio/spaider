"""Teach-by-demonstration endpoints (contract v1.5)."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..agent.models import TeachFinishBody, TeachStartBody
from ..agent.teach import TeachError
from ..context import AppContext
from ..live.sessions import SourceIsSelf, SourceNotFound
from .deps import get_ctx
from .errors import ApiError

router = APIRouter(prefix="/agent/teach")


class CaptureBody(BaseModel):
    invoke_name: Optional[str] = Field(default=None, max_length=120)


class CheckBody(BaseModel):
    check: dict[str, Any]


def _map(exc: Exception) -> ApiError:
    if isinstance(exc, TeachError):
        return ApiError(exc.status, exc.code, exc.detail)
    if isinstance(exc, SourceNotFound):
        return ApiError(404, "not_found", "Fenster nicht gefunden.")
    if isinstance(exc, SourceIsSelf):
        return ApiError(409, "self", "Eigene Fenster können nicht aufgezeichnet werden.")
    return ApiError(500, "teach_error", str(exc)[:200])


@router.get("")
async def status(ctx: AppContext = Depends(get_ctx)):
    return ctx.teach.status()


@router.post("", status_code=201)
async def start(body: TeachStartBody, ctx: AppContext = Depends(get_ctx)):
    try:
        s = await ctx.teach.start(name=body.name, source=body.source, scopes=body.scopes, url=body.url, source_id=body.source_id)
    except Exception as exc:  # noqa: BLE001
        raise _map(exc)
    return {"session": s.to_api()}


@router.get("/{sid}")
async def get_session(sid: str, ctx: AppContext = Depends(get_ctx)):
    try:
        return {"session": ctx.teach.get(sid).to_api()}
    except TeachError as exc:
        raise _map(exc)


@router.post("/{sid}/capture")
async def capture(sid: str, body: CaptureBody, ctx: AppContext = Depends(get_ctx)):
    try:
        new = await ctx.teach.capture(sid, invoke_name=body.invoke_name)
    except Exception as exc:  # noqa: BLE001
        raise _map(exc)
    return {"new_steps": new, "session": ctx.teach.get(sid).to_api()}


@router.post("/{sid}/check")
async def add_check(sid: str, body: CheckBody, ctx: AppContext = Depends(get_ctx)):
    try:
        step = ctx.teach.add_check(sid, body.check)
    except Exception as exc:  # noqa: BLE001
        raise _map(exc)
    return {"step": step, "session": ctx.teach.get(sid).to_api()}


@router.delete("/{sid}/steps/{index}")
async def remove_step(sid: str, index: int, ctx: AppContext = Depends(get_ctx)):
    try:
        ctx.teach.remove_step(sid, index)
    except TeachError as exc:
        raise _map(exc)
    return {"session": ctx.teach.get(sid).to_api()}


@router.post("/{sid}/finish", status_code=201)
async def finish(sid: str, body: TeachFinishBody, ctx: AppContext = Depends(get_ctx)):
    try:
        return await ctx.teach.finish(sid, parameters=body.parameters, checks=body.checks)
    except Exception as exc:  # noqa: BLE001
        raise _map(exc)


@router.post("/{sid}/cancel")
async def cancel(sid: str, ctx: AppContext = Depends(get_ctx)):
    try:
        s = await ctx.teach.cancel(sid)
    except TeachError as exc:
        raise _map(exc)
    return {"session": s.to_api()}
