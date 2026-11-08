"""
Local text embeddings by feature hashing.

These need no model and no network, so episodes can be written in milliseconds
and the same vectors can be computed on a robot. They capture shared words and
spelling, not meaning: "car" and "automobile" are unrelated here. Long-term
memory still uses the real embedding model.

Standard library only, because the edge SDK must produce identical vectors.
"""
from __future__ import annotations

import hashlib
import math
import re

HASH_EMBEDDING_DIM = 256
_TOKEN = re.compile(r"\w+", re.UNICODE)


def _bucket(feature: str, dim: int) -> tuple[int, float]:
    digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "big")
    return value % dim, 1.0 if value >> 63 else -1.0


def hash_embed(text: str, dim: int = HASH_EMBEDDING_DIM) -> list[float]:
    """A deterministic, unit-length vector. All zeros only when there are no tokens."""
    tokens = _TOKEN.findall(text.lower())
    vector = [0.0] * dim
    for index, token in enumerate(tokens):
        features = [f"w:{token}"]
        if index:
            features.append(f"b:{tokens[index - 1]} {token}")
        padded = f"<{token}>"
        features += [f"c:{padded[i:i + 3]}" for i in range(max(len(padded) - 2, 0))]
        for feature in features:
            bucket, sign = _bucket(feature, dim)
            vector[bucket] += sign
    norm = math.sqrt(sum(x * x for x in vector))
    return [x / norm for x in vector] if norm else vector
