"""
Analytics Service: fire-and-forget ClickHouse event logging.

All inserts run in background tasks — zero latency added to API responses.
Tables use MergeTree with monthly partitions for fast time-series queries.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ClickHouse DDL — executed once on startup
_DDL = [
    """
    CREATE DATABASE IF NOT EXISTS spaider
    """,
    """
    CREATE TABLE IF NOT EXISTS spaider.ingest_events (
        timestamp   DateTime DEFAULT now(),
        agent_id    LowCardinality(String),
        text_length UInt32,
        chunk_count UInt8,
        nodes_created UInt16,
        nodes_merged  UInt16,
        edges_created UInt16,
        latency_ms  Float32
    ) ENGINE = MergeTree()
    PARTITION BY toYYYYMM(timestamp)
    ORDER BY (agent_id, timestamp)
    """,
    """
    CREATE TABLE IF NOT EXISTS spaider.query_events (
        timestamp         DateTime DEFAULT now(),
        agent_id          LowCardinality(String),
        question_length   UInt16,
        answer_length     UInt16,
        nodes_in_result   UInt16,
        edges_in_result   UInt16,
        cypher_used       UInt8,   -- 0/1 boolean
        latency_ms        Float32,
        -- Backend (server-side) LLM grounding cost: counts only, never text.
        prompt_tokens     UInt32 DEFAULT 0,
        completion_tokens UInt32 DEFAULT 0
    ) ENGINE = MergeTree()
    PARTITION BY toYYYYMM(timestamp)
    ORDER BY (agent_id, timestamp)
    """,
    """
    CREATE TABLE IF NOT EXISTS spaider.extraction_failed_events (
        timestamp    DateTime DEFAULT now(),
        agent_id     LowCardinality(String),
        source       LowCardinality(String), -- sync | stream | kafka
        text_length  UInt32,
        attempts     UInt8,
        last_error   String
    ) ENGINE = MergeTree()
    PARTITION BY toYYYYMM(timestamp)
    ORDER BY (agent_id, timestamp)
    """,
]

# Idempotent online schema migrations applied after _DDL on every startup.
# ADD COLUMN IF NOT EXISTS lets a table created by an older build pick up new
# columns without a manual migration step or data loss.
_MIGRATIONS = [
    "ALTER TABLE spaider.query_events "
    "ADD COLUMN IF NOT EXISTS prompt_tokens UInt32 DEFAULT 0",
    "ALTER TABLE spaider.query_events "
    "ADD COLUMN IF NOT EXISTS completion_tokens UInt32 DEFAULT 0",
]


class AnalyticsService:
    """
    Thin async wrapper around clickhouse-connect.
    All public methods are fire-and-forget — they schedule a background task
    and return immediately so the API response is never blocked.
    """

    def __init__(self) -> None:
        self._client = None
        self._ready = False

    async def initialize(self) -> None:
        """Connect to ClickHouse and create tables. Called once at startup."""
        try:
            import clickhouse_connect

            from app.config import settings

            self._client = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: clickhouse_connect.get_client(
                    host=settings.clickhouse_host,
                    port=settings.clickhouse_port,
                    username=settings.clickhouse_user,
                    password=settings.clickhouse_password,
                ),
            )
            for ddl in _DDL:
                await asyncio.get_event_loop().run_in_executor(
                    None, lambda q=ddl: self._client.command(q)
                )
            # Online migration: backfill the token columns onto query_events
            # tables created before backend token accounting existed. IF NOT
            # EXISTS makes this idempotent and safe on every startup.
            for mig in _MIGRATIONS:
                await asyncio.get_event_loop().run_in_executor(
                    None, lambda q=mig: self._client.command(q)
                )
            self._ready = True
            logger.info(
                "ClickHouse connected at %s:%s — analytics tables ready",
                settings.clickhouse_host,
                settings.clickhouse_port,
            )
        except Exception as exc:
            logger.warning("ClickHouse unavailable — analytics disabled: %s", exc)
            self._ready = False

    async def close(self) -> None:
        if self._client is not None:
            try:
                await asyncio.get_event_loop().run_in_executor(None, self._client.close)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Public fire-and-forget helpers
    # ------------------------------------------------------------------

    def record_ingest(
        self,
        *,
        agent_id: str,
        text_length: int,
        chunk_count: int,
        nodes_created: int,
        nodes_merged: int,
        edges_created: int,
        latency_ms: float,
    ) -> None:
        """Schedule an ingest event insert in the background."""
        if not self._ready:
            return
        asyncio.create_task(
            self._insert_ingest(
                agent_id=agent_id,
                text_length=text_length,
                chunk_count=chunk_count,
                nodes_created=nodes_created,
                nodes_merged=nodes_merged,
                edges_created=edges_created,
                latency_ms=latency_ms,
            )
        )

    def record_query(
        self,
        *,
        agent_id: str,
        question_length: int,
        answer_length: int,
        nodes_in_result: int,
        edges_in_result: int,
        cypher_used: bool,
        latency_ms: float,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> None:
        """Schedule a query event insert in the background.

        ``prompt_tokens`` / ``completion_tokens`` are the backend LLM grounding
        cost (counts only). They default to 0 so existing callers and cache
        hits (no backend spend) record cleanly.
        """
        if not self._ready:
            return
        asyncio.create_task(
            self._insert_query(
                agent_id=agent_id,
                question_length=question_length,
                answer_length=answer_length,
                nodes_in_result=nodes_in_result,
                edges_in_result=edges_in_result,
                cypher_used=cypher_used,
                latency_ms=latency_ms,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
        )

    def record_extraction_failed(
        self,
        *,
        agent_id: str,
        source: str,
        text_length: int,
        attempts: int,
        last_error: str,
    ) -> None:
        """Schedule an extraction-failure event insert in the background."""
        if not self._ready:
            return
        asyncio.create_task(
            self._insert_extraction_failed(
                agent_id=agent_id,
                source=source,
                text_length=text_length,
                attempts=attempts,
                last_error=last_error,
            )
        )

    # ------------------------------------------------------------------
    # Analytics queries
    # ------------------------------------------------------------------

    async def get_ingest_timeseries(
        self, agent_id: str, days: int = 7
    ) -> list[dict]:
        """Hourly ingest volume and latency for the last N days."""
        if not self._ready:
            return []
        sql = f"""
            SELECT
                toStartOfHour(timestamp)          AS hour,
                count()                           AS ingests,
                sum(nodes_created + nodes_merged) AS total_nodes,
                sum(edges_created)                AS total_edges,
                avg(latency_ms)                   AS avg_latency_ms
            FROM spaider.ingest_events
            WHERE agent_id = '{agent_id}'
              AND timestamp >= now() - INTERVAL {days} DAY
            GROUP BY hour
            ORDER BY hour
        """
        return await self._query(sql)

    async def get_query_timeseries(
        self, agent_id: str, days: int = 7
    ) -> list[dict]:
        """Hourly query volume and latency for the last N days."""
        if not self._ready:
            return []
        sql = f"""
            SELECT
                toStartOfHour(timestamp) AS hour,
                count()                  AS queries,
                avg(latency_ms)          AS avg_latency_ms,
                avg(nodes_in_result)     AS avg_nodes_returned,
                sum(cypher_used)         AS cypher_queries
            FROM spaider.query_events
            WHERE agent_id = '{agent_id}'
              AND timestamp >= now() - INTERVAL {days} DAY
            GROUP BY hour
            ORDER BY hour
        """
        return await self._query(sql)

    async def get_overview(self, agent_id: str) -> dict:
        """Aggregate overview stats for an agent."""
        if not self._ready:
            return {}

        ingest_sql = f"""
            SELECT
                count()            AS total_ingests,
                sum(nodes_created) AS total_nodes_created,
                sum(edges_created) AS total_edges_created,
                avg(latency_ms)    AS avg_ingest_latency_ms,
                max(latency_ms)    AS max_ingest_latency_ms
            FROM spaider.ingest_events
            WHERE agent_id = '{agent_id}'
        """
        query_sql = f"""
            SELECT
                count()          AS total_queries,
                avg(latency_ms)  AS avg_query_latency_ms,
                max(latency_ms)  AS max_query_latency_ms
            FROM spaider.query_events
            WHERE agent_id = '{agent_id}'
        """
        ingest_rows = await self._query(ingest_sql)
        query_rows = await self._query(query_sql)
        return {
            "ingest": ingest_rows[0] if ingest_rows else {},
            "query": query_rows[0] if query_rows else {},
        }

    async def get_top_agents(self, limit: int = 10) -> list[dict]:
        """Most active agents by ingest count."""
        if not self._ready:
            return []
        sql = f"""
            SELECT
                agent_id,
                count()            AS ingests,
                sum(nodes_created) AS nodes_created,
                avg(latency_ms)    AS avg_latency_ms
            FROM spaider.ingest_events
            GROUP BY agent_id
            ORDER BY ingests DESC
            LIMIT {limit}
        """
        return await self._query(sql)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _insert_ingest(self, **kwargs) -> None:
        try:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            data = [[
                now,
                kwargs["agent_id"],
                kwargs["text_length"],
                kwargs["chunk_count"],
                kwargs["nodes_created"],
                kwargs["nodes_merged"],
                kwargs["edges_created"],
                kwargs["latency_ms"],
            ]]
            cols = ["timestamp", "agent_id", "text_length", "chunk_count",
                    "nodes_created", "nodes_merged", "edges_created", "latency_ms"]
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._client.insert("spaider.ingest_events", data, column_names=cols),
            )
        except Exception as exc:
            logger.debug("ClickHouse ingest insert failed: %s", exc)

    async def _insert_query(self, **kwargs) -> None:
        try:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            data = [[
                now,
                kwargs["agent_id"],
                kwargs["question_length"],
                kwargs["answer_length"],
                kwargs["nodes_in_result"],
                kwargs["edges_in_result"],
                1 if kwargs["cypher_used"] else 0,
                kwargs["latency_ms"],
                int(kwargs.get("prompt_tokens", 0) or 0),
                int(kwargs.get("completion_tokens", 0) or 0),
            ]]
            cols = ["timestamp", "agent_id", "question_length", "answer_length",
                    "nodes_in_result", "edges_in_result", "cypher_used", "latency_ms",
                    "prompt_tokens", "completion_tokens"]
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._client.insert("spaider.query_events", data, column_names=cols),
            )
        except Exception as exc:
            logger.debug("ClickHouse query insert failed: %s", exc)

    async def _insert_extraction_failed(self, **kwargs) -> None:
        try:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            # ClickHouse String columns reject NULLs here; coerce to empty string.
            last_error = (kwargs.get("last_error") or "")[:1000]
            data = [[
                now,
                kwargs["agent_id"],
                kwargs["source"],
                kwargs["text_length"],
                kwargs["attempts"],
                last_error,
            ]]
            cols = ["timestamp", "agent_id", "source", "text_length", "attempts", "last_error"]
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._client.insert(
                    "spaider.extraction_failed_events", data, column_names=cols
                ),
            )
        except Exception as exc:
            logger.debug("ClickHouse extraction_failed insert failed: %s", exc)

    async def _query(self, sql: str) -> list[dict]:
        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._client.query(sql)
            )
            cols = result.column_names
            return [dict(zip(cols, row)) for row in result.result_rows]
        except Exception as exc:
            logger.warning("ClickHouse query failed: %s", exc)
            return []
