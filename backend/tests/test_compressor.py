"""
Tests for SemanticCompressor.

The real SemanticCompressor:
  - __init__(): loads system prompt, sets litellm.api_key
  - extract(text, context=None) -> GraphPayload  (async)

Uses litellm.acompletion (NOT anthropic SDK).
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.schemas import GraphPayload


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_litellm_response(text: str) -> MagicMock:
    """Build a litellm-style response mock."""
    choice = MagicMock()
    choice.message.content = text
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage = MagicMock(prompt_tokens=10, completion_tokens=20, total_tokens=30)
    return resp


SAMPLE_JSON = json.dumps({
    "nodes": [
        {"label": "Elon Musk", "type": "PERSON", "properties": {"description": "CEO of Tesla and SpaceX"}},
        {"label": "Tesla", "type": "ORGANIZATION", "properties": {"description": "Electric vehicle manufacturer"}},
        {"label": "SpaceX", "type": "ORGANIZATION", "properties": {"description": "Space exploration company"}},
    ],
    "edges": [
        {"source": "Elon Musk", "target": "Tesla", "relation": "CEO_OF", "properties": {}},
        {"source": "Elon Musk", "target": "SpaceX", "relation": "FOUNDED", "properties": {}},
    ],
})


@pytest.fixture
def compressor():
    """Return a SemanticCompressor with litellm and Redis cache patched."""
    with patch("litellm.acompletion", new_callable=AsyncMock):
        from app.services.compressor import SemanticCompressor
        c = SemanticCompressor()
        # Disable Redis caching so tests are isolated from each other
        c._get_cached_payload = AsyncMock(return_value=None)
        c._set_cached_payload = AsyncMock(return_value=None)
        return c


# ---------------------------------------------------------------------------
# test_extract_returns_graph_payload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_returns_graph_payload(compressor):
    with patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = _mock_litellm_response(SAMPLE_JSON)
        payload = await compressor.extract("Elon Musk is the CEO of Tesla and founded SpaceX.")

    assert isinstance(payload, GraphPayload)
    assert len(payload.nodes) == 3
    assert len(payload.edges) == 2


@pytest.mark.asyncio
async def test_extract_node_types(compressor):
    with patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = _mock_litellm_response(SAMPLE_JSON)
        payload = await compressor.extract("test")

    types = {n.type for n in payload.nodes}
    assert "PERSON" in types
    assert "ORGANIZATION" in types


@pytest.mark.asyncio
async def test_extract_edge_relations(compressor):
    with patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = _mock_litellm_response(SAMPLE_JSON)
        payload = await compressor.extract("test")

    relations = {e.relation for e in payload.edges}
    assert "CEO_OF" in relations
    assert "FOUNDED" in relations


@pytest.mark.asyncio
async def test_extract_skips_edges_with_unknown_nodes(compressor):
    bad_json = json.dumps({
        "nodes": [{"label": "Alice", "type": "Person", "properties": {}}],
        "edges": [
            {"source": "Alice", "target": "UNKNOWN", "relation": "KNOWS", "properties": {}}
        ],
    })
    with patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = _mock_litellm_response(bad_json)
        payload = await compressor.extract("test")

    assert len(payload.nodes) == 1
    assert len(payload.edges) == 0


@pytest.mark.asyncio
async def test_extract_strips_markdown_code_block(compressor):
    wrapped = f"```json\n{SAMPLE_JSON}\n```"
    with patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = _mock_litellm_response(wrapped)
        payload = await compressor.extract("test")

    assert len(payload.nodes) == 3


@pytest.mark.asyncio
async def test_extract_empty_text_returns_empty_payload(compressor):
    """Empty/blank text should return an empty GraphPayload without calling LLM."""
    with patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm:
        payload = await compressor.extract("")

    assert isinstance(payload, GraphPayload)
    assert len(payload.nodes) == 0
    assert len(payload.edges) == 0
    mock_llm.assert_not_called()


@pytest.mark.asyncio
async def test_extract_invalid_json_raises_extraction_error(compressor):
    """All-retries-fail must RAISE ExtractionError, not silently return empty.

    Previous behaviour: sync ingest would return 200 OK with 0 nodes and the
    Kafka consumer would commit the offset — data silently dropped. The raise
    lets sync return 422 and the Kafka worker route the message to the DLQ.
    """
    from app.services.compressor import ExtractionError

    with patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = _mock_litellm_response("not json at all")
        with pytest.raises(ExtractionError) as excinfo:
            await compressor.extract("some text")

    err = excinfo.value
    assert err.attempts == 3
    assert err.last_error is not None
    assert "JSON parse error" in err.last_error
    # Raw preview should contain the (rejected) LLM output so DLQ consumers
    # can diagnose without re-running the call.
    assert "not json" in err.last_raw_preview


@pytest.mark.asyncio
async def test_extract_self_correction_succeeds_on_retry(compressor):
    """One invalid response followed by a valid one should succeed — the
    self-correction loop is not broken by the raise-on-exhaustion change."""
    valid = _mock_litellm_response(SAMPLE_JSON)
    invalid = _mock_litellm_response("garbage")
    with patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm:
        mock_llm.side_effect = [invalid, valid]
        payload = await compressor.extract("test")

    assert isinstance(payload, GraphPayload)
    assert len(payload.nodes) == 3


@pytest.mark.asyncio
async def test_extract_with_context(compressor):
    """extract() with a context dict should still return a valid GraphPayload."""
    with patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = _mock_litellm_response(SAMPLE_JSON)
        payload = await compressor.extract("test", context={"source": "wikipedia"})

    assert isinstance(payload, GraphPayload)
    assert len(payload.nodes) == 3
