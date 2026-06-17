"""
Tests for KafkaConsumerService hardening.

Covers:
- Consumer constructor receives static group_instance_id + tuned timeouts
- _connect_with_backoff retries on transient KafkaError then succeeds
- _connect_with_backoff raises after _CONNECT_MAX_ATTEMPTS failures
- _send_to_dlq retries transient KafkaError then succeeds
- _send_to_dlq logs critical and returns (does not raise) after final failure
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiokafka.errors import KafkaError

from app.services.kafka_consumer import (
    KafkaConsumerService,
    _CONNECT_MAX_ATTEMPTS,
    _DLQ_SEND_MAX_ATTEMPTS,
    _HEARTBEAT_INTERVAL_MS,
    _MAX_POLL_INTERVAL_MS,
    _SESSION_TIMEOUT_MS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _patch_sleep():
    """Zero out asyncio.sleep inside kafka_consumer so backoff tests are fast."""
    return patch("app.services.kafka_consumer.asyncio.sleep", new=AsyncMock())


# ---------------------------------------------------------------------------
# Constructor wiring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consumer_constructed_with_static_membership_and_timeouts():
    """
    AIOKafkaConsumer must receive:
      - group_instance_id derived from HOSTNAME (static membership)
      - session/heartbeat/max_poll timeouts from the module constants
    """
    svc = KafkaConsumerService()

    with (
        patch("app.services.kafka_consumer.AIOKafkaConsumer") as consumer_cls,
        patch("app.services.kafka_consumer.AIOKafkaProducer") as producer_cls,
        patch.dict("os.environ", {"HOSTNAME": "worker-abc123"}, clear=False),
    ):
        consumer_cls.return_value = AsyncMock()
        producer_cls.return_value = AsyncMock()

        # We only care about construction — bail out before the poll loop.
        svc._micro_batch_loop = AsyncMock()  # type: ignore[method-assign]

        await svc.start(
            compressor=MagicMock(),
            entity_resolver=MagicMock(),
            graph_service=MagicMock(),
        )

        kwargs = consumer_cls.call_args.kwargs
        assert kwargs["group_instance_id"] == "worker-worker-abc123"
        assert kwargs["session_timeout_ms"] == _SESSION_TIMEOUT_MS
        assert kwargs["heartbeat_interval_ms"] == _HEARTBEAT_INTERVAL_MS
        assert kwargs["max_poll_interval_ms"] == _MAX_POLL_INTERVAL_MS
        assert kwargs["enable_auto_commit"] is False


@pytest.mark.asyncio
async def test_consumer_instance_id_falls_back_when_hostname_missing():
    """Without HOSTNAME (e.g. bare metal dev), instance_id must still be set."""
    svc = KafkaConsumerService()

    with (
        patch("app.services.kafka_consumer.AIOKafkaConsumer") as consumer_cls,
        patch("app.services.kafka_consumer.AIOKafkaProducer") as producer_cls,
        patch.dict("os.environ", {}, clear=True),
    ):
        consumer_cls.return_value = AsyncMock()
        producer_cls.return_value = AsyncMock()
        svc._micro_batch_loop = AsyncMock()  # type: ignore[method-assign]

        await svc.start(
            compressor=MagicMock(),
            entity_resolver=MagicMock(),
            graph_service=MagicMock(),
        )

        assert consumer_cls.call_args.kwargs["group_instance_id"] == "worker-local"


# ---------------------------------------------------------------------------
# _connect_with_backoff
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connect_retries_then_succeeds():
    """Two transient KafkaErrors, then success on the third attempt."""
    svc = KafkaConsumerService()
    svc._consumer = AsyncMock()
    svc._dlq_producer = AsyncMock()

    svc._consumer.start = AsyncMock(
        side_effect=[KafkaError("boom"), KafkaError("boom again"), None]
    )

    with _patch_sleep() as slept:
        await svc._connect_with_backoff()

    assert svc._consumer.start.await_count == 3
    svc._dlq_producer.start.assert_awaited_once()
    # Backoff between attempts: 1s, 2s (no sleep after the successful start)
    assert slept.await_count == 2


@pytest.mark.asyncio
async def test_connect_raises_after_max_attempts():
    """Persistent KafkaError must raise once the attempt budget is exhausted."""
    svc = KafkaConsumerService()
    svc._consumer = AsyncMock()
    svc._dlq_producer = AsyncMock()

    svc._consumer.start = AsyncMock(side_effect=KafkaError("broker down"))

    with _patch_sleep():
        with pytest.raises(KafkaError):
            await svc._connect_with_backoff()

    assert svc._consumer.start.await_count == _CONNECT_MAX_ATTEMPTS
    # DLQ producer was never reached because consumer.start kept failing
    svc._dlq_producer.start.assert_not_awaited()


@pytest.mark.asyncio
async def test_connect_succeeds_first_try_without_sleep():
    """Happy path: no retries, no backoff sleeps."""
    svc = KafkaConsumerService()
    svc._consumer = AsyncMock()
    svc._dlq_producer = AsyncMock()

    with _patch_sleep() as slept:
        await svc._connect_with_backoff()

    svc._consumer.start.assert_awaited_once()
    svc._dlq_producer.start.assert_awaited_once()
    slept.assert_not_awaited()


# ---------------------------------------------------------------------------
# _send_to_dlq
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dlq_send_retries_then_succeeds():
    """One transient KafkaError, then success on the second attempt."""
    svc = KafkaConsumerService()
    svc._dlq_producer = AsyncMock()
    svc._dlq_producer.send_and_wait = AsyncMock(
        side_effect=[KafkaError("topic not available"), None]
    )

    with _patch_sleep():
        await svc._send_to_dlq(
            original_payload={"message_id": "m1", "agent_id": "a1"},
            error="boom",
            attempt=3,
        )

    assert svc._dlq_producer.send_and_wait.await_count == 2


@pytest.mark.asyncio
async def test_dlq_send_gives_up_after_max_attempts_without_raising():
    """
    DLQ send must NOT raise after exhausting retries.  The caller has already
    decided the source message is dead; propagating here would crash the
    consumer mid-batch and leak the record.
    """
    svc = KafkaConsumerService()
    svc._dlq_producer = AsyncMock()
    svc._dlq_producer.send_and_wait = AsyncMock(side_effect=KafkaError("down"))

    with _patch_sleep():
        # No exception, no re-raise
        await svc._send_to_dlq(
            original_payload={"message_id": "m2", "agent_id": "a2"},
            error="boom",
            attempt=3,
        )

    assert svc._dlq_producer.send_and_wait.await_count == _DLQ_SEND_MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_dlq_send_noop_when_producer_missing():
    """Uninitialised DLQ producer must not raise — just log and return."""
    svc = KafkaConsumerService()
    svc._dlq_producer = None

    # Should not raise
    await svc._send_to_dlq(
        original_payload={"message_id": "m3"},
        error="boom",
        attempt=1,
    )


@pytest.mark.asyncio
async def test_dlq_headers_include_error_metadata():
    """DLQ record must carry error metadata as Kafka headers for forensics."""
    svc = KafkaConsumerService()
    svc._dlq_producer = AsyncMock()
    svc._dlq_producer.send_and_wait = AsyncMock(return_value=None)

    await svc._send_to_dlq(
        original_payload={"message_id": "m4", "agent_id": "a4"},
        error="bad json",
        attempt=3,
        reason="extraction_failed",
    )

    kwargs = svc._dlq_producer.send_and_wait.call_args.kwargs
    header_keys = {k for k, _ in kwargs["headers"]}
    assert {
        "dlq-error",
        "dlq-reason",
        "dlq-source-topic",
        "dlq-attempts",
        "dlq-timestamp",
        "dlq-message-id",
        "dlq-agent-id",
    }.issubset(header_keys)

    reason_header = dict(kwargs["headers"])["dlq-reason"]
    assert reason_header == b"extraction_failed"
