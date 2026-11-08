"""The ``openai_stub`` fixture: points every LLM and embedding call at the stub."""
from __future__ import annotations

import pytest

from tests.stubs.openai_stub import OpenAIStub


@pytest.fixture
def openai_stub(monkeypatch):
    import litellm

    from app.config import settings

    # litellm caches async clients per loop; each test has a fresh loop.
    litellm.in_memory_llm_clients_cache.flush_cache()
    stub = OpenAIStub()
    stub.dimensions = settings.embedding_dimensions
    stub.start()
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "llm_model", "gpt-4o-mini")
    monkeypatch.setattr(settings, "llm_base_url", stub.base_url)
    monkeypatch.setattr(settings, "llm_api_key", "stub-key")
    monkeypatch.setattr(settings, "embedding_model", "text-embedding-3-small")
    monkeypatch.setattr(settings, "embedding_base_url", stub.base_url)
    monkeypatch.setattr(settings, "embedding_api_key", "stub-key")
    # Services built earlier hold the previous stub's address.
    from app.api.v1 import ingest, mcp_server, query

    monkeypatch.setattr(ingest, "_compressor", None)
    monkeypatch.setattr(ingest, "_resolver", None)
    monkeypatch.setattr(query, "_query_service", None)
    monkeypatch.setattr(mcp_server, "_query_service", None)
    monkeypatch.setattr(settings, "litellm_retry_max_attempts", 1)
    monkeypatch.setattr("app.services.compressor._MAX_RETRIES", 1)
    yield stub
    litellm.in_memory_llm_clients_cache.flush_cache()
    stub.stop()
