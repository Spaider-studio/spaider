"""Providers, connections, credentials, projects and conversation bindings (contract v1.3)."""

from __future__ import annotations

import logging
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..context import AppContext
from ..providers.connections import ModelBinding
from ..providers.credentials import CredentialError
from ..providers.events import GenerationError
from ..providers import policy as sharing_policy
from ..store.common import NotFound
from .deps import get_ctx
from .errors import ApiError

logger = logging.getLogger(__name__)
router = APIRouter(tags=["providers"])


# ---- presets ---------------------------------------------------------------------------
@router.get("/providers")
async def providers(ctx: AppContext = Depends(get_ctx)):
    items = []
    for p in ctx.presets.values():
        d = p.model_dump()
        d["connections"] = [c.id for c in ctx.connections.list() if c.provider == p.id]
        items.append(d)
    return {"providers": items, "credential_store": ctx.credentials.status()}


# ---- connections ------------------------------------------------------------------------
class ConnectionBody(BaseModel):
    provider: str
    name: str = Field(min_length=1, max_length=80)
    base_url: Optional[str] = None
    auth_method: Literal["none", "api_key", "oauth_siwc"] = "api_key"
    api_key: Optional[str] = Field(default=None, max_length=4000)
    credential_storage: Literal["host", "vault", "session"] = "host"
    execution: Optional[Literal["local", "lan", "cloud", "unknown"]] = None
    data_sharing: Literal["allow", "ask", "deny"] = "ask"
    default_model: Optional[str] = Field(default=None, max_length=200)
    extra: dict[str, Any] = Field(default_factory=dict)
    account_label: str = Field(default="", max_length=120)


class ConnectionPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    base_url: Optional[str] = None
    api_key: Optional[str] = Field(default=None, max_length=4000)
    credential_storage: Optional[Literal["host", "vault", "session"]] = None
    execution: Optional[Literal["local", "lan", "cloud", "unknown"]] = None
    data_sharing: Optional[Literal["allow", "ask", "deny"]] = None
    default_model: Optional[str] = Field(default=None, max_length=200)
    extra: Optional[dict[str, Any]] = None
    enabled: Optional[bool] = None
    account_label: Optional[str] = Field(default=None, max_length=120)


def _conn_or_404(ctx: AppContext, conn_id: str):
    c = ctx.connections.get(conn_id)
    if c is None:
        raise ApiError(404, "not_found", "Verbindung nicht gefunden.")
    return c


def _store_secret(ctx: AppContext, secret: str, storage: str, label: str) -> str:
    try:
        return ctx.credentials.put(secret, storage=storage, label=label)
    except CredentialError as exc:
        raise ApiError(409, exc.code, exc.detail)


@router.get("/connections")
async def list_connections(ctx: AppContext = Depends(get_ctx)):
    return {"connections": [c.public() for c in ctx.connections.list()]}


@router.post("/connections", status_code=201)
async def create_connection(body: ConnectionBody, ctx: AppContext = Depends(get_ctx)):
    try:
        preset = ctx.providers.preset(body.provider)
    except GenerationError as exc:
        raise ApiError(404, "not_found", exc.message_de)
    if preset.protocol == "llama_local":
        raise ApiError(409, "invalid", "Die eingebettete Laufzeit braucht keine Verbindung.")
    if body.auth_method not in preset.auth_methods:
        raise ApiError(422, "invalid_auth", f"Zugangsmethode {body.auth_method} ist für {preset.name} nicht vorgesehen (erlaubt: {', '.join(preset.auth_methods)}).")
    base_url = (body.base_url or preset.base_url or "").strip()
    if not base_url:
        raise ApiError(422, "invalid", "Serveradresse fehlt.")
    if body.base_url and not preset.base_url_editable and body.base_url.rstrip("/") != preset.base_url.rstrip("/"):
        raise ApiError(422, "invalid", f"Die Basis-URL von {preset.name} ist festgelegt ({preset.base_url}); für eigene Server das Preset „Eigener Server“ verwenden.")
    credential_ref = None
    storage = "none"
    if body.auth_method == "api_key":
        if not body.api_key:
            raise ApiError(422, "invalid", "API-Schlüssel fehlt.")
        credential_ref = _store_secret(ctx, body.api_key, body.credential_storage, f"{preset.id}:{body.name}")
        storage = body.credential_storage
    execution = body.execution or preset.execution
    conn = ctx.connections.create(
        provider=preset.id,
        protocol=preset.protocol,
        name=body.name.strip(),
        base_url=base_url,
        auth_method=body.auth_method,
        credential_ref=credential_ref,
        credential_storage=storage,
        account_label=body.account_label,
        execution=execution,
        data_sharing=body.data_sharing,
        default_model=body.default_model,
        extra=body.extra,
    )
    return {"connection": conn.public()}


