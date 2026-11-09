"""
The sleep cycle: what a knowledge base does when nobody is asking it anything.

Ten passes run in a fixed order under a per-KB lease held in Neo4j, so only one
worker sleeps a KB at a time. A heartbeat renews the lease for the whole run;
long passes check it between items and stop if another worker has taken over.
A pass that fails is recorded and the rest still run. Reports hold counts, never
content.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Awaitable, Callable, Optional

import numpy as np

from app.config import settings
from app.core.hashing import hash_embed
from app.core.jobs import spawn
from app.core.kv import bump_cache_generation
from app.lib.consolidation import _fuse_agent_duplicates
from app.lib.litellm_retry import acompletion_with_retry, track_tokens
from app.services import recall_ranker
from app.services.supersession import resolve_supersession

logger = logging.getLogger(__name__)

PASSES = (
    "extract", "contradictions", "fusion", "decay", "archive",
    "prune", "lessons", "skills", "compaction", "ranker",
)
PROTECTED_TYPES = ["FACT", "IMAGE", "LESSON", "SKILL"]
_MAX_EXTRACT_ATTEMPTS = 3
_BACKOFF_BASE_SECONDS = 300
_OUTAGE_MARKERS = (
    "connection", "timeout", "timed out", "rate limit", "ratelimit", "unavailable", "overloaded",
    "authentication", "api key", "quota", "502", "503", "504", "529", "401", "429",
)


class SleepBusy(RuntimeError):
    """Another worker holds this KB's lease."""


class LeaseLost(RuntimeError):
    """The lease expired or was taken over mid-run."""


def is_outage(exc: BaseException) -> bool:
    """True if the failure says nothing about the episode itself.

    A provider outage must not use up an episode's attempts, or a bad night
    would mark good observations as permanently failed.
    """
    text = " ".join([type(exc).__name__, str(exc), str(getattr(exc, "last_error", "") or "")]).lower()
    return any(marker in text for marker in _OUTAGE_MARKERS)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stable_id(prefix: str, *parts: str) -> str:
    return f"{prefix}-" + hashlib.sha256("\0".join(parts).encode()).hexdigest()[:24]


async def default_complete(prompt: str) -> str:
    kwargs: dict = {
        "model": settings.litellm_fast_model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
    }
    if settings.llm_base_url:
        kwargs["api_base"] = settings.llm_base_url
    if settings.llm_api_key:
        kwargs["api_key"] = settings.llm_api_key
    response = await acompletion_with_retry(**kwargs)
    return (response.choices[0].message.content or "").strip()


