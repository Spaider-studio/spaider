"""Tests for the structured-logging stack.

Covers:
  - X-Request-ID response header is always present (generated when absent,
    echoed when supplied by the caller)
  - The per-request log line is valid JSON with request_id / agent_id fields
  - Kafka headers carry request_id from producer to consumer and bind the
    contextvars during extraction
"""
from __future__ import annotations

import io
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.logging_config import ContextFilter, SpaiderJsonFormatter, configure_logging
from app.logging_context import (
    agent_id_var,
    bind_request_context,
    request_id_var,
    reset_request_context,
)
from app.middleware.logging_middleware import RequestLoggingMiddleware
from app.services.kafka_consumer import _extract_header


# ---------------------------------------------------------------------------
# Test app — minimal FastAPI with just the logging middleware mounted
# ---------------------------------------------------------------------------


def _build_app() -> FastAPI:
    configure_logging()
    app = FastAPI()
    app.add_middleware(RequestLoggingMiddleware)

    @app.get("/ok")
    async def _ok():
        return {"status": "ok"}

    @app.get("/boom")
    async def _boom():
        raise ValueError("boom")

    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_build_app(), raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Response header
# ---------------------------------------------------------------------------


def test_request_id_generated_when_missing(client: TestClient) -> None:
    r = client.get("/ok")
    assert r.status_code == 200
    assert "X-Request-ID" in r.headers
    assert len(r.headers["X-Request-ID"]) >= 16  # uuid4 hex is 32 chars


def test_request_id_echoed_from_header(client: TestClient) -> None:
    r = client.get("/ok", headers={"X-Request-ID": "trace-abc-123"})
    assert r.headers["X-Request-ID"] == "trace-abc-123"


def test_request_id_present_on_error_responses(client: TestClient) -> None:
    r = client.get("/boom")
    assert r.status_code == 500
    assert "X-Request-ID" in r.headers


# ---------------------------------------------------------------------------
# JSON formatter
# ---------------------------------------------------------------------------


def _capture_log(record_fn) -> dict:
    """Render a single log call through SpaiderJsonFormatter + ContextFilter."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(SpaiderJsonFormatter("%(message)s"))
    handler.addFilter(ContextFilter())

    logger = logging.getLogger("spaider.test.formatter")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False

    record_fn(logger)
    handler.flush()
    return json.loads(stream.getvalue().strip().splitlines()[-1])


def test_formatter_emits_required_fields() -> None:
    tokens = bind_request_context(request_id="rid-1", agent_id="agent-42")
    try:
        parsed = _capture_log(lambda log: log.info("hello"))
    finally:
        reset_request_context(tokens)

    assert parsed["message"] == "hello"
    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "spaider.test.formatter"
    assert parsed["request_id"] == "rid-1"
    assert parsed["agent_id"] == "agent-42"
    assert "timestamp" in parsed and parsed["timestamp"].endswith("+00:00")


def test_formatter_handles_missing_context() -> None:
    # No contextvars bound — fields must still be present (None), not missing.
    # Use fresh vars by resetting.
    request_id_var.set(None)
    agent_id_var.set(None)
    parsed = _capture_log(lambda log: log.info("anon"))

    assert parsed["request_id"] is None
    assert parsed["agent_id"] is None


# ---------------------------------------------------------------------------
# Kafka header plumbing
# ---------------------------------------------------------------------------


def test_extract_header_reads_bytes_headers() -> None:
    msg = SimpleNamespace(headers=[("x-request-id", b"rid-xyz")])
    assert _extract_header(msg, "x-request-id") == "rid-xyz"


def test_extract_header_is_case_insensitive() -> None:
    msg = SimpleNamespace(headers=[("X-Request-ID", b"rid-zzz")])
    assert _extract_header(msg, "x-request-id") == "rid-zzz"


def test_extract_header_missing_returns_none() -> None:
    msg = SimpleNamespace(headers=[])
    assert _extract_header(msg, "x-request-id") is None

    msg2 = SimpleNamespace(headers=None)
    assert _extract_header(msg2, "x-request-id") is None


@pytest.mark.asyncio
async def test_kafka_consumer_binds_context_during_extraction(monkeypatch) -> None:
    """The consumer must set request_id / agent_id contextvars while extract()
    runs so downstream log lines from compressor / resolver carry the trace ID.
    """
    from app.services.kafka_consumer import KafkaConsumerService, _resolve_no_graph

    captured: dict = {}

    async def fake_extract(*, text: str, context=None):
        captured["request_id"] = request_id_var.get()
        captured["agent_id"] = agent_id_var.get()
        # Return a minimal payload-shaped object; the consumer only forwards
        # it to `_resolve_no_graph`, which iterates nodes/edges.
        return SimpleNamespace(nodes=[], edges=[])

    compressor = SimpleNamespace(extract=fake_extract)
    resolver = SimpleNamespace()
    service = KafkaConsumerService()

    message = SimpleNamespace(
        value={
            "message_id": "m-1",
            "agent_id": "agent-xyz",
            "text": "hello world",
            "metadata": None,
        },
        headers=[("x-request-id", b"rid-from-producer")],
    )

    result, agent_id = await service._extract_one(message, compressor, resolver)
    assert agent_id == "agent-xyz"
    assert captured["request_id"] == "rid-from-producer"
    assert captured["agent_id"] == "agent-xyz"

    # After the call, contextvars must be reset (no leak into later tasks).
    assert request_id_var.get() is None
    assert agent_id_var.get() != "agent-xyz"
