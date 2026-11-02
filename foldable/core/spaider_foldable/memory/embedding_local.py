"""Embedding adapter with the same surface as ``app.services.embedding_service
.EmbeddingService`` (``embed``, ``embed_batch``, ``cosine_similarity``) so the
reused ``EntityResolver`` works unchanged. Vectors come from the local
embedding runtime; when none is active, ``embed_batch`` returns an empty list
and callers degrade to label/fulltext matching."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from app.services.embedding_service import EmbeddingService as _SpaiderEmbeddingService

if TYPE_CHECKING:
    from ..inference.manager import InferenceManager

logger = logging.getLogger(__name__)

_BATCH = 16
_MAX_CHARS = 6000
QUERY_INSTRUCTION = (
    "Instruct: Given a search query, retrieve relevant facts and passages that answer the query\nQuery: "
)


class LocalEmbeddingService:
    def __init__(self, manager: "InferenceManager"):
        self._manager = manager

    @property
    def available(self) -> bool:
        return self._manager.embedding.state == "ready" and self._manager.embedding.client is not None

    @property
    def model_id(self) -> Optional[str]:
        return self._manager.embedding.model_id if self.available else None

    async def embed(self, text: str) -> list[float]:
        vectors = await self.embed_batch([text])
        return vectors[0] if vectors else []

    async def embed_query(self, text: str) -> list[float]:
        return await self.embed(QUERY_INSTRUCTION + text)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        client = self._manager.embedding.client
        if client is None:
            return []
        out: list[list[float]] = []
        for i in range(0, len(texts), _BATCH):
            batch = [t[:_MAX_CHARS] if t else " " for t in texts[i : i + _BATCH]]
            try:
                out.extend(await client.embeddings(batch))
            except Exception as exc:  # noqa: BLE001
                logger.warning("embedding batch failed (%s); continuing without vectors", exc)
                return []
        return out

    cosine_similarity = staticmethod(_SpaiderEmbeddingService.cosine_similarity)
