"""
The one path knowledge takes into the graph.

Text:    episode -> extraction -> FACT node -> stamp KB -> resolve
Payload (image, BYOV):                         stamp KB -> resolve
Both then commit: write -> mark episode -> supersession -> cache generation.

Routes, the Kafka consumer, the in-process job queue and MCP all come through
here, so a step added once applies everywhere. The episode is written before
any LLM call: if extraction fails or the provider is down the raw observation
is still stored, with status ``new``, and the sleep cycle can pick it up later.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from app.core.jobs import get_job_queue, spawn
from app.core.kb_lock import kb_write_lock
from app.core.kv import bump_cache_generation
from app.models.schemas import Edge, GraphPayload, Node
from app.services.episode_store import EpisodeInput, EpisodeStore
from app.services.supersession import resolve_supersession

logger = logging.getLogger(__name__)

Progress = Callable[[str], Awaitable[None]]

INGEST_JOB = "ingest_text"
_FACT_LABEL_PREVIEW_CHARS = 200


@dataclass
class Prepared:
    """Ready to write: resolved and stamped with its KB."""

    agent_id: str
    payload: GraphPayload
    episode_id: Optional[str] = None
    clearance: int = 1


@dataclass
class IngestOutcome:
    payload: GraphPayload
    result: object


def attach_fact_node(payload: GraphPayload, text: str, source: Optional[str]) -> None:
    """Keep the verbatim text as a FACT node that MENTIONS every extracted entity.

    Attached even when extraction found nothing. Terse facts often yield an
    empty payload, and dropping them would lose the fact for good.
    """
    if not text or not text.strip():
        return
    label_preview = text.strip().replace("\n", " ")[:_FACT_LABEL_PREVIEW_CHARS]
    if len(text) > _FACT_LABEL_PREVIEW_CHARS:
        label_preview += "…"
    fact_source = source or "ingest_text_sync"
    fact_node = Node(
        label=f"fact: {label_preview}",
        type="FACT",
        description=text,
        properties={"source": fact_source},
    )
    fact_edges = [
        Edge(source_id=fact_node.id, target_id=entity.id, relation="MENTIONS", properties={"source": fact_source})
        for entity in payload.nodes
    ]
    payload.nodes.append(fact_node)
    payload.edges.extend(fact_edges)


def _trace_summary(event: str, data: dict) -> str:
    summaries = {
        "ingest_queued": "Ingest request was accepted and placed on the queue for background extraction.",
        "ingest_received": "Ingest pipeline started: text will be extracted into entities and relationships.",
        "graph_mutation": "Extracted entities were merged into the knowledge graph using KB-scoped upserts.",
        "ingest_failed": "Ingest pipeline failed; see metadata/error fields for root cause.",
    }
    if event.lower() == "ingest_completed":
        nodes, edges = data.get("nodes_total"), data.get("edges_total")
        return f"Ingest pipeline finished successfully (nodes={nodes}, edges={edges})."
    return summaries.get(event.lower(), "Replay audit event captured for deterministic workflow reconstruction.")


def fire_replay_event(
    workflow_id: str,
    agent_id: str,
    event_type: str,
    payload: dict,
    graph_state_hash: Optional[str] = None,
) -> None:
    """Record a replay event in the background. Replay is never allowed to fail an ingest."""

    async def _emit() -> None:
        try:
            from app.api.v1.replay import get_replay_service

            service = await get_replay_service()
            if service is None:
                return
            body = dict(payload or {})
            if not str(body.get("trace_summary", "")).strip():
                body["trace_summary"] = _trace_summary(event_type, body)
            await service.record_event(
                workflow_id=workflow_id,
                agent_id=agent_id,
                event_type=event_type,
                payload=body,
                graph_state_hash=graph_state_hash,
            )
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001
            logger.debug("Replay event skipped (%s): %s", event_type, exc)

    try:
        spawn(_emit())
    except RuntimeError:
        pass  # no running loop


async def _bump(agent_id: str) -> None:
    try:
        await bump_cache_generation(agent_id)
    except Exception as exc:  # noqa: BLE001 — a stale cache entry expires on its own
        logger.warning("Cache generation not bumped for %s: %s", agent_id, type(exc).__name__)


def _episode_key(agent_id: str, text: str, source: Optional[str]) -> str:
    return hashlib.sha256(f"{agent_id}\0{source or ''}\0{text}".encode()).hexdigest()


class IngestPipeline:
    def __init__(self, graph, compressor=None, resolver=None) -> None:
        self._graph = graph
        self._compressor = compressor
        self._resolver = resolver

    # ── Preparation ───────────────────────────────────────────────────────

    async def _record_episode(self, agent_id: str, text: str, source: Optional[str]) -> Optional[str]:
        episode = EpisodeInput(
            content=text,
            kind="ingest",
            source=source,
            idempotency_key=_episode_key(agent_id, text, source),
        )
        try:
            rows = await EpisodeStore(self._graph._driver).write_batch(agent_id, [episode])
            return rows[0]["id"]
        except Exception as exc:  # noqa: BLE001
            # The graph write still carries the fact, so this must not stop ingest.
            logger.warning("Episode not recorded for %s: %s", agent_id, type(exc).__name__)
            return None

    async def prepare_text(
        self,
        agent_id: str,
        text: str,
        *,
        source: Optional[str] = None,
        context: Optional[dict] = None,
        caller_context: str = "api",
        on_progress: Optional[Progress] = None,
        episode_id: Optional[str] = None,
        clearance: int = 1,
    ) -> Prepared:
        """``episode_id`` is set when sleep extracts an episode that already exists."""
        payload, episode_id = await self._extract(agent_id, text, source, context, on_progress, episode_id)
        prepared = await self.prepare_payload(agent_id, payload, caller_context=caller_context, episode_id=episode_id)
        prepared.clearance = clearance
        return prepared

    async def _extract(
        self,
        agent_id: str,
        text: str,
        source: Optional[str],
        context: Optional[dict],
        on_progress: Optional[Progress],
        episode_id: Optional[str] = None,
    ) -> tuple[GraphPayload, Optional[str]]:
        """Everything before the write lock: the LLM call must not block other writers."""
        if episode_id is None:
            episode_id = await self._record_episode(agent_id, text, source)
        payload = await self._compressor.extract(
            text=text, context=context if context is not None else ({"source": source} if source else None)
        )
        if on_progress:
            await on_progress(f"Extracted {len(payload.nodes)} entities — resolving duplicates…")
        attach_fact_node(payload, text, source)
        return payload, episode_id

    async def prepare_payload(
        self,
        agent_id: str,
        payload: GraphPayload,
        *,
        caller_context: str = "api",
        episode_id: Optional[str] = None,
    ) -> Prepared:
        for node in payload.nodes:
            node.agent_id = agent_id
        for edge in payload.edges:
            edge.agent_id = agent_id
        resolved = await self._resolver.resolve(payload, agent_id, self._graph, caller_context=caller_context)
        return Prepared(agent_id=agent_id, payload=resolved, episode_id=episode_id)

    # ── Commit ────────────────────────────────────────────────────────────

    async def commit(self, prepared: Prepared, *, workflow_id: Optional[str] = None):
        result = await self._graph.write_graph(prepared.payload, prepared.agent_id)
        await self._finish(prepared, result, workflow_id)
        return result

    async def commit_many(self, items: list[Prepared]):
        """Write several in one transaction; all land or none do."""
        result = await self._graph.write_graph_batch([(item.payload, item.agent_id) for item in items])
        for item in items:
            await self._finish(item, None, None)
        return result

    async def _mark_episode(self, episode_id: Optional[str]) -> None:
        if episode_id is None:
            return
        try:
            async with self._graph._driver.session() as session:
                await session.run(
                    "MATCH (e:Episode {id: $id}) SET e.status = 'extracted', e.extracted_at = $now",
                    id=episode_id,
                    now=datetime.now(timezone.utc).isoformat(),
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Episode %s not marked extracted: %s", episode_id, type(exc).__name__)

    async def _supersede_then_bump(self, prepared: Prepared) -> None:
        try:
            await resolve_supersession(self._graph._driver, prepared.agent_id, prepared.payload)
        finally:
            # A superseded fact must also stop being served from the cache.
            await _bump(prepared.agent_id)

    async def _raise_clearance(self, prepared: Prepared) -> None:
        """Knowledge drawn from a sensitive episode is at least as sensitive."""
        if prepared.clearance <= 1:
            return
        async with self._graph._driver.session() as session:
            await session.run(
                """
                UNWIND $ids AS node_id
                MATCH (n:SpaiderNode {id: node_id, agent_id: $kb})
                WHERE coalesce(n.clearance_level, 1) < $clearance
                SET n.clearance_level = $clearance
                """,
                ids=[node.id for node in prepared.payload.nodes],
                kb=prepared.agent_id,
                clearance=prepared.clearance,
            )

    async def _finish(self, prepared: Prepared, result, workflow_id: Optional[str]) -> None:
        await self._raise_clearance(prepared)
        await self._mark_episode(prepared.episode_id)
        await _bump(prepared.agent_id)
        # Supersession is consolidation, not perception: ingest latency excludes it.
        spawn(self._supersede_then_bump(prepared), name=f"supersede:{prepared.agent_id}")
        if workflow_id and result is not None:
            digest = hashlib.sha256(json.dumps(sorted(n.id for n in prepared.payload.nodes)).encode()).hexdigest()[:16]
            fire_replay_event(
                workflow_id,
                prepared.agent_id,
                "graph_mutation",
                {
                    "nodes_created": result.nodes_created,
                    "nodes_merged": result.nodes_merged,
                    "edges_created": result.edges_created,
                    "edges_merged": result.edges_merged,
                },
                graph_state_hash=digest,
            )

    # ── Whole paths ───────────────────────────────────────────────────────

    async def ingest_text(
        self,
        agent_id: str,
        text: str,
        *,
        source: Optional[str] = None,
        workflow_id: Optional[str] = None,
        on_progress: Optional[Progress] = None,
    ) -> IngestOutcome:
        payload, episode_id = await self._extract(agent_id, text, source, None, on_progress)
        async with kb_write_lock(agent_id):
            prepared = await self.prepare_payload(agent_id, payload, episode_id=episode_id)
            if on_progress:
                await on_progress(f"Writing {len(prepared.payload.nodes)} nodes to graph…")
            result = await self.commit(prepared, workflow_id=workflow_id)
        return IngestOutcome(payload=prepared.payload, result=result)

    async def ingest_payload(
        self,
        agent_id: str,
        payload: GraphPayload,
        *,
        workflow_id: Optional[str] = None,
    ) -> IngestOutcome:
        async with kb_write_lock(agent_id):
            prepared = await self.prepare_payload(agent_id, payload)
            result = await self.commit(prepared, workflow_id=workflow_id)
        return IngestOutcome(payload=prepared.payload, result=result)


async def _run_ingest_job(job: dict) -> None:
    from app.services.compressor import SemanticCompressor
    from app.services.entity_resolver import EntityResolver
    from app.services.graph_service import get_graph_service

    pipeline = IngestPipeline(get_graph_service(), SemanticCompressor(), EntityResolver())
    await pipeline.ingest_text(
        job["agent_id"], job["text"], source=job.get("source"), workflow_id=job.get("workflow_id")
    )


async def enqueue_text(agent_id: str, text: str, source: Optional[str]) -> str:
    """Background ingest without Kafka. Raises ``QueueFull`` at capacity."""
    queue = get_job_queue()
    queue.register(INGEST_JOB, _run_ingest_job)
    queue.start()
    job_id = await queue.submit(INGEST_JOB, {"agent_id": agent_id, "text": text, "source": source}, agent_id=agent_id)
    return job_id
