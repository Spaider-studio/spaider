"""Integration tests for POST /agents and POST /agents/{agent_id}/rotate-key.

Covers API key rotation without knowledge loss. ``AuthService``
runs its real code paths against a dict-backed Redis mock, so the hashing
contract is actually exercised rather than mocked away. Neo4j is stubbed
via ``AsyncGraphDatabase`` so no real bolt connection is attempted.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.v1 import agents as agents_module
from app.core.security import hash_api_key
from app.services.auth_service import _API_KEY_PREFIX, AuthService


# ---------------------------------------------------------------------------
# Test harness: a FastAPI app mounting only the agents router, with every
# external dependency (Redis, Neo4j, AuthService) wired through deterministic
# in-process fakes. AuthService itself runs for real against the fake Redis.
# ---------------------------------------------------------------------------


def _make_fake_redis() -> tuple[AsyncMock, dict]:
    """Dict-backed Redis mock supporting everything the agents router + AuthService touch."""
    store: dict = {}
    sets: dict[str, set] = {}

    mock = AsyncMock()

    async def _set(k, v, **_kw):
        store[k] = v

    async def _get(k):
        return store.get(k)

    async def _delete(*keys):
        removed = 0
        for k in keys:
            if store.pop(k, None) is not None:
                removed += 1
        return removed

    async def _sadd(k, *vals):
        sets.setdefault(k, set()).update(vals)
        return len(vals)

    async def _srem(k, *vals):
        s = sets.get(k, set())
        removed = 0
        for v in vals:
            if v in s:
                s.discard(v)
                removed += 1
        return removed

    async def _smembers(k):
        return set(sets.get(k, set()))

    async def _scan_iter(match: str = "*", count: int = 100):
        prefix = match[:-1] if match.endswith("*") else match
        for k in list(store.keys()):
            if k.startswith(prefix):
                yield k

    mock.set = AsyncMock(side_effect=_set)
    mock.get = AsyncMock(side_effect=_get)
    mock.delete = AsyncMock(side_effect=_delete)
    mock.sadd = AsyncMock(side_effect=_sadd)
    mock.srem = AsyncMock(side_effect=_srem)
    mock.smembers = AsyncMock(side_effect=_smembers)
    mock.scan_iter = _scan_iter
    mock.aclose = AsyncMock()

    return mock, store


def _make_fake_graph_service() -> MagicMock:
    """Neo4j stub that tracks how many times the agent graph is touched."""
    mock_session = AsyncMock()
    mock_result = AsyncMock()
    mock_result.data = AsyncMock(return_value=[])
    mock_result.single = AsyncMock(return_value=None)
    mock_session.run = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    mock_driver = MagicMock()
    mock_driver.session = MagicMock(return_value=mock_session)

    gs = MagicMock()
    gs._driver = mock_driver
    gs.create_agent_node = AsyncMock()
    gs.delete_agent_graph = AsyncMock()
    gs.write_graph_batch = AsyncMock()
    return gs


@pytest.fixture
async def client_and_state():
    """Yield (AsyncClient, redis-store, graph_service_mock).

    Resets the agents-module singletons so each test is isolated.
    """
    fake_redis, store = _make_fake_redis()
    fake_graph = _make_fake_graph_service()
    auth = AuthService()
    auth._redis = fake_redis  # run real AuthService code against the fake

    # Patch the module-level caches agents.py uses. We patch the functions
    # rather than the attributes so the router always gets our fakes even
    # after teardown of a prior test.
    with (
        patch.object(agents_module, "_get_redis", AsyncMock(return_value=fake_redis)),
        patch.object(agents_module, "_get_graph_service", return_value=fake_graph),
        patch.object(agents_module, "_get_auth_service", return_value=auth),
    ):
        app = FastAPI()
        app.include_router(agents_module.router, prefix="/agents")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac, store, fake_graph, auth


def _create_agent_payload(name: str = "A") -> dict:
    return {
        "name": name,
        "description": "rotation test",
        "tenant_id": "t-1",
        "permissions": ["read", "write"],
        "clearance_level": 1,
    }


def _load_agent_record(store: dict, agent_id: str) -> dict:
    raw = store[f"spaider:agent:{agent_id}"]
    return json.loads(raw)


# ===========================================================================
# TestCreateAgentAuth
# ===========================================================================


class TestCreateAgentAuth:
    @pytest.mark.asyncio
    async def test_response_api_key_is_sk_prefixed(self, client_and_state) -> None:
        client, _, _, _ = client_and_state
        r = await client.post("/agents", json=_create_agent_payload("create-1"))
        assert r.status_code == 201
        api_key = r.json()["agent"]["api_key"]
        assert api_key.startswith("sk-")

    @pytest.mark.asyncio
    async def test_response_api_key_authenticates_immediately(self, client_and_state) -> None:
        client, _, _, auth = client_and_state
        r = await client.post("/agents", json=_create_agent_payload("create-2"))
        api_key = r.json()["agent"]["api_key"]
        agent_id = r.json()["agent"]["id"]

        payload = await auth.verify_token(api_key)
        assert payload["sub"] == agent_id
        assert payload["permissions"] == ["read", "write"]

    @pytest.mark.asyncio
    async def test_redis_agent_record_stores_hash_never_raw(self, client_and_state) -> None:
        client, store, _, _ = client_and_state
        r = await client.post("/agents", json=_create_agent_payload("create-3"))
        body = r.json()
        raw_key = body["agent"]["api_key"]
        agent_id = body["agent"]["id"]

        record = _load_agent_record(store, agent_id)
        assert "api_key" not in record, "Raw key must never land in the agent record"
        assert record["api_key_hash"] == hash_api_key(raw_key)
        # And the apikey slot exists — hashed, not raw.
        assert f"{_API_KEY_PREFIX}{hash_api_key(raw_key)}" in store
        assert f"{_API_KEY_PREFIX}{raw_key}" not in store


# ===========================================================================
# TestKeyRotation
# ===========================================================================


class TestKeyRotation:
    @pytest.mark.asyncio
    async def test_rotated_key_is_new_and_sk_prefixed(self, client_and_state) -> None:
        client, _, _, _ = client_and_state
        r1 = await client.post("/agents", json=_create_agent_payload("rotate-1"))
        old = r1.json()["agent"]["api_key"]
        agent_id = r1.json()["agent"]["id"]

        r2 = await client.post(f"/agents/{agent_id}/rotate-key")
        assert r2.status_code == 200
        new = r2.json()["api_key"]
        assert new.startswith("sk-")
        assert new != old

    @pytest.mark.asyncio
    async def test_rotated_key_authenticates_immediately(self, client_and_state) -> None:
        client, _, _, auth = client_and_state
        r1 = await client.post("/agents", json=_create_agent_payload("rotate-2"))
        agent_id = r1.json()["agent"]["id"]

        r2 = await client.post(f"/agents/{agent_id}/rotate-key")
        new = r2.json()["api_key"]

        payload = await auth.verify_token(new)
        assert payload["sub"] == agent_id

    @pytest.mark.asyncio
    async def test_agent_record_hash_updated_to_new_key(self, client_and_state) -> None:
        client, store, _, _ = client_and_state
        r1 = await client.post("/agents", json=_create_agent_payload("rotate-3"))
        agent_id = r1.json()["agent"]["id"]

        r2 = await client.post(f"/agents/{agent_id}/rotate-key")
        new = r2.json()["api_key"]

        record = _load_agent_record(store, agent_id)
        assert record["api_key_hash"] == hash_api_key(new)

    @pytest.mark.asyncio
    async def test_rotate_nonexistent_agent_returns_404(self, client_and_state) -> None:
        client, _, _, _ = client_and_state
        r = await client.post("/agents/does-not-exist/rotate-key")
        assert r.status_code == 404


# ===========================================================================
# TestOldKeyRevoked
# ===========================================================================


class TestOldKeyRevoked:
    @pytest.mark.asyncio
    async def test_old_key_verify_raises_after_rotation(self, client_and_state) -> None:
        client, _, _, auth = client_and_state
        r1 = await client.post("/agents", json=_create_agent_payload("revoke-1"))
        old = r1.json()["agent"]["api_key"]
        agent_id = r1.json()["agent"]["id"]

        await client.post(f"/agents/{agent_id}/rotate-key")

        with pytest.raises(ValueError):
            await auth.verify_token(old)

    @pytest.mark.asyncio
    async def test_old_hash_slot_absent_from_redis(self, client_and_state) -> None:
        client, store, _, _ = client_and_state
        r1 = await client.post("/agents", json=_create_agent_payload("revoke-2"))
        old = r1.json()["agent"]["api_key"]
        agent_id = r1.json()["agent"]["id"]
        old_slot = f"{_API_KEY_PREFIX}{hash_api_key(old)}"
        assert old_slot in store  # pre-condition

        await client.post(f"/agents/{agent_id}/rotate-key")

        assert old_slot not in store, "Old hashed apikey slot must be deleted on rotation"


# ===========================================================================
# TestKnowledgePersistence — the core issue assertion: the knowledge graph
# is keyed on agent_id, so rotation must not touch it.
# ===========================================================================


class TestKnowledgePersistence:
    @pytest.mark.asyncio
    async def test_rotation_does_not_touch_neo4j(self, client_and_state) -> None:
        client, _, graph, _ = client_and_state
        r1 = await client.post("/agents", json=_create_agent_payload("persist-1"))
        agent_id = r1.json()["agent"]["id"]

        # Reset counters so we only measure the rotate call.
        graph.create_agent_node.reset_mock()
        graph.write_graph_batch.reset_mock()
        graph.delete_agent_graph.reset_mock()
        graph._driver.session.reset_mock()

        r2 = await client.post(f"/agents/{agent_id}/rotate-key")
        assert r2.status_code == 200

        assert graph.create_agent_node.call_count == 0
        assert graph.write_graph_batch.call_count == 0
        assert graph.delete_agent_graph.call_count == 0
        assert graph._driver.session.call_count == 0

    @pytest.mark.asyncio
    async def test_agent_metadata_unchanged_by_rotation(self, client_and_state) -> None:
        client, store, _, _ = client_and_state
        r1 = await client.post("/agents", json=_create_agent_payload("persist-2"))
        agent_id = r1.json()["agent"]["id"]
        before = _load_agent_record(store, agent_id)

        await client.post(f"/agents/{agent_id}/rotate-key")
        after = _load_agent_record(store, agent_id)

        # Every field except api_key_hash must be preserved byte-for-byte.
        for field in ("id", "name", "description", "tenant_id", "permissions", "clearance_level", "created_at"):
            assert before[field] == after[field], f"{field} mutated by rotation"
        assert before["api_key_hash"] != after["api_key_hash"]

    @pytest.mark.asyncio
    async def test_list_and_get_never_expose_hash(self, client_and_state) -> None:
        client, _, _, _ = client_and_state
        r1 = await client.post("/agents", json=_create_agent_payload("persist-3"))
        agent_id = r1.json()["agent"]["id"]

        r_get = await client.get(f"/agents/{agent_id}")
        assert r_get.json()["agent"].get("api_key_hash") is None

        r_list = await client.get("/agents")
        for a in r_list.json()["agents"]:
            assert a.get("api_key_hash") is None
