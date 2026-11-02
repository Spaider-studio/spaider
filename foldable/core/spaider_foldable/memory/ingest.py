"""Memory ingestion: user messages and document chunks become FACT nodes plus
extracted entities/relations, resolved against the existing graph, written to
SQLite, then checked for supersession. Mirrors the sync ingest path of
``backend/app/api/v1/ingest.py`` (steps 1, 1a, 2, 3, 4, 4b) with local
adapters.

Consistency rule (v0.2): the chat path stores the verbatim user text as a
``raw`` FACT node *synchronously* (``LocalMemoryStore.write_raw_fact``), so it
is retrievable before this background pipeline runs. ``ingest_text`` then
enriches that same node (``fact_id``) instead of creating a second one.

Robustness rule: the FACT node is ALWAYS kept, even when the local model fails
to produce valid extraction JSON.

Scheduling: the worker runs items through ``LLMScheduler.run_background``; a
chat request preempts the running item (its LLM call is cancelled and the item
returns to the queue unchanged).
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from app.models.schemas import Edge, GraphPayload, Node
from app.services import compressor as _compressor_module
from app.services.compressor import ExtractionError, SemanticCompressor
from app.services.entity_resolver import EntityResolver, build_embed_text

from .. import AGENT_ID
from ..db import Database
from ..scheduler import LLMScheduler, Preempted
from .embedding_local import LocalEmbeddingService
from .store import LocalMemoryStore, Origin
from .corrections import is_question_only
from .supersession_local import resolve_supersession_local

logger = logging.getLogger(__name__)

_FACT_LABEL_PREVIEW_CHARS = 200
_MIN_TEXT_CHARS = 12
_GUARD_WORD_RE = re.compile(r"\w+", re.UNICODE)


EXTRACTION_MAX_TOKENS = 1500


def _install_extraction_budget() -> None:
    """The reused SemanticCompressor asks for up to 4000 completion tokens per
    chunk with a 120 s timeout. On a CPU-bound 4B model that budget cannot be
    produced in time, so every chunk times out three times (observed in the
    baseline: 6 minutes of wasted work per message). Cap the budget at the
    adapter boundary; the compressor module keeps its own name binding, so
    the supersession judge is unaffected."""
    original = getattr(_compressor_module, "acompletion_with_retry", None)
    if original is None or getattr(original, "_spaider_capped", False):
        return

    async def capped(*args, **kwargs):
        if kwargs.get("max_tokens", 0) and kwargs["max_tokens"] > EXTRACTION_MAX_TOKENS:
            kwargs["max_tokens"] = EXTRACTION_MAX_TOKENS
        return await original(*args, **kwargs)

    capped._spaider_capped = True  # type: ignore[attr-defined]
    _compressor_module.acompletion_with_retry = capped


class _NullCache:
    """Replaces the Redis LLM cache inside the reused SemanticCompressor."""

    async def get(self, key):
        return None

    async def set(self, key, value, ex=None):
        return None


def attach_fact_node(payload: GraphPayload, text: str, source: Optional[str], fact_id: Optional[str] = None) -> Node:
    """Same behaviour as ``app.api.v1.ingest._attach_fact_node`` (not importable
    without the Kafka/Postgres stack, so the 15 lines are mirrored here). With
    ``fact_id`` the existing raw node is reused."""
    label_preview = text.strip().replace("\n", " ")[:_FACT_LABEL_PREVIEW_CHARS]
    if len(text) > _FACT_LABEL_PREVIEW_CHARS:
        label_preview += "…"
    fact_source = source or "chat"
    fact_node = Node(label=f"fact: {label_preview}", type="FACT", description=text, properties={"source": fact_source})
    if fact_id:
        fact_node.id = fact_id
    fact_edges = [
        Edge(source_id=fact_node.id, target_id=entity.id, relation="MENTIONS", properties={"source": fact_source})
        for entity in payload.nodes
    ]
    payload.nodes.append(fact_node)
    payload.edges.extend(fact_edges)
    return fact_node


def _words(text: str) -> set[str]:
    return {w.lower() for w in _GUARD_WORD_RE.findall(text or "") if len(w) >= 3}


def guard_against_prompt_leakage(payload: GraphPayload, text: str) -> GraphPayload:
    """Small local models sometimes copy the few-shot example of the extraction
    prompt ("Electric vehicle manufacturer", "founded in 2003") instead of the
    input. A node or edge whose ``source_text`` shares fewer than half of its
    words with the ingested text is treated as unsupported: its description and
    source text are dropped and its confidence is lowered so retrieval shows it
    as "unsicher". Entities and relations themselves are kept."""
    input_words = _words(text)
    if not input_words:
        return payload
    for node in payload.nodes:
        props = node.properties or {}
        src = props.get("source_text") or ""
        words = _words(src)
        if words and len(words & input_words) / len(words) < 0.5:
            props.pop("source_text", None)
            props.pop("description", None)
            props["confidence"] = min(float(props.get("confidence", 1.0) or 1.0), 0.4)
            props["unsupported_extraction"] = True
            node.description = None
            node.properties = props
    for edge in payload.edges:
        props = edge.properties or {}
        src = props.get("source_text") or props.get("description") or ""
        words = _words(src)
        if words and len(words & input_words) / len(words) < 0.5:
            props["confidence"] = min(float(props.get("confidence", 1.0) or 1.0), 0.4)
            props["unsupported_extraction"] = True
            edge.properties = props
    return payload


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class MemoryIngestor:
    def __init__(self, db: Database, store: LocalMemoryStore, embeddings: LocalEmbeddingService):
        self.db = db
        self.store = store
        self.embeddings = embeddings
        _install_extraction_budget()
        self.compressor = SemanticCompressor()
        self.compressor._redis = _NullCache()  # adapter injection; see docs/portable/ARCHITECTURE.md
        self.resolver = EntityResolver(embedding_service=embeddings)
        self.extraction_enabled = True
        self.supersession_enabled = True
        self.last_error: Optional[str] = None

    async def ingest_text(
        self,
        text: str,
        origin: Origin,
        *,
        source: Optional[str] = None,
        llm_available: bool = True,
        fact_id: Optional[str] = None,
    ) -> dict:
        text = (text or "").strip()
        if len(text) < _MIN_TEXT_CHARS:
            return {"skipped": True}
        payload = GraphPayload()
        extraction_error: Optional[str] = None
        if self.extraction_enabled and llm_available:
            try:
                payload = await self.compressor.extract(text, context={"source": source} if source else None)
            except asyncio.CancelledError:
                raise
            except ExtractionError as exc:
                extraction_error = f"extraction failed: {exc}"
                payload = GraphPayload()
            except Exception as exc:  # noqa: BLE001
                extraction_error = f"extraction error: {exc}"
                payload = GraphPayload()
        payload = guard_against_prompt_leakage(payload, text)
        question_only = origin.kind == "conversation" and is_question_only(text)
        fact = None if question_only else attach_fact_node(payload, text, source or origin.kind, fact_id=fact_id)
        for node in payload.nodes:
            node.agent_id = AGENT_ID
        for edge in payload.edges:
            edge.agent_id = AGENT_ID
        try:
            resolved = await self.resolver.resolve(payload, AGENT_ID, self.store, caller_context="api")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("entity resolution failed (%s); writing unresolved payload", exc)
            resolved = payload
        result = self.store.write_graph(resolved, origin, embedding_model_id=self.embeddings.model_id)
        superseded = 0
        if fact is not None and origin.kind == "conversation":
            # deterministic floor, independent of the model (idempotent after the raw write)
            superseded += len(self.store.apply_explicit_correction(fact.id, text))
        if self.supersession_enabled and llm_available and resolved.edges:
            superseded += await resolve_supersession_local(self.store, resolved, enabled=True)
        if extraction_error:
            self.last_error = extraction_error
        return {
            "fact_id": fact.id if fact is not None else None,
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

    def enqueue(self, kind: str, ref_id: str, *, fact_id: Optional[str] = None) -> str:
        qid = str(uuid.uuid4())
        now = utcnow()
        self.db.execute(
            "INSERT INTO ingest_queue(id, kind, ref_id, state, created_at, updated_at, fact_id) "
            "VALUES (?,?,?,'pending',?,?,?)",
            (qid, kind, ref_id, now, now, fact_id),
        )
        return qid

    def next_pending(self):
        return self.db.query_one(
            "SELECT * FROM ingest_queue WHERE state = 'pending' AND attempts < 3 ORDER BY created_at LIMIT 1"
        )

    def mark(self, qid: str, state: str, error: Optional[str] = None, *, count_attempt: Optional[bool] = None) -> None:
        if count_attempt is None:
            count_attempt = state in ("done", "failed")
        self.db.execute(
            "UPDATE ingest_queue SET state = ?, error = ?, attempts = attempts + ?, updated_at = ? WHERE id = ?",
            (state, error, 1 if count_attempt else 0, utcnow(), qid),
        )

    def requeue_running(self) -> None:
        """After a crash, items left 'running' go back to pending."""
        self.db.execute(
            "UPDATE ingest_queue SET state = 'pending', updated_at = ? WHERE state = 'running'", (utcnow(),)
        )

    def pending_count(self) -> int:
        return int(self.db.scalar("SELECT count(*) FROM ingest_queue WHERE state IN ('pending','running')") or 0)

    def pending_for_conversation(self, conversation_id: str) -> int:
        return int(
            self.db.scalar(
                "SELECT count(*) FROM ingest_queue q JOIN messages m ON m.id = q.ref_id "
                "WHERE m.conversation_id = ? AND q.state IN ('pending','running')",
                (conversation_id,),
            )
            or 0
        )


class IngestWorker:
    """Single asyncio task draining the queue through the scheduler. A chat
    request preempts the running item; the item goes back to 'pending' without
    counting as an attempt."""

    def __init__(
        self,
        queue: IngestQueue,
        ingestor: MemoryIngestor,
        db: Database,
        *,
        is_llm_ready,
        scheduler: LLMScheduler,
        on_event=None,
        on_document_progress=None,
        llm_state=None,
    ):
        self.queue = queue
        self.ingestor = ingestor
        self.db = db
        self.is_llm_ready = is_llm_ready
        self.llm_state = llm_state  # callable() -> stopped|starting|ready|error (optional)
        self.scheduler = scheduler
        self.on_event = on_event  # callable(kind:str, payload:dict)
        self.on_document_progress = on_document_progress
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self.busy = False
        self.current: Optional[dict] = None

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

    def _emit(self, kind: str, payload: dict) -> None:
        if self.on_event:
            try:
                self.on_event(kind, payload)
            except Exception:  # noqa: BLE001
                pass

    async def _run(self) -> None:
        while not self._stop.is_set():
            item = await asyncio.to_thread(self.queue.next_pending)
            if item is None:
                await asyncio.sleep(1.0)
                continue
            item = dict(item)
            if self._llm_loading():
                # a chat model is configured and still loading: do not degrade the
                # item to a no-LLM ingest, wait for the runtime instead
                await asyncio.sleep(1.0)
                continue
            llm_ready = bool(self.is_llm_ready())
            conv_id = self._conversation_of(item)
            self.busy = True
            self.current = {"kind": item["kind"], "id": item["id"], "conversation_id": conv_id}
            self.queue.mark(item["id"], "running")
            self._emit(
                "postprocess_running",
                {"conversation_id": conv_id, "kind": item["kind"], "pending": self.queue.pending_count()},
            )
            try:
                await self.scheduler.run_background(item["kind"], self._process(item, llm_ready))
                self.queue.mark(item["id"], "done")
                self._emit(
                    "postprocess_done",
                    {"conversation_id": conv_id, "kind": item["kind"], "pending": self.queue.pending_count()},
                )
            except Preempted:
                logger.info("ingest item %s preempted by chat; requeued", item["id"])
                self.queue.mark(item["id"], "pending", count_attempt=False)
            except asyncio.CancelledError:
                self.queue.mark(item["id"], "pending", count_attempt=False)
                raise
            except Exception as exc:  # noqa: BLE001
                logger.exception("ingest item %s failed", item["id"])
                self.queue.mark(item["id"], "failed", str(exc)[:500])
                self._emit(
                    "postprocess_failed",
                    {
                        "conversation_id": conv_id,
                        "kind": item["kind"],
                        "error": str(exc)[:200],
                        "pending": self.queue.pending_count(),
                    },
                )
            finally:
                self.busy = False
                self.current = None

    def _llm_loading(self) -> bool:
        if self.llm_state is None:
            return False
        try:
            return self.llm_state() == "starting"
        except Exception:  # noqa: BLE001
            return False

    def _conversation_of(self, item: dict) -> Optional[str]:
        if item["kind"] == "message":
            row = self.db.query_one("SELECT conversation_id FROM messages WHERE id = ?", (item["ref_id"],))
            return row["conversation_id"] if row else None
        return None

    async def _process(self, item: dict, llm_ready: bool) -> None:
        if item["kind"] == "message":
            row = self.db.query_one(
                "SELECT m.id, m.content, m.conversation_id, c.title FROM messages m "
                "JOIN conversations c ON c.id = m.conversation_id WHERE m.id = ? AND m.role = 'user'",
                (item["ref_id"],),
            )
            if row is None:
                return
            origin = Origin("conversation", row["conversation_id"], row["title"])
            await self.ingestor.ingest_text(
                row["content"],
                origin,
                source=f"conversation:{row['conversation_id']}",
                llm_available=llm_ready,
                fact_id=item.get("fact_id"),
            )
        elif item["kind"] == "chunk":
            row = self.db.query_one(
                "SELECT ch.id, ch.text, ch.ord, d.id AS document_id, d.name FROM chunks ch "
                "JOIN documents d ON d.id = ch.document_id WHERE ch.id = ?",
                (item["ref_id"],),
            )
            if row is None:
                return
            origin = Origin("document", row["document_id"], row["name"])
            await self.ingestor.ingest_text(
                row["text"],
                origin,
                source=f"document:{row['name']}#{row['ord']}",
                llm_available=llm_ready,
                fact_id=item.get("fact_id"),
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