@router.get("/connections/{conn_id}")
async def get_connection(conn_id: str, ctx: AppContext = Depends(get_ctx)):
    c = _conn_or_404(ctx, conn_id)
    return {"connection": c.public(), "capabilities": [p.model_dump() for p in ctx.connections.capabilities(f"connection:{c.id}")]}


@router.patch("/connections/{conn_id}")
async def patch_connection(conn_id: str, body: ConnectionPatch, ctx: AppContext = Depends(get_ctx)):
    c = _conn_or_404(ctx, conn_id)
    fields = {k: v for k, v in body.model_dump().items() if v is not None and k not in ("api_key", "credential_storage")}
    if body.api_key:
        storage = body.credential_storage or (c.credential_storage if c.credential_storage != "none" else "host")
        new_ref = _store_secret(ctx, body.api_key, storage, f"{c.provider}:{c.name}")
        if c.credential_ref:
            try:
                ctx.credentials.delete(c.credential_ref)
            except CredentialError:
                pass
        fields["credential_ref"] = new_ref
        fields["credential_storage"] = storage
    conn = ctx.connections.update(conn_id, **fields)
    await ctx.providers.drop(conn_id)
    return {"connection": conn.public()}


@router.delete("/connections/{conn_id}")
async def delete_connection(conn_id: str, ctx: AppContext = Depends(get_ctx)):
    c = _conn_or_404(ctx, conn_id)
    await ctx.providers.drop(conn_id)
    if c.credential_ref:
        try:
            ctx.credentials.delete(c.credential_ref)
        except CredentialError:
            pass
    ctx.connections.delete(conn_id)
    return {"ok": True}


class TestBody(BaseModel):
    probe_model: Optional[str] = None
    allow_generation: bool = False


@router.post("/connections/{conn_id}/test")
async def test_connection(conn_id: str, body: TestBody, ctx: AppContext = Depends(get_ctx)):
    c = _conn_or_404(ctx, conn_id)
    res = await ctx.providers.test_connection(c, probe_model=body.probe_model, allow_generation=body.allow_generation)
    return {"result": res.to_dict(), "connection": (ctx.connections.get(conn_id) or c).public()}


@router.get("/connections/{conn_id}/models")
async def connection_models(conn_id: str, ctx: AppContext = Depends(get_ctx)):
    c = _conn_or_404(ctx, conn_id)
    try:
        adapter = await ctx.providers.adapter_for(c)
        models = await adapter.list_models()
    except GenerationError as exc:
        raise ApiError(502 if exc.code in ("network", "timeout", "server_error") else 409, exc.code, exc.message_de)
    caps = {p.model: p.model_dump() for p in ctx.connections.capabilities(f"connection:{c.id}")}
    return {"models": [{"id": m.id, "display_name": m.display_name, "owned_by": m.owned_by, "context_length": m.context_length, "capabilities": caps.get(m.id), "declared": m.capabilities} for m in models]}


