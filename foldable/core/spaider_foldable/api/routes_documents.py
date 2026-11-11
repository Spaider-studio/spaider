from __future__ import annotations

import asyncio
import json
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import StreamingResponse

from ..context import AppContext
from ..store.documents import MAX_BYTES, DocumentTooLarge, UnsupportedDocument
from .deps import get_ctx
from .errors import ApiError

router = APIRouter(prefix="/documents")


@router.get("")
async def list_documents(ctx: AppContext = Depends(get_ctx)):
    return {"documents": ctx.documents.list()}


@router.post("", status_code=201)
async def upload(
    file: UploadFile = File(...),
    conversation_id: Optional[str] = Form(default=None),
    ctx: AppContext = Depends(get_ctx),
):
    payload = await file.read()
    if len(payload) > MAX_BYTES:
        raise ApiError(413, "file_too_large", "Die Datei ist größer als 50 MB.")
    if conversation_id:
        ctx.conversations.get(conversation_id)
    try:
        doc, chunk_ids = await asyncio.to_thread(
            ctx.documents.import_bytes, file.filename or "dokument.txt", payload, conversation_id=conversation_id
        )
    except UnsupportedDocument as exc:
        raise ApiError(415, "unsupported_type", str(exc)) from exc
    except DocumentTooLarge as exc:
        raise ApiError(413, "file_too_large", str(exc)) from exc
    if ctx.settings.load().memory_auto_extract:
        for cid in chunk_ids:
            ctx.ingest_queue.enqueue("chunk", cid)
    elif chunk_ids:
        ctx.db.execute(
            "UPDATE documents SET status = 'indexed', progress_done = progress_total WHERE id = ?", (doc["id"],)
        )
        doc = ctx.documents.get(doc["id"])
    return doc


@router.get("/{did}")
async def get(did: str, ctx: AppContext = Depends(get_ctx)):
    return ctx.documents.get(did)


@router.get("/{did}/text")
async def text(did: str, offset: int = 0, limit: int = 5000, ctx: AppContext = Depends(get_ctx)):
    return ctx.documents.text(did, max(0, offset), max(1, min(limit, 50_000)))


@router.delete("/{did}")
async def delete(did: str, ctx: AppContext = Depends(get_ctx)):
    ctx.documents.get(did)
    await asyncio.to_thread(ctx.documents.delete, did)
    ctx.store.mark_origin_deleted("document", did)
    return {"ok": True}


@router.get("/{did}/events")
async def events(did: str, ctx: AppContext = Depends(get_ctx)):
    ctx.documents.get(did)

    async def gen():
        last = None
        for _ in range(3600):
            try:
                doc = ctx.documents.get(did)
            except Exception:  # noqa: BLE001
                return
            snapshot = json.dumps(doc, ensure_ascii=False)
            if snapshot != last:
                yield f"event: document\ndata: {snapshot}\n\n"
                last = snapshot
            if doc["status"] in ("indexed", "failed"):
                return
            await asyncio.sleep(1.0)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