class _Lease:
    def __init__(self, driver, agent_id: str) -> None:
        self._driver = driver
        self._agent_id = agent_id
        self._owner = str(uuid.uuid4())
        self._ttl = settings.sleep_lease_ttl_seconds
        self._heartbeat: Optional[asyncio.Task] = None
        self._lost = False

    def _expiry(self) -> str:
        return (_now() + timedelta(seconds=self._ttl)).isoformat()

    async def acquire(self) -> bool:
        async def _claim(tx) -> bool:
            # Writing first takes the row lock, so two claims run one at a time.
            await tx.run(
                "MERGE (l:SleepLease {agent_id: $kb}) SET l.touched_at = $now",
                kb=self._agent_id,
                now=_now().isoformat(),
            )
            result = await tx.run(
                """
                MATCH (l:SleepLease {agent_id: $kb})
                WHERE l.owner IS NULL OR l.expires_at <= $now
                SET l.owner = $owner, l.expires_at = $expiry
                RETURN count(l) AS claimed
                """,
                kb=self._agent_id,
                now=_now().isoformat(),
                owner=self._owner,
                expiry=self._expiry(),
            )
            return (await result.single())["claimed"] == 1

        async with self._driver.session() as session:
            claimed = await session.execute_write(_claim)
        if claimed:
            self._heartbeat = spawn(self._beat(), name=f"sleep-lease:{self._agent_id}")
        return claimed

    async def _renew(self) -> bool:
        async with self._driver.session() as session:
            record = await (
                await session.run(
                    """
                    MATCH (l:SleepLease {agent_id: $kb, owner: $owner})
                    SET l.expires_at = $expiry
                    RETURN count(l) AS renewed
                    """,
                    kb=self._agent_id,
                    owner=self._owner,
                    expiry=self._expiry(),
                )
            ).single()
        return record["renewed"] == 1

    async def _beat(self) -> None:
        deadline = time.monotonic() + self._ttl
        while True:
            await asyncio.sleep(self._ttl / 3)
            try:
                if not await self._renew():
                    self._lost = True
                    return
                deadline = time.monotonic() + self._ttl
            except Exception as exc:  # noqa: BLE001 — a blip is survivable until the lease runs out
                logger.warning("Sleep lease renewal failed: %s", type(exc).__name__)
                if time.monotonic() > deadline:
                    self._lost = True
                    return

    def ensure_held(self) -> None:
        if self._lost:
            raise LeaseLost(self._agent_id)

    async def release(self) -> None:
        if self._heartbeat is not None:
            self._heartbeat.cancel()
            await asyncio.gather(self._heartbeat, return_exceptions=True)
        async with self._driver.session() as session:
            await session.run(
                "MATCH (l:SleepLease {agent_id: $kb, owner: $owner}) REMOVE l.owner, l.expires_at",
                kb=self._agent_id,
                owner=self._owner,
            )