# ---- credential store ------------------------------------------------------------------
class VaultBody(BaseModel):
    password: str = Field(min_length=1, max_length=256)
    new_password: Optional[str] = Field(default=None, min_length=8, max_length=256)


@router.get("/credentials/status")
async def credentials_status(ctx: AppContext = Depends(get_ctx)):
    return ctx.credentials.status()


@router.post("/credentials/vault/create")
async def vault_create(body: VaultBody, ctx: AppContext = Depends(get_ctx)):
    try:
        ctx.credentials.vault.create(body.password)
    except CredentialError as exc:
        raise ApiError(409, exc.code, exc.detail)
    return ctx.credentials.status()


@router.post("/credentials/vault/unlock")
async def vault_unlock(body: VaultBody, ctx: AppContext = Depends(get_ctx)):
    try:
        info = ctx.credentials.vault.unlock(body.password)
    except CredentialError as exc:
        raise ApiError(409 if exc.code != "wrong_password" else 401, exc.code, exc.detail)
    return {**ctx.credentials.status(), **info}


@router.post("/credentials/vault/lock")
async def vault_lock(ctx: AppContext = Depends(get_ctx)):
    ctx.credentials.vault.lock()
    await ctx.providers.close_all()
    return ctx.credentials.status()


@router.post("/credentials/vault/change-password")
async def vault_change(body: VaultBody, ctx: AppContext = Depends(get_ctx)):
    if not body.new_password:
        raise ApiError(422, "invalid", "Neues Passwort fehlt.")
    try:
        ctx.credentials.vault.change_password(body.password, body.new_password)
    except CredentialError as exc:
        raise ApiError(409 if exc.code != "wrong_password" else 401, exc.code, exc.detail)
    return ctx.credentials.status()


# ---- projects and consents ----------------------------------------------------------------
class ProjectBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    local_only: bool = False


class ProjectPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    local_only: Optional[bool] = None


@router.get("/projects")
async def list_projects(ctx: AppContext = Depends(get_ctx)):
    return {"projects": ctx.projects.list(), "consents": ctx.connections.consents()}


@router.post("/projects", status_code=201)
async def create_project(body: ProjectBody, ctx: AppContext = Depends(get_ctx)):
    return {"project": ctx.projects.create(body.name, local_only=body.local_only)}


@router.patch("/projects/{project_id}")
async def patch_project(project_id: str, body: ProjectPatch, ctx: AppContext = Depends(get_ctx)):
    try:
        return {"project": ctx.projects.update(project_id, name=body.name, local_only=body.local_only)}
    except NotFound:
        raise ApiError(404, "not_found", "Projekt nicht gefunden.")


@router.delete("/projects/{project_id}")
async def delete_project(project_id: str, ctx: AppContext = Depends(get_ctx)):
    try:
        ctx.projects.delete(project_id)
    except ValueError as exc:
        raise ApiError(409, "invalid", str(exc))
    except NotFound:
        raise ApiError(404, "not_found", "Projekt nicht gefunden.")
    return {"ok": True}


class ConsentBody(BaseModel):
    connection_id: str
    project_id: str = "default"
    scope: str = "chat"


@router.post("/consents")
async def grant_consent(body: ConsentBody, ctx: AppContext = Depends(get_ctx)):
    _conn_or_404(ctx, body.connection_id)
    try:
        project = ctx.projects.get(body.project_id)
    except NotFound:
        raise ApiError(404, "not_found", "Projekt nicht gefunden.")
    if project["local_only"]:
        raise ApiError(409, "local_only", "Für ein „Nur lokal“-Projekt kann keine Freigabe erteilt werden.")
    return {"consent": ctx.connections.grant_consent(body.connection_id, body.project_id, scope=body.scope)}


@router.delete("/consents/{connection_id}/{project_id}")
async def revoke_consent(connection_id: str, project_id: str, ctx: AppContext = Depends(get_ctx)):
    return {"ok": ctx.connections.revoke_consent(connection_id, project_id)}


