from __future__ import annotations

import asyncio
import json
import re
from typing import Optional

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..context import AppContext
from ..models import hub
from ..models.catalog import CatalogEntry, CatalogFile, License, Verification
from ..models.jobs import TERMINAL
from ..models.recommend import recommend
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


class ResolveBody(BaseModel):
    repo: str
    file: Optional[str] = None


@router.get("/catalog")
async def catalog(ctx: AppContext = Depends(get_ctx)):
    host = await asyncio.to_thread(host_info, ctx.paths.data_dir)
    installed = {m["catalog_id"]: m["id"] for m in ctx.registry.list() if m["catalog_id"]}
    prefs = ctx.settings.load()
    entries = [e.to_api(host, installed.get(e.id), context_tokens=prefs.context_tokens) for e in ctx.catalog.entries]
    return {"entries": entries, "catalog_date": ctx.catalog.generated, "local_extensions": ctx.catalog.local_extensions}


@router.get("/installed")
async def installed(ctx: AppContext = Depends(get_ctx)):
    return {"models": [ctx.decorate_model(m) for m in ctx.registry.list()]}


@router.get("/active")
async def active(ctx: AppContext = Depends(get_ctx)):
    return ctx.active_models()


@router.get("/recommendations")
async def recommendations(focus: str = "alltag", ctx: AppContext = Depends(get_ctx)):
    focus = focus if focus in ("alltag", "dokumente", "coding") else "alltag"
    hw = await ctx.hardware_profile()
    if hw is None:
        raise ApiError(503, "hardware_pending", "Hardware wird noch erkannt.")
    prefs = ctx.settings.load()
    installed = {m["catalog_id"]: m["id"] for m in ctx.registry.list() if m["catalog_id"]}
    host = await asyncio.to_thread(host_info, ctx.paths.data_dir)
    result = recommend(
        ctx.catalog.entries,
        hw,
        focus=focus,
        context_tokens=prefs.context_tokens,
        installed_ids=installed,
        measured_for=ctx.measured_for_entry,
        embedding_enabled=prefs.embedding_enabled,
    )
    picks = []
    for p in result["picks"]:
        e: CatalogEntry = p["entry"]
        picks.append({**p, "entry": e.to_api(host, installed.get(e.id), context_tokens=prefs.context_tokens)})
    return {
        "hardware": {**hw.to_dict(), "active_backend": ctx.planned_backend()},
        "focus": focus,
        "picks": picks,
        "excluded": result["excluded"],
        "catalog_date": ctx.catalog.generated,
    }


@router.get("/search")
async def search(q: str = "", limit: int = 20, ctx: AppContext = Depends(get_ctx)):
    key = f"search:{q.strip().lower()}:{limit}"
    cached = ctx.db.query_one("SELECT value_json, fetched_at FROM catalog_cache WHERE key = ?", (key,))
    try:
        results = await asyncio.to_thread(hub.search, q, limit=max(1, min(limit, 50)))
    except hub.HubError as exc:
        if cached:
            return {
                "results": json.loads(cached["value_json"]),
                "cached_at": cached["fetched_at"],
                "offline": True,
                "detail": exc.detail,
            }
        if exc.code == "rate_limited":
            raise ApiError(429, exc.code, exc.detail)
        return {"results": [], "cached_at": None, "offline": True, "detail": exc.detail}
    except Exception as exc:  # noqa: BLE001  (no network)
        if cached:
            return {"results": json.loads(cached["value_json"]), "cached_at": cached["fetched_at"], "offline": True}
        return {"results": [], "cached_at": None, "offline": True, "detail": str(exc)[:120]}
    now = hub.now_iso()
    ctx.db.execute(
        "INSERT INTO catalog_cache(key, value_json, fetched_at) VALUES (?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, fetched_at = excluded.fetched_at",
        (key, json.dumps(results, ensure_ascii=False), now),
    )
    return {"results": results, "cached_at": now, "offline": False}


_SAFE_FILE = re.compile(r"^[\w][\w .+()/-]{0,200}\.gguf$", re.IGNORECASE)