class SleepOrchestrator:
    def __init__(
        self,
        graph,
        *,
        pipeline=None,
        embed: Optional[Callable[[str], Awaitable[list[float]]]] = None,
        complete: Optional[Callable[[str], Awaitable[str]]] = None,
    ) -> None:
        self._graph = graph
        self._driver = graph._driver
        self._pipeline = pipeline
        self._embed = embed
        self._complete = complete or default_complete
        self._lease: Optional[_Lease] = None

    def _get_pipeline(self):
        if self._pipeline is None:
            from app.services.compressor import SemanticCompressor
            from app.services.entity_resolver import EntityResolver
            from app.services.ingest_pipeline import IngestPipeline

            self._pipeline = IngestPipeline(self._graph, SemanticCompressor(), EntityResolver())
        return self._pipeline

    async def _embedding(self, text: str) -> list[float]:
        if self._embed is None:
            from app.services.embedding_service import EmbeddingService

            self._embed = EmbeddingService().embed
        return await self._embed(text)

    async def _rows(self, cypher: str, **params) -> list[dict]:
        async with self._driver.session() as session:
            result = await session.run(cypher, **params)
            return [dict(record) async for record in result]

    async def _run(self, cypher: str, **params) -> None:
        async with self._driver.session() as session:
            await session.run(cypher, **params)

    # ── Orchestration ─────────────────────────────────────────────────────

    async def run(self, agent_id: str, passes: tuple[str, ...] = PASSES) -> dict:
        """Run the passes in order. Raises ``SleepBusy`` if the KB is already sleeping."""
        lease = _Lease(self._driver, agent_id)
        if not await lease.acquire():
            raise SleepBusy(agent_id)
        self._lease = lease
        report: dict = {
            "id": str(uuid.uuid4()),
            "agent_id": agent_id,
            "started_at": _now().isoformat(),
            "status": "completed",
            "passes": {},
        }
        try:
            with track_tokens() as tokens:
                for name in passes:
                    lease.ensure_held()
                    report["passes"][name] = await self._run_pass(name, agent_id)
            report["tokens"] = dict(tokens)
            await self._run(
                "MATCH (a:SystemAgent {agent_id: $kb}) SET a.last_consolidated_at = $now",
                kb=agent_id,
                now=_now().isoformat(),
            )
        except LeaseLost:
            report["status"] = "lease_lost"
            logger.warning("Sleep lease lost for KB %s; stopping", agent_id)
        finally:
            await lease.release()
            report["finished_at"] = _now().isoformat()
            await self._save_report(report)
        await bump_cache_generation(agent_id)
        return report

    async def _run_pass(self, name: str, agent_id: str) -> dict:
        started = time.perf_counter()
        try:
            result = await getattr(self, f"_pass_{name}")(agent_id)
            status = "ok"
        except LeaseLost:
            raise
        except Exception as exc:  # noqa: BLE001 — one failing pass must not stop the rest
            logger.exception("Sleep pass %s failed for KB %s", name, agent_id)
            result, status = {"error": type(exc).__name__}, "failed"
        return {"status": status, "ms": round((time.perf_counter() - started) * 1000, 1), **result}

    async def _save_report(self, report: dict) -> None:
        await self._run(
            """
            CREATE (:SleepReport {
                id: $id, agent_id: $kb, started_at: $started, finished_at: $finished,
                status: $status, body: $body
            })
            """,
            id=report["id"],
            kb=report["agent_id"],
            started=report["started_at"],
            finished=report["finished_at"],
            status=report["status"],
            body=json.dumps(report),
        )

    # ── 1. Extract ────────────────────────────────────────────────────────

    async def _pass_extract(self, agent_id: str) -> dict:
        now = _now()
        rows = await self._rows(
            """
            MATCH (e:Episode {agent_id: $kb, status: 'new'})
            WHERE e.kind IN ['ingest', 'observation']
              AND coalesce(e.next_attempt_at, '') <= $now
              AND e.recorded_at <= $settled
            RETURN e.id AS id, e.content AS content, e.source AS source,
                   coalesce(e.extract_attempts, 0) AS attempts, coalesce(e.clearance_level, 1) AS clearance
            ORDER BY e.recorded_at LIMIT $limit
            """,
            kb=agent_id,
            now=now.isoformat(),
            # A synchronous ingest in progress also has status 'new'.
            settled=(now - timedelta(seconds=settings.sleep_extract_min_age_seconds)).isoformat(),
            limit=settings.sleep_extract_batch,
        )
        extracted = failed = 0
        outage = False
        pipeline = self._get_pipeline()
        for row in rows:
            self._lease.ensure_held()
            try:
                prepared = await pipeline.prepare_text(
                    agent_id,
                    row["content"],
                    source=row["source"],
                    episode_id=row["id"],
                    clearance=row["clearance"],
                    caller_context="sleep",
                )
                await pipeline.commit(prepared)
                extracted += 1
            except Exception as exc:  # noqa: BLE001
                if is_outage(exc):
                    outage = True
                    break
                failed += 1
                await self._record_failed_attempt(row["id"], row["attempts"] + 1)
        return {"candidates": len(rows), "extracted": extracted, "failed": failed, "outage": outage}

    async def _record_failed_attempt(self, episode_id: str, attempts: int) -> None:
        delay = _BACKOFF_BASE_SECONDS * (2 ** (attempts - 1))
        await self._run(
            """
            MATCH (e:Episode {id: $id})
            SET e.extract_attempts = $attempts, e.next_attempt_at = $next,
                e.status = CASE WHEN $attempts >= $max THEN 'failed' ELSE e.status END
            """,
            id=episode_id,
            attempts=attempts,
            next=(_now() + timedelta(seconds=delay)).isoformat(),
            max=_MAX_EXTRACT_ATTEMPTS,
        )

    # ── 2. Contradictions ─────────────────────────────────────────────────

    async def _pass_contradictions(self, agent_id: str) -> dict:
        """Catch edges the ingest-time pass missed, oldest first.

        Order matters: a newer fact has to see its predecessors already settled,
        or the later of two updates could be judged before the earlier one.
        """
        rows = await self._rows(
            """
            MATCH (a:SpaiderNode {agent_id: $kb})-[r:RELATION {agent_id: $kb}]->(b:SpaiderNode {agent_id: $kb})
            WHERE r.contradiction_checked IS NULL
              AND NOT coalesce(r.superseded, false)
              AND coalesce(r.relation, '') <> 'MENTIONS'
            RETURN r.id AS id, r.relation AS relation, a.id AS source, b.id AS target,
                   a.label AS subject, b.label AS object, coalesce(r.properties, '{}') AS properties
            ORDER BY coalesce(r.recorded_at, r.created_at) LIMIT $limit
            """,
            kb=agent_id,
            limit=settings.sleep_contradictions_batch,
        )
        superseded = 0
        for row in rows:
            self._lease.ensure_held()
            props = json.loads(row["properties"]) if isinstance(row["properties"], str) else row["properties"]
            props = {"source_text": f"{row['subject']} {row['relation']} {row['object']}", **(props or {})}
            edge = SimpleNamespace(
                id=row["id"],
                relation=row["relation"],
                source_id=row["source"],
                target_id=row["target"],
                properties=props,
            )
            superseded += await resolve_supersession(self._driver, agent_id, SimpleNamespace(edges=[edge]))
        return {"checked": len(rows), "superseded": superseded}

    # ── 3-6. Fusion, decay, archive, prune ────────────────────────────────

    async def _pass_fusion(self, agent_id: str) -> dict:
        return {"merged": await _fuse_agent_duplicates(self._driver, agent_id, np)}

    async def _pass_decay(self, agent_id: str) -> dict:
        records = await self._rows(
            """
            MATCH (n:SpaiderNode {agent_id: $kb})
            WHERE NOT n:SystemAgent
            WITH n, coalesce(
                n.last_activation,
                CASE WHEN toString(n.created_at) CONTAINS '-' THEN datetime(toString(n.created_at)) ELSE datetime() END
            ) AS activated
            WITH n, activated, duration.inSeconds(activated, datetime()).seconds / 3600.0 AS hours,
                 $base / (1.0 + $factor * sqrt(coalesce(n.retrieval_count, 0))) AS rate
            SET n.last_activation = activated, n.energy_level = exp(-rate * hours)
            RETURN count(n) AS nodes
            """,
            kb=agent_id,
            base=settings.cognitive_base_decay,
            factor=settings.cognitive_consolidation_factor,
        )
        return {"nodes": records[0]["nodes"], "edges": await decay_agent_edges(self._driver, agent_id)}

    async def _pass_archive(self, agent_id: str) -> dict:
        """Retire only what is long idle, barely used and barely connected.

        Energy alone is not enough. It halves in about a day, so it ranks recent
        memories well but would archive most of a healthy graph within days.
        A later write to an archived node makes it active again.
        """
        records = await self._rows(
            """
            MATCH (n:SpaiderNode {agent_id: $kb})
            WHERE NOT n:SystemAgent AND coalesce(n.status, 'active') = 'active'
              AND coalesce(n.energy_level, 1.0) < $threshold
              AND NOT coalesce(n.type, '') IN $protected
              AND n.last_activation < datetime($idle_since)
              AND coalesce(n.retrieval_count, 0) <= 1
              AND COUNT { (n)-[:RELATION]-() } <= 1
            SET n.status = 'archived', n.archived_at = $now
            RETURN count(n) AS archived
            """,
            kb=agent_id,
            threshold=settings.sleep_archive_energy,
            protected=PROTECTED_TYPES,
            now=_now().isoformat(),
            idle_since=(_now() - timedelta(days=settings.sleep_archive_after_days)).isoformat(),
        )
        return {"archived": records[0]["archived"]}

    async def _pass_prune(self, agent_id: str) -> dict:
        return {"pruned": await prune_agent_orphans(self._driver, agent_id)}

    # ── 7. Lessons ────────────────────────────────────────────────────────

    async def _pass_lessons(self, agent_id: str) -> dict:
        """One lesson per answer judged wrong. A verdict that flips retires the lesson."""
        retired = await self._rows(
            """
            MATCH (f:Feedback {agent_id: $kb, verdict: 1})
            WHERE f.lesson_node_id IS NOT NULL
            OPTIONAL MATCH (l:SpaiderNode {agent_id: $kb, id: f.lesson_node_id})
            DETACH DELETE l
            REMOVE f.lesson_node_id
            RETURN count(f) AS retired
            """,
            kb=agent_id,
        )
        rows = await self._rows(
            """
            MATCH (f:Feedback {agent_id: $kb, verdict: -1})
            WHERE NOT coalesce(f.lesson_done, false)
            MATCH (a:Episode {agent_id: $kb, idempotency_key: 'query:' + f.query_id + ':answer'})
            MATCH (q:Episode {agent_id: $kb, idempotency_key: 'query:' + f.query_id + ':query'})
            OPTIONAL MATCH (n:SpaiderNode {agent_id: $kb}) WHERE n.id IN f.node_ids
            WITH f, a, q, max(coalesce(n.clearance_level, 1)) AS node_clearance
            RETURN f.query_id AS query_id, q.content AS question, a.content AS answer, f.node_ids AS node_ids,
                   CASE WHEN coalesce(node_clearance, 1) > a.clearance_level
                        THEN node_clearance ELSE a.clearance_level END AS clearance
            ORDER BY f.updated_at LIMIT $limit
            """,
            kb=agent_id,
            limit=settings.sleep_lessons_batch,
        )
        created = 0
        for row in rows:
            self._lease.ensure_held()
            lesson = await self._complete(
                "SLEEP-LESSON. This answer was marked wrong by a user. In one sentence, state the "
                "general lesson for answering similar questions.\n\n"
                f"Question: {row['question']}\nAnswer: {row['answer']}"
            )
            if not lesson:
                continue
            lesson_id = _stable_id("lesson", agent_id, row["query_id"])
            await self._write_derived_node(
                agent_id,
                lesson_id,
                "LESSON",
                f"lesson: {lesson[:120]}",
                lesson,
                row["clearance"],
                await self._embedding(lesson),
            )
            await self._run(
                """
                MATCH (l:SpaiderNode {id: $lesson, agent_id: $kb})
                MATCH (n:SpaiderNode {agent_id: $kb}) WHERE n.id IN $node_ids AND n.id <> $lesson
                MERGE (l)-[r:RELATION {id: $lesson + ':' + n.id, agent_id: $kb}]->(n)
                ON CREATE SET r.relation = 'LEARNED_FROM', r.recorded_at = $now, r.valid_from = $now,
                              r.created_at = $now, r.contradiction_checked = true
                """,
                lesson=lesson_id,
                kb=agent_id,
                node_ids=row["node_ids"] or [],
                now=_now().isoformat(),
            )
            await self._run(
                """
                MATCH (f:Feedback {agent_id: $kb, query_id: $query_id})
                SET f.lesson_done = true, f.lesson_node_id = $lesson
                """,
                kb=agent_id,
                query_id=row["query_id"],
                lesson=lesson_id,
            )
            created += 1
        return {"created": created, "retired": retired[0]["retired"]}

    async def _write_derived_node(
        self,
        agent_id: str,
        node_id: str,
        node_type: str,
        label: str,
        description: str,
        clearance: int,
        embedding: Optional[list[float]],
    ) -> None:
        """Create or refresh a node built from other material.

        Clearance only ever rises: a summary is as sensitive as the most
        sensitive thing it has ever folded in.
        """
        now = _now().isoformat()
        await self._run(
            """
            MERGE (n:SpaiderNode {id: $id, agent_id: $kb})
            ON CREATE SET n.created_at = $now, n.energy_level = 1.0, n.retrieval_count = 0, n.status = 'active'
            SET n.type = $type, n.label = $label, n.description = $description, n.updated_at = $now,
                n.embedding = $embedding,
                n.clearance_level = CASE WHEN coalesce(n.clearance_level, 1) > $clearance
                                         THEN n.clearance_level ELSE $clearance END
            """,
            id=node_id,
            kb=agent_id,
            type=node_type,
            label=label,
            description=description,
            clearance=clearance,
            embedding=embedding,
            now=now,
        )

    # ── 8. Skills ─────────────────────────────────────────────────────────

    async def _pass_skills(self, agent_id: str) -> dict:
        """Turn a repeatedly successful kind of question into a reusable procedure.

        Skills carry no embedding and stay out of retrieval. A client asks for one
        when it wants it, so they cost nothing until used.
        """
        rows = await self._rows(
            """
            MATCH (f:Feedback {agent_id: $kb, verdict: 1})
            MATCH (q:Episode {agent_id: $kb, idempotency_key: 'query:' + f.query_id + ':query'})
            MATCH (a:Episode {agent_id: $kb, idempotency_key: 'query:' + f.query_id + ':answer'})
            RETURN f.query_id AS query_id, q.content AS question, q.hash_embedding AS embedding,
                   a.clearance_level AS clearance
            ORDER BY q.recorded_at LIMIT 500
            """,
            kb=agent_id,
        )
        created = 0
        for cluster in self._clusters(rows):
            self._lease.ensure_held()
            questions = "\n".join(f"- {row['question']}" for row in cluster[:20])
            procedure = await self._complete(
                "SLEEP-SKILL. These questions were all answered well. Write a short, general "
                f"step-by-step procedure for answering questions of this kind.\n\n{questions}"
            )
            if not procedure:
                continue
            await self._write_derived_node(
                agent_id,
                _stable_id("skill", agent_id, cluster[0]["query_id"]),
                "SKILL",
                f"skill: {cluster[0]['question'][:120]}",
                procedure,
                max(row["clearance"] for row in cluster),
                None,
            )
            created += 1
        return {"questions": len(rows), "created": created}

    @staticmethod
    def _clusters(rows: list[dict]) -> list[list[dict]]:
        clusters: list[list[dict]] = []
        centres: list[np.ndarray] = []
        for row in rows:
            vector = np.asarray(row["embedding"], dtype=np.float32)
            similarities = [float(vector @ centre) for centre in centres]
            if similarities and max(similarities) >= settings.sleep_skill_similarity:
                clusters[int(np.argmax(similarities))].append(row)
            else:
                clusters.append([row])
                centres.append(vector)
        return [cluster for cluster in clusters if len(cluster) >= settings.sleep_skill_min_support]

    # ── 9. Compaction ─────────────────────────────────────────────────────

    async def _pass_compaction(self, agent_id: str) -> dict:
        """Fold old episodes into one summary per session or day.

        Sources stay in the graph with status ``compacted`` and drop out of recall,
        so the summary can be traced back to what it came from.
        """
        rows = await self._rows(
            """
            MATCH (e:Episode {agent_id: $kb})
            WHERE (e.status = 'extracted' OR e.kind IN ['query', 'answer'])
              AND e.status <> 'compacted' AND e.kind <> 'compaction'
              AND e.occurred_at < $cutoff
            RETURN e.id AS id, e.content AS content, e.session_id AS session_id,
                   substring(e.occurred_at, 0, 10) AS day, e.clearance_level AS clearance
            ORDER BY e.occurred_at LIMIT 2000
            """,
            kb=agent_id,
            cutoff=(_now() - timedelta(days=settings.sleep_compact_after_days)).isoformat(),
        )
        groups: dict[str, list[dict]] = {}
        for row in rows:
            groups.setdefault(row["session_id"] or row["day"], []).append(row)

        summaries = folded = 0
        for group_key, members in groups.items():
            if len(members) < settings.sleep_compact_min_group:
                continue
            self._lease.ensure_held()
            summary_id = _stable_id("compaction", agent_id, group_key)
            existing = await self._rows(
                "MATCH (c:Episode {id: $id}) RETURN c.content AS content", id=summary_id
            )
            prior = f"Existing summary: {existing[0]['content']}\n\n" if existing else ""
            events = "\n".join(f"- {row['content'][:500]}" for row in members[:200])
            summary = await self._complete(
                "SLEEP-COMPACT. Summarize these events into a short paragraph that keeps names, "
                f"numbers, and decisions.\n\n{prior}{events}"
            )
            if not summary:
                continue
            now = _now().isoformat()
            await self._run(
                """
                MERGE (c:Episode {agent_id: $kb, idempotency_key: $id})
                ON CREATE SET c.id = $id, c.kind = 'compaction', c.status = 'summary', c.recorded_at = $now,
                              c.occurred_at = $occurred, c.metadata = '{}'
                SET c.content = $summary, c.hash_embedding = $embedding,
                    c.clearance_level = CASE WHEN coalesce(c.clearance_level, 1) > $clearance
                                             THEN c.clearance_level ELSE $clearance END
                WITH c
                UNWIND $members AS member_id
                MATCH (e:Episode {id: member_id})
                MERGE (c)-[:COMPACTS]->(e)
                SET e.status = 'compacted', e.compacted_at = $now
                """,
                kb=agent_id,
                id=summary_id,
                now=now,
                occurred=now,
                summary=summary,
                embedding=hash_embed(summary),
                clearance=max(row["clearance"] for row in members),
                members=[row["id"] for row in members],
            )
            summaries += 1
            folded += len(members)
        return {"summaries": summaries, "episodes_compacted": folded}

    # ── 10. Ranker ────────────────────────────────────────────────────────

    async def _pass_ranker(self, agent_id: str) -> dict:
        result = await recall_ranker.train(self._driver, agent_id)
        return {"trained": result.trained, "examples": result.examples, "accuracy": result.accuracy}


