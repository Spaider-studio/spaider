"""
Tests for Spaider LangChain and LlamaIndex integrations.

LangChain and LlamaIndex are optional dependencies.  Tests that require them
are skipped automatically when the packages are not installed.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from spaider.models import GraphPayload, IngestResult, QueryResult

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_ingest_result(**kwargs) -> IngestResult:
    defaults = {"nodes_created": 1, "edges_created": 1, "nodes_merged": 0, "edges_merged": 0, "status": "ok"}
    defaults.update(kwargs)
    return IngestResult(**defaults)


def _make_query_result(answer: str = "Test answer.") -> QueryResult:
    return QueryResult(
        answer=answer,
        subgraph=GraphPayload(nodes=[], edges=[]),
        confidence_score=0.9,
    )


# ── LangChain integration ─────────────────────────────────────────────────────

langchain_available = False
try:
    import langchain_core  # noqa: F401
    langchain_available = True
except ImportError:
    pass


@pytest.mark.skipif(not langchain_available, reason="langchain-core not installed")
class TestSpaiderMemory:
    """Tests for SpaiderMemory (LangChain)."""

    def _make_memory(self, mock_client: Any):
        from spaider.integrations.langchain import SpaiderMemory

        mem = SpaiderMemory(api_key="sk-test", agent_id="test-agent")
        mem._client = mock_client
        return mem

    def test_memory_variables(self):
        from spaider.integrations.langchain import SpaiderMemory

        mem = SpaiderMemory(api_key="sk-test", agent_id="test-agent")
        assert "history" in mem.memory_variables

    def test_load_memory_variables_calls_query(self):
        mock_client = MagicMock()
        mock_client.query.return_value = _make_query_result("Max works at Google.")

        mem = self._make_memory(mock_client)
        result = mem.load_memory_variables({"input": "Where does Max work?"})

        mock_client.query.assert_called_once_with("Where does Max work?", top_k=5)
        assert result["history"] == "Max works at Google."

    def test_load_memory_variables_empty_input(self):
        mock_client = MagicMock()
        mem = self._make_memory(mock_client)

        result = mem.load_memory_variables({"input": ""})
        mock_client.query.assert_not_called()
        assert result["history"] == ""

    def test_load_memory_variables_api_error_returns_empty(self):
        from spaider.exceptions import ServerError

        mock_client = MagicMock()
        mock_client.query.side_effect = ServerError()

        mem = self._make_memory(mock_client)
        result = mem.load_memory_variables({"input": "question"})
        assert result["history"] == ""

    def test_save_context_ingests_both_messages(self):
        mock_client = MagicMock()
        mock_client.ingest.return_value = _make_ingest_result()

        mem = self._make_memory(mock_client)
        mem.save_context(
            inputs={"input": "Human message"},
            outputs={"output": "AI response"},
        )

        assert mock_client.ingest.call_count == 2
        calls = [str(c) for c in mock_client.ingest.call_args_list]
        assert any("Human message" in c for c in calls)
        assert any("AI response" in c for c in calls)

    def test_save_context_ingest_error_does_not_raise(self):
        from spaider.exceptions import ServerError

        mock_client = MagicMock()
        mock_client.ingest.side_effect = ServerError()

        mem = self._make_memory(mock_client)
        # Should not raise
        mem.save_context(inputs={"input": "text"}, outputs={"output": "response"})

    def test_clear_does_not_raise(self):
        mock_client = MagicMock()
        mem = self._make_memory(mock_client)
        mem.clear()  # smoke test — no assertion needed

    def test_custom_memory_key(self):
        from spaider.integrations.langchain import SpaiderMemory

        mem = SpaiderMemory(
            api_key="sk-test",
            agent_id="test-agent",
            memory_key="context",
        )
        assert "context" in mem.memory_variables


# ── LlamaIndex integration ────────────────────────────────────────────────────

llamaindex_available = False
try:
    import llama_index  # noqa: F401
    llamaindex_available = True
except ImportError:
    pass


class TestSpaiderIndex:
    """Tests for SpaiderIndex — does NOT require llama-index to be installed."""

    def _make_index(self, mock_client: Any):
        from spaider.integrations.llamaindex import SpaiderIndex

        idx = SpaiderIndex(api_key="sk-test", agent_id="test-agent")
        idx._client = mock_client
        return idx

    def test_add_text_calls_ingest(self):
        mock_client = MagicMock()
        mock_client.ingest.return_value = _make_ingest_result()

        idx = self._make_index(mock_client)
        idx.add_text("Max works at Google.")

        mock_client.ingest.assert_called_once_with("Max works at Google.", source=None)

    def test_add_text_with_source(self):
        mock_client = MagicMock()
        mock_client.ingest.return_value = _make_ingest_result()

        idx = self._make_index(mock_client)
        idx.add_text("text", source="pdf")

        mock_client.ingest.assert_called_once_with("text", source="pdf")

    def test_add_texts_calls_ingest_for_each(self):
        mock_client = MagicMock()
        mock_client.ingest.return_value = _make_ingest_result()

        idx = self._make_index(mock_client)
        idx.add_texts(["text A", "text B", "text C"])

        assert mock_client.ingest.call_count == 3

    def test_query_returns_query_result(self):
        mock_client = MagicMock()
        mock_client.query.return_value = _make_query_result("The answer is 42.")

        idx = self._make_index(mock_client)
        result = idx.query("What is the answer?")

        mock_client.query.assert_called_once_with("What is the answer?", top_k=10)
        assert result.answer == "The answer is 42."

    def test_traverse_delegates_to_client(self):
        mock_client = MagicMock()
        mock_client.traverse.return_value = GraphPayload(nodes=[], edges=[])

        idx = self._make_index(mock_client)
        result = idx.traverse("uuid-1", depth=3)

        mock_client.traverse.assert_called_once_with(node_id="uuid-1", depth=3)
        assert isinstance(result, GraphPayload)

    def test_get_graph_delegates_to_client(self):
        mock_client = MagicMock()
        mock_client.get_graph.return_value = GraphPayload(nodes=[], edges=[])

        idx = self._make_index(mock_client)
        result = idx.get_graph()

        mock_client.get_graph.assert_called_once()
        assert isinstance(result, GraphPayload)

    def test_context_manager(self):
        mock_client = MagicMock()

        from spaider.integrations.llamaindex import SpaiderIndex

        idx = SpaiderIndex(api_key="sk-test", agent_id="test-agent")
        idx._client = mock_client

        with idx as i:
            assert i is idx

        mock_client.close.assert_called_once()


@pytest.mark.skipif(not llamaindex_available, reason="llama-index not installed")
class TestSpaiderQueryEngine:
    """Tests for SpaiderQueryEngine — requires llama-index."""

    def test_query_returns_response(self):
        from llama_index.core.schema import QueryBundle

        from spaider.integrations.llamaindex import SpaiderQueryEngine

        engine = SpaiderQueryEngine(api_key="sk-test", agent_id="test-agent")
        engine._index._client = MagicMock()
        engine._index._client.query.return_value = _make_query_result("Found it.")

        bundle = QueryBundle(query_str="test query")
        response = engine._query(bundle)

        assert str(response) == "Found it."
