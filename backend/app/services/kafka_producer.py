"""
Kafka Producer Service: Async producer using aiokafka with retry and backoff.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from aiokafka import AIOKafkaProducer
from aiokafka.errors import KafkaError

from app.config import settings
from app.logging_context import request_id_var

logger = logging.getLogger(__name__)

_REQUEST_ID_HEADER = "x-request-id"


class KafkaProducerService:
    """Async Kafka producer with retry logic and exponential backoff."""

    def __init__(self) -> None:
        self._producer: Optional[AIOKafkaProducer] = None
        self._max_retries = 3
        self._base_backoff = 0.5  # seconds

    async def start(self) -> None:
        """Start the Kafka producer."""
        self._producer = AIOKafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            key_serializer=lambda k: k.encode("utf-8") if k else None,
            acks="all",
            enable_idempotence=True,
            max_batch_size=16384,
            compression_type="gzip",
        )
        await self._producer.start()
        logger.info(
            "KafkaProducerService started. Bootstrap servers: %s",
            settings.kafka_bootstrap_servers,
        )

    async def stop(self) -> None:
        """Gracefully stop the Kafka producer."""
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None
            logger.info("KafkaProducerService stopped.")

    async def produce_ingest_event(
        self,
        text: str,
        agent_id: Optional[str] = None,
        source: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> str:
        """
        Publish a raw-text ingest event to the ingest topic.

        Returns:
            message_id: A unique identifier for the produced message.

        Raises:
            RuntimeError: If the producer is not started.
            KafkaError: If all retry attempts are exhausted.
        """
        if self._producer is None:
            raise RuntimeError("KafkaProducerService is not started. Call start() first.")

        message_id = str(uuid.uuid4())
        effective_agent_id = agent_id or settings.kafka_agent_namespace
        payload = {
            "message_id": message_id,
            "agent_id": effective_agent_id,
            "text": text,
            "source": source,
            "metadata": metadata or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "schema_version": "1.0",
        }

        # Propagate the current request_id so the consumer can bind it for
        # extraction/write logs — gives end-to-end trace across the async path.
        rid = request_id_var.get()
        headers = (
            [(_REQUEST_ID_HEADER, rid.encode("utf-8"))] if rid else None
        )

        last_exception: Optional[Exception] = None
        for attempt in range(1, self._max_retries + 1):
            try:
                await self._producer.send_and_wait(
                    topic=settings.kafka_topic_ingest,
                    value=payload,
                    key=effective_agent_id,
                    headers=headers,
                )
                logger.info(
                    "Produced ingest event | message_id=%s agent_id=%s topic=%s",
                    message_id,
                    effective_agent_id,
                    settings.kafka_topic_ingest,
                )
                return message_id
            except KafkaError as exc:
                last_exception = exc
                backoff = self._base_backoff * (2 ** (attempt - 1))
                logger.warning(
                    "Kafka produce attempt %d/%d failed: %s. Retrying in %.1fs.",
                    attempt,
                    self._max_retries,
                    exc,
                    backoff,
                )
                if attempt < self._max_retries:
                    await asyncio.sleep(backoff)

        logger.error(
            "All %d Kafka produce attempts exhausted for message_id=%s",
            self._max_retries,
            message_id,
        )
        raise last_exception  # type: ignore[misc]

    async def _produce_to_dlq(self, payload: dict) -> None:
        """Send a failed message to the Dead Letter Queue."""
        if self._producer is None:
            logger.error("Cannot send to DLQ: producer not started.")
            return
        try:
            await self._producer.send_and_wait(
                topic=settings.kafka_topic_dlq,
                value=payload,
                key=payload.get("agent_id"),
            )
            logger.info(
                "Sent message %s to DLQ topic %s",
                payload.get("message_id"),
                settings.kafka_topic_dlq,
            )
        except KafkaError as exc:
            logger.error("Failed to send to DLQ: %s", exc)
