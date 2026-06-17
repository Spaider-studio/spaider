"""
Shared pytest fixtures for SpAIder backend tests.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.schemas import Agent, Edge, GraphPayload, Node


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
