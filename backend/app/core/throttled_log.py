"""Warnings for failures nobody is waiting on, at most one a minute per kind.

Background work such as recording an audit event or an analytics row has no caller
to tell when it fails. Swallowing the error hid dead features for as long as the
process ran. One line a minute per kind is enough to notice, and cannot flood a log.
"""
from __future__ import annotations

import logging
import time

_EVERY_SECONDS = 60.0
_last: dict[str, float] = {}


def warn_throttled(logger: logging.Logger, key: str, message: str, *args: object) -> None:
    now = time.monotonic()
    if now - _last.get(key, -_EVERY_SECONDS) < _EVERY_SECONDS:
        return
    _last[key] = now
    logger.warning(message + " (repeats within a minute are not logged)", *args)


def reset() -> None:
    _last.clear()
