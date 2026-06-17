"""
WebSocket connection manager — broadcasts live ingest events to connected frontends.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import DefaultDict, Set

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WebSocketManager:
    def __init__(self) -> None:
        self._connections: DefaultDict[str, Set[WebSocket]] = defaultdict(set)

    async def connect(self, ws: WebSocket, agent_id: str) -> None:
        await ws.accept()
        self._connections[agent_id].add(ws)
        logger.info("WS connected: agent=%s total=%d", agent_id, len(self._connections[agent_id]))

    def disconnect(self, ws: WebSocket, agent_id: str) -> None:
        self._connections[agent_id].discard(ws)
        logger.info("WS disconnected: agent=%s total=%d", agent_id, len(self._connections[agent_id]))

    async def broadcast(self, agent_id: str, event: dict) -> None:
        """Broadcast a JSON event to all WebSocket clients watching this agent."""
        if not self._connections[agent_id]:
            return
        payload = json.dumps(event)
        dead: list[WebSocket] = []
        for ws in list(self._connections[agent_id]):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._connections[agent_id].discard(ws)


# Global singleton shared across the app
ws_manager = WebSocketManager()
