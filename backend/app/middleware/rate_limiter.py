"""
Redis-backed sliding window rate limiter middleware for FastAPI.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import redis.asyncio as aioredis
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

from app.config import settings

logger = logging.getLogger(__name__)

_REDIS_PREFIX = "spaider:ratelimit:"


# ---------------------------------------------------------------------------
# Core rate-limit check function (reusable outside middleware)
# ---------------------------------------------------------------------------

async def check_rate_limit(
    api_key: str,
    limit: int,
    window_seconds: int = 60,
    redis: Optional[aioredis.Redis] = None,
) -> tuple[bool, dict]:
    """
    Sliding window rate limiter using Redis sorted sets.

    Args:
        api_key: Identifier for the client (API key or IP).
        limit: Maximum number of requests allowed in the window.
        window_seconds: Size of the sliding window in seconds.
        redis: An existing aioredis client; a new one is created if None.

    Returns:
        (allowed: bool, info: {"remaining": int, "reset_at": float, "limit": int})
    """
    own_redis = False
    if redis is None:
        redis = aioredis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
        own_redis = True

    try:
        now = time.time()
        window_start = now - window_seconds
        key = f"{_REDIS_PREFIX}{api_key}"

        pipe = redis.pipeline()
        # Remove requests outside the current window
        pipe.zremrangebyscore(key, "-inf", window_start)
        # Count requests in the window
        pipe.zcard(key)
        # Add current request with score = current timestamp
        pipe.zadd(key, {str(now): now})
        # Expire the key after the window
        pipe.expire(key, window_seconds + 1)
        results = await pipe.execute()

        current_count = results[1]  # count BEFORE adding the new request

        if current_count >= limit:
            # Determine when the oldest request in window expires
            oldest = await redis.zrange(key, 0, 0, withscores=True)
            reset_at = (oldest[0][1] + window_seconds) if oldest else (now + window_seconds)
            info = {
                "allowed": False,
                "remaining": 0,
                "reset_at": reset_at,
                "limit": limit,
            }
            return False, info

        remaining = limit - current_count - 1
        reset_at = now + window_seconds
        info = {
            "allowed": True,
            "remaining": remaining,
            "reset_at": reset_at,
            "limit": limit,
        }
        return True, info

    except Exception as exc:
        logger.error("Rate limiter Redis error: %s. Allowing request.", exc)
        return True, {"remaining": -1, "reset_at": 0.0, "limit": limit}
    finally:
        if own_redis:
            await redis.aclose()


# ---------------------------------------------------------------------------
# FastAPI middleware
# ---------------------------------------------------------------------------

class RateLimiterMiddleware(BaseHTTPMiddleware):
    """
    Sliding-window rate limiter middleware.
    Uses X-API-Key header (or client IP as fallback) to identify clients.
    Returns 429 with Retry-After header when the limit is exceeded.
    """

    def __init__(
        self,
        app: ASGIApp,
        limit: int = settings.rate_limit_requests_per_minute,
        window_seconds: int = 60,
    ) -> None:
        super().__init__(app)
        self._limit = limit
        self._window_seconds = window_seconds
        self._redis: Optional[aioredis.Redis] = None

    async def _get_redis(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = aioredis.from_url(
                settings.redis_url, encoding="utf-8", decode_responses=True
            )
        return self._redis

    async def dispatch(self, request: Request, call_next) -> Response:
        # Identify client by API key or IP
        api_key = (
            request.headers.get("X-API-Key")
            or request.headers.get("Authorization", "").replace("Bearer ", "")
            or (request.client.host if request.client else "anonymous")
        )

        redis = await self._get_redis()
        allowed, info = await check_rate_limit(
            api_key=api_key,
            limit=self._limit,
            window_seconds=self._window_seconds,
            redis=redis,
        )

        if not allowed:
            retry_after = int(info["reset_at"] - time.time()) + 1
            return JSONResponse(
                status_code=429,
                content={
                    "error": "Rate limit exceeded",
                    "limit": info["limit"],
                    "reset_at": info["reset_at"],
                },
                headers={
                    "Retry-After": str(max(retry_after, 1)),
                    "X-RateLimit-Limit": str(info["limit"]),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(int(info["reset_at"])),
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(info["limit"])
        response.headers["X-RateLimit-Remaining"] = str(info["remaining"])
        response.headers["X-RateLimit-Reset"] = str(int(info["reset_at"]))
        return response
