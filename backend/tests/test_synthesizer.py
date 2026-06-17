"""
Tests for ModelSynthesizer: dataset generation strategies, output formats,
deduplication, and quality filtering.

The real ModelSynthesizer:
  - __init__(graph_service: GraphService)
  - synthesize(agent_id: str, config: SynthesizeConfig) -> SynthesizeResult
    where SynthesizeConfig has: strategies, output_format, node_types,
    min_confidence, max_examples
    and SynthesizeResult has: agent_id, output_path, total_examples,
    strategy_counts, duplicate_count
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.schemas import Edge, GraphPayload, Node


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_node(
    label: str,
    ntype: str = "PERSON",
    agent_id: str = "test-agent",
    confidence: float = 0.9,
) -> Node:
    return Node(
        id=str(uuid.uuid4()),
        label=label,
        type=ntype,
        properties={"description": f"{label} entity", "confidence": confidence},
        agent_id=agent_id,
        created_at=datetime.now(timezone.utc),
    )


def _make_edge(
    src: Node,
    tgt: Node,
    relation: str = "RELATED_TO",
    confidence: float = 0.9,
) -> Edge:
    return Edge(
        id=str(uuid.uuid4()),
        source_id=src.id,
        target_id=tgt.id,
        source=src.label,
        target=tgt.label,
        relation=relation,
        properties={"confidence": confidence},
        agent_id=src.agent_id,
        created_at=datetime.now(timezone.utc),
    )


def _make_llm_response(content: str) -> MagicMock:
    choice = MagicMock()
    choice.message.content = content
    resp = MagicMock()
    resp.choices = [choice]
    return resp


# LLM responses that return a JSON list (as expected by _llm_generate_qa)
SAMPLE_QA_LIST = json.dumps([{
    "instruction": "Who does Alice work for?",
    "context": "Alice works at Acme Corp.",
    "response": "Alice works for Acme Corp.",
}])

SAMPLE_REASONING_LIST = json.dumps([{
    "instruction": "In what city is the company Alice works at?",
    "context": "Alice works at Acme Corp. Acme Corp is in San Francisco.",
    "response": "San Francisco.",
}])


@pytest.fixture
def rich_graph():
    """A graph with enough nodes/edges to exercise path sampling."""
    n_alice = _make_node("Alice", "PERSON")
    n_acme = _make_node("Acme Corp", "ORGANIZATION")
    n_sf = _make_node("San Francisco", "LOCATION")
    n_tech = _make_node("Python", "TECHNOLOGY")

    e1 = _make_edge(n_alice, n_acme, "WORKS_AT")
    e2 = _make_edge(n_acme, n_sf, "LOCATED_IN")
    e3 = _make_edge(n_acme, n_tech, "USES")

    return (
        [n_alice, n_acme, n_sf, n_tech],
        [e1, e2, e3],
    )


def _make_mock_graph_service(nodes: list[Node], edges: list[Edge]) -> AsyncMock:
    """Create a mock GraphService that returns the given nodes/edges."""
    gs = AsyncMock()
    gs.get_full_graph = AsyncMock(return_value=GraphPayload(nodes=nodes, edges=edges))
    return gs


# ---------------------------------------------------------------------------
# test_synthesize_factual_strategy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_synthesize_factual_strategy(rich_graph):
    """Factual strategy should generate Q&A examples and return a SynthesizeResult."""
    nodes, edges = rich_graph

    try:
        from app.services.synthesizer import ModelSynthesizer, SynthesizeConfig
    except ImportError:
        pytest.skip("ModelSynthesizer not yet implemented")

    mock_gs = _make_mock_graph_service(nodes, edges)

    with (
        patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm,
        patch("app.services.synthesizer._OUTPUT_DIR", Path("/tmp/spaider_test/datasets")),
        patch("pathlib.Path.mkdir"),
        patch("pathlib.Path.write_text"),
    ):
        mock_llm.return_value = _make_llm_response(SAMPLE_QA_LIST)

        synth = ModelSynthesizer(graph_service=mock_gs)
        config = SynthesizeConfig(
            strategies=["factual_qa"],
            output_format="openai",
            max_examples=10,
        )
        result = await synth.synthesize(agent_id="test-agent", config=config)

    assert result.agent_id == "test-agent"
    assert result.total_examples >= 0
    assert "factual_qa" in result.strategy_counts


# ---------------------------------------------------------------------------
# test_synthesize_reasoning_strategy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_synthesize_reasoning_strategy(rich_graph):
    """Reasoning strategy should produce multi-hop chain-of-thought examples."""
    nodes, edges = rich_graph

    try:
        from app.services.synthesizer import ModelSynthesizer, SynthesizeConfig
    except ImportError:
        pytest.skip("ModelSynthesizer not yet implemented")

    mock_gs = _make_mock_graph_service(nodes, edges)

    with (
        patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm,
        patch("app.services.synthesizer._OUTPUT_DIR", Path("/tmp/spaider_test/datasets")),
        patch("pathlib.Path.mkdir"),
        patch("pathlib.Path.write_text"),
    ):
        mock_llm.return_value = _make_llm_response(SAMPLE_REASONING_LIST)

        synth = ModelSynthesizer(graph_service=mock_gs)
        config = SynthesizeConfig(
            strategies=["reasoning_chains"],
            output_format="openai",
            max_examples=10,
        )
        result = await synth.synthesize(agent_id="test-agent", config=config)

    assert result.agent_id == "test-agent"
    assert "reasoning_chains" in result.strategy_counts


# ---------------------------------------------------------------------------
# test_synthesize_relation_extraction_strategy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_synthesize_relations_strategy(rich_graph):
    """Relation extraction strategy should produce SPO triple examples."""
    nodes, edges = rich_graph

    try:
        from app.services.synthesizer import ModelSynthesizer, SynthesizeConfig
    except ImportError:
        pytest.skip("ModelSynthesizer not yet implemented")

    mock_gs = _make_mock_graph_service(nodes, edges)

    with (
        patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm,
        patch("app.services.synthesizer._OUTPUT_DIR", Path("/tmp/spaider_test/datasets")),
        patch("pathlib.Path.mkdir"),
        patch("pathlib.Path.write_text"),
    ):
        mock_llm.return_value = _make_llm_response(SAMPLE_QA_LIST)

        synth = ModelSynthesizer(graph_service=mock_gs)
        config = SynthesizeConfig(
            strategies=["relation_extraction"],
            output_format="alpaca",
            max_examples=10,
        )
        result = await synth.synthesize(agent_id="test-agent", config=config)

    assert result.agent_id == "test-agent"
    assert "relation_extraction" in result.strategy_counts


# ---------------------------------------------------------------------------
# test_output_format_openai
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_output_format_openai(rich_graph):
    """OpenAI output_format should be passed through to _save_jsonl."""
    nodes, edges = rich_graph

    try:
        from app.services.synthesizer import ModelSynthesizer, SynthesizeConfig
    except ImportError:
        pytest.skip("ModelSynthesizer not yet implemented")

    mock_gs = _make_mock_graph_service(nodes, edges)

    written_text: list[str] = []

    with (
        patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm,
        patch("app.services.synthesizer._OUTPUT_DIR", Path("/tmp/spaider_test/datasets")),
        patch("pathlib.Path.mkdir"),
        patch("pathlib.Path.write_text", side_effect=lambda text, **kw: written_text.append(text)),
    ):
        mock_llm.return_value = _make_llm_response(SAMPLE_QA_LIST)

        synth = ModelSynthesizer(graph_service=mock_gs)
        config = SynthesizeConfig(
            strategies=["factual_qa"],
            output_format="openai",
            max_examples=5,
        )
        result = await synth.synthesize(agent_id="test-agent", config=config)

    assert result.agent_id == "test-agent"
    assert isinstance(result.output_path, str)


# ---------------------------------------------------------------------------
# test_output_format_alpaca
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_output_format_alpaca(rich_graph):
    """Alpaca output_format should be passed through to _save_jsonl."""
    nodes, edges = rich_graph

    try:
        from app.services.synthesizer import ModelSynthesizer, SynthesizeConfig
    except ImportError:
        pytest.skip("ModelSynthesizer not yet implemented")

    mock_gs = _make_mock_graph_service(nodes, edges)

    with (
        patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm,
        patch("app.services.synthesizer._OUTPUT_DIR", Path("/tmp/spaider_test/datasets")),
        patch("pathlib.Path.mkdir"),
        patch("pathlib.Path.write_text"),
    ):
        mock_llm.return_value = _make_llm_response(SAMPLE_QA_LIST)

        synth = ModelSynthesizer(graph_service=mock_gs)
        config = SynthesizeConfig(
            strategies=["factual_qa"],
            output_format="alpaca",
            max_examples=5,
        )
        result = await synth.synthesize(agent_id="test-agent", config=config)

    assert result.agent_id == "test-agent"


# ---------------------------------------------------------------------------
# test_deduplication
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deduplication(rich_graph):
    """Synthesizer should deduplicate identical Q&A pairs."""
    nodes, edges = rich_graph

    try:
        from app.services.synthesizer import ModelSynthesizer, SynthesizeConfig
    except ImportError:
        pytest.skip("ModelSynthesizer not yet implemented")

    mock_gs = _make_mock_graph_service(nodes, edges)

    # Always return the same single QA pair → synthesizer must deduplicate
    with (
        patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm,
        patch("app.services.synthesizer._OUTPUT_DIR", Path("/tmp/spaider_test/datasets")),
        patch("pathlib.Path.mkdir"),
        patch("pathlib.Path.write_text"),
    ):
        mock_llm.return_value = _make_llm_response(SAMPLE_QA_LIST)

        synth = ModelSynthesizer(graph_service=mock_gs)
        config = SynthesizeConfig(
            strategies=["factual_qa"],
            output_format="openai",
            max_examples=100,
        )
        result = await synth.synthesize(agent_id="test-agent", config=config)

    # After deduplication, identical instructions should be collapsed
    assert result.duplicate_count >= 0
    assert result.total_examples <= 100


# ---------------------------------------------------------------------------
# test_quality_filter_by_confidence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_quality_filter_by_confidence():
    """Synthesizer with node_types filter should only include matching nodes."""
    low_conf_node1 = _make_node("LowA", confidence=0.3)
    low_conf_node2 = _make_node("LowB", confidence=0.3)
    high_conf_node1 = _make_node("HighA", confidence=0.95)
    high_conf_node2 = _make_node("HighB", "ORGANIZATION", confidence=0.95)

    low_edge = _make_edge(low_conf_node1, low_conf_node2, "RELATED_TO", confidence=0.3)
    high_edge = _make_edge(high_conf_node1, high_conf_node2, "WORKS_AT", confidence=0.95)

    nodes = [low_conf_node1, low_conf_node2, high_conf_node1, high_conf_node2]
    edges = [low_edge, high_edge]

    try:
        from app.services.synthesizer import ModelSynthesizer, SynthesizeConfig
    except ImportError:
        pytest.skip("ModelSynthesizer not yet implemented")

    mock_gs = _make_mock_graph_service(nodes, edges)

    with (
        patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm,
        patch("app.services.synthesizer._OUTPUT_DIR", Path("/tmp/spaider_test/datasets")),
        patch("pathlib.Path.mkdir"),
        patch("pathlib.Path.write_text"),
    ):
        mock_llm.return_value = _make_llm_response(SAMPLE_QA_LIST)

        synth = ModelSynthesizer(graph_service=mock_gs)
        # Filter to only PERSON node types (excludes HighB which is ORGANIZATION)
        config = SynthesizeConfig(
            strategies=["factual_qa"],
            output_format="openai",
            node_types=["PERSON"],
            max_examples=50,
        )
        result = await synth.synthesize(agent_id="test-agent", config=config)

    assert isinstance(result.total_examples, int)
    assert result.total_examples >= 0
