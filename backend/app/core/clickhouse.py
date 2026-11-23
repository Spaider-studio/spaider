"""One way to build the ClickHouse client, shared by the audit log and analytics."""
from __future__ import annotations

import logging
import time
from collections import deque
from typing import Awaitable, Callable

from app.config import settings

logger = logging.getLogger(__name__)

_LOG_EVERY_SECONDS = 60.0
# How often rows that could not be written are tried again.
BACKLOG_RETRY_SECONDS = 10.0
_last_logged: dict[str, float] = {}


def create_client():
    """A client that is safe to call from several threads at once.

    By default the driver gives each client a ClickHouse session, and ClickHouse
    refuses a second query while one is running in the same session. Both
    services run their queries on a thread pool and share one client, so
    parallel requests failed with "concurrent queries within the same session".
    Without a session id every request stands alone.
    """
    import clickhouse_connect

    return clickhouse_connect.get_client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        username=settings.clickhouse_user,
        password=settings.clickhouse_password,
        autogenerate_session_id=False,
    )


def log_write_failure(what: str, exc: Exception) -> None:
    """Make a failed audit or analytics write visible without flooding the log.

    These writes run in the background, so nothing else would ever notice that a
    row was lost. One warning per kind of write per minute is enough to see it.
    """
    now = time.monotonic()
    if now - _last_logged.get(what, -_LOG_EVERY_SECONDS) < _LOG_EVERY_SECONDS:
        return
    _last_logged[what] = now
    logger.warning(
        "ClickHouse %s insert failed; the row is kept and retried (repeats within a minute are not logged): %s",
        what,
        exc,
    )


def reset_write_failure_throttle() -> None:
    _last_logged.clear()


class WriteBuffer:
    """Rows that could not be written yet, kept so a ClickHouse outage costs none.

    The audit log and analytics write in the background, so a failed insert has
    no caller to tell. Keeping the row and trying again later means an outage of
    a few minutes loses nothing. The backlog lives in memory and is bounded: past
    ``limit`` rows per table the oldest are dropped, with a warning, so a long
    outage cannot grow without end. A restart loses what is still waiting.
    """

    def __init__(self, limit: int = 5000) -> None:
        self._limit = limit
        self._rows: dict[str, deque] = {}
        self._columns: dict[str, list[str]] = {}
        self.dropped = 0

    def add(self, table: str, columns: list[str], row: list) -> None:
        queue = self._rows.setdefault(table, deque())
        self._columns[table] = columns
        queue.append(row)
        if len(queue) > self._limit:
            queue.popleft()
            self.dropped += 1
            logger.warning(
                "ClickHouse backlog for %s is full (%d rows); dropped the oldest row, %d dropped so far",
                table,
                self._limit,
                self.dropped,
            )

    def pending(self) -> int:
        return sum(len(queue) for queue in self._rows.values())

    async def flush(self, insert: Callable[[str, list[str], list], Awaitable[None]], batch: int = 500) -> int:
        """Write what is waiting, oldest first, and stop at the first failure. Returns the rows written."""
        written = 0
        for table, queue in self._rows.items():
            while queue:
                rows = [queue[index] for index in range(min(batch, len(queue)))]
                try:
                    await insert(table, self._columns[table], rows)
                except Exception as exc:  # noqa: BLE001 - still down, the rows stay for the next try
                    logger.debug("ClickHouse backlog flush failed for %s: %s", table, exc)
                    return written
                for _ in rows:
                    queue.popleft()
                written += len(rows)
        return written
