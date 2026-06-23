"""
Semantic Compressor: THE core service.
Uses LiteLLM to extract structured entity/relationship graphs from raw text.
Long texts are split into parallel chunks for faster extraction.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

import redis.asyncio as aioredis

from app.config import settings
from app.lib.litellm_retry import acompletion_with_retry
from app.models.schemas import Edge, GraphPayload, Node, RelationType

# ---------------------------------------------------------------------------
# Phase 1 — Tier-1 Ingestion Patch (INGESTION_ARCHITECTURE.md §2).
#
# Two feature flags, both default OFF so production behavior on merge is
# byte-identical to current main. Operators flip each independently after
# the benchmark canary on bench-hotpotqa-clean shows F1 within σ of the
# pre-flag baseline.
#
#   CLOSED_EDGE_VOCAB_ENABLED=true  → RelationOntologyManager normalizes
#                                     every edge to a canonical verb from
#                                     the RelationType enum (with alias
#                                     map). Unknown verbs are downgraded
#                                     to RELATED_TO. Neutralizes audit
#                                     finding L1 (open edge vocabulary
#                                     polluting Hebbian convergence).
#
#   TOKEN_CHUNKING_ENABLED=true     → _split_chunks switches from
#                                     character arithmetic to tiktoken
#                                     token-aware budgets. Closes audit
#                                     findings L2 (naive char splitting)
#                                     and L10 (no token budget guard).
# ---------------------------------------------------------------------------
_CLOSED_EDGE_VOCAB_ENABLED: bool = (
    os.environ.get("CLOSED_EDGE_VOCAB_ENABLED", "false").lower() == "true"
)
_TOKEN_CHUNKING_ENABLED: bool = (
    os.environ.get("TOKEN_CHUNKING_ENABLED", "false").lower() == "true"
)

# Graceful tiktoken import. The package is NOT in pyproject.toml — if
# absent, the token-chunking path degrades to the legacy character path
# unconditionally and logs a warning on first invocation. This lets the
# Phase 1 patch ship without forcing a dependency bump; operators add
# tiktoken to their image when they enable the flag.
try:
    import tiktoken as _tiktoken  # type: ignore[import-not-found]
    _TIKTOKEN_AVAILABLE: bool = True
except ImportError:
    _tiktoken = None  # type: ignore[assignment]
    _TIKTOKEN_AVAILABLE = False

# ---------------------------------------------------------------------------
# Ontology: canonical node types + alias mapping
# ---------------------------------------------------------------------------

_VALID_TYPES = frozenset({
    "PERSON", "ORGANIZATION", "LOCATION", "EVENT", "CONCEPT",
    "PRODUCT", "TECHNOLOGY", "DATE", "METRIC", "DOCUMENT",
    "TEAM", "ROLE", "PROJECT", "OTHER",
})

# Maps LLM hallucinated / non-standard types → canonical type
_TYPE_ALIASES: dict[str, str] = {
    # Organization variants
    "COMPANY":       "ORGANIZATION",
    "STARTUP":       "ORGANIZATION",
    "CORPORATION":   "ORGANIZATION",
    "FIRM":          "ORGANIZATION",
    "ENTERPRISE":    "ORGANIZATION",
    "INSTITUTION":   "ORGANIZATION",
    "UNIVERSITY":    "ORGANIZATION",
    "SCHOOL":        "ORGANIZATION",
    "GOVERNMENT":    "ORGANIZATION",
    "AGENCY":        "ORGANIZATION",
    "NGO":           "ORGANIZATION",
    "GROUP":         "ORGANIZATION",
    # Location variants
    "COUNTRY":       "LOCATION",
    "CITY":          "LOCATION",
    "PLACE":         "LOCATION",
    "REGION":        "LOCATION",
    "STATE":         "LOCATION",
    "CONTINENT":     "LOCATION",
    "BUILDING":      "LOCATION",
    "VENUE":         "LOCATION",
    # Technology variants
    "TOOL":          "TECHNOLOGY",
    "FRAMEWORK":     "TECHNOLOGY",
    "SOFTWARE":      "TECHNOLOGY",
    "LIBRARY":       "TECHNOLOGY",
    "PLATFORM":      "TECHNOLOGY",
    "LANGUAGE":      "TECHNOLOGY",
    "PROTOCOL":      "TECHNOLOGY",
    "ALGORITHM":     "TECHNOLOGY",
    "MODEL":         "TECHNOLOGY",
    "SYSTEM":        "TECHNOLOGY",
    # Concept variants
    "AWARD":         "CONCEPT",
    "THEORY":        "CONCEPT",
    "IDEA":          "CONCEPT",
    "FIELD":         "CONCEPT",
    "DOMAIN":        "CONCEPT",
    "DISCIPLINE":    "CONCEPT",
    "TOPIC":         "CONCEPT",
    "SUBJECT":       "CONCEPT",
    # Date variants
    "YEAR":          "DATE",
    "MONTH":         "DATE",
    "TIME":          "DATE",
    "PERIOD":        "DATE",
    "ERA":           "DATE",
    "DECADE":        "DATE",
    # Metric variants
    "AMOUNT":        "METRIC",
    "PERCENTAGE":    "METRIC",
    "REVENUE":       "METRIC",
    "FUNDING":       "METRIC",
    "VALUATION":     "METRIC",
    "NUMBER":        "METRIC",
    "QUANTITY":      "METRIC",
    "STATISTIC":     "METRIC",
    # Document variants
    "REPORT":        "DOCUMENT",
    "PAPER":         "DOCUMENT",
    "PUBLICATION":   "DOCUMENT",
    "BOOK":          "DOCUMENT",
    "ARTICLE":       "DOCUMENT",
    "STUDY":         "DOCUMENT",
    "RESEARCH":      "DOCUMENT",
    "PATENT":        "DOCUMENT",
}


class OntologyManager:
    """
    Enforces a strict set of canonical node types.
    Converts LLM-hallucinated types (e.g. 'STARTUP', 'COUNTRY') to
    their canonical equivalents before entities reach the graph.
    """

    @staticmethod
    def normalize_type(raw_type: str) -> str:
        upper = (raw_type or "OTHER").upper().strip()
        if upper in _VALID_TYPES:
            return upper
        if upper in _TYPE_ALIASES:
            mapped = _TYPE_ALIASES[upper]
            logger.debug("OntologyManager: '%s' → '%s'", raw_type, mapped)
            return mapped
        logger.debug("OntologyManager: unknown type '%s' → 'OTHER'", raw_type)
        return "OTHER"

    @staticmethod
    def enforce(payload: GraphPayload) -> GraphPayload:
        """Normalize all node types in a payload in-place and return it."""
        corrections = 0
        for node in payload.nodes:
            normalized = OntologyManager.normalize_type(node.type)
            if normalized != node.type:
                corrections += 1
            node.type = normalized
        if corrections:
            logger.info("OntologyManager: corrected %d node type(s)", corrections)
        return payload


# ---------------------------------------------------------------------------
# Closed edge vocabulary (Goal A of the Tier-1 Ingestion Patch).
#
# Canonical set seeded from RelationType in app/models/schemas.py — the
# enum is the source of truth, so extending it (PR to schemas.py) extends
# this set automatically without touching this file. Wrapping with set()
# dedupes the legacy synonyms in the enum (FOUNDED/FOUNDED_BY,
# IS_CEO_OF/CEO_OF) for the membership check.
# ---------------------------------------------------------------------------
_VALID_RELATIONS: frozenset[str] = frozenset({r.value for r in RelationType})

# Maps LLM-hallucinated / non-standard verbs → canonical. Patterns:
#   • Past tense → present (LED → LEADS)
#   • Active → passive variants (FOUNDS → FOUNDED, ACQUIRED_BY → ACQUIRED)
#   • Verb synonyms (BUILT/DEVELOPED/INVENTED → CREATED)
#   • Wordy variants (WAS_FOUNDED_BY → FOUNDED_BY)
#   • Generic association (LINKED_TO/CONNECTED_TO → RELATED_TO)
# This dict is intentionally extensible: new entries get added as
# production telemetry surfaces fresh hallucinations.
_RELATION_ALIASES: dict[str, str] = {
    # Employment / membership
    "WORKED_AT":          "WORKS_AT",
    "EMPLOYED_BY":        "WORKS_AT",
    "EMPLOYEE_OF":        "WORKS_AT",
    "WORKS_FOR":          "WORKS_AT",
    "JOINED":             "WORKS_AT",
    # Leadership
    "LED":                "LEADS",
    "LEADING":            "LEADS",
    "HEADS":              "LEADS",
    "HEADED":             "LEADS",
    "MANAGES":            "LEADS",
    "MANAGED":            "LEADS",
    "REPORTS":            "REPORTS_TO",
    "REPORTED_TO":        "REPORTS_TO",
    # Founders + CEOs (RelationType enum has both IS_CEO_OF + CEO_OF;
    # canonical the verbose variant)
    "IS_CEO_OF":          "CEO_OF",
    "WAS_CEO_OF":         "CEO_OF",
    "CHIEF_EXECUTIVE_OF": "CEO_OF",
    "WAS_FOUNDED_BY":     "FOUNDED_BY",
    "CO_FOUNDED_BY":      "FOUNDED_BY",
    "FOUNDS":             "FOUNDED",
    "CO_FOUNDED":         "FOUNDED",
    "ESTABLISHED":        "FOUNDED",
    # Creation
    "BUILT":              "CREATED",
    "DEVELOPED":          "CREATED",
    "INVENTED":           "CREATED",
    "DESIGNED":           "CREATED",
    "WROTE":              "CREATED",
    "AUTHORED":           "CREATED",
    "MADE":               "CREATED",
    "PRODUCED":           "CREATED",
    "RELEASED":           "CREATED",
    # Collaboration
    "COLLABORATED_WITH":  "COLLABORATES_WITH",
    "PARTNERED_WITH":     "COLLABORATES_WITH",
    "WORKED_WITH":        "COLLABORATES_WITH",
    "ALLIED_WITH":        "COLLABORATES_WITH",
    # Usage / dependency
    "USED":               "USES",
    "USING":              "USES",
    "UTILIZES":           "USES",
    "RELIES_ON":          "DEPENDS_ON",
    "DEPENDS":            "DEPENDS_ON",
    "REQUIRES":           "DEPENDS_ON",
    # Composition / containment
    "HAS":                "CONTAINS",
    "INCLUDES":           "CONTAINS",
    "INCLUDED":           "CONTAINS",
    "COMPRISES":          "CONTAINS",
    "MEMBER_OF":          "PART_OF",
    "BELONGS_TO":         "PART_OF",
    "BRANCH_OF":          "PART_OF",
    "DIVISION_OF":        "PART_OF",
    # Location
    "LOCATED":            "LOCATED_IN",
    "BASED_IN":           "LOCATED_IN",
    "HEADQUARTERED_IN":   "LOCATED_IN",
    "SITUATED_IN":        "LOCATED_IN",
    "IS_IN":              "LOCATED_IN",
    # Causality
    "CAUSED":             "CAUSED_BY",
    "RESULTED_FROM":      "CAUSED_BY",
    "DUE_TO":             "CAUSED_BY",
    "BLOCKED":            "BLOCKED_BY",
    "BLOCKS":             "BLOCKED_BY",
    "PREVENTED_BY":       "BLOCKED_BY",
    # Approval
    "APPROVED_BY":        "APPROVED",
    "ENDORSED":           "APPROVED",
    "RATIFIED":           "APPROVED",
    # Funding / acquisition
    "FUNDED":             "FUNDED_BY",
    "INVESTED_IN":        "FUNDED_BY",
    "BACKED_BY":          "FUNDED_BY",
    "ACQUIRED_BY":        "ACQUIRED",
    "BOUGHT":             "ACQUIRED",
    "PURCHASED":          "ACQUIRED",
    # Competition
    "COMPETES_WITH":      "COMPETING_WITH",
    "COMPETED_WITH":      "COMPETING_WITH",
    "RIVAL_OF":           "COMPETING_WITH",
    # Temporal sequence
    "SUCCEEDED":          "PRECEDED_BY",
    "REPLACED":           "PRECEDED_BY",
    "CAME_AFTER":         "PRECEDED_BY",
    "FOLLOWED":           "FOLLOWED_BY",
    "CAME_BEFORE":        "FOLLOWED_BY",
    "SUCCEEDED_BY":       "FOLLOWED_BY",
    # Generic association (catch-all hallucinations)
    "RELATED":            "RELATED_TO",
    "LINKED_TO":          "RELATED_TO",
    "CONNECTED_TO":       "RELATED_TO",
    "ASSOCIATED_WITH":    "RELATED_TO",
    "TIED_TO":            "RELATED_TO",
    "REFERENCES":         "RELATED_TO",
    "MENTIONS":           "RELATED_TO",
}


class RelationOntologyManager:
    """
    Enforces a strict set of canonical edge relation verbs.

    Mirrors ``OntologyManager`` for nodes. Converts LLM hallucinations
    (``COLLABORATED_WITH``, ``WAS_FOUNDED_BY``, ``BUILT``) to canonical
    verbs from the ``RelationType`` enum. Unknown verbs fall through to
    ``RELATED_TO`` — the safe catch-all that preserves the edge while
    quarantining it from the high-signal canonical attractor basins.

    Why this exists (Goal A, INGESTION_ARCHITECTURE.md §2):
    Without normalization, every novel verb the LLM invents starts at
    ``utility_weight = 1.0`` and lives forever in its own attractor.
    Hebbian feedback never converges across synonyms. With this
    manager, ``Alice -[CREATED]-> X`` from document A and
    ``Alice -[BUILT]-> X`` from document B both collapse onto a single
    ``CREATED`` edge, allowing utility weight to accumulate against a
    single canonical verb rather than splitting across hallucinated
    synonyms.
    """

    @staticmethod
    def normalize_relation(raw_relation: str) -> str:
        upper = (raw_relation or "RELATED_TO").upper().strip()
        if upper in _VALID_RELATIONS:
            return upper
        if upper in _RELATION_ALIASES:
            mapped = _RELATION_ALIASES[upper]
            logger.debug("RelationOntologyManager: '%s' → '%s'", raw_relation, mapped)
            return mapped
        # Strip leading copula/auxiliary prefixes the LLM tacks on
        # (IS_HEADQUARTERED_IN, WAS_LOCATED_IN, ARE_PART_OF, ...) then re-check.
        for _prefix in ("IS_", "WAS_", "ARE_", "WERE_", "BEEN_", "BE_"):
            if upper.startswith(_prefix):
                stripped = upper[len(_prefix):]
                if stripped in _VALID_RELATIONS:
                    return stripped
                if stripped in _RELATION_ALIASES:
                    return _RELATION_ALIASES[stripped]
                break
        logger.debug(
            "RelationOntologyManager: unknown relation '%s' → 'RELATED_TO'",
            raw_relation,
        )
        return "RELATED_TO"

    @staticmethod
    def enforce(payload: GraphPayload) -> GraphPayload:
        """Normalize all edge relation verbs in a payload in-place and return it.

        Logs at INFO when any corrections are applied so the cost of L1
        (today invisible because edges are accepted as-is) becomes a
        measurable signal in the analytics dashboard once the flag is on.
        """
        corrections = 0
        for edge in payload.edges:
            normalized = RelationOntologyManager.normalize_relation(edge.relation)
            if normalized != edge.relation:
                corrections += 1
            edge.relation = normalized
        if corrections:
            logger.info(
                "RelationOntologyManager: corrected %d edge relation(s) "
                "(closed_edge_vocab=true)",
                corrections,
            )
        return payload


# LLM response cache TTL (24 h)
_LLM_CACHE_TTL = 86_400

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "compressor_system.txt"
_MAX_RETRIES = 3


class ExtractionError(RuntimeError):
    """
    Raised when the LLM fails to return a parseable, schema-valid
    GraphPayload after every retry exhausts.

    The previous behaviour of returning an empty `GraphPayload()` silently
    corrupted both the sync ingest response (200 OK with zero nodes) and
    the Kafka consumer (offset committed, nothing routed to DLQ — data
    permanently dropped). Raising lets callers decide: sync ingest returns
    422, the Kafka worker routes the message to the DLQ with headers, and
    analytics records a `extraction_failed` event.
    """

    def __init__(
        self,
        message: str,
        *,
        attempts: int,
        last_error: Optional[str] = None,
        last_raw: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.last_error = last_error
        # Preview a truncated sample of the last LLM response so DLQ
        # consumers can diagnose without re-running the call.
        self.last_raw_preview = (last_raw or "")[:500]


class SemanticCompressor:
    """
    Extracts a GraphPayload (nodes + edges) from arbitrary text using an LLM.

    Long texts are automatically chunked and extracted in parallel, then merged.
    Retry logic: up to _MAX_RETRIES attempts per chunk with self-correction.
    """

    # Legacy character chunking (used when TOKEN_CHUNKING_ENABLED=false or
    # when tiktoken is unavailable). Overlap avoids cutting mid-sentence.
    _CHUNK_SIZE = 1200
    _CHUNK_OVERLAP = 150
    _MAX_PARALLEL = 6

    # Token chunking (Goal B). Used when TOKEN_CHUNKING_ENABLED=true AND
    # tiktoken is importable AND an encoder for settings.litellm_model can
    # be resolved. Budget is the per-chunk token count handed to the LLM;
    # overlap is added back to the next chunk's window to preserve sentence
    # context across cuts. Defaults chosen to match the docstring estimate
    # for the legacy character path (1200 chars ≈ 500 English tokens).
    _TOKEN_CHUNK_SIZE: int = int(os.environ.get("TOKEN_CHUNK_SIZE", "500"))
    _TOKEN_OVERLAP: int = int(os.environ.get("TOKEN_OVERLAP", "50"))

    def __init__(self) -> None:
        self._system_prompt: str = self._load_system_prompt()
        self._redis: Optional[aioredis.Redis] = None

    def _get_redis(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = aioredis.from_url(
                settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_timeout=2,
                socket_connect_timeout=2,
            )
        return self._redis

    def _cache_key(self, text: str) -> str:
        digest = hashlib.sha256(
            f"{text}:{settings.litellm_model}".encode("utf-8")
        ).hexdigest()
        return f"spaider:cache:llm:{digest}"

    async def _get_cached_payload(self, text: str) -> Optional[GraphPayload]:
        try:
            raw = await self._get_redis().get(self._cache_key(text))
            if raw:
                data = json.loads(raw)
                payload = self._build_payload(data)
                logger.info("LLM cache HIT for text length=%d", len(text))
                return payload
        except Exception as exc:
            logger.debug("LLM cache GET failed (non-fatal): %s", exc)
        return None

    async def _set_cached_payload(self, text: str, payload: GraphPayload) -> None:
        try:
            data = {
                "nodes": [
                    {"label": n.label, "type": n.type, "properties": n.properties}
                    for n in payload.nodes
                ],
                "edges": [
                    {"source": n.label, "target": t.label, "relation": e.relation, "properties": e.properties}
                    for e in payload.edges
                    for n in payload.nodes if n.id == e.source_id
                    for t in payload.nodes if t.id == e.target_id
                ],
            }
            await self._get_redis().set(
                self._cache_key(text), json.dumps(data), ex=_LLM_CACHE_TTL
            )
            logger.debug("LLM cache SET for text length=%d (TTL=%ds)", len(text), _LLM_CACHE_TTL)
        except Exception as exc:
            logger.debug("LLM cache SET failed (non-fatal): %s", exc)

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _load_system_prompt(self) -> str:
        try:
            return _PROMPT_PATH.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.warning(
                "Compressor system prompt not found at %s. Using default.", _PROMPT_PATH
            )
            return (
                "You are a knowledge-graph extraction assistant. "
                "Given a passage of text, extract all named entities and the relationships "
                "between them. Return ONLY a JSON object with two keys:\n"
                '  "nodes": list of {label, type, properties} objects\n'
                '  "edges": list of {source, target, relation, properties} objects\n'
                "where source/target refer to node labels. "
                "Do NOT include any markdown, commentary, or extra text."
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def extract(
        self,
        text: str,
        context: Optional[dict] = None,
    ) -> GraphPayload:
        """
        Extract a GraphPayload from raw text.
        Long texts are split into overlapping chunks extracted in parallel,
        then merged — much faster than one sequential LLM call.
        """
        text = (text or "").strip()
        if not text:
            logger.info("SemanticCompressor: received empty text, returning empty payload.")
            return GraphPayload()

        # ── Redis LLM cache check (skip API call if identical text seen before) ──
        cached = await self._get_cached_payload(text)
        if cached is not None:
            cached = OntologyManager.enforce(cached)
            if _CLOSED_EDGE_VOCAB_ENABLED:
                cached = RelationOntologyManager.enforce(cached)
            return cached

        context_note = ""
        if context:
            context_note = f"\n\n[Context: {json.dumps(context)}]"

        chunks = self._split_chunks(text)
        t0 = time.perf_counter()

        if len(chunks) == 1:
            msg = (
                f"Extract entities and relationships from the following text:"
                f"{context_note}\n\n{chunks[0]}"
            )
            # Single-chunk path: any ExtractionError propagates directly.
            payload, _ = await self._extract_with_retry(msg)
        else:
            logger.info(
                "SemanticCompressor: %d chars → %d parallel chunks", len(text), len(chunks)
            )
            all_payloads: list[GraphPayload] = []
            chunk_failures: list[Exception] = []
            # Process in batches of _MAX_PARALLEL
            for i in range(0, len(chunks), self._MAX_PARALLEL):
                batch = chunks[i : i + self._MAX_PARALLEL]
                tasks = [
                    self._extract_with_retry(
                        f"Extract entities and relationships from the following text:"
                        f"{context_note}\n\n{chunk}"
                    )
                    for chunk in batch
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for r in results:
                    if isinstance(r, Exception):
                        logger.warning("Chunk extraction failed: %s", r)
                        chunk_failures.append(r)
                    else:
                        all_payloads.append(r[0])

            # If every single chunk failed we must not silently return an
            # empty payload — escalate so the caller can 422 / DLQ. Partial
            # success is still tolerated: losing one chunk out of many is a
            # degraded result, not a dropped message.
            if not all_payloads and chunk_failures:
                first = chunk_failures[0]
                if isinstance(first, ExtractionError):
                    raise first
                raise ExtractionError(
                    f"All {len(chunks)} chunks failed extraction: {first}",
                    attempts=_MAX_RETRIES,
                    last_error=str(first),
                )

            payload = self._merge_payloads(all_payloads)

        latency = time.perf_counter() - t0

        # ── Ontology enforcement: normalize hallucinated node types ───────────
        payload = OntologyManager.enforce(payload)

        # ── Edge ontology enforcement (Goal A, gated by feature flag) ────────
        # When CLOSED_EDGE_VOCAB_ENABLED=true, every edge verb is collapsed
        # onto a canonical RelationType. Default-off preserves byte-identical
        # pre-patch behavior. Operators flip on after the canary confirms no
        # F1 regression vs the open-vocabulary baseline.
        if _CLOSED_EDGE_VOCAB_ENABLED:
            payload = RelationOntologyManager.enforce(payload)

        logger.info(
            "SemanticCompressor extract | chars=%d chunks=%d nodes=%d edges=%d latency=%.2fs",
            len(text), len(chunks), len(payload.nodes), len(payload.edges), latency,
        )

        # ── Cache the result for future identical requests ────────────────────
        # Only cache non-empty payloads: caching an empty result would make
        # every subsequent call for the same text silently return zero nodes.
        if payload.nodes:
            await self._set_cached_payload(text, payload)

        return payload

    # ------------------------------------------------------------------
    # Chunking & merging
    # ------------------------------------------------------------------

    def _split_chunks(self, text: str) -> list[str]:
        """Split text into overlapping chunks. Dispatches between two paths.

        When TOKEN_CHUNKING_ENABLED=true AND tiktoken is importable AND an
        encoder can be resolved for the active model, use the token-aware
        path (Goal B — closes audit L2 + L10). Otherwise fall back to the
        legacy character path, which is byte-identical to pre-patch main.

        The token path degrades gracefully through three fallbacks:
          1. Encoder for ``settings.litellm_model`` via ``encoding_for_model``
          2. ``cl100k_base`` (the OpenAI default for unknown models)
          3. Character path (logged at WARNING, one-time)

        Any failure inside the token path falls back to characters so a
        broken encoder cannot break ingestion.
        """
        if _TOKEN_CHUNKING_ENABLED and _TIKTOKEN_AVAILABLE:
            try:
                return self._split_chunks_token_aware(text)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Token-aware chunking failed (%s) — falling back to "
                    "character path for this call.", exc,
                )
                # fall through to character path
        elif _TOKEN_CHUNKING_ENABLED and not _TIKTOKEN_AVAILABLE:
            # Log once per process invocation rather than per call — the
            # logger module-level dedup is sufficient for ops awareness.
            logger.warning(
                "TOKEN_CHUNKING_ENABLED=true but tiktoken is not installed. "
                "Falling back to character chunking. Add 'tiktoken>=0.7' to "
                "pyproject.toml dependencies and rebuild to enable the "
                "token-aware path."
            )
        return self._split_chunks_char_legacy(text)

    def _split_chunks_char_legacy(self, text: str) -> list[str]:
        """Legacy character chunking. Preserved unchanged from pre-patch."""
        if len(text) <= self._CHUNK_SIZE:
            return [text]
        chunks: list[str] = []
        start = 0
        while start < len(text):
            end = min(start + self._CHUNK_SIZE, len(text))
            chunk = text[start:end]
            # Break at last sentence boundary in the chunk
            if end < len(text):
                last_period = chunk.rfind(". ")
                if last_period > self._CHUNK_SIZE // 2:
                    chunk = chunk[: last_period + 1]
            chunk = chunk.strip()
            if chunk:
                chunks.append(chunk)
            advance = max(len(chunk) - self._CHUNK_OVERLAP, 1)
            start += advance
        return chunks

    # Sentence-ending punctuation across languages. Token-path snap walks
    # the decoded chunk text backward looking for the last occurrence past
    # the midpoint of the chunk; this set covers Latin + CJK + Arabic.
    _SENTENCE_TERMINATORS: tuple[str, ...] = (
        ". ", ".\n", "? ", "?\n", "! ", "!\n",
        "。", "？", "！",        # CJK
        "؟", "۔",                  # Arabic + Urdu
    )

    def _resolve_encoder(self):
        """Return a tiktoken encoder for the active model, or None on failure.

        Cached on the instance so we don't re-resolve on every call. Two
        fallbacks: model-specific encoder, then cl100k_base, then None.
        """
        if getattr(self, "_token_encoder", None) is not None:
            return self._token_encoder
        if not _TIKTOKEN_AVAILABLE:
            return None
        encoder = None
        try:
            encoder = _tiktoken.encoding_for_model(settings.litellm_model)
        except KeyError:
            # Model name not in tiktoken's registry (Anthropic, Ollama,
            # most non-OpenAI providers). Fall back to the OpenAI default.
            try:
                encoder = _tiktoken.get_encoding("cl100k_base")
                logger.info(
                    "tiktoken: no encoder for model '%s', using cl100k_base.",
                    settings.litellm_model,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "tiktoken: failed to load cl100k_base fallback: %s", exc,
                )
                return None
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "tiktoken: encoder resolution failed for '%s': %s",
                settings.litellm_model, exc,
            )
            return None
        self._token_encoder = encoder
        return encoder

    def _split_chunks_token_aware(self, text: str) -> list[str]:
        """Token-aware overlapping chunker with sentence-boundary snap.

        Algorithm:
          1. Encode the whole text once.
          2. Walk in stride ``_TOKEN_CHUNK_SIZE - _TOKEN_OVERLAP``.
          3. For each window, decode → look for last sentence terminator
             past the midpoint of the decoded text → snap there if found.
          4. Re-encode the trimmed chunk to compute the next stride.
          5. Repeat until the encoded position covers the whole text.

        Guarantees:
          • Every emitted chunk has a known token count, never exceeding
            ``_TOKEN_CHUNK_SIZE``.
          • Non-English / multibyte corpora tokenize correctly (closes L2).
          • Per-model budget enforcement happens at the chunking layer,
            not at provider-error time (closes L10).
        """
        encoder = self._resolve_encoder()
        if encoder is None:
            return self._split_chunks_char_legacy(text)

        token_budget = self._TOKEN_CHUNK_SIZE
        overlap = self._TOKEN_OVERLAP

        all_tokens = encoder.encode(text)
        if len(all_tokens) <= token_budget:
            return [text]

        chunks: list[str] = []
        cursor = 0
        total = len(all_tokens)
        while cursor < total:
            window_end = min(cursor + token_budget, total)
            window_tokens = all_tokens[cursor:window_end]
            decoded = encoder.decode(window_tokens)

            # Sentence-boundary snap — only meaningful when there is more
            # text after this window; the final chunk takes the full
            # remainder verbatim regardless of where the last sentence
            # ends.
            if window_end < total:
                midpoint = len(decoded) // 2
                best_cut = -1
                for terminator in self._SENTENCE_TERMINATORS:
                    pos = decoded.rfind(terminator)
                    # +len(terminator) so the chunk ends just past the
                    # punctuation (matching legacy char-path semantics
                    # of "chunk = chunk[: last_period + 1]" which kept
                    # the period and the space).
                    if pos > midpoint and (pos + len(terminator)) > best_cut:
                        best_cut = pos + len(terminator)
                if best_cut > 0:
                    decoded = decoded[:best_cut]
                    # Re-encode to know the actual token count consumed.
                    window_tokens = encoder.encode(decoded)

            decoded = decoded.strip()
            if decoded:
                chunks.append(decoded)

            consumed = len(window_tokens)
            # Advance by consumed - overlap, with minimum of 1 so a
            # pathological zero-token chunk (e.g., terminator-only) cannot
            # produce an infinite loop.
            advance = max(consumed - overlap, 1)
            cursor += advance

        return chunks

    @staticmethod
    def _merge_payloads(payloads: list[GraphPayload]) -> GraphPayload:
        """Merge multiple payloads, deduplicating nodes by label (case-insensitive)."""
        seen_labels: dict[str, str] = {}  # lower_label -> canonical_id
        merged_nodes: list[Node] = []
        id_remap: dict[str, str] = {}

        for payload in payloads:
            for node in payload.nodes:
                key = node.label.lower()
                if key in seen_labels:
                    id_remap[node.id] = seen_labels[key]
                else:
                    seen_labels[key] = node.id
                    merged_nodes.append(node)

        merged_edges: list[Edge] = []
        seen_edge_keys: set[tuple[str, str, str]] = set()
        for payload in payloads:
            for edge in payload.edges:
                src = id_remap.get(edge.source_id, edge.source_id)
                tgt = id_remap.get(edge.target_id, edge.target_id)
                if src == tgt:
                    continue
                ekey = (src, tgt, edge.relation)
                if ekey not in seen_edge_keys:
                    seen_edge_keys.add(ekey)
                    merged_edges.append(
                        Edge(
                            id=edge.id,
                            source_id=src,
                            target_id=tgt,
                            relation=edge.relation,
                            properties=edge.properties,
                        )
                    )

        return GraphPayload(nodes=merged_nodes, edges=merged_edges)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _extract_with_retry(
        self,
        user_message: str,
    ) -> tuple[GraphPayload, dict]:
        """
        Call the LLM up to _MAX_RETRIES times, feeding back validation errors
        to allow the model to self-correct.
        """
        conversation: list[dict] = [{"role": "user", "content": user_message}]
        last_error: Optional[str] = None
        last_raw: str = ""
        token_usage: dict = {}

        for attempt in range(1, _MAX_RETRIES + 1):
            if last_error and attempt > 1:
                conversation.append({"role": "assistant", "content": last_raw})
                conversation.append(
                    {
                        "role": "user",
                        "content": (
                            f"Your previous response was invalid. Error: {last_error}\n"
                            "Please fix the JSON and return the FULL, corrected JSON object. "
                            "Do NOT cut off the end of the JSON object."
                        ),
                    }
                )

            call_kwargs: dict = dict(
                model=settings.litellm_model,
                messages=[
                    {"role": "system", "content": self._system_prompt},
                    *conversation,
                ],
                temperature=0.0,
                max_tokens=4000,
            )
            if settings.llm_base_url:
                call_kwargs["api_base"] = settings.llm_base_url
            if settings.llm_api_key:
                call_kwargs["api_key"] = settings.llm_api_key

            try:
                response = await asyncio.wait_for(
                    acompletion_with_retry(**call_kwargs),
                    timeout=120,
                )
            except Exception as exc:
                logger.error(
                    "LLM call failed on attempt %d/%d: %s", attempt, _MAX_RETRIES, exc
                )
                if attempt == _MAX_RETRIES:
                    break
                await asyncio.sleep(2 ** (attempt - 1))
                last_error = str(exc)
                last_raw = ""
                continue

            if response.usage:
                token_usage = {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                }

            last_raw = response.choices[0].message.content or ""
            raw = self._strip_markdown(last_raw)

            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                last_error = f"JSON parse error: {exc}"
                logger.warning("Attempt %d/%d: JSON parse error: %s", attempt, _MAX_RETRIES, exc)
                continue

            try:
                payload = self._build_payload(data)
                return payload, token_usage
            except (KeyError, TypeError, ValueError) as exc:
                last_error = f"Validation error: {exc}"
                logger.warning("Attempt %d/%d: validation error: %s", attempt, _MAX_RETRIES, exc)
                continue

        logger.error(
            "SemanticCompressor: all %d attempts failed (last error: %s). "
            "Raising ExtractionError so the caller can DLQ / 422 appropriately.",
            _MAX_RETRIES,
            last_error,
        )
        raise ExtractionError(
            f"LLM extraction failed after {_MAX_RETRIES} attempts: {last_error}",
            attempts=_MAX_RETRIES,
            last_error=last_error,
            last_raw=last_raw,
        )

    @staticmethod
    def _strip_markdown(raw: str) -> str:
        """Remove markdown code fences and surrounding text from LLM output."""
        raw = raw.strip()
        # Remove common markdown code fences
        if raw.startswith("```"):
            first_newline = raw.find("\n")
            if first_newline != -1:
                raw = raw[first_newline + 1:]
            if raw.endswith("```"):
                raw = raw[:-3]

        raw = raw.strip()

        # Try to find the outermost JSON object bounds if there's still junk text
        start_idx = raw.find("{")
        end_idx = raw.rfind("}")

        if start_idx != -1 and end_idx != -1 and end_idx >= start_idx:
            raw = raw[start_idx:end_idx + 1]

        return raw

    @staticmethod
    def _build_payload(data: dict) -> GraphPayload:
        """Construct a validated GraphPayload from the parsed LLM JSON dict."""
        label_to_id: dict[str, str] = {}
        nodes: list[Node] = []

        for n in data.get("nodes", []):
            node = Node(
                label=str(n["label"]),
                type=str(n.get("type", "Other")),
                properties=dict(n.get("properties", {})),
            )
            nodes.append(node)
            label_to_id[n["label"]] = node.id

        edges: list[Edge] = []
        for e in data.get("edges", []):
            src_label = e.get("source")
            tgt_label = e.get("target")
            if src_label not in label_to_id or tgt_label not in label_to_id:
                logger.debug(
                    "Skipping edge (%s -> %s): unknown node label.", src_label, tgt_label
                )
                continue
            edge = Edge(
                source_id=label_to_id[src_label],
                target_id=label_to_id[tgt_label],
                relation=str(e.get("relation", "RELATED_TO")),
                properties=dict(e.get("properties", {})),
            )
            edges.append(edge)

        return GraphPayload(nodes=nodes, edges=edges)
