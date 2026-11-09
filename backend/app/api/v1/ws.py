"""
WebSocket endpoint — clients connect here to receive live ingest events.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.core.authz import Action, can
from app.core.tickets import redeem_ticket
from app.services.ws_manager import ws_manager

router = APIRouter()


async def _authorize_ws(agent_id: str, ticket: Optional[str]) -> bool:
    """Authorize a WebSocket subscription to ``agent_id``.

    Browsers cannot set custom headers on a WebSocket handshake, so the client
    passes a single-use ticket from ``POST /auth/ticket``, never its API key.
    """
    principal = await redeem_ticket(ticket)
    return principal is not None and await can(principal, agent_id, Action.READ)


@router.websocket("/ws/{agent_id}")
async def websocket_endpoint(
    ws: WebSocket,
    agent_id: str,
    ticket: Optional[str] = Query(default=None),
):
    """
    WebSocket endpoint for live ingest streaming.
    Connect from the frontend at: ws://localhost:8000/ws/{agent_id}
    Pass a ticket from POST /auth/ticket as ?ticket=...; its owner needs read
    access to {agent_id}.

    Events pushed to the client:
      {"type": "status",  "message": "..."}
      {"type": "node",    "node": {...}}
      {"type": "edge",    "edge": {...}}
      {"type": "done",    "nodes_created": N, "edges_created": M}
      {"type": "error",   "message": "..."}
    """
    if not await _authorize_ws(agent_id, ticket):
        # 1008 = policy violation. Close before accepting so no events leak.
        await ws.close(code=1008)
        return

    await ws_manager.connect(ws, agent_id)
    try:
        while True:
            await ws.receive_text()  # keep-alive ping loop
    except WebSocketDisconnect:
        ws_manager.disconnect(ws, agent_id)
