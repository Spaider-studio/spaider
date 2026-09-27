"""
WebSocket endpoint — clients connect here to receive live ingest events.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

import app.services.auth_service as _auth
from app.services.auth_service import AuthService, _is_admin
from app.services.ws_manager import ws_manager

logger = logging.getLogger(__name__)

router = APIRouter()


async def _authorize_ws(agent_id: str, token: Optional[str]) -> bool:
    """Authorize a WebSocket subscription to ``agent_id``.

    Browsers cannot set custom headers on a WebSocket handshake, so the key is
    passed as a ``?token=`` query parameter (an ``sk-`` API key). Returns True
    when the connection may proceed:

      - auth disabled (flag off) → always allowed (single-tenant / dev),
      - a valid key whose agent_id matches the requested stream, or
      - a valid admin key (may subscribe to any stream).

    Any other case (missing/invalid key, cross-agent non-admin) is denied.
    """
    if not _auth._REQUIRE_API_KEY_AUTH:
        return True
    if not token:
        return False
    try:
        record = await AuthService().get_agent_by_api_key(token)
    except Exception as exc:  # noqa: BLE001
        logger.warning("WS auth lookup failed: %s", exc)
        return False
    if not record or "agent_id" not in record:
        return False
    return _is_admin(record) or record.get("agent_id") == agent_id


@router.websocket("/ws/{agent_id}")
async def websocket_endpoint(
    ws: WebSocket,
    agent_id: str,
    token: Optional[str] = Query(default=None),
):
    """
    WebSocket endpoint for live ingest streaming.
    Connect from the frontend at: ws://localhost:8000/ws/{agent_id}
    When REQUIRE_API_KEY_AUTH is on, pass the API key as ?token=sk-...; the key
    must belong to {agent_id} (or be an admin key).

    Events pushed to the client:
      {"type": "status",  "message": "..."}
      {"type": "node",    "node": {...}}
      {"type": "edge",    "edge": {...}}
      {"type": "done",    "nodes_created": N, "edges_created": M}
      {"type": "error",   "message": "..."}
    """
    if not await _authorize_ws(agent_id, token):
        # 1008 = policy violation. Close before accepting so no events leak.
        await ws.close(code=1008)
        return

    await ws_manager.connect(ws, agent_id)
    try:
        while True:
            await ws.receive_text()  # keep-alive ping loop
    except WebSocketDisconnect:
        ws_manager.disconnect(ws, agent_id)