async def decay_agent_edges(driver, agent_id: str) -> int:
    """Multiplicatively decay one KB's edge weights, floored at 0.1."""
    rate = settings.edge_decay_rate
    if rate >= 1.0:
        return 0
    async with driver.session() as session:
        record = await (
            await session.run(
                """
                MATCH (:SpaiderNode {agent_id: $kb})-[r:RELATION]->(:SpaiderNode {agent_id: $kb})
                WITH r, coalesce(r.utility_weight, 1.0) AS w
                WITH r, w, CASE WHEN w * $rate < 0.1 THEN 0.1 ELSE w * $rate END AS new_w
                WHERE new_w <> w
                SET r.utility_weight = new_w
                RETURN count(r) AS decayed
                """,
                kb=agent_id,
                rate=rate,
            )
        ).single()
    return int(record["decayed"])


async def prune_agent_orphans(driver, agent_id: str) -> int:
    """Delete old nodes with no connections. Verbatim records are never pruned."""
    from app.lib.consolidation import _ORPHAN_MIN_AGE_DAYS

    cutoff = (_now() - timedelta(days=_ORPHAN_MIN_AGE_DAYS)).isoformat()
    async with driver.session() as session:
        record = await (
            await session.run(
                """
                MATCH (n:SpaiderNode {agent_id: $kb})
                WHERE NOT (n)--()
                  AND n.created_at IS NOT NULL AND toString(n.created_at) < $cutoff
                  AND NOT n:SystemAgent
                  AND NOT coalesce(n.type, '') IN $protected
                WITH collect(n) AS orphans
                FOREACH (n IN orphans | DETACH DELETE n)
                RETURN size(orphans) AS pruned
                """,
                kb=agent_id,
                cutoff=cutoff,
                protected=PROTECTED_TYPES,
            )
        ).single()
    return int(record["pruned"])


async def latest_report(driver, agent_id: str) -> Optional[dict]:
    async with driver.session() as session:
        record = await (
            await session.run(
                """
                MATCH (r:SleepReport {agent_id: $kb})
                RETURN r.body AS body ORDER BY r.started_at DESC LIMIT 1
                """,
                kb=agent_id,
            )
        ).single()
    return json.loads(record["body"]) if record else None
