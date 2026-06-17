"""
Auth Service: API key management and JWT issuance/validation.

API keys are stored in Redis **as SHA-256 digests, never as raw tokens**.
The raw key is shown to the caller exactly once at provisioning time; only
its hex digest ever touches persistent storage.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import redis.asyncio as aioredis
from fastapi import Header, HTTPException, status
from jose import JWTError, jwt

from app.config import settings
from app.core.security import hash_api_key

logger = logging.getLogger(__name__)

_API_KEY_TTL = 0          # 0 = no expiry (permanent until explicitly revoked)
_API_KEY_PREFIX = "spaider:apikey:"
_RAW_KEY_PREFIX = "sk-"

# ---------------------------------------------------------------------------
# Fort Knox Patch — Phase 1, Proposal 1 (see SWARM_SECURITY_MANIFEST.md §2.1).
#
# Feature flag controlling whether REST routes enforce API-key authentication.
# Default OFF preserves byte-identical behavior with pre-patch main, ensuring
# existing callers (benchmark harnesses, ops scripts, internal services) do
# not break on merge. Operators flip to "true" only after every caller has
# been migrated to include the X-Api-Key header. See the manifest's
# Implementation Sequencing table for the recommended rollout order.
# ---------------------------------------------------------------------------
_REQUIRE_API_KEY_AUTH: bool = (
    os.environ.get("REQUIRE_API_KEY_AUTH", "false").lower() == "true"
)

# Sentinel returned by ``verify_api_key`` when the flag is OFF. Lets call
# sites identify bypassed-auth requests without inspecting environment
# state at every check.
_AUTH_BYPASS_SENTINEL: dict = {"agent_id": None, "auth_bypassed": True}


class AuthService:
    """
    Manages API key creation, JWT issuance, and token validation.

    API keys:
        - Generated as "sk-<uuid4.hex>" (128 bits of entropy).
        - Stored in Redis as:  spaider:apikey:<sha256-hex> -> JSON(agent metadata).
        - The raw key is returned to the caller once at creation and then discarded.

    JWT:
        - HS256-signed
        - Payload: {sub, tenant_id, permissions, swarm_access, exp, iat}
        - Expiry: configurable (default 24 hours)

    Bearer auth accepts either:
        - A raw API key ("sk-..."):  hashed, looked up directly in Redis.
        - A JWT:                     decoded and verified with the shared secret.
    """

    def __init__(self) -> None:
        self._redis: Optional[aioredis.Redis] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def _get_redis(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = aioredis.from_url(
                settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
        return self._redis

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    # ------------------------------------------------------------------
    # API Key management
    # ------------------------------------------------------------------

    def create_api_key(self, agent_id: str) -> str:
        """
        Generate a new UUID-based API key.
        The caller must call store_api_key() to persist it.
        """
        return f"{_RAW_KEY_PREFIX}{uuid.uuid4().hex}"

    async def generate_and_store_api_key(
        self,
        agent_id: str,
        tenant_id: str = "default",
        permissions: Optional[list[str]] = None,
        swarm_access: Optional[list[str]] = None,
        metadata: Optional[dict] = None,
    ) -> tuple[str, str]:
        """Generate a new API key, persist its hash, return ``(raw, hashed)``.

        Used by rotation flows that need to remember the new hash on the
        agent record so the *next* rotation can revoke by hash in O(1)
        instead of SCAN-ing the whole keyspace.
        """
        raw_key = self.create_api_key(agent_id)
        hashed = hash_api_key(raw_key)
        record = {
            "agent_id": agent_id,
            "tenant_id": tenant_id,
            "permissions": permissions or ["read", "write"],
            "swarm_access": swarm_access or [],
            "metadata": metadata or {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        redis = await self._get_redis()
        await redis.set(f"{_API_KEY_PREFIX}{hashed}", json.dumps(record))
        logger.info("Stored API key for agent_id=%s hash=%s…", agent_id, hashed[:8])
        return raw_key, hashed

    async def store_api_key(
        self,
        api_key: str,
        agent_id: str,
        tenant_id: str = "default",
        permissions: Optional[list[str]] = None,
        swarm_access: Optional[list[str]] = None,
        metadata: Optional[dict] = None,
    ) -> None:
        """Store a **caller-supplied** raw API key's hash under its agent metadata.

        Kept for callers that already hold a raw key (e.g. tests seeding fixtures,
        or an external identity provider minting keys). New code should prefer
        :meth:`generate_and_store_api_key`, which is atomic and returns the hash
        for later targeted revocation.
        """
        redis = await self._get_redis()
        hashed = hash_api_key(api_key)
        record = {
            "agent_id": agent_id,
            "tenant_id": tenant_id,
            "permissions": permissions or ["read", "write"],
            "swarm_access": swarm_access or [],
            "metadata": metadata or {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await redis.set(f"{_API_KEY_PREFIX}{hashed}", json.dumps(record))
        logger.info("Stored API key for agent_id=%s hash=%s…", agent_id, hashed[:8])

    async def revoke_api_key(self, api_key: str) -> None:
        """Delete an API key from Redis.

        The raw key is hashed first — deleting by the raw value would target
        a non-existent Redis slot and silently leave the credential active.
        """
        redis = await self._get_redis()
        hashed = hash_api_key(api_key)
        await redis.delete(f"{_API_KEY_PREFIX}{hashed}")
        logger.info("Revoked API key hash=%s…", hashed[:8])

    async def revoke_api_key_by_hash(self, hashed_key: str) -> bool:
        """Delete an API key given its SHA-256 hash (no raw key required).

        Used by rotation: after rotation the raw key is long gone, but the
        agent record still carries the hash. Returns ``True`` if a slot was
        actually removed, ``False`` if the hash was already absent.
        """
        redis = await self._get_redis()
        removed = await redis.delete(f"{_API_KEY_PREFIX}{hashed_key}")
        if removed:
            logger.info("Revoked API key by hash=%s…", hashed_key[:8])
        return bool(removed)

    async def revoke_all_for_agent(self, agent_id: str) -> int:
        """Revoke every API key issued for ``agent_id``.

        We index keys by hash (not by agent_id), so we need to scan. Called
        on agent deletion — if we skipped this, a raw API key for a deleted
        agent would keep authenticating as that ``agent_id`` via the raw-key
        path in ``verify_token``.

        Returns the number of keys revoked.
        """
        redis = await self._get_redis()
        revoked = 0
        async for redis_key in redis.scan_iter(match=f"{_API_KEY_PREFIX}*", count=100):
            try:
                raw = await redis.get(redis_key)
                if raw is None:
                    continue
                record = json.loads(raw)
            except Exception:
                continue
            if record.get("agent_id") == agent_id:
                await redis.delete(redis_key)
                revoked += 1
        if revoked:
            logger.info("Revoked %d API key(s) for agent_id=%s", revoked, agent_id)
        return revoked

    async def get_agent_by_api_key(self, api_key: str) -> Optional[dict]:
        """
        Retrieve agent metadata for a given raw API key.
        Returns None if the key is not found.
        """
        try:
            redis = await self._get_redis()
            hashed = hash_api_key(api_key)
            raw = await redis.get(f"{_API_KEY_PREFIX}{hashed}")
            if raw is None:
                return None
            return json.loads(raw)
        except Exception as exc:
            logger.error("Redis lookup failed for API key: %s", exc)
            return None

    # ------------------------------------------------------------------
    # JWT
    # ------------------------------------------------------------------

    async def create_token(self, api_key: str) -> str:
        """
        Issue a JWT for a valid API key.

        Returns:
            Signed JWT string.

        Raises:
            ValueError: If the API key is not found.
        """
        agent_data = await self.get_agent_by_api_key(api_key)
        if agent_data is None:
            raise ValueError("Invalid API key.")

        now = datetime.now(timezone.utc)
        payload = {
            "sub": agent_data["agent_id"],
            "tenant_id": agent_data.get("tenant_id", "default"),
            "permissions": agent_data.get("permissions", []),
            "swarm_access": agent_data.get("swarm_access", []),
            "iat": now,
            "exp": now + timedelta(hours=settings.jwt_expiration_hours),
        }

        token = jwt.encode(
            payload,
            settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )
        logger.info("Issued JWT for agent_id=%s", agent_data["agent_id"])
        return token

    async def verify_token(self, token: str) -> dict:
        """
        Validate a bearer token and return a normalized payload dict with
        keys compatible with JWT callers: ``sub``, ``tenant_id``,
        ``permissions``, ``swarm_access``.

        Two-path dispatch:
            - ``sk-...`` prefix → raw API key, hashed and looked up in Redis.
            - anything else    → treated as a JWT and HS256-decoded.

        Raises:
            ValueError: If the token is invalid, unknown, or expired.
        """
        if token.startswith(_RAW_KEY_PREFIX):
            agent_data = await self.get_agent_by_api_key(token)
            if agent_data is None:
                raise ValueError("Invalid API key.")
            # Normalize to JWT-style payload so middleware / require_auth can
            # keep reading ``sub`` without a code change.
            return {
                "sub": agent_data["agent_id"],
                "tenant_id": agent_data.get("tenant_id", "default"),
                "permissions": agent_data.get("permissions", []),
                "swarm_access": agent_data.get("swarm_access", []),
            }

        try:
            payload = jwt.decode(
                token,
                settings.jwt_secret,
                algorithms=[settings.jwt_algorithm],
            )
            return payload
        except JWTError as exc:
            logger.warning("JWT validation failed: %s", exc)
            raise ValueError(f"Invalid or expired token: {exc}") from exc

    # ------------------------------------------------------------------
    # Convenience: create + store in one call
    # ------------------------------------------------------------------

    async def provision_agent(
        self,
        agent_id: str,
        tenant_id: str = "default",
        permissions: Optional[list[str]] = None,
        swarm_access: Optional[list[str]] = None,
        metadata: Optional[dict] = None,
    ) -> str:
        """Backwards-compatible wrapper: returns only the raw key.

        Callers that also need the hash (to persist on the agent record for
        scan-free rotation) should call :meth:`generate_and_store_api_key`
        directly.
        """
        raw, _hashed = await self.generate_and_store_api_key(
            agent_id=agent_id,
            tenant_id=tenant_id,
            permissions=permissions,
            swarm_access=swarm_access,
            metadata=metadata,
        )
        return raw


# ---------------------------------------------------------------------------
# Fort Knox Patch — FastAPI dependency for REST route authentication.
#
# Module-level (not an AuthService method) so FastAPI's Depends() can
# resolve it directly without manual class instantiation per request.
# Auth bypass when ``REQUIRE_API_KEY_AUTH=false`` returns the bypass
# sentinel; route handlers that call ``_check_idor`` recognize the
# sentinel and skip the identity-match check, preserving pre-patch
# behavior exactly. When the flag is on, every protected route raises
# 401 on missing/invalid keys before any handler logic runs.
# ---------------------------------------------------------------------------


async def verify_api_key(
    x_api_key: Optional[str] = Header(default=None, alias="X-Api-Key"),
    authorization: Optional[str] = Header(default=None),
) -> dict:
    """Authenticate the caller via API key and return the agent record.

    Accepts the key from either:
      • ``X-Api-Key: <raw_key>`` (primary)
      • ``Authorization: Bearer <raw_key>`` (standard fallback)

    When ``REQUIRE_API_KEY_AUTH=false`` (default), returns a bypass
    sentinel so existing unauthenticated callers continue to work
    during migration. When ``true``, raises ``HTTPException(401)`` on
    any auth failure before the route handler runs.

    The returned dict is the agent metadata blob stored in Redis under
    ``spaider:apikey:<sha256-hex>`` — minimally contains ``agent_id``;
    may also contain ``clearance_level``, ``tenant_id``, ``permissions``.
    """
    if not _REQUIRE_API_KEY_AUTH:
        return _AUTH_BYPASS_SENTINEL

    api_key: Optional[str] = x_api_key
    if not api_key and authorization:
        # Tolerate case variation on "bearer" — RFC 6750 says case-insensitive.
        prefix = authorization[:7].lower()
        if prefix == "bearer ":
            api_key = authorization[7:].strip()

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "Missing API key. Provide via the X-Api-Key header or "
                "Authorization: Bearer <key>."
            ),
            headers={"WWW-Authenticate": "Bearer"},
        )

    auth = AuthService()
    agent_record = await auth.get_agent_by_api_key(api_key)
    if not agent_record or "agent_id" not in agent_record:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return agent_record


def _check_idor(authenticated: dict, requested_agent_id: str) -> None:
    """Insecure Direct Object Reference guard.

    Raises ``HTTPException(403)`` when the authenticated agent attempts
    to operate on a different agent's resources. Skipped entirely when
    the auth feature flag is off (preserves pre-patch behavior).

    Future work (manifest Phase 2): allow the request when the
    authenticated agent has an active ``SHARES_KNOWLEDGE_WITH`` (or
    canonical ``:SwarmConnection``) to the requested target. For Phase 1
    we enforce strict identity equality — caller acts as itself only.
    """
    if not _REQUIRE_API_KEY_AUTH or authenticated.get("auth_bypassed"):
        return
    auth_id = authenticated.get("agent_id")
    if auth_id != requested_agent_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Authenticated agent '{auth_id}' cannot act on resources "
                f"belonging to agent '{requested_agent_id}'."
            ),
        )
