"""
SpAIder Kafka Consumer + Connector Scheduler Worker Entry Point.

Run with:
    python -m app.worker

Two concurrent long-running loops are started via ``asyncio.gather``:

1. **Kafka consumer loop** (existing) — reads ``spaider.ingest.raw`` events,
   compresses, resolves entities, writes to Neo4j.

2. **Connector scheduler loop** (new) — polls Postgres every 5 minutes for
   due ``ConnectorConfig`` rows, streams ``ConnectorRecord`` objects through
   ``ConnectorRunner``, offloads parsing to a thread, and publishes enriched
   payloads back to ``spaider.ingest.raw`` for the consumer loop to pick up.

Both loops run on the same asyncio event loop.  A SIGTERM / SIGINT triggers
graceful shutdown of both: the consumer drains in-flight messages while the
scheduler waits for any active connector tasks to finish.
"""
from __future__ import annotations

import asyncio
import logging
import signal

from app.connectors import get_global_registry
from app.db.postgres import dispose_engine, init_db
from app.logging_config import configure_logging
from app.scheduler.connector_scheduler import run_connector_loop
from app.services.compressor import SemanticCompressor
from app.services.embedding_service import EmbeddingService
from app.services.entity_resolver import EntityResolver
from app.services.graph_service import GraphService
from app.services.kafka_consumer import KafkaConsumerService
from app.services.kafka_producer import KafkaProducerService

configure_logging()
logger = logging.getLogger("spaider.worker")


async def _run() -> None:
    """Initialise all services and run both loops concurrently."""

    # ── Service initialisation ────────────────────────────────────────────
    logger.info("Initialising SpAIder worker services...")

    embedding_service = EmbeddingService()

    graph_service = GraphService()
    await graph_service.initialize()

    compressor = SemanticCompressor()
    entity_resolver = EntityResolver(embedding_service=embedding_service)

    consumer = KafkaConsumerService()

    # Kafka producer — shared by the connector scheduler for publishing
    # parsed connector records back onto the ingest topic.
    producer = KafkaProducerService()
    await producer.start()
    logger.info("KafkaProducerService started.")

    # Bootstrap Postgres schema (CREATE TABLE IF NOT EXISTS — idempotent).
    # Safe to call on every worker start; Alembic owns migrations in prod.
    await init_db()

    # ── Graceful shutdown ─────────────────────────────────────────────────
    loop = asyncio.get_running_loop()
    scheduler_task: asyncio.Task | None = None

    async def _shutdown() -> None:
        logger.info("Initiating graceful shutdown...")

        # Stop the scheduler first — waits for active connector tasks to drain
        if scheduler_task is not None and not scheduler_task.done():
            # Import here to access the module-level scheduler stop helper

            # The task holds the scheduler instance; cancel it cleanly
            scheduler_task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(scheduler_task), timeout=30.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        # Stop the Kafka consumer (drains in-flight messages)
        await consumer.stop()

    def _handle_signal(sig: signal.Signals) -> None:
        logger.info("Received signal %s.", sig.name)
        loop.create_task(_shutdown())

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _handle_signal, sig)
        except NotImplementedError:
            # Windows: add_signal_handler not supported for all signals
            signal.signal(
                sig,
                lambda s, f: loop.call_soon_threadsafe(
                    _handle_signal, signal.Signals(s)
                ),
            )

    # ── Start both loops concurrently ─────────────────────────────────────
    logger.info("Starting Kafka consumer loop and connector scheduler loop...")

    scheduler_task = asyncio.create_task(
        run_connector_loop(
            registry=get_global_registry(),
            kafka_producer=producer,
        ),
        name="connector-scheduler",
    )

    try:
        # consumer.start() blocks until consumer.stop() is called.
        # The scheduler runs concurrently as a background task.
        await consumer.start(
            compressor=compressor,
            entity_resolver=entity_resolver,
            graph_service=graph_service,
        )
    finally:
        # Ensure the scheduler task is cancelled if the consumer exits
        if scheduler_task and not scheduler_task.done():
            scheduler_task.cancel()
            try:
                await scheduler_task
            except asyncio.CancelledError:
                pass

        await producer.stop()
        logger.info("KafkaProducerService stopped.")

        await graph_service.close()
        logger.info("GraphService connection closed.")

        await dispose_engine()
        logger.info("PostgreSQL engine disposed.")

        logger.info("SpAIder worker shut down cleanly.")


def main() -> None:
    """Entry point."""
    asyncio.run(_run())


if __name__ == "__main__":
    main()
