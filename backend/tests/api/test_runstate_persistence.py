"""
Unit tests for the Redis-backed RunState helpers in
``app.api.v1.ingest`` ([](/Spaider-studio/spaider/issues/51)).

Coverage:

  1. Cache miss with no Redis client: returns a fresh empty RunState and
     stores it in the in-process cache.
  2. Cache hit (in-process): never touches Redis.
  3. Redis-backed read: when the in-process cache is empty and Redis has a
     stored RunState JSON, ``_get_run_state`` rehydrates it.
  4. Redis-backed write: ``_save_run_state`` calls ``redis.set`` with the
     correct key and TTL.
  5. Corrupt JSON in Redis falls back to a fresh RunState (does not raise).
  6. Redis save failure is swallowed (in-process cache still updated).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.api.v1 import ingest as ingest_module
from app.api.v1.ingest import (
    _RUN_STATE_TTL_SECONDS,
    _get_run_state,
    _run_state_redis_key,
    _save_run_state,
)
from app.connectors import RunState


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Each test starts with empty in-process cache + None Redis client."""
    ingest_module._run_states.clear()
    ingest_module._run_state_redis = None
    yield
    ingest_module._run_states.clear()
    ingest_module._run_state_redis = None


# ---------------------------------------------------------------------------
# 1. Empty cache, no Redis available → fresh empty RunState
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_run_state_falls_back_to_empty_when_redis_unavailable():
    with patch.object(ingest_module, "_get_run_state_redis", AsyncMock(return_value=None)):
        state = await _get_run_state("url", "agent-x")
    assert isinstance(state, RunState)
    assert state.connector_id == "url"
    assert state.source_states == {}
    # Cached for next time
    assert ingest_module._run_states[("url", "agent-x")] is state


# ---------------------------------------------------------------------------
# 2. In-process cache hit short-circuits Redis
# ---------------------------------------------------------------------------


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_get_run_state_in_process_cache_hit_skips_redis():
    cached = RunState(connector_id="url", source_states={"https://x": {"etag": "abc"}})
    ingest_module._run_states[("url", "agent-x")] = cached

    redis_mock = AsyncMock()
    with patch.object(ingest_module, "_get_run_state_redis", AsyncMock(return_value=redis_mock)):
        state = await _get_run_state("url", "agent-x")

    assert state is cached
    redis_mock.get.assert_not_called()


# ---------------------------------------------------------------------------
# 3. Empty cache, Redis has a stored value → rehydrate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_run_state_rehydrates_from_redis():
    stored = RunState(
        connector_id="url",
        source_states={"https://x": {"etag": '"abc"', "last_modified": "Wed"}},
    ).model_dump_json()

    redis_mock = AsyncMock()
    redis_mock.get = AsyncMock(return_value=stored)
    with patch.object(ingest_module, "_get_run_state_redis", AsyncMock(return_value=redis_mock)):
        state = await _get_run_state("url", "agent-x")

    redis_mock.get.assert_awaited_once_with(_run_state_redis_key("url", "agent-x"))
    assert state.connector_id == "url"
    assert state.source_states["https://x"]["etag"] == '"abc"'
    # Cached so a subsequent call doesn't re-read
    assert ingest_module._run_states[("url", "agent-x")] is state


# ---------------------------------------------------------------------------
# 4. Save writes JSON to Redis with the right key + TTL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_run_state_writes_to_redis_with_ttl():
    state = RunState(connector_id="mcp", source_states={"file:///doc": {"content_hash": "abc"}})
    redis_mock = AsyncMock()
    redis_mock.set = AsyncMock()
    with patch.object(ingest_module, "_get_run_state_redis", AsyncMock(return_value=redis_mock)):
        await _save_run_state("mcp", "agent-x", state)

    redis_mock.set.assert_awaited_once()
    args, kwargs = redis_mock.set.call_args
    assert args[0] == _run_state_redis_key("mcp", "agent-x")
    # second positional is the JSON body
    assert "content_hash" in args[1]
    assert kwargs["ex"] == _RUN_STATE_TTL_SECONDS
    # In-process cache is also updated
    assert ingest_module._run_states[("mcp", "agent-x")] is state


# ---------------------------------------------------------------------------
# 5. Corrupt Redis JSON → fall back to fresh state, no crash
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_run_state_handles_corrupt_redis_json():
    redis_mock = AsyncMock()
    redis_mock.get = AsyncMock(return_value="not valid json {{{")
    with patch.object(ingest_module, "_get_run_state_redis", AsyncMock(return_value=redis_mock)):
        state = await _get_run_state("url", "agent-x")
    assert isinstance(state, RunState)
    assert state.source_states == {}


# ---------------------------------------------------------------------------
# 6. Save failure is swallowed (in-process cache still updated)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_run_state_swallows_redis_errors():
    state = RunState(connector_id="url", source_states={})
    redis_mock = AsyncMock()
    redis_mock.set = AsyncMock(side_effect=RuntimeError("connection refused"))
    with patch.object(ingest_module, "_get_run_state_redis", AsyncMock(return_value=redis_mock)):
        # Should NOT raise — connector callers cannot fail because Redis is down.
        await _save_run_state("url", "agent-x", state)

    # In-process cache is still updated even though Redis write failed
    assert ingest_module._run_states[("url", "agent-x")] is state
