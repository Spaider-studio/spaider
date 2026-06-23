"""
Query Service: fast GraphRAG-first pipeline with Redis caching.

V1 Pipeline (single LLM call):
  1. Check Redis cache → return instantly on hit
  2. Embed question → vector search top-K nodes
  3. Expand each seed node 1 hop for context
  4. Single LLM call: question + graph context → answer
  5. Cache result in Redis (5 min TTL)

V2 Pipeline (Unified Cognitive Graph — Synaptic Plasticity aware):
  Same as V1 but retrieval uses a fused Synaptic Score:

    Synaptic_Score = avg(U) · ( E · exp(−λ · Δt) )

  where U = r.utility_weight (Hebbian reinforcement),
        E = n.energy_level   (LTP baseline),
        Δt = hours since n.last_activation,
        λ  = 0.05 / (1 + 0.1·√retrieval_count)   (consolidation-aware decay).

  • Managed Forgetting    — nodes whose synaptic_score < 0.3 are invisible to RAG
  • Strength Priorisation — facts sorted descending by synaptic_score
  • Annotated context     — [Strength: X.X] tags in LLM prompt for high-score facts

Cypher path still available via query_cypher() for raw/advanced use.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import time
from typing import AsyncIterator, Optional

import numpy as np
from fastapi import HTTPException, status

from app.config import settings
from app.lib.litellm_retry import acompletion_with_retry, track_tokens
from app.models.schemas import Edge, GraphPayload, Node, VerifierResult
from app.services.cognitive_engine import CognitiveGraphService
from app.services.embedding_service import EmbeddingService
from app.services.graph_service import GraphService

logger = logging.getLogger(__name__)

_WRITE_KEYWORDS = re.compile(
    r"\b(CREATE|MERGE|DELETE|SET|REMOVE|DETACH|DROP)\b",
    re.IGNORECASE,
)

_CACHE_TTL = 300              # 5 minutes
_V2_FORGET_THRESHOLD = 0.3    # edges below this are invisible in V2 mode
_DEFAULT_TOP_K = 8            # default seed nodes for vector/text search
_MMR_FETCH_N = 40             # candidate pool size fetched from Neo4j for MMR
# Diverse subset returned after MMR filtering. Retrieval depth is the single
# most score-sensitive knob in published memory-system ablations (gains up to
# ~k=30, regression beyond ~50 as noise crowds the context); 15 balances
# recall against the context-token budget.
_MMR_SELECT_K = 15

# ---------------------------------------------------------------------------
# Fort Knox Patch — Phase 1, Proposals 2 + 3 (SWARM_SECURITY_MANIFEST.md §2).
#
# Two feature flags, both default OFF to preserve byte-identical behavior
# with pre-patch main. Operators flip each independently per the manifest's
# Implementation Sequencing table.
#
#   CLEARANCE_DEFAULT_DENY=true  → unlabeled nodes default to clearance 5
#                                  (admin-only). When OFF, the legacy
#                                  default of 1 (public) is preserved.
#                                  Cypher binds the value as a parameter
#                                  ($clearance_default) so the predicate
#                                  stays plan-cacheable across both modes.
#
#   CLEARANCE_FAIL_CLOSED=true   → Redis lookup failures in
#                                  _get_agent_clearance raise HTTP 503
#                                  instead of silently returning the
#                                  default clearance. Closes the
#                                  fail-open path documented in §1.2 of
#                                  the manifest.
# ---------------------------------------------------------------------------
_CLEARANCE_DEFAULT_DENY: bool = (
    os.environ.get("CLEARANCE_DEFAULT_DENY", "false").lower() == "true"
)
_CLEARANCE_FAIL_CLOSED: bool = (
    os.environ.get("CLEARANCE_FAIL_CLOSED", "false").lower() == "true"
)

# Numeric value bound as the $clearance_default Cypher parameter on every
# retrieval. 1 = public (legacy permissive default); 5 = admin-only
# (default-deny posture). The Cypher uses coalesce(n.clearance_level,
# $clearance_default) so the same query plan serves both modes.
_CLEARANCE_DEFAULT_VALUE: int = 5 if _CLEARANCE_DEFAULT_DENY else 1

# B1 — Pillar 5 token-bleed mitigation. Caps the raw `source_text` payload
# that ``_format_context_records`` injects into the LLM prompt when a node
# lacks a `description`. Pre-patch, the full source_text was emitted
# verbatim — for FACT-type nodes carrying entire paragraphs,
# this drove the 20-record swarm context up to ~10K tokens unbounded.
# Default 300 characters → roughly 75-100 tokens per record worst case;
# overridable per deployment via the MAX_SOURCE_TEXT_CHARS env var.
MAX_SOURCE_TEXT_CHARS: int = int(os.environ.get("MAX_SOURCE_TEXT_CHARS", "300"))

# Property keys that are internal bookkeeping (synaptic/ACT-R state, provenance,
# fields already surfaced explicitly) and must NOT be echoed into the LLM
# context. Everything else in a node's `properties` is treated as an
# answer-bearing attribute (e.g. an extracted date, metric, or amount) and is
# surfaced — pre-patch these lived only in `properties` and were invisible to
# the model, so it answered "not available" even when retrieval hit the node.
# Cap on the number of extra scalar properties surfaced per context record —
# enough for the answer-bearing attributes (dates, amounts, rates) without
# letting a property-heavy node flood the prompt (token economics).
_CONTEXT_MAX_EXTRA_PROPS: int = 6

# Questions that expect a short factual span (entity / date / number / phrase)
# rather than an explanation — only these get the concise-answer extraction.
_FACTOID_RE = re.compile(
    r"^\s*(who|whom|whose|when|where|which|what|how (much|many|long|old|fast|big)|"
    r"name the|list the)\b"
)

# Node cap for the synthesis context built from the accumulated subgraph.
# Sized to the hybrid seed budget (up to 2×top_k seeds) plus 1-hop expansion
# survivors: a cap below the seed count silently drops retrieval hits before
# the model ever sees them.
_CONTEXT_MAX_NODES: int = 40

_CONTEXT_SKIP_PROPS: frozenset[str] = frozenset({
    "description", "source_text", "temporal", "aliases", "confidence", "source",
    "energy_level", "retrieval_count", "last_activation", "utility_weight",
    "synaptic_score", "avg_weight", "embedding",
})

# Maximum number of sub-questions emitted by ``_decompose_question``. The
# router-LLM prompt asks for ≤ 5; this cap is the second line of defence
# against runaway output (a buggy LLM emitting 50 sub-questions would
# multiply embedding + vector-search cost proportionally).
_DECOMPOSITION_MAX_SUBQUERIES = 5

# Router prompt for ``_decompose_question``. Splits a multi-entity / multi-
# hop question into focused atomic sub-questions before retrieval. Returns
# the original question unchanged for already-atomic questions so we don't
# pay an LLM call for nothing on the easy cases.
_DECOMPOSITION_PROMPT_TEMPLATE = """\
You are a retrieval planner. Given a user question, write the smallest
list of focused sub-questions (1 to 5) such that answering each from a
knowledge graph independently and combining the results would resolve
the original.

- For an atomic question about one entity or fact, return the original
  question unchanged as a single-element array.