@router.post("/resolve")
async def resolve(body: ResolveBody, ctx: AppContext = Depends(get_ctx)):
    try:
        info = await asyncio.to_thread(hub.repo_info, body.repo.strip())
    except hub.HubError as exc:
        raise ApiError(exc.status, exc.code, exc.detail, requires_account=exc.code == "gated")
    except Exception as exc:  # noqa: BLE001
        raise ApiError(503, "offline", f"Hugging Face ist nicht erreichbar: {str(exc)[:120]}")
    if not info.files:
        raise ApiError(409, "no_gguf", "Dieses Repository enthält keine GGUF-Dateien.")
    if info.gated:
        raise ApiError(
            409,
            "gated",
            "Dieses Repository erfordert ein Hugging-Face-Konto oder eine Freischaltung. "
            "Ein gültig heruntergeladenes Modell kann lokal importiert werden.",
            requires_account=True,
        )
    chosen = None
    if body.file:
        if not _SAFE_FILE.match(body.file) or ".." in body.file:
            raise ApiError(422, "invalid_file", "Ungültiger Dateiname.")
        chosen = next((f for f in info.files if f.path == body.file), None)
        if chosen is None:
            raise ApiError(404, "not_found", "Datei nicht im Repository.")
    else:
        chosen = hub.pick_default_file(info.files)
    if chosen is None:
        raise ApiError(409, "no_gguf", "Keine geeignete GGUF-Datei gefunden.")
    try:
        parts = hub.split_group(info.files, chosen)
    except hub.HubError as exc:
        raise ApiError(exc.status, exc.code, exc.detail)
    try:
        header = await asyncio.to_thread(hub.remote_gguf_header, info.repo, info.revision, parts[0].path)
    except hub.HubError as exc:
        raise ApiError(exc.status, exc.code, exc.detail, requires_account=exc.code == "gated")
    reasons: list[str] = []
    ok = True
    mtype = "chat"
    if header.get("pooling_type") not in (None, 0):
        mtype = "embedding"
    if not header.get("architecture_supported"):
        ok = False
        reasons.append(
            f"Architektur „{header.get('architecture')}“ wird vom gebündelten llama.cpp-Build nicht unterstützt."
        )
    if mtype == "chat" and not header.get("has_chat_template"):
        ok = False
        mtype = "base"
        reasons.append("Keine Chatvorlage in der Datei: vermutlich ein Basismodell, nicht für Chat geeignet.")
    if any("mmproj" in f.path.lower() for f in parts):
        ok = False
        mtype = "vision"
        reasons.append("Datei ist ein Bildprojektor, kein Chatmodell.")
    if ok:
        reasons.append("Architektur und Chatvorlage laut GGUF-Header geeignet; Ausführung hier noch nicht geprüft.")
    missing_sha = [f.path for f in parts if not f.sha256]
    if missing_sha:
        reasons.append(
            "Für einzelne Dateien liefert Hugging Face keine SHA-256; die Übertragung wird nur über die Größe geprüft."
        )
    license_id = (info.license or "other").lower()
    kind = (
        "open-source"
        if license_id in ("apache-2.0", "mit", "bsd-3-clause", "bsd-2-clause", "cc0-1.0")
        else (
            "open-weights"
            if license_id in ("llama3.2", "llama3.1", "llama3", "gemma", "llama2", "openrail", "creativeml-openrail-m")
            else "other"
        )
    )
    entry = CatalogEntry(
        id=f"hf:{info.repo}:{chosen.path}".lower().replace(" ", "_")[:120],
        name=f"{chosen.path.rsplit('.', 1)[0]} ({info.repo})",
        role="embedding" if mtype == "embedding" else "chat",
        type=mtype,
        family=header.get("architecture") or "unbekannt",
        families=[header.get("architecture") or "unbekannt"],
        publisher=info.author or info.repo.split("/")[0],
        source="official" if (info.base_model or "").split("/")[0] == info.author else "community",
        converter=info.author,
        original_model=info.base_model,
        repo=info.repo,
        revision=info.revision,
        files=[CatalogFile(path=f.path, size=f.size, sha256=f.sha256) for f in parts],
        license=License(
            id=license_id,
            name=info.license or "unbekannt",
            url=f"https://huggingface.co/{info.repo}",
            kind=kind,
            gated=bool(info.gated),
            requires_account=bool(info.gated),
        ),
        architecture=header.get("architecture") or "",
        parameters="?",
        quantization=header.get("quantization") or (chosen.quantization or "unbekannt"),
        context_length=int(header.get("context_length") or 4096),
        embedding_dimensions=int(header["embedding_length"])
        if mtype == "embedding" and header.get("embedding_length")
        else None,
        description_de="Aus der Hugging-Face-Suche übernommen; Kompatibilität nur anhand des GGUF-Headers geprüft.",
        verification=Verification(
            status="metadata" if ok else "unverified",
            date=hub.now_iso()[:10],
            scope="GGUF-Header per Range-Request gelesen; nicht ausgeführt",
        ),
        resources={
            "n_layers": header.get("block_count"),
            "n_kv_heads": header.get("head_count_kv"),
            "head_dim": (int(header["embedding_length"]) // int(header["head_count"]))
            if header.get("embedding_length") and header.get("head_count")
            else None,
        },
        template={
            "reasoning_toggle": "enable_thinking"
            if "enable_thinking" in (header.get("chat_template_excerpt") or "")
            else None
        },
        checked_at=hub.now_iso()[:10],
        source_kind="search",
    )
    if ok:
        ctx.save_dynamic_entry(entry)
    host = await asyncio.to_thread(host_info, ctx.paths.data_dir)
    prefs = ctx.settings.load()
    return {
        "entry": entry.to_api(host, None, context_tokens=prefs.context_tokens),
        "files": [{"path": f.path, "size": f.size, "sha256": f.sha256} for f in info.files],
        "compatibility": {
            "ok": ok,
            "reasons_de": reasons,
            "architecture": header.get("architecture"),
            "context_length": header.get("context_length"),
            "type": mtype,
            "has_chat_template": header.get("has_chat_template"),
        },
        "fit": entry.fit_for(host, installed=False, context_tokens=prefs.context_tokens).model_dump(),
    }


@router.post("/catalog/refresh")
async def catalog_refresh(ctx: AppContext = Depends(get_ctx)):
    checked = changed = 0
    errors: list[str] = []
    for e in list(ctx.catalog.entries):
        if e.source_kind != "builtin":
            continue
        try:
            info = await asyncio.to_thread(hub.repo_info, e.repo)
        except hub.HubError as exc:
            errors.append(f"{e.repo}: {exc.detail}")
            continue
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{e.repo}: nicht erreichbar ({str(exc)[:80]})")
            break
        checked += 1
        names = {f.path: f for f in info.files}
        for f in e.files:
            rf = names.get(f.path)
            if rf is None:
                errors.append(f"{e.repo}: Datei {f.path} nicht mehr vorhanden")
            elif rf.sha256 and f.sha256 and rf.sha256 != f.sha256 and info.revision != e.revision:
                errors.append(
                    f"{e.repo}: {f.path} wurde in Revision {info.revision[:10]} geändert (Katalog bleibt bei {e.revision[:10]})"  # noqa: E501
                )
                changed += 1
        if info.gated and not e.license.gated:
            errors.append(f"{e.repo}: inzwischen zugangsbeschränkt")
            changed += 1
    now = hub.now_iso()
    ctx.db.execute(
        "INSERT INTO catalog_cache(key, value_json, fetched_at) VALUES ('refresh', ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, fetched_at = excluded.fetched_at",
        (json.dumps({"checked": checked, "changed": changed, "errors": errors}), now),
    )
    return {
        "catalog_date": ctx.catalog.generated,
        "checked": checked,
        "changed": changed,
        "errors": errors,
        "refreshed_at": now,
    }


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


@router.post("/{model_id}/calibrate", status_code=202)
async def calibrate(model_id: str, ctx: AppContext = Depends(get_ctx)):
    job = await ctx.jobs.start_calibrate(model_id)
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
