"""One way to build the ClickHouse client, shared by the audit log and analytics."""
from __future__ import annotations

import logging
import time

from app.config import settings

logger = logging.getLogger(__name__)

_LOG_EVERY_SECONDS = 60.0
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
        "ClickHouse %s insert failed; a row was lost (repeats within a minute are not logged): %s", what, exc
    )


def reset_write_failure_throttle() -> None:
    _last_logged.clear()
