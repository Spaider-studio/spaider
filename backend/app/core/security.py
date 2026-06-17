"""Primitives for authentication / credential handling.

SHA-256 is deliberate here (not bcrypt/Argon2): API keys are generated with
`uuid.uuid4().hex` — 128 bits of entropy — which makes dictionary and
brute-force attacks computationally infeasible. A slow hash would add CPU
cost per auth request without improving security for high-entropy secrets.
"""
from __future__ import annotations

import hashlib


def hash_api_key(raw_key: str) -> str:
    """Return the SHA-256 hex digest of ``raw_key`` (64 lowercase hex chars)."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
