"""Activity, hardware and per-message activity history endpoints (contract v1.1)."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from ..context import AppContext
from .deps import get_ctx
from .errors import ApiError

router = APIRouter()


@router.get("/activity")
async def activity_stream(ctx: AppContext = Depends(get_ctx)):
    async def gen():
        q = ctx.bus.subscribe()
        try:
            yield f"event: scheduler\ndata: {json.dumps(ctx.scheduler.state(), ensure_ascii=False)}\n\n"
            yield f"event: runtime\ndata: {json.dumps(ctx.inference.status(), ensure_ascii=False)}\n\n"
            while not ctx.shutting_down:
                try:
                    kind, payload = await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield f"event: {kind}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        finally:
            ctx.bus.unsubscribe(q)

    return StreamingResponse(
        gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@router.get("/system/hardware")
async def hardware(ctx: AppContext = Depends(get_ctx)):
    hw = await ctx.hardware_profile()
    if hw is None:
        raise ApiError(503, "hardware_pending", "Hardware wird noch erkannt.")
    data = hw.to_dict()
    data["active_backend"] = ctx.inference.chat.backend if ctx.inference.chat.process else ctx.planned_backend()
    data["gpu_failures"] = sorted(ctx.inference.gpu_failures)
    return data


@router.get("/conversations/{cid}/messages/{mid}/activity")
async def message_activity(cid: str, mid: str, ctx: AppContext = Depends(get_ctx)):
    msg = ctx.conversations.get_message(mid)
    if msg["conversation_id"] != cid:
        raise ApiError(404, "not_found", "Nicht gefunden.")
    return {"events": ctx.bus.history(mid), "timings": msg.get("timings")}
