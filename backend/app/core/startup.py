"""Starting what is not up yet.

After a Docker restart every container starts at once, ignoring startup order,
and Neo4j, Redis, ClickHouse and Postgres each need longer than the API. Trying
once at startup and then running without a service for as long as the API stays
up turned a few seconds of delay into a permanent fault.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

DEFAULT_DELAYS: tuple[float, ...] = (1, 2, 4, 8, 16, 30)


async def retry_until_ready(
    name: str,
    attempt: Callable[[], Awaitable[bool]],
    *,
    delays: tuple[float, ...] = DEFAULT_DELAYS,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Run ``attempt`` until it returns True, waiting longer after each failure, up to the last delay.

    An attempt that raises counts as a failed attempt. Cancelling the task ends it.
    """
    tries = 0
    while True:
        try:
            if await attempt():
                if tries:
                    logger.info("%s is ready after %d failed attempts", name, tries)
                return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - not ready yet, try again
            logger.debug("%s attempt failed: %s", name, exc)
        delay = delays[min(tries, len(delays) - 1)]
        tries += 1
        if tries == 1:
            logger.warning("%s is not ready yet. Trying again every few seconds, up to %ss apart.", name, delays[-1])
        await sleep(delay)
