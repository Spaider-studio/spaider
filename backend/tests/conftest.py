"""
Shared pytest fixtures for SpAIder backend tests.
"""
from __future__ import annotations

# Must run before anything below imports app.config. See tests/_safety.py.
from tests import _safety  # isort: skip

_safety.install_test_environment()
_safety.block_real_services()

import json  # noqa: E402
import os  # noqa: E402
import uuid  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from typing import AsyncIterator  # noqa: E402
from unittest.mock import AsyncMock, MagicMock, patch  # noqa: E402

import pytest  # noqa: E402

from app.models.schemas import Agent, Edge, GraphPayload, Node  # noqa: E402
from tests.stubs.fixtures import openai_stub  # noqa: E402, F401


def _integration_unavailable(service: str, exc: Exception) -> None:
    """Skip locally; fail when the gate demands real services."""
    message = f"{service} test service unreachable: {exc}"
    if os.environ.get("SPAIDER_REQUIRE_INTEGRATION") == "1":
        pytest.fail(message)
    pytest.skip(message)


@pytest.fixture(autouse=True)
def _fresh_kv():
    """Cached clients must not leak mocks or event loops between tests."""
    from app.core.jobs import reset_job_queue
    from app.core.kv import reset_kv

    reset_kv()
    reset_job_queue()
    yield
    reset_kv()
    reset_job_queue()


@pytest.fixture
async def neo4j_test_driver():
    """A driver for the throwaway Neo4j, wiped before each test."""
    from neo4j import AsyncGraphDatabase

    driver = AsyncGraphDatabase.driver(
        _safety.neo4j_uri_for_tests(),
        auth=("neo4j", _safety.neo4j_password_for_tests()),
    )
    try:
        await driver.verify_connectivity()
    except Exception as exc:  # noqa: BLE001
        await driver.close()
        _integration_unavailable("Neo4j", exc)
    await _safety.assert_test_database(driver)
    await _safety.wipe_test_database(driver)
    yield driver
    await _safety.wipe_test_database(driver)
    await driver.close()


class AuthWorld:
    """An org on the real test database, driven through the real API router."""

    def __init__(self, client, identity, driver, owner_key, org_id) -> None:
        self.client = client
        self.identity = identity
        self.driver = driver
        self.owner_key = owner_key
        self.org_id = org_id

    @staticmethod
    def headers(key: str) -> dict:
        return {"X-Api-Key": key}

    async def create_kb(self, name: str = "kb", key: str | None = None, **body) -> dict:
        response = await self.client.post(
            "/api/v1/agents",
            json={"name": name, **body},
            headers=self.headers(key or self.owner_key),
        )
        assert response.status_code == 201, response.text
        return response.json()["agent"]

    async def user(self, name: str = "u", **kwargs) -> dict:
        return await self.identity.create_user(self.org_id, name, **kwargs)

    async def member(self, kb_id: str, role: str, **kwargs) -> dict:
        """A user holding ``role`` on one KB."""
        user = await self.user(role, **kwargs)
        await self.identity.grant_role(self.org_id, kb_id, user["id"], role)
        return user


@pytest.fixture
async def auth_world(neo4j_test_driver, monkeypatch):
    """Authentication on, schema created, API router mounted, one org bootstrapped."""
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from app.api.router import api_router
    from app.config import settings
    from app.services import graph_service as graph_module
    from app.services.identity_service import get_identity_service

    monkeypatch.setattr(settings, "spaider_insecure_dev", False)
    monkeypatch.setattr(settings, "spaider_bootstrap_token", "bootstrap-secret")
    service = graph_module.GraphService()
    await service.initialize()
    monkeypatch.setattr(graph_module, "_shared_graph_service", service)

    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")
    identity = get_identity_service()
    created = await identity.bootstrap("Acme", "Owner")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        yield AuthWorld(client, identity, service._driver, created["api_key"], created["org_id"])
    await service.close()


# ---------------------------------------------------------------------------
# Neo4j mock
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_neo4j():
    """Patch AsyncGraphDatabase so no real Neo4j connection is made."""
    with patch("app.services.graph_service.AsyncGraphDatabase") as mock_db_cls:
        mock_driver = AsyncMock()

        # Default session mock that returns empty results
        mock_result = AsyncMock()
        mock_result.data = AsyncMock(return_value=[])
        mock_result.single = AsyncMock(return_value=None)

        mock_session = AsyncMock()
        mock_session.run = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_driver.session = MagicMock(return_value=mock_session)
        mock_db_cls.driver.return_value = mock_driver

        yield mock_driver


# ---------------------------------------------------------------------------
# Kafka mock
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_kafka():
    """Patch aiokafka producer and consumer."""
    with (
        patch("aiokafka.AIOKafkaProducer") as mock_producer_cls,
        patch("aiokafka.AIOKafkaConsumer") as mock_consumer_cls,
    ):
        mock_producer = AsyncMock()
        mock_producer.start = AsyncMock()
        mock_producer.stop = AsyncMock()
        mock_producer.send_and_wait = AsyncMock()
        mock_producer_cls.return_value = mock_producer

        mock_consumer = AsyncMock()
        mock_consumer.start = AsyncMock()
        mock_consumer.stop = AsyncMock()
        mock_consumer_cls.return_value = mock_consumer

        yield {"producer": mock_producer, "consumer": mock_consumer}


