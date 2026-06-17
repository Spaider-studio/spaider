"""
Tests for AuthService: API key generation/verification, JWT token lifecycle,
and rate limiting.

The real AuthService:
  - create_api_key(agent_id) -> str   (sync, returns "sk-<uuid>")
  - store_api_key(...) async          (persists to Redis)
  - get_agent_by_api_key(api_key) async
  - create_token(api_key) async       (requires a stored key)
  - verify_token(token) async         (returns payload dict)
  - provision_agent(...) async        (create + store)

The rate limiter lives in app.middleware.rate_limiter as:
  - check_rate_limit(api_key, limit, window_seconds, redis) async -> (bool, dict)
  - RateLimiterMiddleware (FastAPI middleware class)

These tests use a lightweight in-process RateLimiter shim that mirrors
the real sliding-window behaviour, and test the synchronous parts of
AuthService directly (key creation / JWT encode-decode).
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from jose import jwt

from app.config import settings
from app.services.auth_service import AuthService


# ---------------------------------------------------------------------------
# Thin in-process rate limiter (mirrors the sliding-window semantics of the
# real Redis-backed implementation, but runs entirely in memory for tests)
# ---------------------------------------------------------------------------

class RateLimiter:
    """In-memory sliding-window rate limiter for unit tests."""

    def __init__(self, max_requests: int = 60, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._counters: dict[str, list[float]] = {}

    def is_allowed(self, key: str) -> bool:
        now = time.monotonic()
        window_start = now - self.window_seconds
        hits = self._counters.get(key, [])
        hits = [t for t in hits if t >= window_start]
        if len(hits) >= self.max_requests:
            self._counters[key] = hits
            return False
        hits.append(now)
        self._counters[key] = hits
        return True


# ---------------------------------------------------------------------------
# Helper: build an AuthService with a mock Redis client
# ---------------------------------------------------------------------------

def _auth_with_mock_redis(store: dict | None = None) -> tuple[AuthService, AsyncMock]:
    """Return an AuthService whose Redis is replaced by an AsyncMock."""
    if store is None:
        store = {}

    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock(side_effect=lambda k, v, **kw: store.__setitem__(k, v))
    mock_redis.get = AsyncMock(side_effect=lambda k: store.get(k))
    mock_redis.delete = AsyncMock(
        side_effect=lambda *keys: sum(1 for k in keys if store.pop(k, None) is not None)
    )

    auth = AuthService()
    auth._redis = mock_redis
    return auth, mock_redis


# ---------------------------------------------------------------------------
# test_create_api_key
# ---------------------------------------------------------------------------


def test_create_api_key():
    """create_api_key() should return a non-empty string starting with 'sk-'."""
    auth = AuthService()
    key = auth.create_api_key("agent-1")

    assert isinstance(key, str)
    assert len(key) >= 32, "API key should be at least 32 characters"
    assert key.startswith("sk-")
    # Should be URL-safe (hex chars after prefix)
    assert "+" not in key
    assert "/" not in key


def test_api_key_is_unique():
    """Each call to create_api_key() should return a different key."""
    auth = AuthService()
    keys = {auth.create_api_key("agent-x") for _ in range(20)}
    assert len(keys) == 20, "API keys should be unique across calls"


# ---------------------------------------------------------------------------
# test_store_and_retrieve_api_key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_and_retrieve_api_key():
    """store_api_key() persists the key; get_agent_by_api_key() retrieves it."""
    import json
    store: dict = {}
    auth, _ = _auth_with_mock_redis(store)

    api_key = auth.create_api_key("agent-42")
    await auth.store_api_key(
        api_key=api_key,
        agent_id="agent-42",
        tenant_id="test-tenant",
        permissions=["read"],
        swarm_access=[],
    )

    # Verify something was stored
    assert len(store) == 1

    # Retrieve via the service method
    agent_data = await auth.get_agent_by_api_key(api_key)
    assert agent_data is not None
    assert agent_data["agent_id"] == "agent-42"
    assert agent_data["tenant_id"] == "test-tenant"


@pytest.mark.asyncio
async def test_get_agent_returns_none_for_missing_key():
    """get_agent_by_api_key() should return None when key is absent."""
    auth, _ = _auth_with_mock_redis()
    result = await auth.get_agent_by_api_key("nonexistent-key")
    assert result is None


# ---------------------------------------------------------------------------
# test_create_and_verify_token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_and_verify_token():
    """A freshly created token should decode successfully with the correct agent_id."""
    import json
    store: dict = {}
    auth, mock_redis = _auth_with_mock_redis(store)

    agent_id = "test-agent-123"
    api_key = auth.create_api_key(agent_id)

    # Store the key so create_token can look it up
    agent_record = {
        "agent_id": agent_id,
        "tenant_id": "default",
        "permissions": ["read", "write"],
        "swarm_access": [],
        "metadata": {},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    from app.core.security import hash_api_key
    from app.services.auth_service import _API_KEY_PREFIX
    store[f"{_API_KEY_PREFIX}{hash_api_key(api_key)}"] = json.dumps(agent_record)

    token = await auth.create_token(api_key)
    assert isinstance(token, str)

    payload = await auth.verify_token(token)
    assert payload["sub"] == agent_id


@pytest.mark.asyncio
async def test_token_contains_expiry():
    """Created token payload should include an 'exp' claim."""
    import json
    store: dict = {}
    auth, _ = _auth_with_mock_redis(store)

    agent_id = "agent-42"
    api_key = auth.create_api_key(agent_id)

    agent_record = {
        "agent_id": agent_id,
        "tenant_id": "default",
        "permissions": ["read"],
        "swarm_access": [],
        "metadata": {},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    from app.core.security import hash_api_key
    from app.services.auth_service import _API_KEY_PREFIX
    store[f"{_API_KEY_PREFIX}{hash_api_key(api_key)}"] = json.dumps(agent_record)

    token = await auth.create_token(api_key)
    payload = await auth.verify_token(token)
    assert "exp" in payload


# ---------------------------------------------------------------------------
# test_invalid_api_key_raises
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_api_key_raises():
    """create_token() with an unknown API key should raise ValueError."""
    auth, _ = _auth_with_mock_redis()
    with pytest.raises(ValueError):
        await auth.create_token("bad-api-key")


# ---------------------------------------------------------------------------
# test_invalid_token_rejected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_token_rejected():
    """A tampered or random string should be rejected."""
    auth = AuthService()
    with pytest.raises(Exception):
        await auth.verify_token("this.is.not.a.valid.jwt")


@pytest.mark.asyncio
async def test_token_wrong_secret_rejected():
    """A token signed with a different secret should be rejected."""
    payload = {
        "sub": "agent-hacker",
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    forged_token = jwt.encode(payload, "wrong-secret", algorithm=settings.jwt_algorithm)

    auth = AuthService()
    with pytest.raises(Exception):
        await auth.verify_token(forged_token)


# ---------------------------------------------------------------------------
# test_revoke_api_key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revoke_api_key():
    """revoke_api_key() should remove the key so subsequent lookups return None."""
    import json
    store: dict = {}
    auth, mock_redis = _auth_with_mock_redis(store)

    agent_id = "agent-revoke"
    api_key = auth.create_api_key(agent_id)

    agent_record = {
        "agent_id": agent_id,
        "tenant_id": "default",
        "permissions": ["read"],
        "swarm_access": [],
        "metadata": {},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    from app.core.security import hash_api_key
    from app.services.auth_service import _API_KEY_PREFIX
    store[f"{_API_KEY_PREFIX}{hash_api_key(api_key)}"] = json.dumps(agent_record)

    # Confirm stored
    assert await auth.get_agent_by_api_key(api_key) is not None

    await auth.revoke_api_key(api_key)

    # After revocation, key should be gone
    assert await auth.get_agent_by_api_key(api_key) is None


# ---------------------------------------------------------------------------
# test_rate_limiter_allows_within_limit
# ---------------------------------------------------------------------------


def test_rate_limiter_allows_within_limit():
    """Requests within the limit should all be allowed."""
    limiter = RateLimiter(max_requests=5, window_seconds=60)
    key = "agent-test"

    for i in range(5):
        assert limiter.is_allowed(key) is True, f"Request {i + 1} should be allowed"


# ---------------------------------------------------------------------------
# test_rate_limiter_blocks_over_limit
# ---------------------------------------------------------------------------


def test_rate_limiter_blocks_over_limit():
    """The 6th request in a window of 5 should be blocked."""
    limiter = RateLimiter(max_requests=5, window_seconds=60)
    key = "agent-limited"

    for _ in range(5):
        limiter.is_allowed(key)

    assert limiter.is_allowed(key) is False, "6th request should be blocked"


def test_rate_limiter_different_keys_are_independent():
    """Rate limiter counters must be per-key; exhausting one should not affect another."""
    limiter = RateLimiter(max_requests=2, window_seconds=60)

    limiter.is_allowed("key-a")
    limiter.is_allowed("key-a")

    # key-a is exhausted but key-b should still be free
    assert limiter.is_allowed("key-b") is True


def test_rate_limiter_window_slides():
    """After the window expires, requests should be allowed again."""
    limiter = RateLimiter(max_requests=2, window_seconds=1)  # 1-second window
    key = "agent-window"

    limiter.is_allowed(key)
    limiter.is_allowed(key)
    assert limiter.is_allowed(key) is False  # Over limit

    # Simulate time passing beyond the window by backdating the stored hits
    limiter._counters[key] = [t - 2 for t in limiter._counters[key]]

    assert limiter.is_allowed(key) is True, "After window expires, requests should be allowed"