# ---- conversation binding -------------------------------------------------------------------
class BindingBody(BaseModel):
    kind: Literal["local", "connection"] = "local"
    connection_id: Optional[str] = None
    provider_model_id: Optional[str] = Field(default=None, max_length=200)


@router.get("/conversations/{conversation_id}/binding")
async def get_binding(conversation_id: str, ctx: AppContext = Depends(get_ctx)):
    conv = ctx.conversations.get(conversation_id)
    binding, project, decision, conn = ctx.chat.policy_for(conv)
    return {"binding": binding.model_dump(), "project": project, "policy": decision.to_dict(), "connection": conn.public() if conn else None}


@router.put("/conversations/{conversation_id}/binding")
async def set_binding(conversation_id: str, body: BindingBody, ctx: AppContext = Depends(get_ctx)):
    conv = ctx.conversations.get(conversation_id)
    if body.kind == "connection":
        if not body.connection_id or not body.provider_model_id:
            raise ApiError(422, "invalid", "Verbindung und Modell-ID sind erforderlich.")
        _conn_or_404(ctx, body.connection_id)
        binding = ModelBinding(kind="connection", connection_id=body.connection_id, provider_model_id=body.provider_model_id)
    else:
        binding = ModelBinding(kind="local")
    conv = ctx.conversations.set_binding(conversation_id, binding.model_dump() if binding.kind != "local" else None)
    b, project, decision, conn = ctx.chat.policy_for(conv)
    return {"binding": b.model_dump(), "project": project, "policy": decision.to_dict(), "connection": conn.public() if conn else None}


@router.put("/conversations/{conversation_id}/project")
async def set_project(conversation_id: str, body: dict, ctx: AppContext = Depends(get_ctx)):
    pid = str(body.get("project_id") or "default")
    try:
        ctx.projects.get(pid)
    except NotFound:
        raise ApiError(404, "not_found", "Projekt nicht gefunden.")
    return {"conversation": ctx.conversations.set_project(conversation_id, pid)}


# ---- Sign in with ChatGPT (optional; never started unattended) -------------------------
def _siwc_conn(ctx: AppContext, conn_id: str):
    conn = ctx.connections.get(conn_id)
    if conn is None:
        raise ApiError(404, "not_found", "Verbindung nicht gefunden.")
    if conn.auth_method != "oauth_siwc":
        raise ApiError(409, "unsupported", "Diese Verbindung nutzt keine ChatGPT-Anmeldung.")
    return conn


@router.get("/connections/{conn_id}/siwc/status")
async def siwc_status(conn_id: str, ctx: AppContext = Depends(get_ctx)):
    return ctx.siwc.status(_siwc_conn(ctx, conn_id))


@router.post("/connections/{conn_id}/siwc/start")
async def siwc_start(conn_id: str, ctx: AppContext = Depends(get_ctx)):
    conn = _siwc_conn(ctx, conn_id)
    try:
        started = await ctx.siwc.start_login(conn)
    except GenerationError as exc:
        raise ApiError(409, exc.code, exc.message_de)
    return {**started, "status": ctx.siwc.status(conn)}


@router.post("/connections/{conn_id}/siwc/cancel")
async def siwc_cancel(conn_id: str, ctx: AppContext = Depends(get_ctx)):
    conn = _siwc_conn(ctx, conn_id)
    cancelled = await ctx.siwc.cancel(conn.id)
    return {"ok": True, "cancelled": cancelled, "status": ctx.siwc.status(ctx.connections.get(conn.id))}


@router.post("/connections/{conn_id}/siwc/logout")
async def siwc_logout(conn_id: str, ctx: AppContext = Depends(get_ctx)):
    conn = _siwc_conn(ctx, conn_id)
    out = await ctx.siwc.logout(conn)
    await ctx.providers.drop(conn.id)
    return {**out, "status": ctx.siwc.status(ctx.connections.get(conn.id))}
