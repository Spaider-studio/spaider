"""
WebSocket endpoint — clients connect here to receive live ingest events.
"""
from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.ws_manager import ws_manager

router = APIRouter()


@router.websocket("/ws/{agent_id}")
async def websocket_endpoint(ws: WebSocket, agent_id: str):
    """
    WebSocket endpoint for live ingest streaming.
    Connect from the frontend at: ws://localhost:8000/ws/{agent_id}

    Events pushed to the client:
      {"type": "status",  "message": "..."}
      {"type": "node",    "node": {...}}
      {"type": "edge",    "edge": {...}}
      {"type": "done",    "nodes_created": N, "edges_created": M}
      {"type": "error",   "message": "..."}
    """
    await ws_manager.connect(ws, agent_id)
    try:
        while True:
            await ws.receive_text()  # keep-alive ping loop
    except WebSocketDisconnect:
        ws_manager.disconnect(ws, agent_id)
