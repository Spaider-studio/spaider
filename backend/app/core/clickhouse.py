"""One way to build the ClickHouse client, shared by the audit log and analytics."""
from __future__ import annotations

from app.config import settings


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
