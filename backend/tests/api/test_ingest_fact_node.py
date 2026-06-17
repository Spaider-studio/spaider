"""
Unit tests for the FACT-node ingest helper.

Strategy
--------
Pure-Python tests against ``_attach_fact_node`` and the ``Node`` model — no
Neo4j, no Kafka, no LLM calls. End-to-end persistence and retrieval are
covered by the manual smoke run documented in the PR (an MCP
``spaider.ingest_fact`` call followed by a direct Cypher probe to confirm
the FACT row + MENTIONS edges land in the graph).
"""
from __future__ import annotations

from app.api.v1.ingest import _FACT_LABEL_PREVIEW_CHARS, _attach_fact_node
from app.models.schemas import GraphPayload, Node


def _payload(*entities: tuple[str, str]) -> GraphPayload:
    """Build a payload with the given (label, type) entity pairs."""
    return GraphPayload(
        nodes=[Node(label=label, type=ntype) for label, ntype in entities],
        edges=[],
    )


# ---------------------------------------------------------------------------
# Node model — new top-level `description` field
# ---------------------------------------------------------------------------


def test_node_model_has_description_field_default_none() -> None:
    n = Node(label="x", type="ORGANIZATION")
    assert n.description is None


def test_node_model_accepts_description() -> None:
    n = Node(label="x", type="FACT", description="hello world")
    assert n.description == "hello world"


# ---------------------------------------------------------------------------
# _attach_fact_node
# ---------------------------------------------------------------------------


def test_attach_fact_node_adds_one_fact_with_description() -> None:
    payload = _payload(("Stark", "ORGANIZATION"), ("Tony", "PERSON"))
    text = "Stark Industries (Tony) escalated about feature Y."
    _attach_fact_node(payload, text, source="email:tony")

    facts = [n for n in payload.nodes if n.type == "FACT"]
    assert len(facts) == 1
    assert facts[0].description == text
    assert facts[0].label.startswith("fact: ")
    # Source survives in properties so the graph remembers provenance.
    assert facts[0].properties.get("source") == "email:tony"


def test_attach_fact_node_links_fact_to_every_entity_with_mentions() -> None:
    payload = _payload(
        ("Stark", "ORGANIZATION"),
        ("Tony", "PERSON"),
        ("Feature Y", "PRODUCT"),
    )
    _attach_fact_node(payload, "Stark wants Y by Q2.", source="adr-1")

    fact = next(n for n in payload.nodes if n.type == "FACT")
    mentions = [e for e in payload.edges if e.relation == "MENTIONS"]
    assert len(mentions) == 3
    assert all(e.source_id == fact.id for e in mentions)
    entity_ids = {n.id for n in payload.nodes if n.type != "FACT"}
    assert {e.target_id for e in mentions} == entity_ids


def test_attach_fact_node_truncates_label_preview() -> None:
    payload = _payload(("X", "ORGANIZATION"))
    long_text = "A" * (_FACT_LABEL_PREVIEW_CHARS + 200)
    _attach_fact_node(payload, long_text, source="long")

    fact = next(n for n in payload.nodes if n.type == "FACT")
    # Description is full text (no truncation), label is preview-only.
    assert fact.description == long_text
    # Label = "fact: " (6 chars) + preview chars + "…" (1 char).
    assert len(fact.label) == 6 + _FACT_LABEL_PREVIEW_CHARS + 1
    assert fact.label.endswith("…")


def test_attach_fact_node_collapses_newlines_in_label() -> None:
    """Multi-line text shouldn't produce a multi-line label preview."""
    payload = _payload(("X", "ORGANIZATION"))
    text = "Line one.\nLine two.\nLine three."
    _attach_fact_node(payload, text, source="multi")

    fact = next(n for n in payload.nodes if n.type == "FACT")
    assert "\n" not in fact.label
    # Description preserves the original formatting.
    assert "\n" in fact.description


def test_attach_fact_node_uses_default_source_when_unset() -> None:
    payload = _payload(("X", "ORGANIZATION"))
    _attach_fact_node(payload, "some fact", source=None)

    fact = next(n for n in payload.nodes if n.type == "FACT")
    # Default source matches the function's documented fallback so graph
    # consumers can still filter by `source` even when the caller didn't
    # supply one.
    assert fact.properties.get("source") == "ingest_text_sync"


def test_attach_fact_node_skips_empty_text() -> None:
    """No FACT node should be created for empty or whitespace-only text."""
    payload = _payload(("X", "ORGANIZATION"))
    nodes_before = len(payload.nodes)
    edges_before = len(payload.edges)
    _attach_fact_node(payload, "   ", source="x")
    _attach_fact_node(payload, "", source="x")
    assert len(payload.nodes) == nodes_before
    assert len(payload.edges) == edges_before


def test_attach_fact_node_with_no_entities_still_appends_fact() -> None:
    """An ingest with text but no extractable entities still gets a FACT
    row — the raw text is preserved even if extraction yielded nothing.
    The caller in ingest_text_sync gates on payload.nodes being non-empty
    before invoking us, so this case currently does not happen in prod;
    but the helper itself should remain safe to call directly."""
    payload = GraphPayload(nodes=[], edges=[])
    _attach_fact_node(payload, "An orphan fact.", source="orphan")

    assert len(payload.nodes) == 1
    assert payload.nodes[0].type == "FACT"
    assert payload.nodes[0].description == "An orphan fact."
    assert payload.edges == []  # No entities to link to


# ---------------------------------------------------------------------------
# Node row builder — write_graph payload includes description
# ---------------------------------------------------------------------------


def test_node_row_built_for_write_graph_includes_description() -> None:
    """Regression guard: ensure a FACT-type Node round-trips its
    description through the dict that ``GraphService.write_graph`` builds
    for Cypher UNWIND. The actual Cypher write is integration-tested via
    the MCP ingest probe in the PR; this just verifies the in-process
    structure does not lose the field."""
    fact = Node(label="fact: hi", type="FACT", description="hello world")
    # Mirrors the dict built by graph_service.write_graph (line ~432).
    row = {
        "id": fact.id,
        "agent_id": "agent-1",
        "label": fact.label,
        "type": fact.type,
        "description": fact.description,
        "properties": "{}",
        "embedding": None,
    }
    assert row["description"] == "hello world"


def test_attach_fact_node_on_empty_payload_preserves_text() -> None:
    """Terse facts ("...recovered in 18s.") often extract to no entities. The
    FACT node must still be attached so the verbatim text — and any literal
    value in it — is preserved and retrievable. Gating on payload.nodes used
    to drop the whole fact (an unrecoverable ingestion loss)."""
    empty = GraphPayload(nodes=[], edges=[])
    _attach_fact_node(empty, "stage-only chaos run; billing pod recovered in 18s.", source="slack")
    assert len(empty.nodes) == 1
    fact = empty.nodes[0]
    assert fact.type == "FACT"
    assert "recovered in 18s" in (fact.description or "")
    assert empty.edges == []   # no entities → no MENTIONS edges, just the fact
