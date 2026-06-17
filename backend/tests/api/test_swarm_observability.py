"""The swarm-query endpoint must log to the Audit Log + analytics like /query.

Regression guard for the gap where /swarm/query fired no replay/analytics
events, so "Swarm intelligence" queries were invisible in the Audit Log.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.v1 import swarm as swarm_module
from app.models.responses import SwarmQueryResponse


def _make_app(auth: dict) -> FastAPI:
    app = FastAPI()
    app.include_router(swarm_module.router, prefix="/api/v1/swarm")
    app.dependency_overrides[swarm_module.verify_api_key] = lambda: auth
    return app


_RESP = SwarmQueryResponse(
    answer="Olivia leads Beacon.",
    source_node_ids=["n1", "n2"],
    agents_involved=["agent-a"],
)


@pytest.mark.asyncio
async def test_swarm_query_fires_audit_events_federated():
    app = _make_app({"agent_id": "agent-a", "clearance_level": 1})
    with patch("app.api.v1.swarm._federated_deep_query", new=AsyncMock(return_value=_RESP)), \
         patch("app.api.v1.query._fire_replay_event") as fire:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            resp = await c.post("/api/v1/swarm/query", json={"query": "who leads Beacon?"})

    assert resp.status_code == 200, resp.text
    fired = [call.args[2] for call in fire.call_args_list]
    assert "query_received" in fired
    assert "query_answered" in fired
    # every event is tagged swarm so the Audit Log can distinguish it
    for call in fire.call_args_list:
        assert call.args[3].get("mode") == "swarm"


@pytest.mark.asyncio
async def test_swarm_query_fires_audit_events_broad_scan():
    # auth bypassed → no agent scoping → broad multiverse scan branch
    app = _make_app({"auth_bypassed": True})
    with patch("app.api.v1.swarm._broad_vector_scan", new=AsyncMock(return_value=_RESP)), \
         patch("app.api.v1.query._fire_replay_event") as fire:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            resp = await c.post("/api/v1/swarm/query", json={"query": "anything"})

    assert resp.status_code == 200, resp.text
    fired = [call.args[2] for call in fire.call_args_list]
    assert fired == ["query_received", "query_answered"]


@pytest.mark.asyncio
async def test_swarm_query_fires_failed_event_on_error():
    app = _make_app({"auth_bypassed": True})
    # raise_app_exceptions=False so the test sees the 500 response instead of
    # the raw exception propagating (FastAPI's ServerErrorMiddleware does this
    # in production).
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    with patch("app.api.v1.swarm._broad_vector_scan", new=AsyncMock(side_effect=RuntimeError("boom"))), \
         patch("app.api.v1.query._fire_replay_event") as fire:
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            resp = await c.post("/api/v1/swarm/query", json={"query": "anything"})

    assert resp.status_code == 500
    fired = [call.args[2] for call in fire.call_args_list]
    assert "query_failed" in fired
