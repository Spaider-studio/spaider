from __future__ import annotations

import asyncio
import json
from typing import Optional

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..context import AppContext
from ..models.jobs import TERMINAL
from ..sysinfo import host_info
from .deps import get_ctx
from .errors import ApiError

router = APIRouter(prefix="/models")


class DownloadBody(BaseModel):
    catalog_id: str


class ImportBody(BaseModel):
    path: str


class ActivateBody(BaseModel):
    model_id: str
    remove_previous: bool = False


@router.get("/catalog")
async def catalog(ctx: AppContext = Depends(get_ctx)):
    host = await asyncio.to_thread(host_info, ctx.paths.data_dir)
    installed = {m["catalog_id"]: m["id"] for m in ctx.registry.list() if m["catalog_id"]}
    prefs = ctx.settings.load()
    entries = [e.to_api(host, installed.get(e.id), context_tokens=prefs.context_tokens) for e in ctx.catalog.entries]
    return {"entries": entries}


@router.get("/installed")
async def installed(ctx: AppContext = Depends(get_ctx)):
    return {"models": ctx.registry.list()}


@router.get("/active")
async def active(ctx: AppContext = Depends(get_ctx)):
    return ctx.active_models()


@router.post("/download", status_code=202)
async def download(body: DownloadBody, ctx: AppContext = Depends(get_ctx)):
    job = await ctx.jobs.start_download(body.catalog_id)
    return {"job": job}


@router.post("/import", status_code=202)
async def import_model(body: ImportBody, ctx: AppContext = Depends(get_ctx)):
    job = await ctx.jobs.start_import(body.path)
    return {"job": job}


@router.post("/activate", status_code=202)
async def activate(body: ActivateBody, ctx: AppContext = Depends(get_ctx)):
    job = await ctx.jobs.start_activate(body.model_id, remove_previous=body.remove_previous)
    return {"job": job}


@router.delete("/{model_id}")
async def delete_model(model_id: str, ctx: AppContext = Depends(get_ctx)):
    await ctx.jobs.delete_model(model_id)
    return {"ok": True}


@router.get("/jobs")
async def jobs(ctx: AppContext = Depends(get_ctx)):
    return {"jobs": ctx.jobs.list()}


@router.get("/jobs/{job_id}")
async def job(job_id: str, ctx: AppContext = Depends(get_ctx)):
    try:
        return {"job": ctx.jobs.get(job_id)}
    except KeyError:
        raise ApiError(404, "not_found", "Unbekannter Vorgang.")


@router.post("/jobs/{job_id}/cancel")
async def cancel(job_id: str, ctx: AppContext = Depends(get_ctx)):
    try:
        return {"job": await ctx.jobs.cancel(job_id)}
    except KeyError:
        raise ApiError(404, "not_found", "Unbekannter Vorgang.")


@router.post("/jobs/{job_id}/resume")
async def resume(job_id: str, ctx: AppContext = Depends(get_ctx)):
    try:
        return {"job": await ctx.jobs.resume(job_id)}
    except KeyError:
        raise ApiError(404, "not_found", "Unbekannter Vorgang.")


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: str, ctx: AppContext = Depends(get_ctx)):
    try:
        current = ctx.jobs.get(job_id)
    except KeyError:
        raise ApiError(404, "not_found", "Unbekannter Vorgang.")

    async def gen():
        q = ctx.jobs.subscribe(job_id)
        try:
            yield f"event: job\ndata: {json.dumps(current, ensure_ascii=False)}\n\n"
            if current["state"] in TERMINAL or current["state"] == "paused":
                return
            while True:
                try:
                    snap: Optional[dict] = await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield f"event: job\ndata: {json.dumps(snap, ensure_ascii=False)}\n\n"
                if snap["state"] in TERMINAL or snap["state"] == "paused":
                    return
        finally:
            ctx.jobs.unsubscribe(job_id, q)

    return StreamingResponse(
        gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )
