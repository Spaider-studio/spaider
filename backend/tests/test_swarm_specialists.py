"""
Tests for the swarm specialist workers: summariser, classifier, enricher.

Each specialist is exercised against a mocked GraphService and a patched LLM
so the tests are hermetic (no Neo4j, no provider calls). They assert the graph
write that each specialist is responsible for, plus the input-sanitising guards.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.schemas import Node
from app.workers import swarm_listener as sl


def _node(**kw) -> Node:
    base = dict(label="Acme Corp", type="OTHER", agent_id="agent-1")
    base.update(kw)
    return Node(**base)


# ---------------------------------------------------------------------------
# summariser
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_summariser_writes_description():
    graph = AsyncMock()
    graph.get_node_by_id = AsyncMock(return_value=_node(description="A long body of source text."))
    with patch.object(sl, "_llm_complete", AsyncMock(return_value="A concise summary.")):
        await sl._specialist_summariser("n1", [], graph)
    graph.set_node_description.assert_awaited_once_with("n1", "A concise summary.")


@pytest.mark.asyncio
async def test_summariser_skips_node_without_text():
    graph = AsyncMock()
    graph.get_node_by_id = AsyncMock(return_value=_node(label="", description=None))
    with patch.object(sl, "_llm_complete", AsyncMock()) as llm:
        await sl._specialist_summariser("n1", [], graph)
    llm.assert_not_awaited()
    graph.set_node_description.assert_not_awaited()


@pytest.mark.asyncio
async def test_summariser_missing_node_is_safe():
    graph = AsyncMock()
    graph.get_node_by_id = AsyncMock(return_value=None)
    await sl._specialist_summariser("ghost", [], graph)
    graph.set_node_description.assert_not_awaited()


# ---------------------------------------------------------------------------
# classifier
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_classifier_sets_valid_type():
    graph = AsyncMock()
    graph.get_node_by_id = AsyncMock(return_value=_node(label="Marie Curie"))
    with patch.object(sl, "_llm_complete", AsyncMock(return_value="person")):
        await sl._specialist_classifier("n1", [], graph)
    graph.set_node_type.assert_awaited_once_with("n1", "PERSON")


@pytest.mark.asyncio
async def test_classifier_rejects_unknown_type():
    graph = AsyncMock()
    graph.get_node_by_id = AsyncMock(return_value=_node(label="???"))
    with patch.object(sl, "_llm_complete", AsyncMock(return_value="WIDGET")):
        await sl._specialist_classifier("n1", [], graph)
    graph.set_node_type.assert_not_awaited()


# ---------------------------------------------------------------------------
# enricher
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enricher_merges_sanitised_entities():
    graph = AsyncMock()
    graph.get_node_by_id = AsyncMock(return_value=_node(label="OpenAI", agent_id="agent-1"))
    reply = (
        '[{"label": "Sam Altman", "type": "PERSON", "relation": "CEO_OF"}, '
        '{"label": "GPT-4", "type": "WIDGET", "relation": "NONSENSE"}]'
    )
    fake_embedder = MagicMock()
    fake_embedder.embed = AsyncMock(return_value=[0.1, 0.2, 0.3])
    fake_embedder.close = AsyncMock()

    with patch.object(sl, "_llm_complete", AsyncMock(return_value=reply)), \
         patch.object(sl, "EmbeddingService", return_value=fake_embedder):
        await sl._specialist_enricher("n1", [], graph)

    graph.write_graph.assert_awaited_once()
    payload, agent_id = graph.write_graph.await_args.args
    assert agent_id == "agent-1"
    assert [n.label for n in payload.nodes] == ["Sam Altman", "GPT-4"]
    # Second entity's out-of-vocab type/relation were coerced to safe defaults.
    assert payload.nodes[1].type == "OTHER"
    assert payload.edges[1].relation == "RELATED_TO"
    # Every new node carries an embedding so it is vector-searchable.
    assert all(n.embedding == [0.1, 0.2, 0.3] for n in payload.nodes)
    fake_embedder.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_enricher_no_entities_does_not_write():
    graph = AsyncMock()
    graph.get_node_by_id = AsyncMock(return_value=_node(label="Obscure"))
    with patch.object(sl, "_llm_complete", AsyncMock(return_value="[]")), \
         patch.object(sl, "EmbeddingService", return_value=MagicMock()):
        await sl._specialist_enricher("n1", [], graph)
    graph.write_graph.assert_not_awaited()


@pytest.mark.asyncio
async def test_enricher_skips_node_without_agent():
    graph = AsyncMock()
    graph.get_node_by_id = AsyncMock(return_value=_node(agent_id=None))
    with patch.object(sl, "_llm_complete", AsyncMock()) as llm:
        await sl._specialist_enricher("n1", [], graph)
    llm.assert_not_awaited()
    graph.write_graph.assert_not_awaited()


# ---------------------------------------------------------------------------
# dispatch + helpers
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_routes_to_each_specialist():
    graph = AsyncMock()
    with patch.object(sl, "_specialist_summariser", AsyncMock()) as summ, \
         patch.object(sl, "_specialist_enricher", AsyncMock()) as enr, \
         patch.object(sl, "_specialist_classifier", AsyncMock()) as cls:
        await sl._dispatch("n1", "summariser", [], graph)
        await sl._dispatch("n1", "enricher", [], graph)
        await sl._dispatch("n1", "classifier", [], graph)
        await sl._dispatch("n1", "unknown", [], graph)  # no-op, must not raise
    summ.assert_awaited_once()
    enr.assert_awaited_once()
    cls.assert_awaited_once()


def test_parse_json_array_handles_fences_and_garbage():
    assert sl._parse_json_array('```json\n[{"a": 1}]\n```') == [{"a": 1}]
    assert sl._parse_json_array('[{"a": 1}, {"b": 2}]') == [{"a": 1}, {"b": 2}]
    assert sl._parse_json_array("not json at all") == []
    assert sl._parse_json_array('["string", {"a": 1}]') == [{"a": 1}]


def test_format_working_memory():
    assert sl._format_working_memory([]) == ""
    out = sl._format_working_memory([{"role": "user", "content": "hi"}])
    assert "user: hi" in out


# ---------------------------------------------------------------------------
# Stream-consumer connection — regression for the Redis pool-churn bug
# ---------------------------------------------------------------------------


def test_stream_socket_timeout_exceeds_block():
    """The dedicated stream read must out-wait the server-side BLOCK.

    redis-py 8.x derives a client read timeout from BLOCK; if it equals the
    block time the client timeout wins the race on every idle read, tearing
    the connection down. The socket timeout must be strictly larger.
    """
    assert sl._STREAM_SOCKET_TIMEOUT_S > sl._BLOCK_MS / 1000


@pytest.mark.asyncio
async def test_swarm_listener_idle_read_timeout_is_benign(monkeypatch):
    """An idle blocking read (RedisTimeoutError) is treated as "no messages":
    the loop continues immediately, with no reconnect-delay penalty, and the
    read runs on a dedicated client built with the long socket timeout."""
    import asyncio

    from redis.exceptions import TimeoutError as RedisTimeoutError

    # Dedicated stream client: first read times out (idle), second read breaks
    # the otherwise-infinite loop so the test terminates.
    stream_client = AsyncMock()
    calls = {"n": 0}

    async def _xreadgroup(**_kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RedisTimeoutError("Timeout reading from redis:6379")
        raise asyncio.CancelledError()

    stream_client.xreadgroup = _xreadgroup
    stream_client.aclose = AsyncMock()

    captured = {}

    def _from_url(url, **kw):
        captured["url"] = url
        captured["kw"] = kw
        return stream_client

    monkeypatch.setattr("redis.asyncio.from_url", _from_url)

    # Record every sleep so we can prove the reconnect penalty was never paid.
    slept: list[float] = []

    async def _fake_sleep(delay, *a, **k):
        slept.append(delay)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
    monkeypatch.setattr(sl, "_heartbeat_loop", AsyncMock())  # keep heartbeat inert

    graph = AsyncMock()
    graph._driver = MagicMock()

    with pytest.raises(asyncio.CancelledError):
        await sl.swarm_listener(redis_client=AsyncMock(), graph_service=graph)

    # Dedicated connection, off the shared request pool, with the long timeout.
    assert captured["kw"].get("socket_timeout") == sl._STREAM_SOCKET_TIMEOUT_S
    # The idle timeout did NOT incur the reconnect-delay penalty.
    assert sl._RECONNECT_DELAY not in slept
    # Both reads were attempted (benign timeout → continue → next read).
    assert calls["n"] == 2
    # Dedicated connection released on shutdown.
    stream_client.aclose.assert_awaited()
