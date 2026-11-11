from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import ValidationError

from .. import __version__
from ..context import AppContext
from ..sysinfo import dir_size, host_info
from .deps import get_ctx
from .errors import ApiError

router = APIRouter()


@router.get("/health")
async def health(ctx: AppContext = Depends(get_ctx)):
    return {
        "status": "ok",
        "version": __version__,
        "app_root": str(ctx.paths.app_root),
        "data_dir": str(ctx.paths.shared_dir),
        "profile_dir": str(ctx.paths.data_dir),
        "profile_id": ctx.profile_id,
    }


@router.get("/setup/status")
async def setup_status(ctx: AppContext = Depends(get_ctx)):
    return ctx.setup_status()


@router.get("/system/info")
async def system_info(ctx: AppContext = Depends(get_ctx)):
    host = await asyncio.to_thread(host_info, ctx.paths.data_dir)
    usage = await asyncio.to_thread(
        lambda: {
            "models_bytes": dir_size(ctx.paths.models_dir),
            "db_bytes": ctx.db.file_size_bytes(),
            "imports_bytes": dir_size(ctx.paths.imports_dir),
            "logs_bytes": dir_size(ctx.paths.logs_dir),
            "downloads_bytes": dir_size(ctx.paths.downloads_dir),
        }
    )
    data: dict[str, Any] = host.to_dict()
    data.update(
        {
            "app_root": str(ctx.paths.app_root),
            "data_dir": str(ctx.paths.shared_dir),
            "profile_dir": str(ctx.paths.data_dir),
            "profile_id": ctx.profile_id,
            "usage": usage,
            "runtime": ctx.runtime_info(),
        }
    )
    return data


@router.get("/system/eject-status")
async def eject_status(ctx: AppContext = Depends(get_ctx)):
    return ctx.eject_status()


@router.post("/system/shutdown")
async def shutdown(ctx: AppContext = Depends(get_ctx)):
    ctx.request_shutdown()
    return {"ok": True}


@router.get("/system/logs")
async def logs(tail: int = 200, ctx: AppContext = Depends(get_ctx)):
    tail = max(1, min(tail, 2000))
    lines = await asyncio.to_thread(ctx.read_log_tail, tail)
    return {"lines": lines}


@router.get("/settings")
async def get_settings(ctx: AppContext = Depends(get_ctx)):
    return ctx.settings.load().model_dump()


@router.patch("/settings")
async def patch_settings(patch: dict[str, Any], ctx: AppContext = Depends(get_ctx)):
    try:
        updated = ctx.settings.update(patch)
    except (ValidationError, ValueError) as exc:
        raise ApiError(422, "invalid_setting", f"Ungültige Einstellung: {exc}") from exc
    ctx.apply_settings(updated)
    return updated.model_dump()
