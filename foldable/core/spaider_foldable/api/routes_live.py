"""Live observation endpoints (contract v1.2)."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..chat.service import ChatBusy, NoModel, RuntimeNotReady
from ..context import AppContext
from ..live.sessions import LiveUnavailable, SourceIsSelf, SourceNotFound
from .deps import get_ctx
from .errors import ApiError

router = APIRouter(prefix="/live")


class SessionBody(BaseModel):
    source_id: str = Field(min_length=3, max_length=80)
    conversation_id: Optional[str] = None


class SourceBody(BaseModel):
    source_id: str = Field(min_length=3, max_length=80)


class SnapshotBody(BaseModel):
    include_image: bool = False
    preview: bool = False
    max_elements: int = Field(default=200, ge=0, le=1000)  # 0 = no structure read (preview only)


class AskBody(BaseModel):
    content: str = Field(min_length=1, max_length=20000)
    use_memory: bool = True
    use_image: str = Field(default="auto", pattern="^(auto|always|never)$")


def _map(exc: Exception) -> ApiError:
    if isinstance(exc, SourceIsSelf):
        return ApiError(409, "source_is_self", "Eigene SpAIder-Fenster können nicht beobachtet werden.")
    if isinstance(exc, SourceNotFound):
        return ApiError(404, "source_not_found", "Quelle nicht gefunden (Fenster geschlossen?).")
    if isinstance(exc, LiveUnavailable):
        return ApiError(409, "live_unavailable", str(exc))
    return ApiError(500, "internal", str(exc))


@router.get("/sources")
async def sources(ctx: AppContext = Depends(get_ctx)):
    return ctx.live.list_sources()


@router.post("/foreground")
async def foreground(ctx: AppContext = Depends(get_ctx)):
    return ctx.live.foreground()


@router.get("/sessions")
async def list_sessions(ctx: AppContext = Depends(get_ctx)):
    v = ctx.vision_available()
    return {"sessions": [s.to_api(v) for s in ctx.live.sessions.values()]}


@router.post("/sessions", status_code=201)
async def create_session(body: SessionBody, ctx: AppContext = Depends(get_ctx)):
    try:
        if body.conversation_id:
            ctx.conversations.get(body.conversation_id)
        sess = ctx.live.create(body.source_id, body.conversation_id)
    except (SourceIsSelf, SourceNotFound, LiveUnavailable) as exc:
        raise _map(exc)
    return {"session": sess.to_api(ctx.vision_available())}


@router.get("/sessions/{sid}")
async def get_session(sid: str, ctx: AppContext = Depends(get_ctx)):
    try:
        return {"session": ctx.live.get(sid).to_api(ctx.vision_available())}
    except SourceNotFound:
        raise ApiError(404, "not_found", "Sitzung nicht gefunden.")


@router.patch("/sessions/{sid}")
async def change_source(sid: str, body: SourceBody, ctx: AppContext = Depends(get_ctx)):
    try:
        return {"session": ctx.live.change_source(sid, body.source_id).to_api(ctx.vision_available())}
    except (SourceIsSelf, SourceNotFound, LiveUnavailable) as exc:
        raise _map(exc)


@router.delete("/sessions/{sid}")
async def end_session(sid: str, ctx: AppContext = Depends(get_ctx)):
    ctx.live.end(sid)
    return {"ok": True}


@router.post("/sessions/{sid}/snapshot")
async def snapshot(sid: str, body: SnapshotBody, ctx: AppContext = Depends(get_ctx)):
    try:
        ctx.live.get(sid)
        snap = await ctx.live.snapshot(sid, want_image=body.include_image or body.preview, max_elements=body.max_elements)
    except SourceNotFound:
        raise ApiError(404, "not_found", "Sitzung nicht gefunden.")
    return {"snapshot": snap.to_api(include_image=body.include_image, preview=body.preview)}


@router.post("/sessions/{sid}/ask")
async def ask(sid: str, body: AskBody, ctx: AppContext = Depends(get_ctx)):
    try:
        sess = ctx.live.get(sid)
    except SourceNotFound:
        raise ApiError(404, "not_found", "Sitzung nicht gefunden.")
    if body.use_image == "always" and not ctx.vision_available():
        raise ApiError(409, "vision_unavailable", "Kein Bildmodell aktiv.")
    ctx.scheduler.touch_user_activity()

    async def observe(tracker):
        return await ctx.live.observe_for_question(sess.id, tracker, use_image=body.use_image)

    try:
        gen = ctx.chat.send(sess.conversation_id, body.content, observe=observe, use_memory=body.use_memory)
        first = await gen.__anext__()
    except ChatBusy:
        raise ApiError(409, "generation_running", "In diesem Gespräch läuft bereits eine Antwort.")
    except NoModel:
        raise ApiError(409, "no_model", "Es ist kein Chatmodell aktiv.")
    except RuntimeNotReady:
        raise ApiError(409, "runtime_not_ready", "Die Modell-Laufzeit ist nicht bereit.")
    except LiveUnavailable as exc:
        raise ApiError(409, "source_lost", str(exc))

    async def stream():
        yield first
        async for chunk in gen:
            yield chunk

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/sessions/{sid}/stop")
async def stop(sid: str, ctx: AppContext = Depends(get_ctx)):
    try:
        sess = ctx.live.get(sid)
    except SourceNotFound:
        raise ApiError(404, "not_found", "Sitzung nicht gefunden.")
    return {"ok": True, "stopped": await ctx.chat.stop(sess.conversation_id)}
