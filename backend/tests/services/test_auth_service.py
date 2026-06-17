"""Tests for AuthService hashing behaviour.

The primary security assertion is that the raw API key string is never
written to the Redis client — only its SHA-256 digest. The Redis mock is
always inspected via ``call_args`` so we see exactly what the service
passed, rather than only what we happened to store.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from jose import jwt

from app.config import settings
from app.core.security import hash_api_key
from app.services.auth_service import (
    _API_KEY_PREFIX,
    AuthService,
)


# ---------------------------------------------------------------------------
# Helper: AuthService with an in-memory Redis mock that actually stores things
# ---------------------------------------------------------------------------


def _auth_with_mock_redis(
    store: dict | None = None,
) -> tuple[AuthService, AsyncMock, dict]:
    if store is None:
        store = {}

    mock_redis = AsyncMock()

    async def _set(k, v, **kw):
        store[k] = v

    async def _get(k):
        return store.get(k)

    async def _delete(*keys):
        return sum(1 for k in keys if store.pop(k, None) is not None)

    mock_redis.set = AsyncMock(side_effect=_set)
    mock_redis.get = AsyncMock(side_effect=_get)
    mock_redis.delete = AsyncMock(side_effect=_delete)

    auth = AuthService()
    auth._redis = mock_redis
    return auth, mock_redis, store


# ===========================================================================
# TestHashApiKey — the primitive
# ===========================================================================


class TestHashApiKey:
    def test_returns_64_char_hex(self) -> None:
        h = hash_api_key("sk-abc")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_is_deterministic(self) -> None:
        assert hash_api_key("sk-xyz") == hash_api_key("sk-xyz")

    def test_matches_stdlib_sha256(self) -> None:
        raw = "sk-deadbeef"
        expected = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        assert hash_api_key(raw) == expected

    def test_no_collisions_across_500_keys(self) -> None:
        hashes = {hash_api_key(f"sk-{i:08d}") for i in range(500)}
        assert len(hashes) == 500

    def test_handles_empty_string(self) -> None:
        # SHA-256 of the empty string is well-known: e3b0c44…
        assert hash_api_key("") == hashlib.sha256(b"").hexdigest()

    def test_handles_unicode(self) -> None:
        h = hash_api_key("sk-ünïcødé-🔐")
        assert len(h) == 64


# ===========================================================================
# TestStoreApiKey — raw key must never reach Redis
# ===========================================================================


class TestStoreApiKey:
    @pytest.mark.asyncio
    async def test_raw_key_never_passed_to_redis(self) -> None:
        auth, mock_redis, _ = _auth_with_mock_redis()
        raw = "sk-plaintext-never-store-me"

        await auth.store_api_key(api_key=raw, agent_id="a-1")

        # Introspect the actual call — not the stored value, which could have
        # been shaped correctly by accident.
        stored_key = mock_redis.set.call_args.args[0]
        assert raw not in stored_key, "Raw API key leaked into Redis key"
        assert stored_key == f"{_API_KEY_PREFIX}{hash_api_key(raw)}"

    @pytest.mark.asyncio
    async def test_key_has_correct_prefix_and_hash_suffix(self) -> None:
        auth, mock_redis, _ = _auth_with_mock_redis()
        await auth.store_api_key(api_key="sk-abc", agent_id="a-1")

        stored_key = mock_redis.set.call_args.args[0]
        assert stored_key.startswith(_API_KEY_PREFIX)
        suffix = stored_key[len(_API_KEY_PREFIX):]
        assert len(suffix) == 64 and all(c in "0123456789abcdef" for c in suffix)

    @pytest.mark.asyncio
    async def test_payload_shape(self) -> None:
        auth, mock_redis, _ = _auth_with_mock_redis()
        await auth.store_api_key(
            api_key="sk-abc",
            agent_id="a-1",
            tenant_id="t-1",
            permissions=["read"],
        )
        stored_value = json.loads(mock_redis.set.call_args.args[1])
        assert stored_value["agent_id"] == "a-1"
        assert stored_value["tenant_id"] == "t-1"
        assert stored_value["permissions"] == ["read"]
        assert "created_at" in stored_value

    @pytest.mark.asyncio
    async def test_two_keys_for_same_agent_occupy_different_slots(self) -> None:
        auth, _, store = _auth_with_mock_redis()
        await auth.store_api_key(api_key="sk-one", agent_id="a-1")
        await auth.store_api_key(api_key="sk-two", agent_id="a-1")
        assert len(store) == 2


# ===========================================================================
# TestGetAgentByApiKey — lookup must use the hash
# ===========================================================================


class TestGetAgentByApiKey:
    @pytest.mark.asyncio
    async def test_lookup_uses_hash(self) -> None:
        auth, mock_redis, _ = _auth_with_mock_redis()
        raw = "sk-lookup-me"
        await auth.store_api_key(api_key=raw, agent_id="a-1")

        mock_redis.get.reset_mock()
        await auth.get_agent_by_api_key(raw)
        called_with = mock_redis.get.call_args.args[0]
        assert called_with == f"{_API_KEY_PREFIX}{hash_api_key(raw)}"

    @pytest.mark.asyncio
    async def test_missing_key_returns_none(self) -> None:
        auth, _, _ = _auth_with_mock_redis()
        assert await auth.get_agent_by_api_key("sk-nobody") is None

    @pytest.mark.asyncio
    async def test_legacy_plaintext_entry_is_not_found(self) -> None:
        """Regression: seed a legacy (unhashed) entry and confirm the new
        lookup path cannot accidentally find it. Proves the hash step is
        load-bearing rather than a cosmetic rename.
        """
        store: dict = {}
        auth, _, _ = _auth_with_mock_redis(store)
        raw = "sk-legacy-plaintext"
        # Legacy layout: raw key as suffix.
        store[f"{_API_KEY_PREFIX}{raw}"] = json.dumps({"agent_id": "ghost"})

        assert await auth.get_agent_by_api_key(raw) is None


# ===========================================================================
# TestRevokeApiKey — delete must use the hash
# ===========================================================================


class TestRevokeApiKey:
    @pytest.mark.asyncio
    async def test_revoke_uses_hash(self) -> None:
        auth, mock_redis, _ = _auth_with_mock_redis()
        raw = "sk-revoke-me"
        await auth.store_api_key(api_key=raw, agent_id="a-1")

        mock_redis.delete.reset_mock()
        await auth.revoke_api_key(raw)
        called_with = mock_redis.delete.call_args.args[0]
        assert called_with == f"{_API_KEY_PREFIX}{hash_api_key(raw)}"

    @pytest.mark.asyncio
    async def test_revoke_actually_removes_the_hashed_entry(self) -> None:
        auth, _, store = _auth_with_mock_redis()
        raw = "sk-revoke-me"
        await auth.store_api_key(api_key=raw, agent_id="a-1")
        assert len(store) == 1

        await auth.revoke_api_key(raw)
        assert len(store) == 0

    @pytest.mark.asyncio
    async def test_revoke_by_raw_does_not_accidentally_target_legacy_slot(self) -> None:
        """Prove the fix is load-bearing: if revoke were still passing the raw
        key to Redis.delete, a seeded legacy entry would get wiped and the
        hashed entry would survive. We want the opposite.
        """
        store: dict = {}
        auth, _, _ = _auth_with_mock_redis(store)
        raw = "sk-target"
        # Seed both layouts side-by-side.
        store[f"{_API_KEY_PREFIX}{raw}"] = json.dumps({"agent_id": "legacy"})
        store[f"{_API_KEY_PREFIX}{hash_api_key(raw)}"] = json.dumps({"agent_id": "new"})

        await auth.revoke_api_key(raw)

        # Hashed entry is gone; legacy (un-migrated) entry is still there.
        assert f"{_API_KEY_PREFIX}{hash_api_key(raw)}" not in store
        assert f"{_API_KEY_PREFIX}{raw}" in store


# ===========================================================================
# TestRevokeAllForAgent — agent deletion must revoke every issued key
# ===========================================================================


class TestRevokeAllForAgent:
    @pytest.mark.asyncio
    async def test_scans_and_revokes_by_agent_id(self) -> None:
        store: dict = {}
        auth, mock_redis, _ = _auth_with_mock_redis(store)

        # scan_iter isn't on the AsyncMock by default — wire it.
        async def _scan_iter(match: str = "*", count: int = 100):
            for k in list(store.keys()):
                if match.endswith("*") and k.startswith(match[:-1]):
                    yield k

        mock_redis.scan_iter = _scan_iter

        # Seed three keys for agent-1, one for agent-2.
        for raw in ("sk-a", "sk-b", "sk-c"):
            await auth.store_api_key(api_key=raw, agent_id="agent-1")
        await auth.store_api_key(api_key="sk-other", agent_id="agent-2")
        assert len(store) == 4

        count = await auth.revoke_all_for_agent("agent-1")
        assert count == 3
        # Only agent-2's key remains.
        assert len(store) == 1
        remaining = json.loads(next(iter(store.values())))
        assert remaining["agent_id"] == "agent-2"

    @pytest.mark.asyncio
    async def test_returns_zero_when_agent_has_no_keys(self) -> None:
        store: dict = {}
        auth, mock_redis, _ = _auth_with_mock_redis(store)

        async def _scan_iter(match: str = "*", count: int = 100):
            for k in list(store.keys()):
                if match.endswith("*") and k.startswith(match[:-1]):
                    yield k

        mock_redis.scan_iter = _scan_iter
        assert await auth.revoke_all_for_agent("ghost-agent") == 0


# ===========================================================================
# TestVerifyTokenRawKeyPath — the two-path dispatch
# ===========================================================================


class TestVerifyTokenRawKeyPath:
    @pytest.mark.asyncio
    async def test_raw_key_is_hashed_for_lookup(self) -> None:
        auth, mock_redis, _ = _auth_with_mock_redis()
        raw = "sk-verify-me"
        await auth.store_api_key(api_key=raw, agent_id="a-1", permissions=["read"])

        mock_redis.get.reset_mock()
        payload = await auth.verify_token(raw)

        called_with = mock_redis.get.call_args.args[0]
        assert called_with == f"{_API_KEY_PREFIX}{hash_api_key(raw)}"
        # Normalized to JWT shape so middleware keeps working unchanged.
        assert payload["sub"] == "a-1"
        assert payload["permissions"] == ["read"]

    @pytest.mark.asyncio
    async def test_unknown_raw_key_raises(self) -> None:
        auth, _, _ = _auth_with_mock_redis()
        with pytest.raises(ValueError):
            await auth.verify_token("sk-nobody")

    @pytest.mark.asyncio
    async def test_jwt_bypasses_redis(self) -> None:
        auth, mock_redis, _ = _auth_with_mock_redis()
        now = datetime.now(timezone.utc)
        token = jwt.encode(
            {
                "sub": "a-1",
                "permissions": ["read"],
                "iat": now,
                "exp": now + timedelta(hours=1),
            },
            settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )

        payload = await auth.verify_token(token)
        assert payload["sub"] == "a-1"
        # Redis was never consulted — JWT is self-contained.
        mock_redis.get.assert_not_called()


# ===========================================================================
# TestProvisionAgent — full lifecycle, no raw keys anywhere in Redis
# ===========================================================================


class TestProvisionAgent:
    @pytest.mark.asyncio
    async def test_full_lifecycle_never_stores_raw(self) -> None:
        auth, _, store = _auth_with_mock_redis()

        raw = await auth.provision_agent(
            agent_id="a-1",
            tenant_id="t-1",
            permissions=["read"],
        )
        assert raw.startswith("sk-")
        # The only Redis key is the hashed slot.
        assert list(store.keys()) == [f"{_API_KEY_PREFIX}{hash_api_key(raw)}"]

        # Verify works.
        payload = await auth.verify_token(raw)
        assert payload["sub"] == "a-1"

        # Revoke.
        await auth.revoke_api_key(raw)
        assert store == {}

        # Post-revoke verify fails.
        with pytest.raises(ValueError):
            await auth.verify_token(raw)

    @pytest.mark.asyncio
    async def test_returned_key_is_sk_prefixed_uuid(self) -> None:
        auth, _, _ = _auth_with_mock_redis()
        raw = await auth.provision_agent(agent_id="a-1")
        assert raw.startswith("sk-")
        # sk- + 32 uuid hex chars
        assert len(raw) == 3 + 32
