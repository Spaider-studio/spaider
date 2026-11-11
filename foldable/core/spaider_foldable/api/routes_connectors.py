"""Memory connectors (contract v1.3, ADR-6).

Management routes (``/api/connectors``) use the normal app token. The RPC
route (``/api/connector-rpc/call``) is exempt from the app token: it
authenticates with the per-connector token, accepts loopback peers only and
refuses browser requests (any ``Origin`` header).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ..connectors.service import CLIENT_KINDS, TOOL_NAMES, ConnectorError, bridge_command
from ..context import AppContext
from .deps import get_ctx
from .errors import ApiError

logger = logging.getLogger(__name__)
router = APIRouter(tags=["connectors"])
rpc_router = APIRouter(tags=["connector-rpc"])

RPC_HEADER = "x-spaider-connector"


class ConnectorBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    client_kind: str = Field(default="generic_stdio")
    projects: list[str] = Field(default_factory=lambda: ["default"])
    rights: list[str] = Field(default_factory=lambda: ["read"])


class RpcBody(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    client_request_id: Optional[str] = Field(default=None, max_length=120)


def _raise(exc: ConnectorError):
    raise ApiError(exc.status, exc.code, exc.detail)


@router.get("/connectors")
async def list_connectors(ctx: AppContext = Depends(get_ctx)):
    core_json = ctx.paths.host_dir / "core.json"
    exe, lead = bridge_command()
    return {
        "connectors": ctx.connectors.list(),
        "client_kinds": list(CLIENT_KINDS),
        "tools": list(TOOL_NAMES),
        "bridge": {"command": exe, "leading_args": lead, "discovery_file": str(core_json), "discovery_present": core_json.exists()},
    }


@router.get("/connectors/chatgpt-tunnel/status")
async def chatgpt_tunnel_status(ctx: AppContext = Depends(get_ctx)):
    """ChatGPT (Apps/Connectors) cannot start a local stdio process; it needs a Secure MCP
    Tunnel. Only the status and the preparation steps are reported here - nothing is
    published and no tunnel is created unattended."""
    import shutil

    tunnel = shutil.which("tunnel-client") or shutil.which("tunnel-client.exe")
    core_json = ctx.paths.host_dir / "core.json"
    return {
        "state": "prepared",
        "state_de": "Vorbereitet (nicht verbunden)",
        "tunnel_client_found": bool(tunnel),
        "tunnel_client_path": tunnel,
        "core_running": core_json.exists(),
        "requirements_de": [
            "ChatGPT-Workspace/Organisation mit Zugriff auf die Plattform-Tunnel-Einstellungen",
            "Tunnel-ID und Runtime-API-Key (Tunnels Read+Use) aus den Plattform-Einstellungen",
            "tunnel-client (Download aus den Tunnel-Einstellungen oder github.com/openai/tunnel-client)",
            "Ein SpAIder-Connector mit passenden Rechten (siehe „Verbindungen“ → „Gedächtnis-Clients“)",
        ],
        "steps_de": [
            "In SpAIder einen Connector vom Typ „ChatGPT (Tunnel)“ anlegen; die Brücke als MCP-Befehl notieren.",
            "tunnel-client init --profile spaider --tunnel-id <ID> --mcp-command \"<Brückenbefehl>\" ausführen (Runtime-Key wird abgefragt).",
            "tunnel-client doctor und anschließend tunnel-client run starten; SpAIder muss laufen.",
            "In ChatGPT den Connector über die Plattform-Tunnel-Einstellungen dem Workspace zuordnen (nicht öffentlich veröffentlichen).",
        ],
        "limits_de": "Limits laut Dokumentation: 600 Anfragen/Minute, 10 MiB je Anfrage, 120 s Zeitlimit. Keine öffentliche Plugin-Verteilung.",
        "docs_url": "https://platform.openai.com/docs/mcp",
    }


@router.post("/connectors", status_code=201)
async def create_connector(body: ConnectorBody, ctx: AppContext = Depends(get_ctx)):
    try:
        grant, token = ctx.connectors.create(body.name, body.client_kind, body.projects, body.rights)
    except ConnectorError as exc:
        _raise(exc)
    logger.info("connector created: %s (%s) rights=%s projects=%s", grant["id"], grant["client_kind"], grant["rights"], grant["projects"])
    return {"connector": grant, "token_once": token, "setup": ctx.connectors.setup_snippets(grant)}


@router.get("/connectors/{connector_id}/setup")
async def connector_setup(connector_id: str, ctx: AppContext = Depends(get_ctx)):
    grant = next((g for g in ctx.connectors.list() if g["id"] == connector_id), None)
    if grant is None:
        raise ApiError(404, "not_found", "Connector nicht gefunden.")
    return {"connector": grant, "setup": ctx.connectors.setup_snippets(grant)}


@router.delete("/connectors/{connector_id}")
async def revoke_connector(connector_id: str, ctx: AppContext = Depends(get_ctx)):
    if not ctx.connectors.revoke(connector_id):
        raise ApiError(404, "not_found", "Connector nicht gefunden oder bereits widerrufen.")
    logger.info("connector revoked: %s", connector_id)
    return {"ok": True, "connectors": ctx.connectors.list()}


def _loopback_only(request: Request) -> None:
    host = (request.client.host if request.client else "") or ""
    if host not in ("127.0.0.1", "::1", "testclient"):
        raise ApiError(403, "forbidden", "Connector-Aufrufe sind nur von diesem Rechner erlaubt.")
    if request.headers.get("origin"):
        raise ApiError(403, "forbidden", "Connector-Aufrufe aus dem Browser sind nicht erlaubt.")
    hdr_host = (request.headers.get("host") or "").split(":", 1)[0].lower()
    if hdr_host not in ("127.0.0.1", "localhost", "[::1]", "testserver", ""):
        raise ApiError(403, "forbidden", "Ungültiger Host-Header.")


@rpc_router.post("/connector-rpc/call")
async def connector_call(body: RpcBody, request: Request, ctx: AppContext = Depends(get_ctx)):
    _loopback_only(request)
    token = request.headers.get(RPC_HEADER, "")
    try:
        grant = ctx.connectors.authenticate(token)
        result = ctx.connectors.call(grant, body.tool, body.args, client_request_id=body.client_request_id)
    except ConnectorError as exc:
        _raise(exc)
    return {"ok": True, "connector_id": grant["id"], "tool": body.tool, "result": result}
