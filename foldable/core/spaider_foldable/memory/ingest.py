"""Memory ingestion: user messages and document chunks become FACT nodes plus
extracted entities/relations, resolved against the existing graph, written to
SQLite, then checked for supersession. Mirrors the sync ingest path of
``backend/app/api/v1/ingest.py`` (steps 1, 1a, 2, 3, 4, 4b) with local
adapters.

Robustness rule: the FACT node is ALWAYS stored, even when the local model
fails to produce valid extraction JSON. Fulltext (and, when available,
embedding) retrieval then still finds the original sentence.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from app.models.schemas import Edge, GraphPayload, Node
from app.services.compressor import ExtractionError, SemanticCompressor
from app.services.entity_resolver import EntityResolver, build_embed_text

from .. import AGENT_ID
from ..db import Database
from .embedding_local import LocalEmbeddingService
from .store import LocalMemoryStore, Origin
from .supersession_local import resolve_supersession_local

logger = logging.getLogger(__name__)

_FACT_LABEL_PREVIEW_CHARS = 200
_MIN_TEXT_CHARS = 12


class _NullCache:
    """Replaces the Redis LLM cache inside the reused SemanticCompressor."""

    async def get(self, key):
        return None

    async def set(self, key, value, ex=None):
        return None


def attach_fact_node(payload: GraphPayload, text: str, source: Optional[str]) -> Node:
    """Same behaviour as ``app.api.v1.ingest._attach_fact_node`` (not importable
    without the Kafka/Postgres stack, so the 15 lines are mirrored here)."""
    label_preview = text.strip().replace("\n", " ")[:_FACT_LABEL_PREVIEW_CHARS]
    if len(text) > _FACT_LABEL_PREVIEW_CHARS:
        label_preview += "…"
    fact_source = source or "chat"
    fact_node = Node(label=f"fact: {label_preview}", type="FACT", description=text, properties={"source": fact_source})
    fact_edges = [
        Edge(source_id=fact_node.id, target_id=entity.id, relation="MENTIONS", properties={"source": fact_source})
        for entity in payload.nodes
    ]
    payload.nodes.append(fact_node)
    payload.edges.extend(fact_edges)
    return fact_node


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class MemoryIngestor:
    def __init__(self, db: Database, store: LocalMemoryStore, embeddings: LocalEmbeddingService):
        self.db = db
        self.store = store
        self.embeddings = embeddings
        self.compressor = SemanticCompressor()
        self.compressor._redis = _NullCache()  # adapter injection; see docs/portable/ARCHITECTURE.md
        self.resolver = EntityResolver(embedding_service=embeddings)
        self.extraction_enabled = True
        self.supersession_enabled = True
        self.last_error: Optional[str] = None

    async def ingest_text(
        self, text: str, origin: Origin, *, source: Optional[str] = None, llm_available: bool = True
    ) -> dict:
        text = (text or "").strip()
        if len(text) < _MIN_TEXT_CHARS:
            return {"skipped": True}
        payload = GraphPayload()
        extraction_error: Optional[str] = None
        if self.extraction_enabled and llm_available:
            try:
                payload = await self.compressor.extract(text, context={"source": source} if source else None)
            except ExtractionError as exc:
                extraction_error = f"extraction failed: {exc}"
                payload = GraphPayload()
            except Exception as exc:  # noqa: BLE001
                extraction_error = f"extraction error: {exc}"
                payload = GraphPayload()
        fact = attach_fact_node(payload, text, source or origin.kind)
        for node in payload.nodes:
            node.agent_id = AGENT_ID
        for edge in payload.edges:
            edge.agent_id = AGENT_ID
        try:
            resolved = await self.resolver.resolve(payload, AGENT_ID, self.store, caller_context="api")
        except Exception as exc:  # noqa: BLE001
            logger.warning("entity resolution failed (%s); writing unresolved payload", exc)
            resolved = payload
        result = self.store.write_graph(resolved, origin, embedding_model_id=self.embeddings.model_id)
        superseded = 0
        if self.supersession_enabled and llm_available and resolved.edges:
            superseded = await resolve_supersession_local(self.store, resolved, enabled=True)
        if extraction_error:
            self.last_error = extraction_error
        return {
            "fact_id": fact.id,
            "nodes": result["nodes_created"] + result["nodes_merged"],
            "edges": result["edges_created"],
            "superseded": superseded,
            "extraction_error": extraction_error,
        }

    async def reindex_missing(self, model_id: str, batch: int = 32) -> int:
        """Embed nodes that lack a vector for ``model_id`` (after an embedding
        model switch). Returns the number of vectors written."""
        nodes = self.store.nodes_missing_embeddings(model_id, limit=batch)
        if not nodes:
            return 0
        vectors = await self.embeddings.embed_batch([build_embed_text(n) for n in nodes])
        if not vectors:
            return 0
        self.store.store_embeddings([(n.id, v) for n, v in zip(nodes, vectors)], model_id)
        return len(vectors)


class IngestQueue:
    """Durable FIFO in SQLite; the worker drains it when the chat runtime is idle."""

    def __init__(self, db: Database):
        self.db = db

    def enqueue(self, kind: str, ref_id: str) -> str:
        qid = str(uuid.uuid4())
        now = utcnow()
        self.db.execute(
            "INSERT INTO ingest_queue(id, kind, ref_id, state, created_at, updated_at) VALUES (?,?,?,'pending',?,?)",
            (qid, kind, ref_id, now, now),
        )
        return qid

    def next_pending(self):
        return self.db.query_one(
            "SELECT * FROM ingest_queue WHERE state = 'pending' AND attempts < 3 ORDER BY created_at LIMIT 1"
        )

    def mark(self, qid: str, state: str, error: Optional[str] = None) -> None:
        self.db.execute(
            "UPDATE ingest_queue SET state = ?, error = ?, attempts = attempts + ?, updated_at = ? WHERE id = ?",
            (state, error, 1 if state in ("done", "failed") else 0, utcnow(), qid),
        )

    def requeue_running(self) -> None:
        """After a crash, items left 'running' go back to pending."""
        self.db.execute(
            "UPDATE ingest_queue SET state = 'pending', updated_at = ? WHERE state = 'running'", (utcnow(),)
        )

    def pending_count(self) -> int:
        return int(self.db.scalar("SELECT count(*) FROM ingest_queue WHERE state IN ('pending','running')") or 0)


class IngestWorker:
    """Single asyncio task. Waits while a chat answer streams (the llama-server
    has one slot), then processes queue items one by one."""

    def __init__(
        self,
        queue: IngestQueue,
        ingestor: MemoryIngestor,
        db: Database,
        *,
        is_llm_ready,
        chat_idle: asyncio.Event,
        on_document_progress=None,
    ):
        self.queue = queue
        self.ingestor = ingestor
        self.db = db
        self.is_llm_ready = is_llm_ready
        self.chat_idle = chat_idle
        self.on_document_progress = on_document_progress
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self.busy = False

    def start(self) -> None:
        self.queue.requeue_running()
        self._task = asyncio.create_task(self._run(), name="ingest-worker")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            item = await asyncio.to_thread(self.queue.next_pending)
            if item is None:
                await asyncio.sleep(1.0)
                continue
            await self.chat_idle.wait()
            llm_ready = bool(self.is_llm_ready())
            self.busy = True
            self.queue.mark(item["id"], "running")
            try:
                await self._process(item, llm_ready)
                self.queue.mark(item["id"], "done")
            except asyncio.CancelledError:
                self.queue.mark(item["id"], "pending")
                raise
            except Exception as exc:  # noqa: BLE001
                logger.exception("ingest item %s failed", item["id"])
                self.queue.mark(item["id"], "failed", str(exc)[:500])
            finally:
                self.busy = False

    async def _process(self, item, llm_ready: bool) -> None:
        if item["kind"] == "message":
            row = self.db.query_one(
                "SELECT m.id, m.content, m.conversation_id, c.title FROM messages m JOIN conversations c ON c.id = m.conversation_id "  # noqa: E501
                "WHERE m.id = ? AND m.role = 'user'",
                (item["ref_id"],),
            )
            if row is None:
                return
            origin = Origin("conversation", row["conversation_id"], row["title"])
            await self.ingestor.ingest_text(
                row["content"], origin, source=f"conversation:{row['conversation_id']}", llm_available=llm_ready
            )
        elif item["kind"] == "chunk":
            row = self.db.query_one(
                "SELECT ch.id, ch.text, ch.ord, d.id AS document_id, d.name FROM chunks ch JOIN documents d ON d.id = ch.document_id "  # noqa: E501
                "WHERE ch.id = ?",
                (item["ref_id"],),
            )
            if row is None:
                return
            origin = Origin("document", row["document_id"], row["name"])
            await self.ingestor.ingest_text(
                row["text"], origin, source=f"document:{row['name']}#{row['ord']}", llm_available=llm_ready
            )
            self.db.execute(
                "UPDATE documents SET progress_done = progress_done + 1 WHERE id = ?", (row["document_id"],)
            )
            doc = self.db.query_one(
                "SELECT progress_done, progress_total FROM documents WHERE id = ?", (row["document_id"],)
            )
            if doc and doc["progress_done"] >= doc["progress_total"]:
                self.db.execute("UPDATE documents SET status = 'indexed' WHERE id = ?", (row["document_id"],))
            if self.on_document_progress:
                self.on_document_progress(row["document_id"])


def fact_sources_json(facts: list[dict]) -> str:
    return json.dumps(facts, ensure_ascii=False)