- For comparison questions (\"Is A older than B?\"), emit one
  sub-question per entity.
- For bridge questions (\"What is the [attribute] of the thing
  associated with X?\"), emit a sub-question per hop.

Order from broadest to most specific.

Output ONLY a JSON array of strings: [\"...\", \"...\"]
No preamble, no code fence, no commentary.

Question: {question}
"""


def _context_limit(top_k: Optional[int]) -> int:
    """
    Compute the downstream context LIMIT from a caller-supplied top_k.
    context_limit = ceil(top_k * 2.5) — mirrors the default relationship
    where top_k=8 → LIMIT 20.  When top_k is None the caller should use
    the original hardcoded value so existing behaviour is preserved exactly.
    """
    return math.ceil(top_k * 2.5) if top_k is not None else 20


def _keyword_ft_query(text: str, max_terms: int = 10) -> Optional[str]:
    """Build a Lucene OR-of-prefixes query from a natural-language question.

    Same extraction the swarm-context path uses: words of >= 3 chars, each
    with a trailing * for prefix matching. Returns None when nothing usable
    survives (e.g. an all-stopword question).
    """
    words = [w.lower() for w in re.split(r"\W+", text) if len(w) >= 3][:max_terms]
    return " OR ".join(f"{w}*" for w in words) if words else None


def _rrf_fuse(ranked_lists: list[list["Node"]], k: int = 60) -> list["Node"]:
    """Reciprocal-rank fusion of multiple ranked node lists.

    score(node) = Σ 1 / (k + rank + 1) over every list it appears in, the
    standard hybrid-retrieval combiner: it needs no score normalisation
    between vector similarity and Lucene relevance, and nodes found by BOTH
    retrievers float to the top. k=60 is the literature default.
    """
    scores: dict[str, float] = {}
    by_id: dict[str, "Node"] = {}
    for lst in ranked_lists:
        for rank, node in enumerate(lst):
            scores[node.id] = scores.get(node.id, 0.0) + 1.0 / (k + rank + 1)
            by_id.setdefault(node.id, node)
    return [by_id[nid] for nid in sorted(scores, key=lambda i: scores[i], reverse=True)]


def _synaptic_score_py(utility: float, node: Optional["Node"]) -> float:
    """
    Python-side Synaptic Score for in-memory edge filtering.

    Mirrors the inline Cypher expression used in the V2 Cypher queries:
        score = U · (E · exp(−λ · Δt))
    where λ = 0.05 / (1 + 0.1·√retrieval_count).

    Falls back gracefully when node is None or properties are absent.
    """
    from datetime import datetime, timezone

    props: dict = (node.properties or {}) if node else {}
    energy: float = float(props.get("energy_level", 1.0))
    retrieval_count: float = float(props.get("retrieval_count", 1))

    last_activation = props.get("last_activation")
    delta_t: float = 0.0
    if last_activation:
        try:
            la = datetime.fromisoformat(str(last_activation))
            if la.tzinfo is None:
                la = la.replace(tzinfo=timezone.utc)
            delta_t = (datetime.now(timezone.utc) - la).total_seconds() / 3600.0
        except Exception:
            delta_t = 0.0

    lam = 0.05 / (1.0 + 0.1 * math.sqrt(max(retrieval_count, 1)))
    return utility * (energy * math.exp(-lam * delta_t))


def apply_mmr(
    candidate_nodes: list,
    embeddings: np.ndarray,
    base_scores: np.ndarray,
    k: int = 5,
    lambda_param: float = 0.5,
) -> list:
    """
    Maximal Marginal Relevance (MMR) post-retrieval diversity filter.

    Selects K nodes from a candidate pool that collectively maximise both
    relevance to the query and semantic diversity among the selected set.

    Algorithm
    ---------
    At each step, the unselected candidate with the highest MMR score is
    greedily picked:

        MMR_i = λ · Norm_Utility_i  −  (1−λ) · max_{j∈S} cos(e_i, e_j)

    where S is the already-selected set and cos(·,·) is cosine similarity.

    Parameters
    ----------
    candidate_nodes : list
        Ordered list of node objects / dicts from Neo4j (length N).
    embeddings : np.ndarray, shape (N, D)
        Embedding vectors for each candidate (need not be pre-normalised).
        Rows must align 1-to-1 with candidate_nodes.
    base_scores : np.ndarray, shape (N,)
        Raw retrieval scores (UCB, synaptic score, FTS score, …).
        Scale is arbitrary — Min-Max normalised internally.
    k : int
        Number of diverse nodes to return (clamped to len(candidate_nodes)).
    lambda_param : float
        Trade-off in [0, 1]. 1.0 → pure relevance. 0.0 → max diversity.

    Returns
    -------
    list
        The k most diverse + relevant nodes, in greedy selection order.
    """
    n = len(candidate_nodes)
    if n == 0:
        return []

    k = min(k, n)

    # ── Step 1: Min-Max normalise base_scores to [0, 1] ─────────────────────
    scores = base_scores.astype(np.float64)
    s_min, s_max = float(scores.min()), float(scores.max())
    if s_max - s_min < 1e-10:
        norm_scores = np.ones(n, dtype=np.float64)
    else:
        norm_scores = (scores - s_min) / (s_max - s_min)

    # ── Step 2: Build L2-normalised embedding matrix ─────────────────────────
    emb = embeddings.astype(np.float64)
    norms = np.linalg.norm(emb, axis=1, keepdims=True)
    norms = np.where(norms < 1e-10, 1.0, norms)
    emb_normed = emb / norms                   # (N, D)
    sim_matrix = emb_normed @ emb_normed.T     # (N, N) — full cosine sim

    # ── Step 3: Greedy MMR selection ─────────────────────────────────────────
    selected: list[int] = []
    remaining = set(range(n))

    for _ in range(k):
        if not remaining:
            break

        if not selected:
            # No selected set yet — bootstrap with highest-relevance node.
            best = max(remaining, key=lambda i: norm_scores[i])
        else:
            sel = np.array(selected, dtype=np.intp)
            rem = np.array(sorted(remaining), dtype=np.intp)
            # max similarity of each remaining node to any already-selected node
            max_sim = sim_matrix[np.ix_(rem, sel)].max(axis=1)  # (|rem|,)
            mmr = lambda_param * norm_scores[rem] - (1.0 - lambda_param) * max_sim
            best = rem[int(np.argmax(mmr))]

        selected.append(best)
        remaining.discard(best)

    return [candidate_nodes[i] for i in selected]


# ---------------------------------------------------------------------------
# Diplomat Protocol — Clearance Level constants
# ---------------------------------------------------------------------------
# Nodes without an explicit clearance_level property default to 1 (public).
# An agent with clearance_level N can retrieve all nodes where
# coalesce(n.clearance_level, 1) <= N.
#
#  Level  Role
#  -----  -----------
#    1    Public / Guest     (default for new agents and unlabelled nodes)
#    2    Internal
#    3    Confidential
#    4    Secret
#    5    Admin / Top-Secret
# ---------------------------------------------------------------------------
_DEFAULT_CLEARANCE = 1


# ---------------------------------------------------------------------------
# Response type
# ---------------------------------------------------------------------------

class QueryResult:
    def __init__(
        self,
        question: str,
        answer: str,
        subgraph: GraphPayload,
        cypher: Optional[str] = None,
        raw_records: Optional[list[dict]] = None,
        from_cache: bool = False,
        iterations_used: int = 1,
        re_query_happened: bool = False,
        confidence_score: float = 1.0,
        verifier_feedback: Optional[list[str]] = None,
        direct_answer: Optional[str] = None,
        token_usage: Optional[dict[str, int]] = None,
    ) -> None:
        self.question = question
        self.answer = answer
        self.subgraph = subgraph
        self.cypher = cypher
        self.raw_records = raw_records or []
        self.from_cache = from_cache
        self.iterations_used = iterations_used
        self.re_query_happened = re_query_happened
        self.confidence_score = confidence_score
        self.verifier_feedback = verifier_feedback
        self.direct_answer = direct_answer
        # Backend LLM token spend for this query (counts only, no text):
        # {"prompt_tokens": int, "completion_tokens": int}. None on a cache hit
        # (no backend LLM calls were made).
        self.token_usage = token_usage


# ---------------------------------------------------------------------------
# QueryService
# ---------------------------------------------------------------------------

class QueryService:
    def __init__(
        self,
        graph_service: GraphService,
        embedding_service: Optional[EmbeddingService] = None,
    ) -> None:
        self._graph = graph_service
        self._embedding = embedding_service or EmbeddingService()
        self._redis = None
        self._cognitive = CognitiveGraphService(driver=graph_service._driver)

    # ------------------------------------------------------------------
    # Diplomat Protocol — resolve agent clearance level
    # ------------------------------------------------------------------

    async def _get_agent_clearance(self, agent_id: str) -> int:
        """
        Return the Diplomat Protocol clearance level for *agent_id*.

        Reads from the same Redis key that agents.py persists to.

        Behavior depends on the ``CLEARANCE_FAIL_CLOSED`` env flag:

          * Flag OFF (default, legacy): on any Redis or parse failure,
            log a warning and return ``_DEFAULT_CLEARANCE`` (1). This is
            the fail-OPEN path — preserves pre-patch behavior so existing
            deployments do not 503 on transient Redis blips.

          * Flag ON: on any Redis or parse failure, raise
            ``HTTPException(503)`` so the request fails closed and does
            not silently degrade to public-tier access. Required for
            regulated-deployment posture (see SWARM_SECURITY_MANIFEST.md
            §1.2 / §2.3).

        Note on architectural layering: importing FastAPI's
        ``HTTPException`` in a service-layer module is mildly impure but
        is the explicit ask of the Fort Knox patch — the error must
        surface to the caller as 503 without an intermediate translation
        layer, which would require touching main.py (out of scope).
        """
        redis = await self._get_redis()
        if not redis:
            if _CLEARANCE_FAIL_CLOSED:
                logger.error(
                    "_get_agent_clearance: Redis unavailable | agent=%s | "
                    "raising 503 (CLEARANCE_FAIL_CLOSED=true)",
                    agent_id,
                )
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=(
                        "Service Unavailable: Clearance resolution timeout "
                        "(Redis access-control plane unreachable)."
                    ),
                )
            return _DEFAULT_CLEARANCE
        try:
            import json as _json
            raw = await redis.get(f"spaider:agent:{agent_id}")
            if raw:
                data = _json.loads(raw)
                return int(data.get("clearance_level", _DEFAULT_CLEARANCE))
            # Agent record not found in Redis.
            if _CLEARANCE_FAIL_CLOSED:
                logger.error(
                    "_get_agent_clearance: agent record missing in Redis | "
                    "agent=%s | raising 503 (CLEARANCE_FAIL_CLOSED=true)",
                    agent_id,
                )
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=(
                        "Service Unavailable: Clearance resolution timeout "
                        "(agent record not found in access-control plane)."
                    ),
                )
        except HTTPException:
            raise  # Don't swallow our own fail-closed exception
        except Exception as exc:
            if _CLEARANCE_FAIL_CLOSED:
                logger.error(
                    "_get_agent_clearance: Redis lookup raised | agent=%s | "
                    "raising 503 (CLEARANCE_FAIL_CLOSED=true) | err=%s",
                    agent_id, exc,
                )
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=(
                        "Service Unavailable: Clearance resolution timeout "
                        "(Redis lookup failed)."
                    ),
                ) from exc
            logger.warning(
                "_get_agent_clearance failed for agent=%s, defaulting to %d: %s",
                agent_id, _DEFAULT_CLEARANCE, exc,
            )
        return _DEFAULT_CLEARANCE

    async def get_agent_interaction_memory(self, agent_id: str) -> bool:
        """
        Return the interaction_memory flag for *agent_id*.

        Reads from the same Redis key written by agents.py.
        Falls back to False on any error — never blocks the query pipeline.
        """
        redis = await self._get_redis()
        if not redis:
            return False
        try:
            raw = await redis.get(f"spaider:agent:{agent_id}")
            if raw:
                data = json.loads(raw)
                return bool(data.get("interaction_memory", False))
        except Exception as exc:
            logger.warning(
                "get_agent_interaction_memory failed for agent=%s, defaulting to False: %s",
                agent_id, exc,
            )
        return False

    # ------------------------------------------------------------------
    # Redis cache (lazy init)
    # ------------------------------------------------------------------

    async def _get_redis(self):
        if self._redis is None:
            try:
                import redis.asyncio as aioredis
                self._redis = aioredis.from_url(
                    settings.redis_url, decode_responses=True
                )
                await self._redis.ping()
            except Exception as exc:
                logger.warning("Redis unavailable for query cache: %s", exc)
                self._redis = False
        return self._redis if self._redis else None

    async def _publish_pheromone(
        self,
        node_ids: list[str],
        agent_id: str = "system",
        labels: Optional[dict[str, str]] = None,
    ) -> None:
        """
        Emit a lightweight pheromone event for the nodes a query just touched.

        Drives the Live Pheromone Stream (SSE → ``swarm_log_channel``): every
        retrieval "lights up" the nodes it boosted so the studio shows genuine
        live activity. Fire-and-forget — a Redis hiccup must never affect the
        query path, so all failures are swallowed. When a ``labels`` map is
        supplied the preview shows human-readable node names (truncated, so a
        long FACT label never dumps its full text) instead of opaque IDs; the
        raw ``node_ids`` ride along in the payload for the canvas to highlight.
        """
        if not node_ids:
            return
        try:
            redis = await self._get_redis()
            if not redis:
                return
            from app.services.redis_service import publish_swarm_log

            def _name(nid: str) -> str:
                label = (labels or {}).get(nid) or nid[:8]
                label = label.strip().replace("\n", " ")
                return label[:24] + "…" if len(label) > 24 else label

            preview = ", ".join(_name(n) for n in node_ids[:5])
            more = f" (+{len(node_ids) - 5} more)" if len(node_ids) > 5 else ""
            await publish_swarm_log(
                redis,
                "pheromone",
                agent_id,
                f"Boosted {len(node_ids)} node(s): {preview}{more}",
                node_ids=node_ids[:25],
                count=len(node_ids),
            )
        except Exception as exc:  # pragma: no cover — diagnostic only
            logger.debug("query_nl | pheromone publish skipped: %s", exc)

    @staticmethod
    def _cache_key(question: str, agent_id: str) -> str:
        h = hashlib.sha256(f"{agent_id}:{question.strip().lower()}".encode()).hexdigest()[:16]
        return f"spaider:query:cache:{h}"

    async def _cache_get(self, question: str, agent_id: str) -> Optional[QueryResult]:
        redis = await self._get_redis()
        if not redis:
            return None
        try:
            raw = await redis.get(self._cache_key(question, agent_id))
            if raw:
                d = json.loads(raw)
                return QueryResult(
                    question=d["question"],
                    answer=d["answer"],
                    subgraph=GraphPayload(**d["subgraph"]),
                    cypher=d.get("cypher"),
                    from_cache=True,
                )
        except Exception:
            pass
        return None

    async def _cache_set(self, result: QueryResult, agent_id: str) -> None:
        redis = await self._get_redis()
        if not redis:
            return
        try:
            payload = {
                "question": result.question,
                "answer": result.answer,
                "cypher": result.cypher,
                "subgraph": {
                    "nodes": [n.model_dump(exclude={"embedding"}) for n in result.subgraph.nodes],
                    "edges": [e.model_dump() for e in result.subgraph.edges],
                },
            }
            await redis.set(
                self._cache_key(result.question, agent_id),
                json.dumps(payload),
                ex=_CACHE_TTL,
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Engine version check
    # ------------------------------------------------------------------

    async def _get_engine_version(self) -> str:
        """
        Read the active engine version from Neo4j SystemSettings.
        Falls back to "v1" on any error — never blocks the query pipeline.
        """
        try:
            async with self._graph._driver.session() as session:
                result = await session.run(
                    """
                    MATCH (s:SystemSettings {id: "global"})
                    RETURN coalesce(s.engine_version, "v1") AS engine_version
                    """
                )
                record = await result.single()
                return record["engine_version"] if record else "v1"
        except Exception as exc:
            logger.warning("Could not read engine_version, defaulting to v1: %s", exc)
            return "v1"

    # ------------------------------------------------------------------
    # Question decomposition — router LLM call that splits multi-entity
    # questions into focused sub-questions before vector search.
    # Off by default (settings.query_decomposition_enabled).
    # ------------------------------------------------------------------

    async def _decompose_question(self, question: str) -> list[str]:
        """Split ``question`` into 1 to ``_DECOMPOSITION_MAX_SUBQUERIES``
        focused sub-questions via a small LLM call. On any failure (empty
        list, malformed JSON, request error, > cap) returns
        ``[question]`` unchanged so the caller falls back to single-shot
        retrieval — never blocks the query pipeline.
        """
        import json

        prompt = _DECOMPOSITION_PROMPT_TEMPLATE.format(question=question.strip())
        call_kwargs: dict = dict(
            model=settings.litellm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=256,
            request_timeout=10,
        )
        if settings.llm_base_url:
            call_kwargs["api_base"] = settings.llm_base_url
        if settings.llm_api_key:
            call_kwargs["api_key"] = settings.llm_api_key

        try:
            resp = await acompletion_with_retry(**call_kwargs)
            raw = (resp.choices[0].message.content or "").strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning("decompose_question LLM call failed: %s — falling back to original", exc)
            return [question]

        # Tolerate accidental ```json fences a few models still emit.
        if raw.startswith("```"):
            raw = raw.strip("`").lstrip("json").strip()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("decompose_question non-JSON output: %r — falling back", raw[:120])
            return [question]

        if not isinstance(parsed, list) or not parsed:
            return [question]
        # Filter to non-empty strings, dedupe, cap at the safety limit.
        seen: set[str] = set()
        out: list[str] = []
        for item in parsed:
            if not isinstance(item, str):
                continue
            s = item.strip()
            if not s or s.lower() in seen:
                continue
            seen.add(s.lower())
            out.append(s)
            if len(out) >= _DECOMPOSITION_MAX_SUBQUERIES:
                break
        if not out:
            return [question]
        logger.info(
            "decompose_question | original=%r → %d sub-question(s)",
            question[:80], len(out),
        )
        return out

    async def _fulltext_seed_search(
        self,
        question: str,
        agent_id: str,
        limit: int = _DEFAULT_TOP_K,
    ) -> list[Node]:
        """Keyword leg of the hybrid seed retrieval.

        Extracts the question's content words into a Lucene OR-of-prefixes
        query against the label/description/source_text fulltext index and
        returns the relevance-ranked nodes. Fail-safe: any error returns []
        so the vector leg alone still serves the query.
        """
        ft_query = _keyword_ft_query(question)
        if not ft_query:
            return []
        try:
            async with self._graph._driver.session() as session:
                result = await session.run(
                    """
                    CALL db.index.fulltext.queryNodes("spaider_label_fulltext", $ft_query)
                    YIELD node AS n, score
                    WHERE n.agent_id = $agent_id AND NOT n:SystemAgent
                    RETURN n.id AS id, n.label AS label, n.type AS type,
                           n.description AS description,
                           n.properties AS properties, n.embedding AS embedding,
                           n.agent_id AS agent_id
                    ORDER BY score DESC
                    LIMIT $limit
                    """,
                    ft_query=ft_query,
                    agent_id=agent_id,
                    limit=limit,
                )
                records = await result.data()
            nodes: list[Node] = []
            for rec in records:
                props = rec.get("properties")
                if isinstance(props, str):
                    try:
                        props = json.loads(props)
                    except (json.JSONDecodeError, ValueError):
                        props = {}
                nodes.append(Node(
                    id=rec["id"],
                    label=rec.get("label") or "",
                    type=rec.get("type") or "OTHER",
                    description=rec.get("description"),
                    properties=props or {},
                    embedding=rec.get("embedding"),
                    agent_id=rec.get("agent_id"),
                ))
            return nodes
        except Exception as exc:
            logger.warning("fulltext seed search failed (vector leg continues): %s", exc)
            return []

    async def _fetch_relationship_lines(
        self,
        node_ids: list[str],
        allowed_agent_ids: list[str],
        agent_clearance: int,
        *,
        limit: int = 30,
    ) -> list[str]:
        """Render the 1-hop RELATION edges touching the seed nodes as
        ``A -[REL]-> B`` lines.

        A flat list of nodes can't express which entity relates to which, so
        questions that hinge on a relationship (e.g. the decision owner of a
        project) are unanswerable from node text alone. Scoped to allowed
        tenants and the caller's clearance; bounded and fail-safe.
        """
        if not node_ids:
            return []
        try:
            async with self._graph._driver.session() as session:
                result = await session.run(
                    """
                    MATCH (a:SpaiderNode)-[r:RELATION]->(b:SpaiderNode)
                    WHERE (a.id IN $node_ids OR b.id IN $node_ids)
                      AND a.agent_id IN $allowed AND b.agent_id IN $allowed
                      AND coalesce(a.clearance_level, $cd) <= $clr
                      AND coalesce(b.clearance_level, $cd) <= $clr
                    RETURN DISTINCT a.label AS src,
                           coalesce(r.relation, 'RELATED_TO') AS rel,
                           b.label AS tgt
                    LIMIT $limit
                    """,
                    node_ids=node_ids,
                    allowed=allowed_agent_ids,
                    cd=_CLEARANCE_DEFAULT_VALUE,
                    clr=agent_clearance,
                    limit=limit,
                )
                rows = await result.data()
        except Exception as exc:
            logger.warning("relationship context fetch failed: %s", exc)
            return []
        return [
            f"{row['src']} -[{row['rel']}]-> {row['tgt']}"
            for row in rows
            if row.get("src") and row.get("tgt")
        ]

    # ------------------------------------------------------------------
    # V1 Swarm RAG retrieval (unchanged)
    # ------------------------------------------------------------------

    async def retrieve_swarm_context(
        self,
        target_agent_id: str,
        query: str,
        agent_clearance: int = _DEFAULT_CLEARANCE,
        top_k: Optional[int] = None,
    ) -> tuple[str, list[str]]:
        """
        High-performance cross-agent retrieval with provenance (V1 mode).

        Pipeline:
          1. Single Cypher: collect primary + SHARES_KNOWLEDGE_WITH agents.
          2. Full-text index keyword search within allowed namespaces.
          3. Fallback: if keyword returns 0 rows, fetch top-N by agent order.
          4. Format each fact as [Source: Agent <id>] - <content>.

        Diplomat Protocol:
          Only nodes where coalesce(n.clearance_level, 1) <= agent_clearance
          are returned.  Nodes without an explicit clearance_level are treated
          as level 1 (public), so unlabelled data is always visible.

        Returns:
          (formatted_context_string, list_of_involved_agent_ids)
        """
        async with self._graph._driver.session() as session:
            allowed_result = await session.run(
                """
                MATCH (primary:SystemAgent {agent_id: $target_agent_id})
                OPTIONAL MATCH (primary)-[:SHARES_KNOWLEDGE_WITH]->(shared:SystemAgent)
                WITH primary, collect(shared.agent_id) + [primary.agent_id] AS allowed_agent_ids
                RETURN allowed_agent_ids
                """,
                target_agent_id=target_agent_id,
            )
            allowed_record = await allowed_result.single()

        if not allowed_record:
            return "No relevant context found.", [target_agent_id]

        allowed_agent_ids: list[str] = allowed_record["allowed_agent_ids"]

        keywords = [
            w.lower() for w in re.split(r"\W+", query) if len(w) >= 3
        ][:10]

        # Build a Lucene OR query from the extracted keywords.
        # Each token gets a trailing * for prefix matching (e.g. "ali" → "ali*"
        # matches "Alice").  If the query is too short to extract any keyword
        # (all words < 3 chars), ft_query is None and we skip to the fallback.
        ft_query: str | None = (
            " OR ".join(f"{w}*" for w in keywords) if keywords else None
        )

        async with self._graph._driver.session() as session:
            records: list[dict] = []

            if ft_query:
                # Use the full-text index (O(log N + hits)).
                # allowed_agent_ids was pre-fetched above — no need to re-expand
                # the SHARES_KNOWLEDGE_WITH traversal inside this query.
                # Security: WHERE clauses applied immediately after YIELD so only
                # nodes belonging to allowed tenants and within clearance pass.
                search_result = await session.run(
                    """
                    CALL db.index.fulltext.queryNodes("spaider_label_fulltext", $ft_query)
                    YIELD node AS n, score
                    WHERE n.agent_id IN $allowed_agent_ids
                      AND NOT n:SystemAgent
                      AND coalesce(n.clearance_level, $clearance_default) <= $agent_clearance
                    RETURN n.id         AS node_id,
                           n.label      AS label,
                           n.type       AS type,
                           n.description AS description,
                           n.source_text AS source_text,
                           n.properties AS properties,
                           n.agent_id   AS source_agent,
                           1.0          AS avg_weight
                    ORDER BY score DESC
                    LIMIT $ctx_limit
                    """,
                    ft_query=ft_query,
                    allowed_agent_ids=allowed_agent_ids,
                    agent_clearance=agent_clearance,
                    clearance_default=_CLEARANCE_DEFAULT_VALUE,
                    ctx_limit=_context_limit(top_k),
                )
                records = await search_result.data()

            if not records:
                fallback_result = await session.run(
                    """
                    UNWIND $allowed_agent_ids AS aid
                    MATCH (n:SpaiderNode {agent_id: aid})
                    WHERE NOT n:SystemAgent
                      AND coalesce(n.clearance_level, $clearance_default) <= $agent_clearance
                    WITH n ORDER BY n.label LIMIT $ctx_limit
                    RETURN n.id         AS node_id,
                           n.label      AS label,
                           n.type       AS type,
                           n.description AS description,
                           n.source_text AS source_text,
                           n.properties AS properties,
                           n.agent_id   AS source_agent,
                           1.0          AS avg_weight
                    """,
                    allowed_agent_ids=allowed_agent_ids,
                    agent_clearance=agent_clearance,
                    clearance_default=_CLEARANCE_DEFAULT_VALUE,
                    ctx_limit=_context_limit(top_k),
                )
                records = await fallback_result.data()

        if not records:
            return "No relevant context found.", allowed_agent_ids

        context, involved = self._format_context_records(
            records, v2_mode=False
        )

        # Fire-and-forget: boost energy on every node returned to the LLM
        import asyncio
        node_ids = [r["node_id"] for r in records if r.get("node_id")]
        asyncio.create_task(self._cognitive.boost_nodes(node_ids))

        # Append the 1-hop relationships so the model can traverse an edge to
        # answer (e.g. the decision owner of X), not just read disconnected facts.
        rel_lines = await self._fetch_relationship_lines(
            node_ids, allowed_agent_ids, agent_clearance,
        )
        if rel_lines:
            context = f"{context}\n\nRelationships:\n" + "\n".join(rel_lines)

        logger.info(
            "retrieve_swarm_context [V1] | target=%s allowed=%s hits=%d",
            target_agent_id, allowed_agent_ids, len(records),
        )
        return context, list(involved)

    # ------------------------------------------------------------------
    # V2 Swarm RAG retrieval — Managed Forgetting + Strength Priorisation
    # ------------------------------------------------------------------

    async def retrieve_swarm_context_v2(
        self,
        target_agent_id: str,
        query: str,
        agent_clearance: int = _DEFAULT_CLEARANCE,
        top_k: Optional[int] = None,
    ) -> tuple[str, list[str]]:
        """
        V2 Cognitive Graph retrieval with synaptic plasticity awareness.

        Differences from V1:
          • Managed Forgetting: nodes whose adjacent edges all have
            avg(utility_weight) < 0.3 are excluded from retrieval.
            Facts that consistently led to bad answers have been weakened out.
          • Strength Priorisation: remaining facts are returned in descending
            order of avg(utility_weight).  The LLM sees the most historically
            validated facts first — critical for context-window budget.
          • Annotated context: lines for high-weight facts (>= 1.5) carry a
            [Strength: X.X] marker so the LLM knows which are most reliable.
          • Graceful degradation: if V2 retrieval returns nothing (entire graph
            below forgetting threshold), falls back to V1 automatically.

        Diplomat Protocol:
          Only nodes where coalesce(n.clearance_level, 1) <= agent_clearance
          pass the hard filter — applied before the Managed-Forgetting check.

        Returns:
          (formatted_context_string, list_of_involved_agent_ids)
        """
        async with self._graph._driver.session() as session:
            allowed_result = await session.run(
                """
                MATCH (primary:SystemAgent {agent_id: $target_agent_id})
                OPTIONAL MATCH (primary)-[:SHARES_KNOWLEDGE_WITH]->(shared:SystemAgent)
                WITH primary, collect(shared.agent_id) + [primary.agent_id] AS allowed_agent_ids
                RETURN allowed_agent_ids
                """,
                target_agent_id=target_agent_id,
            )
            allowed_record = await allowed_result.single()

        if not allowed_record:
            return "No relevant context found.", [target_agent_id]

        allowed_agent_ids: list[str] = allowed_record["allowed_agent_ids"]

        keywords = [
            w.lower() for w in re.split(r"\W+", query) if len(w) >= 3
        ][:10]

        # Build a Lucene OR query from the extracted keywords (prefix matching).
        ft_query: str | None = (
            " OR ".join(f"{w}*" for w in keywords) if keywords else None
        )

        async with self._graph._driver.session() as session:
            records: list[dict] = []

            if ft_query:
                # ── V2 Full-Text Search with Unified Synaptic Score ───────
                # Pipeline:
                #   1. FTS index lookup — O(log N + hits), index-backed tenant
                #      filter applied immediately after YIELD (pre-filter).
                #   2. Diplomat Protocol: clearance_level hard filter.
                #   3. OPTIONAL MATCH edges → aggregate avg(utility_weight) as avg_u.
                #   4. Managed Forgetting: drop nodes where synaptic_score < threshold.
                #      synaptic_score = avg_u · (E · exp(−λ · Δt))   — inline, no extra WITH.
                #   5. ORDER BY synaptic_score DESC — temporally-aware ranking.
                search_result = await session.run(
                    """
                    CALL db.index.fulltext.queryNodes("spaider_label_fulltext", $ft_query)
                    YIELD node AS n, score
                    WHERE n.agent_id IN $allowed_agent_ids
                      AND NOT n:SystemAgent
                      AND coalesce(n.clearance_level, $clearance_default) <= $agent_clearance
                    OPTIONAL MATCH (n)-[r:RELATION]->()
                    WITH n, avg(coalesce(r.utility_weight, 1.0)) AS avg_u
                    WHERE avg_u * (
                            coalesce(n.energy_level, 1.0) *
                            exp(
                              -(0.05 / (1.0 + 0.1 * sqrt(toFloat(coalesce(n.retrieval_count, 1))))) *
                              (CASE WHEN n.last_activation IS NOT NULL
                                    THEN duration.inSeconds(datetime(n.last_activation), datetime()).seconds / 3600.0
                                    ELSE 0.0 END)
                            )
                          ) >= $forget_threshold
                    RETURN n.id         AS node_id,
                           n.label      AS label,
                           n.type       AS type,
                           n.description AS description,
                           n.source_text AS source_text,
                           n.properties AS properties,
                           n.agent_id   AS source_agent,
                           n.embedding  AS embedding,
                           avg_u * (
                             coalesce(n.energy_level, 1.0) *
                             exp(
                               -(0.05 / (1.0 + 0.1 * sqrt(toFloat(coalesce(n.retrieval_count, 1))))) *
                               (CASE WHEN n.last_activation IS NOT NULL
                                     THEN duration.inSeconds(datetime(n.last_activation), datetime()).seconds / 3600.0
                                     ELSE 0.0 END)
                             )
                           ) AS synaptic_score
                    ORDER BY synaptic_score DESC
                    LIMIT $ctx_limit
                    """,
                    ft_query=ft_query,
                    allowed_agent_ids=allowed_agent_ids,
                    forget_threshold=_V2_FORGET_THRESHOLD,
                    agent_clearance=agent_clearance,
                    clearance_default=_CLEARANCE_DEFAULT_VALUE,
                    ctx_limit=_MMR_FETCH_N,
                )
                records = await search_result.data()

            # ── V2 Fallback — Synaptic-Score-Prioritised, Forgetting-Filtered ─
            if not records:
                fallback_result = await session.run(
                    """
                    UNWIND $allowed_agent_ids AS aid
                    MATCH (n:SpaiderNode {agent_id: aid})
                    WHERE NOT n:SystemAgent
                      AND coalesce(n.clearance_level, $clearance_default) <= $agent_clearance
                    OPTIONAL MATCH (n)-[r:RELATION]->()
                    WITH n, avg(coalesce(r.utility_weight, 1.0)) AS avg_u
                    WHERE avg_u * (
                            coalesce(n.energy_level, 1.0) *
                            exp(
                              -(0.05 / (1.0 + 0.1 * sqrt(toFloat(coalesce(n.retrieval_count, 1))))) *
                              (CASE WHEN n.last_activation IS NOT NULL
                                    THEN duration.inSeconds(datetime(n.last_activation), datetime()).seconds / 3600.0
                                    ELSE 0.0 END)
                            )
                          ) >= $forget_threshold
                    RETURN n.id         AS node_id,
                           n.label      AS label,
                           n.type       AS type,
                           n.description AS description,
                           n.source_text AS source_text,
                           n.properties AS properties,
                           n.agent_id   AS source_agent,
                           n.embedding  AS embedding,
                           avg_u * (
                             coalesce(n.energy_level, 1.0) *
                             exp(
                               -(0.05 / (1.0 + 0.1 * sqrt(toFloat(coalesce(n.retrieval_count, 1))))) *
                               (CASE WHEN n.last_activation IS NOT NULL
                                     THEN duration.inSeconds(datetime(n.last_activation), datetime()).seconds / 3600.0
                                     ELSE 0.0 END)
                             )
                           ) AS synaptic_score
                    ORDER BY synaptic_score DESC
                    LIMIT $ctx_limit
                    """,
                    allowed_agent_ids=allowed_agent_ids,
                    forget_threshold=_V2_FORGET_THRESHOLD,
                    agent_clearance=agent_clearance,
                    clearance_default=_CLEARANCE_DEFAULT_VALUE,
                    ctx_limit=_MMR_FETCH_N,
                )
                records = await fallback_result.data()

        # ── Graceful degradation to V1 if entire graph is below threshold ─
        if not records:
            logger.warning(
                "retrieve_swarm_context_v2 | no nodes above forget threshold "
                "(%.1f), degrading to V1 for target=%s",
                _V2_FORGET_THRESHOLD, target_agent_id,
            )
            return await self.retrieve_swarm_context(
                target_agent_id, query, agent_clearance=agent_clearance, top_k=top_k
            )

        # ── MMR post-filtering: select a diverse subset from the candidate pool ─
        # Attempt to use stored node embeddings for cosine similarity.
        # Falls back to top-K truncation if any embedding is missing (e.g.
        # nodes ingested before the embedding pipeline was deployed).
        if len(records) > _MMR_SELECT_K:
            raw_embs = [r.get("embedding") for r in records]
            if all(e is not None for e in raw_embs):
                try:
                    emb_arr = np.array(raw_embs, dtype=np.float64)
                    score_arr = np.array(
                        [r.get("synaptic_score", 1.0) for r in records],
                        dtype=np.float64,
                    )
                    records = apply_mmr(records, emb_arr, score_arr, k=_MMR_SELECT_K)
                    logger.debug(
                        "retrieve_swarm_context_v2 | MMR selected %d/%d candidates",
                        len(records), _MMR_FETCH_N,
                    )
                except Exception:
                    logger.debug(
                        "retrieve_swarm_context_v2 | MMR failed, truncating to %d",
                        _MMR_SELECT_K, exc_info=True,
                    )
                    records = records[:_MMR_SELECT_K]
            else:
                # Embedding not stored on some nodes — fall back to score-ranked top-K.
                records = records[:_MMR_SELECT_K]

        context, involved = self._format_context_records(
            records, v2_mode=True
        )

        # Fire-and-forget: boost energy on every node returned to the LLM
        import asyncio
        node_ids = [r["node_id"] for r in records if r.get("node_id")]
        asyncio.create_task(self._cognitive.boost_nodes(node_ids))

        # Append the 1-hop relationships so the model can traverse an edge to
        # answer (e.g. the decision owner of X), not just read disconnected facts.
        rel_lines = await self._fetch_relationship_lines(
            node_ids, allowed_agent_ids, agent_clearance,
        )
        if rel_lines:
            context = f"{context}\n\nRelationships:\n" + "\n".join(rel_lines)

        logger.info(
            "retrieve_swarm_context [V2] | target=%s allowed=%s candidates=%d "
            "final=%d synaptic_score_top=%.2f",
            target_agent_id,
            allowed_agent_ids,
            _MMR_FETCH_N,
            len(records),
            records[0].get("synaptic_score", 1.0),
        )
        return context, list(involved)

    # ------------------------------------------------------------------
    # Shared context formatter (V1 + V2)
    # ------------------------------------------------------------------

    @staticmethod
    def _format_context_records(
        records: list[dict],
        *,
        v2_mode: bool,
    ) -> tuple[str, set[str]]:
        """
        Convert Neo4j records to the LLM context string.

        In V2 mode, facts with avg_weight >= 1.5 receive a
        [Strength: X.X] prefix annotation so the LLM can weight them
        accordingly.  Low-weight facts (but above forget threshold) are
        included but unmarked.
        """
        lines: list[str] = []
        involved: set[str] = set()

        for rec in records:
            source_agent: str = rec.get("source_agent") or "unknown"
            label: str        = rec.get("label") or ""
            node_type: str    = rec.get("type") or ""
            # V2 queries return synaptic_score; V1 queries return avg_weight (= 1.0)
            avg_weight: float = float(
                rec.get("synaptic_score") or rec.get("avg_weight") or 1.0
            )

            props_raw = rec.get("properties")
            try:
                if isinstance(props_raw, str):
                    props: dict = json.loads(props_raw)
                elif isinstance(props_raw, dict):
                    props = props_raw
                else:
                    props = {}
            except Exception:
                props = {}

            # Prefer the promoted top-level columns; fall back to the
            # properties JSON for rows written before the column migration.
            description: str = rec.get("description") or props.get("description") or ""
            source_text: str = rec.get("source_text") or props.get("source_text") or ""
            temporal: str = str(props.get("temporal") or "").strip()

            content = f"{label} ({node_type})"
            detail_parts: list[str] = []
            if description:
                detail_parts.append(description)
            # source_text is the raw extracted fact and frequently carries the
            # answer or the linking relationship (e.g. "Olivia (CTO) confirmed
            # the review"). Include it ALONGSIDE the description rather than only
            # when the description is missing — otherwise the fact is dropped
            # whenever a generic description exists. Bounded for token-bleed (B1).
            if source_text and source_text != description:
                if len(source_text) > MAX_SOURCE_TEXT_CHARS:
                    source_text = source_text[:MAX_SOURCE_TEXT_CHARS] + "..."
                detail_parts.append(source_text)
            # Structured extracted attributes (dates, metrics, amounts) live in
            # `properties` and were previously never surfaced, so the model
            # answered "not available" even on a retrieval hit. Surface them —
            # bounded to the first few so a property-heavy node can't flood the
            # context (token economics).
            if temporal:
                detail_parts.append(f"date: {temporal}")
            extra_props = 0
            for key, value in props.items():
                if key in _CONTEXT_SKIP_PROPS:
                    continue
                if isinstance(value, (str, int, float)) and str(value).strip():
                    detail_parts.append(f"{key}: {str(value)[:120]}")
                    extra_props += 1
                    if extra_props >= _CONTEXT_MAX_EXTRA_PROPS:
                        break

            if detail_parts:
                content += ": " + "; ".join(detail_parts)

            # V2: annotate strongly validated facts
            if v2_mode and avg_weight >= 1.5:
                line = (
                    f"[Strength: {avg_weight:.1f}] "
                    f"[Source: Agent {source_agent}] - {content}"
                )
            else:
                line = f"[Source: Agent {source_agent}] - {content}"

            lines.append(line)
            involved.add(source_agent)

        return "\n".join(lines), involved

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def query_nl(
        self,
        question: str,
        agent_id: str,
        top_k: Optional[int] = None,
        decompose: Optional[bool] = None,
    ) -> QueryResult:
        """Token-tracking entry point for the NL query pipeline.

        Wraps the real implementation in ``track_tokens()`` so the result
        carries this query's backend LLM spend (``token_usage``) regardless of
        caller. A cache hit makes no backend LLM calls, so its ``token_usage``
        stays ``None`` rather than a misleading ``{0, 0}``.
        """
        with track_tokens() as bucket:
            result = await self._query_nl_impl(question, agent_id, top_k, decompose)
        if result is not None and not getattr(result, "from_cache", False):
            result.token_usage = dict(bucket)
        return result

    async def _query_nl_impl(
        self,
        question: str,
        agent_id: str,
        top_k: Optional[int] = None,
        decompose: Optional[bool] = None,
    ) -> QueryResult:
        """
        Iterative Self-RAG query loop — Agentic QA architecture.

        Plan-and-Solve / Self-Refine pipeline:
          1. Cache hit → instant return (loop skipped entirely).
          2. Initial parallel setup: embed question, detect engine version,
             resolve agent clearance, fetch swarm context, seed-node search.
          3. Agentic loop (max ``settings.max_qa_iterations``,
             bounded by ``settings.qa_time_budget_seconds``):
               a. Expand retrieved seed nodes 1-hop; merge into O(1) dict keyed
                  by node.id — guarantees deduplication across iterations.
               b. Call ``_verify_evidence`` (Pydantic structured output — never
                  raw json.loads) to assess context sufficiency.
               c. Break if sufficient, iteration cap hit, or time budget exceeded.
               d. Otherwise set ``current_search_query`` from verifier's
                  ``next_search_query`` and continue.
          4. Final synthesis on the accumulated cumulative context.

        V1/V2 routing, Diplomat Protocol clearance filtering, and swarm
        provenance attribution are all preserved from the single-pass pipeline.
        """
        import asyncio

        # ── 1. Cache hit ────────────────────────────────────────────────────
        cached = await self._cache_get(question, agent_id)
        if cached:
            logger.info("query_nl cache HIT for agent=%s", agent_id)
            return cached

        # ── 2. Parallel initial setup ────────────────────────────────────────
        # Optional decomposition: split a multi-entity question into 1-N
        # focused sub-questions, embed each, vector-search per sub-question,
        # and union the seed-node sets. For comparison/bridge questions a
        # single embedding tends to lock onto the more salient entity and
        # miss the other; the per-sub-question fan-out fixes that.
        # Per-call override (used by federated swarm) falls back to the global
        # setting when not specified — so single-agent queries and benchmarks
        # keep their configured behaviour.
        use_decomposition = (
            decompose if decompose is not None else settings.query_decomposition_enabled
        )
        if use_decomposition:
            subqueries = await self._decompose_question(question)
        else:
            subqueries = [question]

        sq_embeddings, engine_version, agent_clearance = await asyncio.gather(
            asyncio.gather(*[self._embedding.embed(sq) for sq in subqueries]),
            self._get_engine_version(),
            self._get_agent_clearance(agent_id),
        )
        # Hybrid seed retrieval per sub-question: dense vector search AND
        # keyword fulltext search run in parallel, fused with reciprocal-rank
        # fusion. Each catches what the other misses — vectors get paraphrase,
        # fulltext gets exact entities/numbers/dates the embedding glosses over.
        seed_k = top_k or _DEFAULT_TOP_K
        per_sq_results = await asyncio.gather(
            *[
                self._graph.vector_search(embedding=emb, agent_id=agent_id, top_k=seed_k)
                for emb in sq_embeddings
            ],
            *[
                self._fulltext_seed_search(sq, agent_id=agent_id, limit=seed_k)
                for sq in subqueries
            ],
        )
        # Fuse all ranked lists (vector + fulltext across sub-questions) and
        # keep roughly double the per-list budget so both modalities survive.
        seed_nodes = _rrf_fuse(list(per_sq_results))[: seed_k * 2]

        # Swarm context retrieved once from the original question (not re-queried
        # per iteration — swarm text enriches synthesis but is not node-accumulative).
        if engine_version == "v2":
            swarm_context, involved_agents = await self.retrieve_swarm_context_v2(
                target_agent_id=agent_id,
                query=question,
                agent_clearance=agent_clearance,
                top_k=top_k,
            )
        else:
            swarm_context, involved_agents = await self.retrieve_swarm_context(
                target_agent_id=agent_id,
                query=question,
                agent_clearance=agent_clearance,
                top_k=top_k,
            )

        # Text-search fallback when vector index is empty
        if not seed_nodes:
            seed_nodes = await self._graph.search_nodes(
                query=question[:50], agent_id=agent_id, limit=top_k or _DEFAULT_TOP_K
            )

        # ── 3. Agentic loop ──────────────────────────────────────────────────
        MAX_ITER: int = settings.max_qa_iterations
        TIME_BUDGET: float = settings.qa_time_budget_seconds
        loop_start = time.time()

        # O(1) deduplication: dict keyed by node.id prevents duplicate context
        # across iterations (list-based approach would be O(n) per insert).
        cumulative_nodes: dict[str, Node] = {}
        cumulative_edges: dict[str, Edge] = {}

        iteration_count: int = 0
        current_search_query: str = question
        verifier_result: Optional[VerifierResult] = None
        all_feedback: list[str] = []

        # Seed nodes for first iteration come from vector search above;
        # subsequent iterations use keyword search with the verifier's query.
        current_iter_seeds = seed_nodes

        while True:
            # ── Iteration cap and time-budget guards ────────────────────────
            if iteration_count >= MAX_ITER:
                logger.info(
                    "QA loop reached max iterations (%d) for agent=%s",
                    MAX_ITER, agent_id,
                )
                break
            if (time.time() - loop_start) > TIME_BUDGET:
                logger.warning(
                    "QA loop hit time budget (%.1fs) after %d iterations for agent=%s",
                    TIME_BUDGET, iteration_count, agent_id,
                )
                break

            iteration_count += 1

            # ── Diplomat Protocol: strip over-clearance seeds ────────────────
            iter_seeds = [
                n for n in current_iter_seeds
                if int((n.properties or {}).get("clearance_level", _DEFAULT_CLEARANCE))
                <= agent_clearance
            ]

            # ── 1-hop expansion → accumulate into cumulative dicts (O(1)) ────
            if iter_seeds:
                expansions = await asyncio.gather(
                    *[self._graph.get_subgraph(n.id, depth=1) for n in iter_seeds],
                    return_exceptions=True,
                )
                for exp in expansions:
                    if isinstance(exp, Exception):
                        continue
                    for n in exp.nodes:
                        node_clearance = int(
                            (n.properties or {}).get("clearance_level", _DEFAULT_CLEARANCE)
                        )
                        if node_clearance <= agent_clearance:
                            cumulative_nodes[n.id] = n  # O(1) insert + dedup
                    for e in exp.edges:
                        if engine_version == "v2":
                            src = cumulative_nodes.get(e.source_id)
                            if _synaptic_score_py(e.utility_weight, src) < _V2_FORGET_THRESHOLD:
                                continue
                        cumulative_edges[e.id] = e

            # ── Verify evidence sufficiency (Pydantic structured output) ─────
            verify_context = self._build_context(
                GraphPayload(nodes=list(cumulative_nodes.values()))
            )
            verifier_result = await self._verify_evidence(question, verify_context)

            if verifier_result.missing_information_categories:
                all_feedback.extend(verifier_result.missing_information_categories)

            logger.info(
                "QA loop | agent=%s iter=%d sufficient=%s confidence=%.2f missing=%s",
                agent_id, iteration_count,
                verifier_result.is_sufficient,
                verifier_result.confidence,
                verifier_result.missing_information_categories,
            )

            # ── Break conditions ─────────────────────────────────────────────
            if verifier_result.is_sufficient:
                break

            next_query = verifier_result.next_search_query
            if not next_query:
                logger.debug("Verifier provided no next_search_query — stopping loop")
                break

            # ── Time-budget re-check before issuing another retrieval ─────────
            if (time.time() - loop_start) > TIME_BUDGET:
                logger.warning(
                    "QA loop hit time budget before re-query, iter=%d agent=%s",
                    iteration_count, agent_id,
                )
                break

            # ── Re-query: text search with verifier-supplied query ────────────
            current_search_query = next_query
            logger.debug(
                "QA loop re-querying | agent=%s iter=%d query=%r",
                agent_id, iteration_count, current_search_query,
            )
            current_iter_seeds = await self._graph.search_nodes(
                query=current_search_query[:80],
                agent_id=agent_id,
                limit=top_k or _DEFAULT_TOP_K,
            )

        # ── 4. Build final subgraph from cumulative context ──────────────────
        valid_node_ids = set(cumulative_nodes.keys())
        clean_edges = {
            eid: e for eid, e in cumulative_edges.items()
            if e.source_id in valid_node_ids and e.target_id in valid_node_ids
        }
        subgraph = GraphPayload(
            nodes=list(cumulative_nodes.values()),
            edges=list(clean_edges.values()),
        )

        # ── 5. Merge swarm text context with accumulated node context ─────────
        vector_context = self._build_context(subgraph)
        if vector_context and vector_context != "No relevant entities found in the knowledge graph.":
            vector_lines = [
                f"[Source: Agent {agent_id}] - {line.lstrip('• ')}"
                for line in vector_context.splitlines()
                if line.strip()
            ]
            merged_context = swarm_context + "\n" + "\n".join(vector_lines)
        else:
            merged_context = swarm_context

        # ── 6. Final synthesis LLM call ───────────────────────────────────────
        is_swarm = len(involved_agents) > 1
        answer = await self._answer_with_context(
            question, merged_context,
            is_swarm=is_swarm,
            v2_mode=(engine_version == "v2"),
        )

        # ── 6b. Concise answer span (factoid questions) ───────────────────────
        direct_answer = await self._extract_direct_answer(question, answer)

        # Fire-and-forget: boost energy on all accumulated nodes and light
        # them up on the Live Pheromone Stream.
        _boosted = list(cumulative_nodes.keys())
        _labels = {nid: getattr(n, "label", "") for nid, n in cumulative_nodes.items()}
        asyncio.create_task(self._cognitive.boost_nodes(_boosted))
        asyncio.create_task(self._publish_pheromone(_boosted, agent_id, _labels))

        result = QueryResult(
            question=question,
            answer=answer,
            direct_answer=direct_answer,
            subgraph=subgraph,
            iterations_used=iteration_count,
            re_query_happened=(iteration_count > 1),
            confidence_score=verifier_result.confidence if verifier_result else 1.0,
            verifier_feedback=all_feedback if all_feedback else None,
        )

        await self._cache_set(result, agent_id)

        logger.info(
            "query_nl | agent=%s engine=%s swarm=%s iter=%d re_query=%s confidence=%.2f",
            agent_id, engine_version, is_swarm,
            result.iterations_used, result.re_query_happened, result.confidence_score,
        )
        return result

    async def stream_query_nl(
        self, question: str, agent_id: str, top_k: Optional[int] = None
    ) -> AsyncIterator[str]:
        """
        Streaming swarm-RAG — V1 or V2 routing based on engine_version.
        Yields tokens as they arrive; V2 enriches context before streaming.
        """
        import asyncio

        cached = await self._cache_get(question, agent_id)
        if cached:
            yield cached.answer
            return

        q_embedding = await self._embedding.embed(question)

        engine_version, agent_clearance, seed_nodes = await asyncio.gather(
            self._get_engine_version(),
            self._get_agent_clearance(agent_id),
            self._graph.vector_search(
                embedding=q_embedding, agent_id=agent_id, top_k=top_k or _DEFAULT_TOP_K
            ),
        )

        if engine_version == "v2":
            swarm_context, involved_agents = await self.retrieve_swarm_context_v2(
                target_agent_id=agent_id,
                query=question,
                agent_clearance=agent_clearance,
                top_k=top_k,
            )
        else:
            swarm_context, involved_agents = await self.retrieve_swarm_context(
                target_agent_id=agent_id,
                query=question,
                agent_clearance=agent_clearance,
                top_k=top_k,
            )

        if not seed_nodes:
            seed_nodes = await self._graph.search_nodes(
                query=question[:50], agent_id=agent_id, limit=top_k or _DEFAULT_TOP_K
            )

        seed_nodes = [
            n for n in seed_nodes
            if int((n.properties or {}).get("clearance_level", _DEFAULT_CLEARANCE))
            <= agent_clearance
        ]

        subgraph_nodes: dict[str, Node] = {}
        subgraph_edges: dict[str, Edge] = {}
        if seed_nodes:
            expansions = await asyncio.gather(
                *[self._graph.get_subgraph(n.id, depth=1) for n in seed_nodes],
                return_exceptions=True,
            )
            for exp in expansions:
                if isinstance(exp, Exception):
                    continue
                for n in exp.nodes:
                    node_clearance = int(
                        (n.properties or {}).get("clearance_level", _DEFAULT_CLEARANCE)
                    )
                    if node_clearance <= agent_clearance:
                        subgraph_nodes[n.id] = n
                for e in exp.edges:
                    if engine_version == "v2":
                        src = subgraph_nodes.get(e.source_id)
                        if _synaptic_score_py(e.utility_weight, src) < _V2_FORGET_THRESHOLD:
                            continue
                    subgraph_edges[e.id] = e

        valid_node_ids = set(subgraph_nodes.keys())
        subgraph_edges = {
            eid: e for eid, e in subgraph_edges.items()
            if e.source_id in valid_node_ids and e.target_id in valid_node_ids
        }

        subgraph = GraphPayload(
            nodes=list(subgraph_nodes.values()),
            edges=list(subgraph_edges.values()),
        )

        # Light up the touched nodes on the Live Pheromone Stream and boost their
        # ACT-R activation, mirroring the non-streaming query_nl path. The studio
        # queries via /query/stream, so without this the pheromone feed never
        # updates even though the same nodes are retrieved. Fire-and-forget.
        _boosted = list(valid_node_ids)
        _labels = {nid: getattr(n, "label", "") for nid, n in subgraph_nodes.items()}
        asyncio.create_task(self._cognitive.boost_nodes(_boosted))
        asyncio.create_task(self._publish_pheromone(_boosted, agent_id, _labels))

        vector_context = self._build_context(subgraph)
        if vector_context and vector_context != "No relevant entities found in the knowledge graph.":
            vector_lines = [
                f"[Source: Agent {agent_id}] - {line.lstrip('• ')}"
                for line in vector_context.splitlines()
                if line.strip()
            ]
            merged_context = swarm_context + "\n" + "\n".join(vector_lines)
        else:
            merged_context = swarm_context

        is_swarm = len(involved_agents) > 1
        full_answer = ""
        async for token in self._stream_answer(
            question, merged_context,
            is_swarm=is_swarm,
            v2_mode=(engine_version == "v2"),
        ):
            full_answer += token
            yield token

        result = QueryResult(
            question=question, answer=full_answer, subgraph=subgraph
        )
        await self._cache_set(result, agent_id)

    async def query_cypher(self, cypher: str, agent_id: str) -> list[dict]:
        """Execute a raw read-only Cypher query."""
        self._validate_read_only(cypher)
        async with self._graph._driver.session() as session:
            result = await session.run(cypher, agent_id=agent_id)
            records = await result.data()
        logger.info("query_cypher | agent=%s records=%d", agent_id, len(records))
        return records

    async def traverse(
        self,
        start_node_id: str,
        depth: int,
        relation_filter: Optional[list[str]] = None,
    ) -> GraphPayload:
        """Traverse from a node up to depth hops, optionally filtered by relation type."""
        depth = max(1, min(depth, 10))

        if relation_filter:
            cypher = """
                MATCH path = (start:SpaiderNode {id: $start_id})-[r:RELATION*1..$depth]-(end:SpaiderNode)
                WHERE r.relation IN $rel_filter
                UNWIND nodes(path) AS n
                WITH COLLECT(DISTINCT n) AS all_nodes, path
                UNWIND relationships(path) AS rel
                WITH all_nodes, COLLECT(DISTINCT rel) AS all_rels
                RETURN all_nodes, all_rels
            """
            params: dict = {"start_id": start_node_id, "depth": depth, "rel_filter": relation_filter}
        else:
            cypher = """
                MATCH path = (start:SpaiderNode {id: $start_id})-[*1..$depth]-(end:SpaiderNode)
                UNWIND nodes(path) AS n
                WITH COLLECT(DISTINCT n) AS all_nodes, path
                UNWIND relationships(path) AS rel
                WITH all_nodes, COLLECT(DISTINCT rel) AS all_rels
                RETURN all_nodes, all_rels
            """
            params = {"start_id": start_node_id, "depth": depth}

        async with self._graph._driver.session() as session:
            result = await session.run(cypher, **params)
            record = await result.single()

        if not record:
            return GraphPayload()

        nodes = [GraphService._record_to_node(n) for n in record["all_nodes"]]
        edges = []
        for r in record["all_rels"]:
            src = r.start_node["id"] if hasattr(r, "start_node") else r.nodes[0]["id"]
            tgt = r.end_node["id"] if hasattr(r, "end_node") else r.nodes[1]["id"]
            edges.append(GraphService._record_to_edge(r, src, tgt))
        return GraphPayload(nodes=nodes, edges=edges)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_context(subgraph: GraphPayload) -> str:
        """Convert a subgraph into a compact text context for the LLM.

        B3 + B4 — Hebbian-aware context assembly. Pre-patch, the [:30]
        node cap and [:5] edge cap operated on insertion-ordered
        collections, ignoring Pillar 1 signals entirely. Now:

          • Nodes are sorted by ``synaptic_score`` (descending) before
            the [:30] cap, so the slice keeps the highest-signal nodes
            rather than the earliest-iteration ones. Default 0 when the
            attribute is absent; today the Node Pydantic model does not
            carry this field, so the sort is a stable no-op until
            upstream propagates the score — the hook is in place.
          • Edges are sorted by ``utility_weight`` (descending) before
            the [:5] per-node cap, so high-Hebbian-weight edges win
            attention. Default 1.0 when missing (the Edge schema
            default), so unweighted edges keep prior behavior.

        Both sorts use Python's ``sorted`` (Timsort, stable) so ties
        preserve the prior insertion order.
        """
        if not subgraph.nodes:
            return "No relevant entities found in the knowledge graph."

        # B4 — order edges by utility_weight per source node so the
        # subsequent [:5] cap inside the node loop keeps the strongest
        # Hebbian-reinforced edges, not arbitrary insertion order.
        # Stable sort: ties preserve the original edge sequence.
        node_label: dict[str, str] = {n.id: n.label for n in subgraph.nodes}

        def _edge_weight(e) -> float:
            # An explicit utility_weight of 0.0 is a real (lowest) weight, not
            # "missing" — so we must NOT use `or 1.0`, which (0.0 being falsy)
            # would clobber it to 1.0 and sort the *weakest* edge to the top.
            # Only None/absent falls back to the Edge schema default of 1.0.
            w = getattr(e, "utility_weight", None)
            return w if w is not None else 1.0

        sorted_edges = sorted(subgraph.edges, key=_edge_weight, reverse=True)
        edge_map: dict[str, list[str]] = {}
        for e in sorted_edges:
            tgt = node_label.get(e.target_id, e.target_id)
            edge_map.setdefault(e.source_id, []).append(f"{e.relation} → {tgt}")

        # B3 — order nodes by synaptic_score before the [:30] cap so the
        # slice retains the highest-signal nodes. Uses getattr with a
        # default of 0 because the Node Pydantic model does not currently
        # declare a `synaptic_score` field — when upstream begins to
        # populate it (either as a model field or via properties) the
        # sort becomes load-bearing automatically with no code change.
        sorted_nodes = sorted(
            subgraph.nodes,
            key=lambda n: (
                getattr(n, "synaptic_score", None)
                or (n.properties or {}).get("synaptic_score", 0)
                or 0
            ),
            reverse=True,
        )

        lines: list[str] = []
        for node in sorted_nodes[:_CONTEXT_MAX_NODES]:
            props = node.properties or {}
            desc = node.description or props.get("description", "")
            if desc and len(desc) > MAX_SOURCE_TEXT_CHARS:
                desc = desc[:MAX_SOURCE_TEXT_CHARS] + "..."
            # The raw extracted fact frequently carries the answer itself
            # ("Olivia (CTO) confirmed launch-readiness review") — a node whose
            # description is a generic gloss is useless to synthesis without
            # it. Bounded like description (token economics).
            source_text = props.get("source_text", "") or ""
            if source_text == desc:
                source_text = ""
            elif len(source_text) > MAX_SOURCE_TEXT_CHARS:
                source_text = source_text[:MAX_SOURCE_TEXT_CHARS] + "..."
            rels = "; ".join(edge_map.get(node.id, [])[:5])
            line = f"• {node.label} ({node.type})"
            if desc:
                line += f": {desc}"
            if source_text:
                line += f' — "{source_text}"'
            if rels:
                line += f" | {rels}"
            lines.append(line)

        return "\n".join(lines)

    @staticmethod
    def _build_system_prompt(is_swarm: bool, *, v2_mode: bool = False) -> str:
        v2_addendum = (
            "\n\nThis context was retrieved via the V2 Cognitive Graph engine. "
            "Facts marked with [Strength: X.X] have been historically validated "
            "through repeated successful query cycles — treat them as highly reliable. "
            "Unmarked facts are valid but have less validation history."
        ) if v2_mode else ""

        if is_swarm:
            return (
                "Du bist eine fortgeschrittene Schwarm-KI. "
                "Du hast Zugriff auf das Wissen deines primären Agenten und verbundener Agenten. "
                "Der dir übergebene Kontext enthält zwingend die Quellenangabe in eckigen Klammern "
                "[Source: Agent <id>]. "
                "Wenn du eine Information aus dem Kontext nutzt, MUSST du den Agenten in deiner "
                "Antwort zitieren (z.B. 'Laut dem HR-Agenten…' oder 'Wie das Tech-Gehirn weiß…'). "
                "Antworte präzise und faktentreu. Wenn der Kontext keine Antwort enthält, sage das."
                + v2_addendum
            )
        return (
            "You are a knowledge graph assistant. "
            "Answer the user's question using only the graph data below. "
            "For simple factoid questions (who/what/when/how much), lead with "
            "the bare answer — the entity, date, number, or short phrase — "
            "before any qualification. "
            "For comparison or multi-hop questions (older/younger, more/less, "
            "higher/lower, or anything that requires combining two or more "
            "facts), first state the relevant facts and reason through them "
            "step by step, THEN give the conclusion — never assert a "
            "comparison you have not derived from the data. "
            "Check the Relationships section: the answer "
            "is often the node connected to the question's subject. "
            "Be factual. If the data doesn't contain the answer, say so."
            + v2_addendum
        )

    async def _verify_evidence(
        self, question: str, context_text: str, mode: str = "sufficiency"
    ) -> VerifierResult:
        """Call the LLM to assess context — sufficiency mode (default) or validator mode.

        Two prompt modes, same ``VerifierResult`` schema:

        - ``"sufficiency"`` (default, used by the agentic-QA loop):
          asks whether the accumulated context contains enough information
          to answer the question; returns ``is_sufficient`` + a re-query
          string when not. Used to drive iterative retrieval.

        - ``"validator"`` (used by the synthesis ensemble's Best-of-N
          ranking): scores a proposed answer adversarially — high
          confidence ONLY for verifiably-grounded answers, sharp drops
          for hallucinations, drift, or contradictions. The 1536d OpenAI
          embeddings make baseline retrieval highly accurate, so the
          validator's role is no longer to second-guess retrieval; it's
          to catch synthesis-stage failure modes.

        Uses ``response_format={"type": "json_object"}`` so the provider
        returns a JSON blob, then parses it strictly via
        ``VerifierResult.model_validate_json`` — raw ``json.loads`` is never
        called, satisfying the Pydantic structured-output guardrail.

        On any LLM or parse failure the method returns a safe default with
        ``is_sufficient=True`` to prevent infinite re-query loops.
        """
        if mode == "validator":
            # Adversarial critic for ensemble ranking. The 1536d embeddings
            # make the retrieved context highly accurate by default, so the
            # validator's job is to detect synthesis failures — hallucination,
            # factual drift, logical contradiction — not to second-guess the
            # baseline retrieval. Confidence stays HIGH unless a specific
            # failure is detected and named.
            system_prompt = (
                "You are an adversarial answer Validator for a knowledge-graph QA system. "
                "A proposed answer follows the context, separated by '---'. "
                "Score it strictly. "
                "The retrieved context is the ground truth — do not second-guess it. "
                "Your sole job is to detect synthesis-stage failures.\n\n"
                "Confidence rubric (be hyper-critical):\n"
                "  • 0.90-1.00 — every claim in the proposed answer is directly supported by the context, "
                "the answer is specific (named entities, numbers, dates), and no detail is invented.\n"
                "  • 0.50-0.89 — answer is plausible and mostly grounded but introduces hedging, "
                "over-broad summarisation, or generic phrasing that loses specificity (FACTUAL DRIFT).\n"
                "  • 0.20-0.49 — answer makes at least one claim NOT supported by the context (HALLUCINATION).\n"
                "  • 0.00-0.19 — answer directly contradicts the context (LOGICAL CONTRADICTION), "
                "or omits an unambiguous multi-hop connection the context makes explicit.\n\n"
                "Set is_sufficient=true when confidence ≥ 0.50, false otherwise. "
                "Use missing_information_categories to name the SPECIFIC failure detected "
                "('hallucinated_claim:<claim>', 'drift:over_broad', 'contradiction:<detail>', "
                "'missing_hop:<entity>'). Leave the list empty when no failure is detected. "
                "Set next_search_query=null — the validator never triggers re-retrieval.\n\n"
                "Respond ONLY with a valid JSON object matching this schema:\n"
                '{"is_sufficient": <bool>, "confidence": <float 0.0-1.0>, '
                '"missing_information_categories": [<str>, ...], '
                '"next_search_query": null}'
            )
            user_content = (
                f"Question: {question}\n\n"
                f"Context and Proposed Answer:\n{context_text}\n\n"
                "Score the proposed answer per the rubric. "
                "Default to a high score; only deduct when you can name a specific failure."
            )
        else:
            system_prompt = (
                "You are an evidence sufficiency verifier for a knowledge-graph QA system. "
                "Assess whether the provided context is sufficient to answer the question accurately. "
                "Respond ONLY with a valid JSON object that exactly matches this schema:\n"
                '{"is_sufficient": <bool>, "confidence": <float 0.0-1.0>, '
                '"missing_information_categories": [<str>, ...], '
                '"next_search_query": <str or null>}\n'
                "Set next_search_query to a targeted query string only when is_sufficient is false."
            )
            user_content = (
                f"Question: {question}\n\n"
                f"Context:\n{context_text}\n\n"
                "Is this context sufficient to answer the question? "
                "If not, list the missing information categories and provide a targeted "
                "search query that would surface the missing facts."
            )

        call_kwargs: dict = dict(
            model=settings.litellm_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=0.0,
            max_tokens=256,
            response_format={"type": "json_object"},
            request_timeout=10,
        )
        if settings.llm_base_url:
            call_kwargs["api_base"] = settings.llm_base_url
        if settings.llm_api_key:
            call_kwargs["api_key"] = settings.llm_api_key

        try:
            response = await acompletion_with_retry(**call_kwargs)
            content = response.choices[0].message.content or ""
            # Strictly parsed via Pydantic — never raw json.loads
            return VerifierResult.model_validate_json(content)
        except Exception as exc:
            logger.warning(
                "_verify_evidence LLM call failed (%s) — treating context as sufficient",
                exc,
            )
            # Safe fallback: break the loop rather than spin indefinitely
            return VerifierResult(
                is_sufficient=True,
                confidence=0.5,
                missing_information_categories=[],
                next_search_query=None,
            )

    async def _extract_direct_answer(
        self, question: str, answer: str,
    ) -> Optional[str]:
        """Reduce a prose answer to its bare answer span for factoid questions.

        Factoid answers ("who/what/when/how much/which …") are graded against a
        terse gold string ("Olivia", "headcount freeze", "$90k"), but synthesis
        produces grounded-but-wordy prose ("No additional hiring in Q2;
        headcount freeze pending Series B close") — semantically correct yet
        penalised by token-overlap metrics. This isolates the minimal span,
        in the source's own words, so callers wanting just the fact don't
        re-trim it.

        Skips when the answer already looks terse, is a refusal, or the
        question is open-ended. One cheap, bounded LLM call; fail-safe (returns
        None, never raises, so the prose answer is always available).
        """
        a = (answer or "").strip()
        if not a or len(a.split()) <= 4:
            return None  # already terse — nothing to trim
        if not _FACTOID_RE.match(question.strip().lower()):
            return None  # open-ended question — a single span is meaningless
        low = a.lower()
        if low.startswith(("i don't", "the data", "no information", "not ", "unknown")):
            return None  # refusal — no span to extract

        prompt = (
            "Extract the single minimal answer to the question from the passage "
            "below — the bare entity, name, date, number, or short phrase, using "
            "the passage's own wording. No preamble, no punctuation beyond what's "
            "part of the answer, no explanation. If the passage does not answer "
            "the question, reply exactly: NONE.\n\n"
            f"Question: {question}\n\nPassage: {a}\n\nAnswer:"
        )
        call_kwargs: dict = dict(
            model=settings.litellm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=40,
        )
        if settings.llm_base_url:
            call_kwargs["api_base"] = settings.llm_base_url
        if settings.llm_api_key:
            call_kwargs["api_key"] = settings.llm_api_key
        try:
            resp = await acompletion_with_retry(**call_kwargs)
            span = (resp.choices[0].message.content or "").strip().strip('"')
        except Exception as exc:
            logger.warning("direct-answer extraction failed (prose answer kept): %s", exc)
            return None
        if not span or span.upper() == "NONE" or len(span) > 200:
            return None
        return span

    async def _answer_with_context(
        self,
        question: str,
        context: str,
        *,
        is_swarm: bool = False,
        v2_mode: bool = False,
    ) -> str:
        """Single or Best-of-N ensemble synthesis (V1/V2-aware).

        Fast path (synthesis_ensemble_n == 1, default):
            One deterministic LLM call — identical to the original behaviour,
            zero ensemble overhead.

        Ensemble path (synthesis_ensemble_n > 1):
            1. Generate N candidate answers in parallel at
               synthesis_ensemble_temperature (higher temp → more diversity).
            2. Filter out any Exception objects captured by return_exceptions=True
               so a single flaky API call never aborts the whole gather.
            3. Verify each valid candidate in parallel by appending it to the
               context and calling _verify_evidence — the verifier's confidence
               score acts as the ranking signal.
            4. Return the candidate with the highest confidence_score.
        """
        import asyncio

        base_kwargs: dict = dict(
            model=settings.litellm_model,
            messages=[
                {
                    "role": "system",
                    "content": self._build_system_prompt(is_swarm, v2_mode=v2_mode),
                },
                {
                    "role": "user",
                    "content": f"Graph data:\n{context}\n\nQuestion: {question}",
                },
            ],
            max_tokens=512,
            request_timeout=20,
        )
        if settings.llm_base_url:
            base_kwargs["api_base"] = settings.llm_base_url
        if settings.llm_api_key:
            base_kwargs["api_key"] = settings.llm_api_key

        # ── Fast path: N=1 (default — zero ensemble overhead) ────────────────
        if settings.synthesis_ensemble_n <= 1:
            response = await acompletion_with_retry(**base_kwargs, temperature=0.0)
            return response.choices[0].message.content or "No answer available."

        # ── Ensemble path: Best-of-N generation → verifier ranking ───────────
        n = settings.synthesis_ensemble_n
        ensemble_kwargs = {**base_kwargs, "temperature": settings.synthesis_ensemble_temperature}

        # Step 1: Generate N candidates in parallel.
        # return_exceptions=True: a 502 / rate-limit on one call is captured as
        # an Exception value rather than propagating and cancelling the others.
        raw_results = await asyncio.gather(
            *[acompletion_with_retry(**ensemble_kwargs) for _ in range(n)],
            return_exceptions=True,
        )

        valid_candidates: list[str] = []
        for res in raw_results:
            if isinstance(res, Exception):
                logger.warning(
                    "_answer_with_context ensemble | generation call failed: %s", res
                )
                continue
            text = (res.choices[0].message.content or "").strip()
            if text:
                valid_candidates.append(text)

        if not valid_candidates:
            raise RuntimeError(
                f"Synthesis ensemble: all {n} generation calls failed — "
                "no valid candidates to rank."
            )

        # Step 2: Verify each valid candidate in parallel via the Validator-mode
        # prompt — adversarial critic that defaults to a HIGH score unless it
        # detects a specific synthesis failure (hallucination, drift,
        # contradiction). Stops the ensemble from overriding accurate baseline
        # retrieval with hedged or invented answers on the 1536d embedding
        # stack. The "sufficiency" mode is reserved for the agentic-QA loop.
        async def _verify_candidate(candidate: str) -> tuple[str, VerifierResult]:
            augmented_context = context + "\n\n---\nProposed Answer:\n" + candidate
            result = await self._verify_evidence(
                question, augmented_context, mode="validator"
            )
            return candidate, result

        verify_results = await asyncio.gather(
            *[_verify_candidate(c) for c in valid_candidates],
            return_exceptions=True,
        )

        ranked: list[tuple[str, float]] = []
        for vr in verify_results:
            if isinstance(vr, Exception):
                logger.warning(
                    "_answer_with_context ensemble | verification call failed: %s", vr
                )
                continue
            candidate_text, verifier = vr
            ranked.append((candidate_text, float(verifier.confidence)))

        if not ranked:
            # All verifications failed — surface first valid candidate rather than crash.
            logger.warning(
                "_answer_with_context ensemble | all verifications failed, "
                "returning first valid candidate as fallback"
            )
            return valid_candidates[0]

        # Step 3: Return the highest-confidence candidate.
        best_answer, best_score = max(ranked, key=lambda x: x[1])
        logger.info(
            "_answer_with_context ensemble | n=%d valid=%d ranked=%d best_confidence=%.2f",
            n, len(valid_candidates), len(ranked), best_score,
        )
        return best_answer

    async def _stream_answer(
        self,
        question: str,
        context: str,
        *,
        is_swarm: bool = False,
        v2_mode: bool = False,
    ) -> AsyncIterator[str]:
        """Streaming LLM call — yields tokens as they arrive (V1/V2-aware)."""
        call_kwargs: dict = dict(
            model=settings.litellm_model,
            messages=[
                {
                    "role": "system",
                    "content": self._build_system_prompt(is_swarm, v2_mode=v2_mode),
                },
                {
                    "role": "user",
                    "content": f"Graph data:\n{context}\n\nQuestion: {question}",
                },
            ],
            temperature=0.0,
            max_tokens=512,
            stream=True,
            request_timeout=20,
        )
        if settings.llm_base_url:
            call_kwargs["api_base"] = settings.llm_base_url
        if settings.llm_api_key:
            call_kwargs["api_key"] = settings.llm_api_key

        response = await acompletion_with_retry(**call_kwargs)
        async for chunk in response:
            token = chunk.choices[0].delta.content or ""
            if token:
                yield token

    def _validate_read_only(self, cypher: str) -> None:
        match = _WRITE_KEYWORDS.search(cypher)
        if match:
            raise ValueError(
                f"Write operation '{match.group()}' is not permitted. "
                "Only read-only queries are allowed."
            )

    @staticmethod
    def _records_to_subgraph(records: list[dict]) -> GraphPayload:
        nodes: list[Node] = []
        seen: set[str] = set()
        for rec in records:
            for val in rec.values():
                if isinstance(val, dict) and "id" in val and "label" in val:
                    nid = val["id"]
                    if nid not in seen:
                        nodes.append(Node(
                            id=nid,
                            label=val.get("label", ""),
                            type=val.get("type", "Other"),
                            properties={k: v for k, v in val.items()
                                        if k not in ("id", "label", "type", "embedding")},
                        ))
                        seen.add(nid)
        return GraphPayload(nodes=nodes, edges=[])
