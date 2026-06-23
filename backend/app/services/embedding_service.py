"""
Embedding Service: API-based embeddings via LiteLLM with Redis caching.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
from typing import Optional

import litellm
import redis.asyncio as aioredis

from app.config import settings

logger = logging.getLogger(__name__)

_CACHE_TTL = 86400  # 24 hours in seconds


class EmbeddingService:
    """
    Generates text embeddings using LiteLLM (text-embedding-3-small by default).
    Results are cached in Redis by SHA-256(text + model) with a 24-hour TTL.
    """

    def __init__(self) -> None:
        self._redis: Optional[aioredis.Redis] = None
        self._model = settings.litellm_embedding_model
        self._api_key = settings.embedding_api_key
        self._api_base = settings.embedding_base_url

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def _get_redis(self) -> aioredis.Redis:
        """Lazy-initialize Redis connection."""
        if self._redis is None:
            self._redis = aioredis.from_url(
                settings.redis_url,
                encoding="utf-8",
                decode_responses=False,
                socket_timeout=2,
                socket_connect_timeout=2,
            )
        return self._redis

    async def close(self) -> None:
        """Close the Redis connection."""
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------

    def _cache_key(self, text: str) -> str:
        digest = hashlib.sha256(f"{text}:{self._model}".encode("utf-8")).hexdigest()
        return f"spaider:emb:{digest}"

    async def _get_cached(self, text: str) -> Optional[list[float]]:
        try:
            redis = await self._get_redis()
            raw = await redis.get(self._cache_key(text))
            if raw is not None:
                return json.loads(raw)
        except Exception as exc:
            logger.warning("Redis cache GET failed: %s", exc)
        return None

    async def _set_cached(self, text: str, embedding: list[float]) -> None:
        try:
            redis = await self._get_redis()
            await redis.set(
                self._cache_key(text),
                json.dumps(embedding),
                ex=_CACHE_TTL,
            )
        except Exception as exc:
            logger.warning("Redis cache SET failed: %s", exc)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def embed(self, text: str) -> list[float]:
        """
        Return the embedding vector for a single text string.
        Checks Redis cache first; falls back to LiteLLM API.
        """
        cached = await self._get_cached(text)
        if cached is not None:
            logger.debug("Embedding cache HIT for text length=%d", len(text))
            return cached

        logger.debug("Embedding cache MISS – calling LiteLLM for text length=%d", len(text))
        emb_kwargs: dict = dict(model=self._model, input=[text])
        if self._api_base:
            emb_kwargs["api_base"] = self._api_base
        if self._api_key:
            emb_kwargs["api_key"] = self._api_key
        response = await asyncio.wait_for(
            litellm.aembedding(**emb_kwargs),
            timeout=60,
        )
        embedding: list[float] = response.data[0]["embedding"]

        await self._set_cached(text, embedding)
        return embedding

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """
        Return embedding vectors for a list of texts.
        Uses per-item caching; uncached texts are batched into a single API call.
        """
        if not texts:
            return []

        results: list[Optional[list[float]]] = [None] * len(texts)
        uncached_indices: list[int] = []
        uncached_texts: list[str] = []

        # Check cache for each text
        for i, text in enumerate(texts):
            cached = await self._get_cached(text)
            if cached is not None:
                results[i] = cached
            else:
                uncached_indices.append(i)
                uncached_texts.append(text)

        # Batch API call for uncached texts
        if uncached_texts:
            logger.debug(
                "Batch embedding: %d cached, %d uncached",
                len(texts) - len(uncached_texts),
                len(uncached_texts),
            )
            batch_kwargs: dict = dict(model=self._model, input=uncached_texts)
            if self._api_base:
                batch_kwargs["api_base"] = self._api_base
            if self._api_key:
                batch_kwargs["api_key"] = self._api_key
            response = await asyncio.wait_for(
                litellm.aembedding(**batch_kwargs),
                timeout=15,
            )
            for batch_idx, orig_idx in enumerate(uncached_indices):
                embedding: list[float] = response.data[batch_idx]["embedding"]
                results[orig_idx] = embedding
                await self._set_cached(uncached_texts[batch_idx], embedding)

        # At this point all results should be populated
        return [r for r in results if r is not None]

    # ------------------------------------------------------------------
    # Similarity
    # ------------------------------------------------------------------

    @staticmethod
    def cosine_similarity(a: list[float], b: list[float]) -> float:
        """
        Compute the cosine similarity between two embedding vectors.

        Returns a float in [-1.0, 1.0].
        """
        if len(a) != len(b):
            raise ValueError(
                f"Vector length mismatch: {len(a)} vs {len(b)}"
            )

        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))

        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0

        return dot / (norm_a * norm_b)
