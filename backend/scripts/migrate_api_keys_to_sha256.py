"""One-shot migration: rewrite plaintext API-key Redis entries to SHA-256.

Before (legacy):  spaider:apikey:sk-<raw>            -> JSON(agent metadata)
After:            spaider:apikey:<sha256-hex-digest> -> JSON(agent metadata)

The script is idempotent — a key whose suffix is already a 64-char hex digest
is treated as migrated and skipped. Safe to re-run after a partial migration.

TTL preservation: ``redis.ttl()`` is read *before* the rewrite so keys that
were provisioned with an explicit expiry keep it. Without this, a 30-day key
would silently become permanent after migration.

Crash safety: ``SET new`` is queued *before* ``DEL old`` in the same pipeline
flush. If the process is killed mid-flight, the old key remains intact — the
failure mode always preserves access, never loses it.

Usage:
    python -m backend.scripts.migrate_api_keys_to_sha256 [--dry-run] [--redis-url URL]
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import sys

import redis.asyncio as aioredis

from app.core.security import hash_api_key

logger = logging.getLogger("spaider.migration.apikeys")

_PREFIX = "spaider:apikey:"
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


def _already_hashed(suffix: str) -> bool:
    return bool(_HEX64_RE.match(suffix))


async def _migrate_one(
    redis: aioredis.Redis,
    old_key: str,
    *,
    dry_run: bool,
) -> str:
    """Migrate a single key. Returns one of: 'migrated' | 'skipped' | 'error'."""
    suffix = old_key[len(_PREFIX):]
    if _already_hashed(suffix):
        logger.info("skip (already hashed): %s", _redact(old_key))
        return "skipped"

    try:
        value = await redis.get(old_key)
        if value is None:
            logger.warning("skip (vanished mid-scan): %s", _redact(old_key))
            return "skipped"

        ttl = await redis.ttl(old_key)
        # redis-py returns -2 for "key does not exist", -1 for "no expiry".
        # We already fetched the value, so -2 here means a race — treat as gone.
        if ttl == -2:
            logger.warning("skip (vanished mid-scan): %s", _redact(old_key))
            return "skipped"

        hashed = hash_api_key(suffix)
        new_key = f"{_PREFIX}{hashed}"

        if dry_run:
            logger.info(
                "[dry-run] would migrate %s -> %s (ttl=%s)",
                _redact(old_key), _redact(new_key), ttl,
            )
            return "migrated"

        # Pipeline the SET-then-DEL so they land in one round trip. SET comes
        # first — if the process crashes between SET and DEL, we have a dup
        # entry (safe) rather than a gap (locks the caller out).
        pipe = redis.pipeline(transaction=False)
        if ttl >= 0:
            pipe.set(new_key, value, ex=ttl)
        else:
            pipe.set(new_key, value)
        pipe.delete(old_key)
        await pipe.execute()

        logger.info("migrated %s -> %s (ttl=%s)", _redact(old_key), _redact(new_key), ttl)
        return "migrated"
    except Exception as exc:
        logger.error("error migrating %s: %s", _redact(old_key), exc)
        return "error"


def _redact(redis_key: str) -> str:
    """Show only prefix + first 8 chars of the suffix in logs."""
    if not redis_key.startswith(_PREFIX):
        return redis_key
    suffix = redis_key[len(_PREFIX):]
    return f"{_PREFIX}{suffix[:8]}…"


async def run(redis_url: str, *, dry_run: bool) -> int:
    logger.info(
        "Connecting to Redis (dry_run=%s) url=%s",
        dry_run,
        _redact_url(redis_url),
    )
    redis = aioredis.from_url(redis_url, encoding="utf-8", decode_responses=True)

    migrated = skipped = errored = 0
    try:
        async for key in redis.scan_iter(match=f"{_PREFIX}*", count=100):
            result = await _migrate_one(redis, key, dry_run=dry_run)
            if result == "migrated":
                migrated += 1
            elif result == "skipped":
                skipped += 1
            else:
                errored += 1
    finally:
        await redis.aclose()

    logger.info(
        "Done. migrated=%d skipped=%d errors=%d (dry_run=%s)",
        migrated, skipped, errored, dry_run,
    )
    return 1 if errored else 0


def _redact_url(url: str) -> str:
    # Strip any password in redis://:password@host so it doesn't land in logs.
    return re.sub(r"(redis://[^:]*:)[^@]+(@)", r"\1***\2", url)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log actions without writing or deleting anything.",
    )
    parser.add_argument(
        "--redis-url",
        default=None,
        help="Redis URL. Defaults to REDIS_URL env var, then settings.redis_url.",
    )
    return parser.parse_args(argv)


def _resolve_redis_url(cli_value: str | None) -> str:
    if cli_value:
        return cli_value
    if env := os.environ.get("REDIS_URL"):
        return env
    # Lazy import so running --help doesn't require the full app env.
    from app.config import settings
    return settings.redis_url


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    args = _parse_args(argv or sys.argv[1:])
    redis_url = _resolve_redis_url(args.redis_url)
    return asyncio.run(run(redis_url, dry_run=args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
