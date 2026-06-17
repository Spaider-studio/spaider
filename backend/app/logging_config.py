"""JSON-structured logging config for SpAIder backend and worker.

Call `configure_logging()` once at process start (FastAPI app import, worker
entrypoint, ad-hoc scripts). It installs a single stdout StreamHandler with a
JSON formatter and a `ContextFilter` that injects `request_id` / `agent_id`
from `app.logging_context` contextvars into every record.

Uvicorn's own access log is suppressed — the request-logging middleware
already emits one structured line per request with status_code + duration.
"""
from __future__ import annotations

import logging
import logging.config
from datetime import datetime, timezone

from pythonjsonlogger import jsonlogger

from app.logging_context import agent_id_var, request_id_var


class ContextFilter(logging.Filter):
    """Attach request-scoped context to every LogRecord.

    Explicit ``extra={"request_id": ..., "agent_id": ...}`` on the log call
    wins — we only fill in from contextvars when the caller did not supply a
    value. This matters for the request-completion log emitted in middleware
    (where Starlette's BaseHTTPMiddleware may run in a different asyncio task
    than the endpoint and not see contextvar writes made by ``require_auth``).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(record, "request_id", None) is None:
            record.request_id = request_id_var.get()
        if getattr(record, "agent_id", None) is None:
            record.agent_id = agent_id_var.get()
        return True


class SpaiderJsonFormatter(jsonlogger.JsonFormatter):
    """JSON formatter with a fixed field order and ISO-8601 UTC timestamps."""

    def add_fields(self, log_record, record, message_dict):  # type: ignore[override]
        super().add_fields(log_record, record, message_dict)
        log_record["timestamp"] = datetime.fromtimestamp(
            record.created, tz=timezone.utc
        ).isoformat()
        log_record["level"] = record.levelname
        log_record["logger"] = record.name
        log_record["request_id"] = getattr(record, "request_id", None)
        log_record["agent_id"] = getattr(record, "agent_id", None)
        log_record.pop("asctime", None)
        log_record.pop("levelname", None)
        log_record.pop("name", None)


_LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "context": {"()": "app.logging_config.ContextFilter"},
    },
    "formatters": {
        "json": {
            "()": "app.logging_config.SpaiderJsonFormatter",
            "format": "%(message)s",
        },
    },
    "handlers": {
        "default": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "formatter": "json",
            "filters": ["context"],
        },
    },
    "root": {"handlers": ["default"], "level": "INFO"},
    "loggers": {
        "uvicorn": {"handlers": ["default"], "level": "INFO", "propagate": False},
        "uvicorn.error": {"handlers": ["default"], "level": "INFO", "propagate": False},
        "uvicorn.access": {"handlers": [], "level": "WARNING", "propagate": False},
    },
}


_configured = False


def configure_logging() -> None:
    """Idempotent — safe to call from every entrypoint (API, worker, scripts)."""
    global _configured
    if _configured:
        return
    logging.config.dictConfig(_LOGGING_CONFIG)
    _configured = True
