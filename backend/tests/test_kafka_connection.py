"""The API must find Kafka again after it was down at startup, and say honestly where it stands.

After a Docker restart every container starts at once and Kafka needs half a
minute longer than the API. The API used to try exactly once, fail, and report
Kafka as unavailable for good.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiokafka.errors import KafkaConnectionError

from app.services import kafka_producer
from app.services.kafka_producer import KafkaProducerService, start_with_retry


@pytest.mark.asyncio
async def test_a_failed_start_closes_the_producer(monkeypatch):
    """Otherwise every failed attempt leaks an open client ("Unclosed AIOKafkaProducer")."""
    producer = MagicMock()
    producer.start = AsyncMock(side_effect=KafkaConnectionError("down"))
    producer.stop = AsyncMock()
    monkeypatch.setattr(kafka_producer, "AIOKafkaProducer", lambda **_: producer)
    service = KafkaProducerService()

    with pytest.raises(KafkaConnectionError):
        await service.start()

    producer.stop.assert_awaited_once()
    assert service._producer is None


class _Flaky:
    """Fails to start a number of times, then starts."""

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.attempts = 0

    async def start(self) -> None:
        self.attempts += 1
        if self.attempts <= self.failures:
            raise KafkaConnectionError("down")


@pytest.mark.asyncio
async def test_start_with_retry_keeps_trying_until_kafka_is_there():
    flaky = _Flaky(failures=3)
    waited: list[float] = []

    async def sleep(seconds: float) -> None:
        waited.append(seconds)

    service = await start_with_retry(factory=lambda: flaky, delays=(1, 2, 4), sleep=sleep)

    assert service is flaky
    assert flaky.attempts == 4
    assert waited == [1, 2, 4]


@pytest.mark.asyncio
async def test_start_with_retry_waits_no_longer_than_its_longest_delay():
    flaky = _Flaky(failures=6)
    waited: list[float] = []

    async def sleep(seconds: float) -> None:
        waited.append(seconds)

    await start_with_retry(factory=lambda: flaky, delays=(1, 2, 4), sleep=sleep)

    assert waited == [1, 2, 4, 4, 4, 4]


@pytest.mark.asyncio
async def test_reachable_is_false_when_the_metadata_request_fails():
    service = KafkaProducerService()
    service._producer = MagicMock()
    service._producer.client.fetch_all_metadata = AsyncMock(side_effect=KafkaConnectionError("down"))

    assert await service.is_reachable() is False


@pytest.mark.asyncio
async def test_reachable_is_true_when_the_broker_answers():
    service = KafkaProducerService()
    service._producer = MagicMock()
    service._producer.client.fetch_all_metadata = AsyncMock(return_value=object())

    assert await service.is_reachable() is True


@pytest.mark.asyncio
async def test_a_producer_that_never_started_is_not_reachable():
    assert await KafkaProducerService().is_reachable() is False


# --- what /health says ------------------------------------------------------


async def _health(monkeypatch, *, bootstrap: str, producer) -> dict:
    from httpx import ASGITransport, AsyncClient

    import app.main as main
    from app.config import settings

    monkeypatch.setattr(settings, "kafka_bootstrap_servers", bootstrap)
    monkeypatch.setattr(main, "_kafka_producer", producer)
    main.reset_health_cache()
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url="http://t") as client:
        return (await client.get("/health")).json()


@pytest.mark.asyncio
async def test_health_says_kafka_is_disabled_when_it_is_not_configured(monkeypatch):
    body = await _health(monkeypatch, bootstrap="", producer=None)

    assert body["services"]["kafka"] == "disabled"


@pytest.mark.asyncio
async def test_health_says_kafka_is_ok_when_the_broker_answers(monkeypatch):
    producer = MagicMock()
    producer.is_reachable = AsyncMock(return_value=True)

    body = await _health(monkeypatch, bootstrap="kafka:9092", producer=producer)

    assert body["services"]["kafka"] == "ok"


@pytest.mark.asyncio
async def test_health_says_kafka_is_unavailable_when_the_broker_stopped_answering(monkeypatch):
    """A producer object that exists is not proof that Kafka is up."""
    producer = MagicMock()
    producer.is_reachable = AsyncMock(return_value=False)

    body = await _health(monkeypatch, bootstrap="kafka:9092", producer=producer)

    assert body["services"]["kafka"] == "unavailable"


@pytest.mark.asyncio
async def test_health_says_kafka_is_unavailable_before_the_first_connection(monkeypatch):
    body = await _health(monkeypatch, bootstrap="kafka:9092", producer=None)

    assert body["services"]["kafka"] == "unavailable"


@pytest.mark.asyncio
async def test_health_does_not_ask_the_broker_on_every_request(monkeypatch):
    producer = MagicMock()
    producer.is_reachable = AsyncMock(return_value=True)

    await _health(monkeypatch, bootstrap="kafka:9092", producer=producer)
    from httpx import ASGITransport, AsyncClient

    import app.main as main

    async with AsyncClient(transport=ASGITransport(app=main.app), base_url="http://t") as client:
        for _ in range(5):
            await client.get("/health")

    assert producer.is_reachable.await_count == 1


@pytest.mark.asyncio
async def test_the_worker_waits_for_kafka_instead_of_crashing(monkeypatch):
    """A worker that exits when Kafka is not up yet restarts in a loop until it is."""
    import app.worker as worker

    class Reached(Exception):
        pass

    graph = MagicMock()
    graph.initialize = AsyncMock()
    monkeypatch.setattr(worker, "get_graph_service", lambda: graph)
    for name in ("EmbeddingService", "SemanticCompressor", "EntityResolver", "KafkaConsumerService"):
        monkeypatch.setattr(worker, name, MagicMock())
    waiting = AsyncMock(side_effect=Reached)
    monkeypatch.setattr(worker, "start_with_retry", waiting)

    with pytest.raises(Reached):
        await worker._run()

    waiting.assert_awaited_once()


@pytest.mark.asyncio
async def test_the_worker_initialises_the_graph_service_the_pipeline_uses(monkeypatch):
    """The ingest pipeline asks for the process-wide GraphService.

    The worker used to initialise a separate instance, so the one the pipeline
    got never learned that the vector index exists and silently skipped semantic
    duplicate matching for every message the worker processed.
    """
    import app.worker as worker
    from app.services import graph_service as graph_module

    class Reached(Exception):
        pass

    shared = MagicMock()
    shared.initialize = AsyncMock()
    monkeypatch.setattr(graph_module, "_shared_graph_service", shared)
    for name in ("EmbeddingService", "SemanticCompressor", "EntityResolver", "KafkaConsumerService"):
        monkeypatch.setattr(worker, name, MagicMock())
    monkeypatch.setattr(worker, "start_with_retry", AsyncMock(side_effect=Reached))

    with pytest.raises(Reached):
        await worker._run()

    shared.initialize.assert_awaited_once()
