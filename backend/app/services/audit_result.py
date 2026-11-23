"""What the audit log hands back, and when it cannot answer."""
from __future__ import annotations


class AuditStoreUnavailable(RuntimeError):
    """ClickHouse failed and the Kafka fallback found nothing either.

    An empty list would then be a guess that looks like an answer, so callers
    report the store as unavailable instead.
    """


class AuditResult(list):
    """Audit records and where they came from.

    ``complete`` is False for the Kafka fallback, which only holds what Kafka
    still retains (seven days), so the caller can say the list may be partial.
    """

    def __init__(self, items=(), *, source: str = "clickhouse", complete: bool = True) -> None:
        super().__init__(items)
        self.source = source
        self.complete = complete
