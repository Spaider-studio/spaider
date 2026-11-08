"""
The one way to get a Redis-compatible client.

With ``REDIS_URL`` set this is real Redis. Without it (Lite mode, Neo4j and the
API only) callers get an in-process fakeredis sharing one server, so caches,
leases, streams and pub/sub keep working inside a single process and nothing
needs a code path for "Redis is absent". State is then per-process and lost on
restart; anything that must survive lives in Neo4j or under ``data_dir``.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.config import settings

logger = logging.getLogger(__name__)

_clients: dict[tuple, Any] = {}
_fake_server: Optional[Any] = None


def kv_is_external() -> bool:
    """True when backed by a real Redis server."""
    return bool(settings.redis_url)


def get_kv(*, decode_responses: bool = True, **options: Any) -> Any:
    """Return a shared client, one per distinct option set.

    ``options`` are connection-level (``socket_timeout`` and friends) and only
    apply to real Redis.
    """
    key = (settings.redis_url, decode_responses, tuple(sorted(options.items())))
    client = _clients.get(key)
    if client is not None:
        return client

    if settings.redis_url:
        import redis.asyncio as aioredis

        client = aioredis.from_url(
            settings.redis_url, decode_responses=decode_responses, **options
        )
    else:
        global _fake_server
        import fakeredis

        if _fake_server is None:
            _fake_server = fakeredis.FakeServer()
            logger.info("REDIS_URL is empty; using in-process key-value store")
        client = fakeredis.aioredis.FakeRedis(
            server=_fake_server, decode_responses=decode_responses
        )
    _clients[key] = client
    return client


async def close_kv() -> None:
    """Close every client. Called on shutdown."""
    for client in list(_clients.values()):
        try:
            await client.aclose()
        except Exception as exc:  # noqa: BLE001
            logger.debug("kv close failed: %s", exc)
    _clients.clear()


def reset_kv() -> None:
    """Forget clients and in-process data without touching a server."""
    global _fake_server
    _clients.clear()
    _fake_server = None


def generation_key(agent_id: str) -> str:
    return f"spaider:cachegen:{agent_id}"


async def cache_generation(agent_id: str) -> int:
    """Part of every query-cache key, so bumping it invalidates a KB's answers."""
    return int(await get_kv().get(generation_key(agent_id)) or 0)


async def bump_cache_generation(agent_id: str) -> None:
    await get_kv().incr(generation_key(agent_id))
