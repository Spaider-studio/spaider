"""
Tests for the Alchemist inverse pass (Pass 4) in consolidation.py.

Covers:
- Hallucinated edge labels are silently discarded (not persisted, no crash).
- Valid proposals with allowed labels and sufficient confidence are persisted.
- LLM failures return None and are skipped without crashing.
- Low-confidence proposals are skipped.
- `is_related=False` proposals are skipped.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.lib.consolidation import (
    ProposedEdge,
    _create_proposed_edge,
    _llm_propose_edge,
    _propose_relations,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_driver(session_records: list[list[dict]]):
    """Return a mock AsyncDriver whose consecutive session() calls yield
    the rows in *session_records* in order."""
    session_mocks = []
    for records in session_records:
        session_obj = AsyncMock()
        session_ctx = MagicMock()
        session_ctx.__aenter__ = AsyncMock(return_value=session_obj)
        session_ctx.__aexit__ = AsyncMock(return_value=False)

        async def _run_rows(*_args, records=records, **_kwargs):
            class _AsyncIter:
                def __init__(self_):
                    self_._it = iter(records)
                def __aiter__(self_):
                    return self_
                async def __anext__(self_):
                    try:
                        return next(self_._it)
                    except StopIteration:
                        raise StopAsyncIteration
            return _AsyncIter()

        session_obj.run = AsyncMock(side_effect=_run_rows)
        session_mocks.append(session_ctx)

    driver = MagicMock()
    driver.session = MagicMock(side_effect=session_mocks)
    return driver


def _minimal_node(node_id: str, embedding: list[float] | None = None) -> dict:
    if embedding is None:
        embedding = [0.1, 0.2, 0.9]
    return {
        "id": node_id,
        "label": f"Entity_{node_id}",
        "type": "concept",
        "properties": json.dumps({"description": f"desc {node_id}"}),
        "embedding": embedding,
    }


# ---------------------------------------------------------------------------
# ProposedEdge model
# ---------------------------------------------------------------------------


class TestProposedEdge:
    def test_parse_valid(self):
        raw = '{"is_related": true, "proposed_label": "RELATES_TO", "confidence": 0.9}'
        edge = ProposedEdge.model_validate_json(raw)
        assert edge.is_related is True
        assert edge.proposed_label == "RELATES_TO"
        assert edge.confidence == pytest.approx(0.9)

    def test_parse_not_related(self):
        raw = '{"is_related": false, "proposed_label": "", "confidence": 0.1}'
        edge = ProposedEdge.model_validate_json(raw)
        assert edge.is_related is False

    def test_parse_invalid_raises(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ProposedEdge.model_validate_json('{"is_related": "not_a_bool"}')


# ---------------------------------------------------------------------------
# _llm_propose_edge
# ---------------------------------------------------------------------------


class TestLlmProposeEdge:
    @pytest.mark.asyncio
    async def test_valid_response_parsed(self):
        pair = {
            "id1": "a", "id2": "b",
            "label1": "Alpha", "type1": "concept", "props1": None,
            "label2": "Beta",  "type2": "concept", "props2": None,
        }
        allowed = {"RELATES_TO", "PART_OF"}
        raw_json = '{"is_related": true, "proposed_label": "RELATES_TO", "confidence": 0.95}'

        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = raw_json

        with patch("litellm.acompletion", new=AsyncMock(return_value=mock_resp)):
            result = await _llm_propose_edge(pair, allowed)

        assert result is not None
        assert result.is_related is True
        assert result.proposed_label == "RELATES_TO"
        assert result.confidence == pytest.approx(0.95)

    @pytest.mark.asyncio
    async def test_llm_exception_returns_none(self):
        pair = {"id1": "a", "id2": "b", "label1": "", "type1": "", "props1": None,
                "label2": "", "type2": "", "props2": None}
        with patch("litellm.acompletion", new=AsyncMock(side_effect=RuntimeError("API down"))):
            result = await _llm_propose_edge(pair, {"RELATES_TO"})
        assert result is None

    @pytest.mark.asyncio
    async def test_invalid_json_returns_none(self):
        pair = {"id1": "a", "id2": "b", "label1": "", "type1": "", "props1": None,
                "label2": "", "type2": "", "props2": None}
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = "not json at all"
        with patch("litellm.acompletion", new=AsyncMock(return_value=mock_resp)):
            result = await _llm_propose_edge(pair, {"RELATES_TO"})
        assert result is None


# ---------------------------------------------------------------------------
# _propose_relations — label and confidence filtering
# ---------------------------------------------------------------------------


@pytest.fixture
def settings_patch():
    """Patch settings so the alchemist band is predictable."""
    with patch("app.lib.consolidation.settings") as mock_settings:
        mock_settings.consolidation_propose_cosine_min = 0.5
        mock_settings.consolidation_propose_cosine_max = 0.99
        mock_settings.consolidation_propose_min_confidence = 0.8
        mock_settings.consolidation_propose_path_max = 1
        mock_settings.litellm_model = "gpt-4o-mini"
        mock_settings.llm_base_url = ""
        mock_settings.llm_api_key = ""
        yield mock_settings


class TestProposeRelations:
    """Test _propose_relations filtering logic without a real Neo4j driver."""

    def _node_pair(self):
        # Two nodes whose cosine similarity is exactly 0.8 (both already unit-length),
        # which falls within the mocked band [0.5, 0.99].
        node_a = _minimal_node("node-a", [1.0, 0.0, 0.0])
        node_b = _minimal_node("node-b", [0.8, 0.6, 0.0])  # already unit-norm, cos=0.8
        return node_a, node_b

    def _build_driver_for_scenario(
        self,
        unconnected_pairs: list[dict],
        allowed_labels: list[str],
    ):
        """Build a driver whose 4 session calls return the expected records."""
        node_a, node_b = self._node_pair()
        nodes_records = [node_a, node_b]
        # Session 1: node fetch
        # Session 2: path check — we return unconnected_pairs
        # Session 3: allowed labels
        return _make_driver([
            nodes_records,
            unconnected_pairs,
            [{"label": lbl} for lbl in allowed_labels],
        ])

    @pytest.mark.asyncio
    async def test_hallucinated_label_discarded(self, settings_patch):
        """LLM proposes a label not in allowed_labels → discarded, returns 0."""
        pair_row = {
            "id1": "node-a", "id2": "node-b",
            "label1": "Entity_node-a", "type1": "concept", "props1": None,
            "label2": "Entity_node-b", "type2": "concept", "props2": None,
        }
        driver = self._build_driver_for_scenario(
            unconnected_pairs=[pair_row],
            allowed_labels=["RELATES_TO", "PART_OF"],
        )

        bad_proposal = ProposedEdge(
            is_related=True,
            proposed_label="HALLUCINATED_EDGE",
            confidence=0.95,
        )

        import numpy as np
        with patch(
            "app.lib.consolidation._llm_propose_edge",
            new=AsyncMock(return_value=bad_proposal),
        ):
            count = await _propose_relations(driver, "agent-1", np)

        assert count == 0

    @pytest.mark.asyncio
    async def test_valid_proposal_persisted(self, settings_patch):
        """LLM proposes an allowed label with sufficient confidence → count=1."""
        pair_row = {
            "id1": "node-a", "id2": "node-b",
            "label1": "Entity_node-a", "type1": "concept", "props1": None,
            "label2": "Entity_node-b", "type2": "concept", "props2": None,
        }

        # Session 4 is the MERGE write — return empty records
        node_a, node_b = self._node_pair()
        driver = _make_driver([
            [node_a, node_b],       # session 1: node fetch
            [pair_row],             # session 2: path check
            [{"label": "RELATES_TO"}, {"label": "PART_OF"}],  # session 3: labels
            [],                     # session 4: MERGE write
        ])

        good_proposal = ProposedEdge(
            is_related=True,
            proposed_label="RELATES_TO",
            confidence=0.92,
        )

        import numpy as np
        with patch(
            "app.lib.consolidation._llm_propose_edge",
            new=AsyncMock(return_value=good_proposal),
        ), patch(
            "app.lib.consolidation._create_proposed_edge",
            new=AsyncMock(),
        ) as mock_create:
            count = await _propose_relations(driver, "agent-1", np)

        assert count == 1
        mock_create.assert_called_once_with(
            driver, "node-a", "node-b", "RELATES_TO", 0.92
        )

    @pytest.mark.asyncio
    async def test_low_confidence_discarded(self, settings_patch):
        """Proposal below confidence threshold → discarded, returns 0."""
        pair_row = {
            "id1": "node-a", "id2": "node-b",
            "label1": "A", "type1": "concept", "props1": None,
            "label2": "B", "type2": "concept", "props2": None,
        }
        node_a, node_b = self._node_pair()
        driver = _make_driver([
            [node_a, node_b],
            [pair_row],
            [{"label": "RELATES_TO"}],
        ])

        low_conf = ProposedEdge(
            is_related=True,
            proposed_label="RELATES_TO",
            confidence=0.5,  # below threshold of 0.8
        )

        import numpy as np
        with patch(
            "app.lib.consolidation._llm_propose_edge",
            new=AsyncMock(return_value=low_conf),
        ), patch(
            "app.lib.consolidation._create_proposed_edge",
            new=AsyncMock(),
        ) as mock_create:
            count = await _propose_relations(driver, "agent-1", np)

        assert count == 0
        mock_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_is_related_false_skipped(self, settings_patch):
        """LLM says is_related=False → skipped without crashing."""
        pair_row = {
            "id1": "node-a", "id2": "node-b",
            "label1": "A", "type1": "concept", "props1": None,
            "label2": "B", "type2": "concept", "props2": None,
        }
        node_a, node_b = self._node_pair()
        driver = _make_driver([
            [node_a, node_b],
            [pair_row],
            [{"label": "RELATES_TO"}],
        ])

        unrelated = ProposedEdge(is_related=False, proposed_label="", confidence=0.1)

        import numpy as np
        with patch(
            "app.lib.consolidation._llm_propose_edge",
            new=AsyncMock(return_value=unrelated),
        ), patch(
            "app.lib.consolidation._create_proposed_edge",
            new=AsyncMock(),
        ) as mock_create:
            count = await _propose_relations(driver, "agent-1", np)

        assert count == 0
        mock_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_nodes_returns_zero(self, settings_patch):
        """Agent with fewer than 2 nodes → returns 0 immediately."""
        driver = _make_driver([[_minimal_node("only-one")]])
        import numpy as np
        count = await _propose_relations(driver, "agent-1", np)
        assert count == 0

    @pytest.mark.asyncio
    async def test_no_unconnected_pairs_returns_zero(self, settings_patch):
        """If path-check finds all pairs already connected → returns 0."""
        node_a, node_b = self._node_pair()
        driver = _make_driver([
            [node_a, node_b],
            [],   # path-check: all pairs already connected
        ])
        import numpy as np
        count = await _propose_relations(driver, "agent-1", np)
        assert count == 0


    @pytest.mark.asyncio
    async def test_path_max_setting_threads_into_cypher(self, settings_patch):
        """``consolidation_propose_path_max`` controls the Cypher path constraint.

        Default value (path_max=1) yields ``[*1..1]`` (no direct edge between
        pair). Setting path_max=2 yields ``[*1..2]`` (no path within 2 hops).
        Verifies the setting is actually read and interpolated into the query.
        """
        import numpy as np
        node_a, node_b = self._node_pair()

        # Build a driver that captures every session.run call so we can
        # inspect the Cypher string for the path-check session.
        captured_queries: list[str] = []

        def _build_session(records: list[dict]):
            session_obj = AsyncMock()
            async def _run(query, **_kwargs):
                captured_queries.append(query)
                result = AsyncMock()
                async def _aiter(self):
                    for r in records:
                        yield r
                result.__aiter__ = _aiter
                return result
            session_obj.run = _run
            session_ctx = MagicMock()
            session_ctx.__aenter__ = AsyncMock(return_value=session_obj)
            session_ctx.__aexit__ = AsyncMock(return_value=False)
            return session_ctx

        # 3 sessions are reached when no unconnected pairs come back:
        # node fetch, path check, (early return after step 4 if empty).
        session_iter = iter([
            _build_session([node_a, node_b]),
            _build_session([]),  # path check returns no unconnected pairs
        ])
        driver = MagicMock()
        driver.session = MagicMock(side_effect=lambda: next(session_iter))

        # Case 1: default path_max=1
        settings_patch.consolidation_propose_path_max = 1
        await _propose_relations(driver, "agent-1", np)
        # The second captured query is the path-check Cypher.
        assert any("[*1..1]" in q for q in captured_queries), (
            f"expected '[*1..1]' in captured queries, got: {captured_queries}"
        )

        # Case 2: path_max=2 (legacy behaviour)
        captured_queries.clear()
        session_iter = iter([
            _build_session([node_a, node_b]),
            _build_session([]),
        ])
        driver.session = MagicMock(side_effect=lambda: next(session_iter))
        settings_patch.consolidation_propose_path_max = 2
        await _propose_relations(driver, "agent-1", np)
        assert any("[*1..2]" in q for q in captured_queries), (
            f"expected '[*1..2]' in captured queries, got: {captured_queries}"
        )


# ---------------------------------------------------------------------------
# _create_proposed_edge
# ---------------------------------------------------------------------------


class TestCreateProposedEdge:
    @pytest.mark.asyncio
    async def test_cypher_invoked_with_correct_params(self):
        session_obj = AsyncMock()
        session_obj.run = AsyncMock()
        session_ctx = MagicMock()
        session_ctx.__aenter__ = AsyncMock(return_value=session_obj)
        session_ctx.__aexit__ = AsyncMock(return_value=False)

        driver = MagicMock()
        driver.session = MagicMock(return_value=session_ctx)

        await _create_proposed_edge(driver, "id-a", "id-b", "RELATES_TO", 0.91)

        session_obj.run.assert_called_once()
        call_kwargs = session_obj.run.call_args[1]
        assert call_kwargs["id1"] == "id-a"
        assert call_kwargs["id2"] == "id-b"
        assert call_kwargs["label"] == "RELATES_TO"
        assert call_kwargs["confidence"] == pytest.approx(0.91)
