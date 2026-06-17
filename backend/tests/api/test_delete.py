"""Endpoint tests for the GDPR node-delete killswitch.

Regression guard for the bug where the handler passed the DeleteResult object
(not its int edge count) into the response model, audit log, and log line,
producing a "1 validation error" 422 even though the node was already deleted.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.v1 import delete as delete_module
from app.services.graph_service import DeleteResult


def _make_app(graph) -> FastAPI:
    app = FastAPI()
    app.include_router(delete_module.router, prefix="/api/v1/node")
    delete_module._graph_service = graph
    delete_module._redis_client = None  # skip the Redis audit write in tests
    return app


def _node(label: str = "fact: something long", node_type: str = "FACT") -> MagicMock:
    n = MagicMock()
    n.label = label
    n.type = node_type
    return n


@pytest.mark.asyncio
async def test_delete_returns_int_edge_count_not_object():
    graph = MagicMock()
    graph.get_node_by_id = AsyncMock(return_value=_node())
    # The bug: this returns a DeleteResult, which must be reduced to an int.
    graph.delete_node_cascade = AsyncMock(return_value=DeleteResult(deleted_nodes=1, deleted_edges=4))

    app = _make_app(graph)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.delete(
            "/api/v1/node/abc-123", headers={"X-Agent-Permission": "admin"}
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["deleted_node_id"] == "abc-123"
    assert body["deleted_edges"] == 4
    assert isinstance(body["deleted_edges"], int)
    # audit_entry must be JSON-serialisable (the object form broke json.dumps)
    assert body["audit_entry"]["deleted_edges"] == 4


@pytest.mark.asyncio
async def test_delete_missing_node_returns_404():
    graph = MagicMock()
    graph.get_node_by_id = AsyncMock(return_value=None)
    app = _make_app(graph)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.delete(
            "/api/v1/node/gone", headers={"X-Agent-Permission": "admin"}
        )
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_delete_requires_admin_permission():
    graph = MagicMock()
    app = _make_app(graph)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        resp = await client.delete("/api/v1/node/abc-123")  # no admin header
    assert resp.status_code == 403
