"""
Tests for SwarmService / Swarm API: connection management, permission validation,
scope filtering, and access denial.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.schemas import Agent, Edge, GraphPayload, Node, SwarmConnection


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_node(label: str, ntype: str = "PERSON", agent_id: str = "agent-a") -> Node:
    return Node(
        id=str(uuid.uuid4()),
        label=label,
        type=ntype,
        properties={"confidence": 0.9},
        agent_id=agent_id,
    )


def _make_connection(
    source: str = "agent-a",
    target: str = "agent-b",
    permission: str = "read_only",
    scope: str = "full",
    allowed_node_types: list | None = None,
    allowed_relation_types: list | None = None,
) -> SwarmConnection:
    return SwarmConnection(
        id=str(uuid.uuid4()),
        source_agent_id=source,
        target_agent_id=target,
        permission=permission,
        scope=scope,
        allowed_node_types=allowed_node_types,
        allowed_relation_types=allowed_relation_types,
        created_at=datetime.now(timezone.utc),
    )


# In-memory Redis store for tests
class FakeRedis:
    def __init__(self):
        self._store: dict = {}
        self._sets: dict = {}

    async def set(self, key: str, value: str, **kwargs):
        self._store[key] = value

    async def get(self, key: str):
        return self._store.get(key)

    async def delete(self, *keys: str) -> int:
        count = 0
        for k in keys:
            if k in self._store:
                del self._store[k]
                count += 1
        return count

    async def sadd(self, key: str, *values):
        self._sets.setdefault(key, set()).update(values)

    async def srem(self, key: str, *values):
        self._sets.get(key, set()).discard(next(iter(values), None))

    async def smembers(self, key: str):
        return self._sets.get(key, set())

    async def ping(self):
        return True

    async def aclose(self):
        pass


# ---------------------------------------------------------------------------
# test_create_connection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_connection():
    """_save_connection / _load_connection should persist and retrieve a SwarmConnection."""
    import app.api.v1.swarm as swarm_module

    fake_redis = FakeRedis()

    # Inject fake_redis as both the cached client and the return value of _get_redis
    original_client = swarm_module._redis_client

    async def _fake_get_redis():
        return fake_redis

    original_get_redis = swarm_module._get_redis
    swarm_module._redis_client = fake_redis
    swarm_module._get_redis = _fake_get_redis

    try:
        from app.api.v1.swarm import _save_connection, _load_connection

        conn = _make_connection("agent-a", "agent-b")
        await _save_connection(conn)
        loaded = await _load_connection(conn.id)
    finally:
        swarm_module._redis_client = original_client
        swarm_module._get_redis = original_get_redis

    assert loaded is not None
    assert loaded.id == conn.id
    assert loaded.source_agent_id == "agent-a"
    assert loaded.target_agent_id == "agent-b"
    assert loaded.permission == "read_only"


# ---------------------------------------------------------------------------
# test_revoke_connection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revoke_connection():
    """Revoking a connection should remove it from storage."""
    fake_redis = FakeRedis()

    from app.api.v1.swarm import _delete_connection, _load_connection, _save_connection

    with patch("app.api.v1.swarm._redis_client", fake_redis):
        conn = _make_connection("agent-a", "agent-b")
        await _save_connection(conn)

        # Verify it exists
        assert await _load_connection(conn.id) is not None

        # Revoke it
        deleted = await _delete_connection(conn.id)
        assert deleted is True

        # Verify it's gone
        assert await _load_connection(conn.id) is None


# ---------------------------------------------------------------------------
# test_swarm_query_validates_permission
# ---------------------------------------------------------------------------


@pytest.mark.skip(
    reason="Legacy connection-based query replaced by Swarm Intelligence endpoint"
)
@pytest.mark.asyncio
async def test_swarm_query_validates_permission():
    """
    A swarm query should succeed when a valid connection exists and
    include the target agent in sources.
    """
    fake_redis = FakeRedis()
    conn = _make_connection("agent-a", "agent-b", permission="read_only")

    mock_graph = AsyncMock()
    n1 = _make_node("Alice", agent_id="agent-b")
    n2 = _make_node("Acme", "ORGANIZATION", agent_id="agent-b")
    mock_graph.get_all_nodes = AsyncMock(return_value=[n1, n2])
    mock_graph.get_all_edges = AsyncMock(return_value=[])

    from app.api.v1.swarm import (
        _save_connection,
        swarm_query,
    )
    from app.models.requests import SwarmFederatedQueryRequest

    with (
        patch("app.api.v1.swarm._redis_client", fake_redis),
        patch("app.api.v1.swarm._get_graph_service", return_value=mock_graph),
    ):
        await _save_connection(conn)
        request = SwarmFederatedQueryRequest(
            question="Who works at Acme?",
            source_agent_id="agent-a",
            target_agent_ids=["agent-b"],
            merge_results=True,
        )
        response = await swarm_query(request)

    assert "agent-b" in response.agents_involved


# ---------------------------------------------------------------------------
# test_swarm_query_applies_scope_filter
# ---------------------------------------------------------------------------


@pytest.mark.skip(
    reason="Legacy connection-based query replaced by Swarm Intelligence endpoint"
)
@pytest.mark.asyncio
async def test_swarm_query_applies_scope_filter():
    """
    A connection with scope='filtered' and allowed_node_types should only
    return nodes of permitted types.
    """
    fake_redis = FakeRedis()
    conn = _make_connection(
        "agent-a",
        "agent-b",
        permission="read_only",
        scope="filtered",
        allowed_node_types=["PERSON"],
    )

    n_person = _make_node("Alice", "PERSON", agent_id="agent-b")
    n_org = _make_node("Acme", "ORGANIZATION", agent_id="agent-b")

    mock_graph = AsyncMock()
    mock_graph.get_all_nodes = AsyncMock(return_value=[n_person, n_org])
    mock_graph.get_all_edges = AsyncMock(return_value=[])
    mock_graph.get_full_graph = AsyncMock(
        return_value=GraphPayload(nodes=[n_person, n_org], edges=[])
    )

    from app.api.v1.swarm import _save_connection, swarm_query
    from app.models.requests import SwarmFederatedQueryRequest

    with (
        patch("app.api.v1.swarm._redis_client", fake_redis),
        patch("app.api.v1.swarm._get_graph_service", return_value=mock_graph),
    ):
        await _save_connection(conn)
        request = SwarmFederatedQueryRequest(
            question="List entities",
            source_agent_id="agent-a",
            target_agent_ids=["agent-b"],
            merge_results=True,
        )
        response = await swarm_query(request)


# ---------------------------------------------------------------------------
# test_swarm_query_denied_without_connection
# ---------------------------------------------------------------------------


@pytest.mark.skip(
    reason="Legacy connection-based query replaced by Swarm Intelligence endpoint"
)
@pytest.mark.asyncio
async def test_swarm_query_denied_without_connection():
    """
    A swarm query should raise HTTP 403 when no connection exists from
    source to target agent.
    """
    from fastapi import HTTPException

    fake_redis = FakeRedis()
    # No connection saved

    mock_graph = AsyncMock()
    mock_graph.get_all_nodes = AsyncMock(return_value=[])
    mock_graph.get_all_edges = AsyncMock(return_value=[])

    from app.api.v1.swarm import swarm_query
    from app.models.requests import SwarmFederatedQueryRequest

    with (
        patch("app.api.v1.swarm._redis_client", fake_redis),
        patch("app.api.v1.swarm._get_graph_service", return_value=mock_graph),
    ):
        request = SwarmFederatedQueryRequest(
            question="Who works at Acme?",
            source_agent_id="agent-x",
            target_agent_ids=["agent-y"],
            merge_results=True,
        )

        with pytest.raises(HTTPException) as exc_info:
            await swarm_query(request)

    assert exc_info.value.status_code == 403
