"""Must produce the same vectors as backend/app/core/hashing.py. A golden file checks both."""
from __future__ import annotations

import hashlib
import math
import re
from typing import List, Tuple

HASH_EMBEDDING_DIM = 256
_TOKEN = re.compile(r"\w+", re.UNICODE)


def _bucket(feature: str, dim: int) -> Tuple[int, float]:
    digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "big")
    return value % dim, 1.0 if value >> 63 else -1.0


def hash_embed(text: str, dim: int = HASH_EMBEDDING_DIM) -> List[float]:
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
