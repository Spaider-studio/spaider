"""
WebSocket endpoint — clients connect here to receive live ingest events.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.core.authz import Action, can, resolve_token
from app.services.ws_manager import ws_manager

router = APIRouter()


async def _authorize_ws(agent_id: str, token: Optional[str]) -> bool:
    """Authorize a WebSocket subscription to ``agent_id``.

    Browsers cannot set custom headers on a WebSocket handshake, so the key is
    passed as a ``?token=`` query parameter. The connection may proceed for
    a valid key with read access to the KB.
    """
    principal = await resolve_token(token)
    return principal is not None and await can(principal, agent_id, Action.READ)


@router.websocket("/ws/{agent_id}")
async def websocket_endpoint(
    ws: WebSocket,
    agent_id: str,
    token: Optional[str] = Query(default=None),
):
    """
    WebSocket endpoint for live ingest streaming.
    Connect from the frontend at: ws://localhost:8000/ws/{agent_id}
    Pass the API key as ?token=...; the key
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
