from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..chat.service import ChatBusy, NoModel, RuntimeNotReady
from ..context import AppContext
from .deps import get_ctx
from .errors import ApiError

router = APIRouter(prefix="/conversations")


class CreateBody(BaseModel):
    title: Optional[str] = None


class PatchBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class MessageBody(BaseModel):
    content: str = Field(min_length=1, max_length=100_000)


@router.get("")
async def list_conversations(ctx: AppContext = Depends(get_ctx)):
    return {"conversations": ctx.conversations.list()}


@router.post("", status_code=201)
async def create(body: CreateBody, ctx: AppContext = Depends(get_ctx)):
    return ctx.conversations.create(body.title)


@router.get("/{cid}")
async def get(cid: str, ctx: AppContext = Depends(get_ctx)):
    return {"conversation": ctx.conversations.get(cid), "messages": ctx.conversations.messages(cid)}


@router.patch("/{cid}")
async def rename(cid: str, body: PatchBody, ctx: AppContext = Depends(get_ctx)):
    return ctx.conversations.rename(cid, body.title)


@router.delete("/{cid}")
async def delete(cid: str, ctx: AppContext = Depends(get_ctx)):
    if ctx.chat.active_streams and cid in ctx.chat._active:
        await ctx.chat.stop(cid)
    ctx.conversations.delete(cid)
    ctx.store.mark_origin_deleted("conversation", cid)
    return {"ok": True}


@router.post("/{cid}/messages")
async def send(cid: str, body: MessageBody, ctx: AppContext = Depends(get_ctx)):
    conv = ctx.conversations.get(cid)
    if cid in ctx.chat._active:
        raise ApiError(409, "generation_running", "In diesem Gespräch wird gerade eine Antwort erzeugt.")
    slot = ctx.inference.chat
    bound_local = ((conv.get("binding") or {}).get("kind") or "local") == "local"
    if bound_local and slot.model_id is None and slot.state == "stopped":
        raise ApiError(409, "no_model", "Es ist kein Chatmodell aktiv. Bitte zuerst ein Modell einrichten.")
    if bound_local and slot.state != "ready":
        raise ApiError(
            409,
            "runtime_not_ready",
            "Das Modell wird noch geladen."
            if slot.state == "starting"
            else f"Die Modell-Laufzeit ist nicht bereit ({slot.error or slot.state}).",
        )

    async def gen():
        try:
            async for chunk in ctx.chat.send(cid, body.content):
                yield chunk
        except ChatBusy:
            yield 'event: error\ndata: {"detail":"In diesem Gespräch wird gerade eine Antwort erzeugt.","code":"generation_running"}\n\n'  # noqa: E501
        except NoModel:
            yield 'event: error\ndata: {"detail":"Es ist kein Chatmodell aktiv.","code":"no_model"}\n\n'
        except RuntimeNotReady:
            yield 'event: error\ndata: {"detail":"Die Modell-Laufzeit ist nicht bereit.","code":"runtime_not_ready"}\n\n'  # noqa: E501

    return StreamingResponse(
        gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@router.post("/{cid}/stop")
async def stop(cid: str, ctx: AppContext = Depends(get_ctx)):
    ctx.conversations.get(cid)
    stopped = await ctx.chat.stop(cid)
    return {"ok": True, "stopped": stopped}


@router.get("/{cid}/messages/{mid}/sources")
async def sources(cid: str, mid: str, ctx: AppContext = Depends(get_ctx)):
    msg = ctx.conversations.get_message(mid)
    if msg["conversation_id"] != cid:
        raise ApiError(404, "not_found", "Nicht gefunden.")
    return {"sources": msg["sources"]}
