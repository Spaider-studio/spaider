import logging

from app.core import throttled_log
from app.core.throttled_log import warn_throttled


def test_a_failure_is_logged_once_a_minute_per_kind(caplog):
    throttled_log.reset()
    logger = logging.getLogger("test")

    with caplog.at_level(logging.WARNING):
        for _ in range(5):
            warn_throttled(logger, "a", "thing failed: %s", "boom")
        warn_throttled(logger, "b", "other thing failed")

    messages = [record.getMessage() for record in caplog.records]
    assert len(messages) == 2
    assert "boom" in messages[0]