# ---------------------------------------------------------------------------
# Redis mock
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_redis():
    """Patch redis.asyncio to return an in-memory store."""
    store: dict = {}

    mock_redis_client = AsyncMock()
    mock_redis_client.ping = AsyncMock(return_value=True)
    mock_redis_client.set = AsyncMock(side_effect=lambda k, v, **kw: store.update({k: v}))
    mock_redis_client.get = AsyncMock(side_effect=lambda k: store.get(k))
    mock_redis_client.delete = AsyncMock(
        side_effect=lambda *keys: sum(1 for k in keys if store.pop(k, None) is not None)
    )
    mock_redis_client.sadd = AsyncMock(
        side_effect=lambda k, *v: store.setdefault(k, set()).update(v)
    )
    mock_redis_client.srem = AsyncMock(
        side_effect=lambda k, *v: store.get(k, set()).discard(next(iter(v), None))
    )
    mock_redis_client.smembers = AsyncMock(
        side_effect=lambda k: store.get(k, set())
    )
    mock_redis_client.aclose = AsyncMock()

    with patch("redis.asyncio.from_url", return_value=mock_redis_client):
        yield mock_redis_client


# ---------------------------------------------------------------------------
# LiteLLM mock
# ---------------------------------------------------------------------------

_DEFAULT_LLM_JSON = json.dumps({
    "nodes": [
        {
            "label": "Alice",
            "type": "PERSON",
            "properties": {
                "description": "A test person",
                "aliases": [],
                "source_text": "Alice works at Acme.",
                "confidence": 0.95,
                "temporal": None,
            },
        },
        {
            "label": "Acme",
            "type": "ORGANIZATION",
            "properties": {
                "description": "A test organization",
                "aliases": [],
                "source_text": "Alice works at Acme.",
                "confidence": 0.95,
                "temporal": None,
            },
        },
    ],
    "edges": [
        {
            "source": "Alice",
            "target": "Acme",
            "relation": "WORKS_AT",
            "properties": {
                "description": "Alice is employed by Acme",
                "source_text": "Alice works at Acme.",
                "confidence": 0.95,
                "temporal": None,
            },
        }
    ],
})


def _make_litellm_response(content: str) -> MagicMock:
    choice = MagicMock()
    choice.message.content = content
    response = MagicMock()
    response.choices = [choice]
    return response


@pytest.fixture
def mock_litellm():
    """
    Patch litellm.acompletion so it returns a valid graph extraction JSON by default.
    Tests can override the return value via mock_litellm.return_value.
    """
    with patch("litellm.acompletion", new_callable=AsyncMock) as mock_acompletion:
        mock_acompletion.return_value = _make_litellm_response(_DEFAULT_LLM_JSON)
        yield mock_acompletion


# ---------------------------------------------------------------------------
# Sample graph payload
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_graph_payload() -> GraphPayload:
    """A GraphPayload with 3 nodes and 2 edges for use in tests."""
    n1 = Node(
        id=str(uuid.uuid4()),
        label="Alice",
        type="PERSON",
        properties={"description": "Software engineer", "confidence": 0.9},
        agent_id="test-agent",
        created_at=datetime.now(timezone.utc),
    )
    n2 = Node(
        id=str(uuid.uuid4()),
        label="Acme Corp",
        type="ORGANIZATION",
        properties={"description": "Technology company", "confidence": 0.95},
        agent_id="test-agent",
        created_at=datetime.now(timezone.utc),
    )
    n3 = Node(
        id=str(uuid.uuid4()),
        label="San Francisco",
        type="LOCATION",
        properties={"description": "City in California", "confidence": 1.0},
        agent_id="test-agent",
        created_at=datetime.now(timezone.utc),
    )

    e1 = Edge(
        id=str(uuid.uuid4()),
        source_id=n1.id,
        target_id=n2.id,
        source=n1.label,
        target=n2.label,
        relation="WORKS_AT",
        properties={"confidence": 0.9},
        agent_id="test-agent",
        created_at=datetime.now(timezone.utc),
    )
    e2 = Edge(
        id=str(uuid.uuid4()),
        source_id=n2.id,
        target_id=n3.id,
        source=n2.label,
        target=n3.label,
        relation="LOCATED_IN",
        properties={"confidence": 1.0},
        agent_id="test-agent",
        created_at=datetime.now(timezone.utc),
    )

    return GraphPayload(nodes=[n1, n2, n3], edges=[e1, e2])


# ---------------------------------------------------------------------------
# Sample agent
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_agent() -> Agent:
    """A fully populated Agent object for use in tests."""
    return Agent(
        id=str(uuid.uuid4()),
        name="Test Agent",
        description="Agent used in unit tests",
        tenant_id="test-tenant",
        permissions=["read", "write", "query"],
        api_key="test-api-key-" + secrets_token(),
        created_at=datetime.now(timezone.utc),
    )


def secrets_token() -> str:
    """Generate a deterministic fake token for tests."""
    return "abcdef1234567890"
