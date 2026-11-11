from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..context import AppContext
from ..memory.retrieve import source_from_fact_row
from .deps import get_ctx

router = APIRouter(prefix="/memory")


class NoteBody(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


@router.get("/overview")
async def overview(ctx: AppContext = Depends(get_ctx)):
    stats = ctx.store.stats()
    emb = ctx.inference.embedding
    return {
        "facts": stats["facts"],
        "entities": stats["entities"],
        "relations": stats["relations"],
        "superseded": stats["superseded"],
        "documents": ctx.documents.count(),
        "notes": ctx.notes.count(),
        "conversations": len(ctx.conversations.list()),
        "messages": int(ctx.db.scalar("SELECT count(*) FROM messages") or 0),
        "pending_ingest": ctx.ingest_queue.pending_count(),
        "index": {
            "fts": True,
            "vectors": {
                "enabled": emb.state == "ready",
                "model_id": emb.model_id,
                "dimensions": ctx.embedding_dimensions(),
                "count": stats["vectors"],
            },
        },
    }


@router.get("/search")
async def search(q: str = "", limit: int = 20, ctx: AppContext = Depends(get_ctx)):
    results = await asyncio.to_thread(ctx.retriever.search, q, max(1, min(limit, 100)))
    return {"results": results}


@router.get("/facts")
async def facts(limit: int = 50, offset: int = 0, include_superseded: bool = False, ctx: AppContext = Depends(get_ctx)):
    rows, total = ctx.store.list_facts(
        limit=max(1, min(limit, 200)), offset=max(0, offset), include_superseded=include_superseded
    )
    return {"facts": [source_from_fact_row(r) for r in rows], "total": total}


@router.get("/notes")
async def list_notes(ctx: AppContext = Depends(get_ctx)):
    return {"notes": ctx.notes.list()}


@router.post("/notes", status_code=201)
async def create_note(body: NoteBody, ctx: AppContext = Depends(get_ctx)):
    return ctx.notes.create(body.text)


@router.patch("/notes/{nid}")
async def update_note(nid: str, body: NoteBody, ctx: AppContext = Depends(get_ctx)):
    return ctx.notes.update(nid, body.text)


@router.delete("/notes/{nid}")
async def delete_note(nid: str, ctx: AppContext = Depends(get_ctx)):
    ctx.notes.delete(nid)
    return {"ok": True}
