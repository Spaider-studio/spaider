"""Memory layer tests with the LLM mocked (judge + extraction); the real-model
version of the same scenario lives in test_real_inference.py."""

from __future__ import annotations

import json

import pytest
from app.models.schemas import Edge, GraphPayload, Node
from app.services.entity_resolver import EntityResolver

from spaider_foldable.memory.ingest import MemoryIngestor, attach_fact_node
from spaider_foldable.memory.retrieve import MemoryRetriever
from spaider_foldable.memory.store import LocalMemoryStore, Origin, fts_query
from spaider_foldable.memory.supersession_local import resolve_supersession_local


class _NoEmbeddings:
    available = False
    model_id = None

    async def embed_batch(self, texts):
        return []

    async def embed_query(self, text):
        return []

    @staticmethod
    def cosine_similarity(a, b):
        return 0.0


class _FakeEmbeddings(_NoEmbeddings):
    """Deterministic 8-dim bag-of-words vectors so cosine dedup is testable."""

    available = True
    model_id = "fake-emb"

    @staticmethod
    def _vec(text):
        words = ["zephyr", "priya", "idris", "ceo", "berlin", "lissabon", "musa", "köln"]
        t = text.lower()
        return [1.0 if w in t else 0.0 for w in words] or [0.0] * 8

    async def embed_batch(self, texts):
        return [self._vec(t) for t in texts]

    async def embed_query(self, text):
        return self._vec(text)

    @staticmethod
    def cosine_similarity(a, b):
        import math

        dot = sum(x * y for x, y in zip(a, b))
        na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
        return dot / (na * nb) if na and nb else 0.0


def _payload(subj, rel, obj, text, *, subj_type="PERSON", obj_type="ORGANIZATION"):
    a = Node(label=subj, type=subj_type, properties={"source_text": text})
    b = Node(label=obj, type=obj_type, properties={"source_text": text})
    e = Edge(source_id=a.id, target_id=b.id, relation=rel, properties={"source_text": text, "description": text})
    p = GraphPayload(nodes=[a, b], edges=[e])
    attach_fact_node(p, text, "test")
    return p


def test_fts_query_builder():
    assert fts_query("Wer ist der CEO von Zephyr?") == '"ceo" OR "zephyr"*'
    assert fts_query("der die das") is None
    assert '"o\'neil"' in fts_query("O'Neil") or '"neil"' in fts_query("O'Neil")


def test_write_fulltext_and_edges(db):
    store = LocalMemoryStore(db)
    p = _payload("Priya Nair", "CEO_OF", "Zephyr", "Priya Nair ist CEO von Zephyr.")
    res = store.write_graph(p, Origin("conversation", "c1", "Chat A"), embedding_model_id=None)
    assert res["nodes_created"] == 3 and res["edges_created"] == 3  # 1 relation + 2 MENTIONS
    hits = store.fulltext_nodes("Wer ist CEO bei Zephyr?")
    assert any(n.type == "FACT" for n in hits)
    fact = [n for n in hits if n.type == "FACT"][0]
    row = store.get_node(fact.id)
    assert row["origin_title"] == "Chat A"
    edges = store.edges_for_nodes([n.id for n in hits if n.type != "FACT"])
    assert any(e.relation == "CEO_OF" for e in edges)
    assert store.stats()["facts"] == 1 and store.stats()["entities"] == 2


@pytest.mark.asyncio
async def test_entity_resolver_reuse_merges_duplicates(db):
    store = LocalMemoryStore(db)
    store.set_index_model("fake-emb")
    resolver = EntityResolver(embedding_service=_FakeEmbeddings())
    p1 = _payload(
        "Zephyr GmbH",
        "LOCATED_IN",
        "Berlin",
        "Zephyr GmbH sitzt in Berlin.",
        subj_type="ORGANIZATION",
        obj_type="LOCATION",
    )
    r1 = await resolver.resolve(p1, "local", store, caller_context="api")
    store.write_graph(r1, Origin("conversation", "c1", "A"), embedding_model_id="fake-emb")
    # same organisation spelled slightly differently -> fuzzy/exact match merges it
    p2 = _payload(
        "Zephyr Gmbh",
        "FOUNDED_BY",
        "Musa",
        "Zephyr Gmbh wurde von Musa gegründet.",
        subj_type="ORGANIZATION",
        obj_type="PERSON",
    )
    r2 = await resolver.resolve(p2, "local", store, caller_context="api")
    store.write_graph(r2, Origin("conversation", "c2", "B"), embedding_model_id="fake-emb")
    orgs = [n for n in await store.list_nodes_for_resolver("local") if n.type == "ORGANIZATION"]
    assert len(orgs) == 1, [o.label for o in orgs]
    assert "Zephyr Gmbh" in orgs[0].properties.get("aliases", []) or orgs[0].label.lower() == "zephyr gmbh"
    # vector search works over stored embeddings
    hits = await store.vector_search(_FakeEmbeddings._vec("berlin zephyr"), "local", top_k=3)
    assert hits and any("Berlin" in (n.label or "") or n.type == "FACT" for n in hits)


@pytest.mark.asyncio
async def test_supersession_marks_old_fact(db, monkeypatch):
    store = LocalMemoryStore(db)
    old = _payload("Idris Kane", "CEO_OF", "Zephyr", "Idris Kane ist CEO von Zephyr.")
    store.write_graph(old, Origin("conversation", "c1", "A"), embedding_model_id=None)
    old_edge = [e for e in old.edges if e.relation == "CEO_OF"][0]
    # reuse the same org node id so the candidate query finds a shared endpoint
    org = [n for n in old.nodes if n.label == "Zephyr"][0]
    new_person = Node(
        label="Priya Nair", type="PERSON", properties={"source_text": "Priya Nair ist jetzt CEO von Zephyr."}
    )
    new_edge = Edge(source_id=new_person.id, target_id=org.id, relation="CEO_OF",
                    properties={"source_text": "Priya Nair ist jetzt CEO von Zephyr.", "description": "Priya Nair ist jetzt CEO von Zephyr."})  # fmt: skip  # noqa: E501
    new = GraphPayload(nodes=[new_person], edges=[new_edge])
    attach_fact_node(new, "Priya Nair ist jetzt CEO von Zephyr.", "test")
    store.write_graph(new, Origin("conversation", "c2", "B"), embedding_model_id=None)

    import spaider_foldable.memory.supersession_local as sl

    async def fake_state(text):
        return True

    async def fake_judge(new_text, candidates):
        return {c["edge_id"] for c in candidates if "Idris" in c["text"]}

    monkeypatch.setattr(sl, "_is_state_update", fake_state)
    monkeypatch.setattr(sl, "_judge", fake_judge)
    n = await resolve_supersession_local(store, new)
    assert n == 1
    assert db.scalar("SELECT superseded FROM edges WHERE id = ?", (old_edge.id,)) == 1
    assert db.scalar("SELECT count(*) FROM nodes WHERE type='FACT' AND superseded=1") == 1
    # retrieval only returns the current fact, but exposes the old one as superseded history
    retriever = MemoryRetriever(db, store, _NoEmbeddings())
    ctx = await retriever.retrieve("Wer ist CEO von Zephyr?")
    assert "Priya Nair" in ctx.context_text
    assert "Idris Kane ist CEO" not in ctx.context_text.split("Ältere")[0]
    assert "veraltet" in ctx.context_text
    statuses = {s["status"] for s in ctx.sources}
    assert "current" in statuses and "superseded" in statuses
    search = retriever.search("Idris")
    assert any(s["status"] == "superseded" for s in search)


@pytest.mark.asyncio
async def test_ingestor_keeps_fact_when_extraction_fails(db, monkeypatch):
    store = LocalMemoryStore(db)
    ing = MemoryIngestor(db, store, _NoEmbeddings())

    async def broken_extract(text, context=None):
        from app.services.compressor import ExtractionError

        raise ExtractionError("no json", attempts=3, last_error="x")

    monkeypatch.setattr(ing.compressor, "extract", broken_extract)
    res = await ing.ingest_text("Das Projekt Orion startet im März 2027 in Köln.", Origin("conversation", "c1", "A"))
    assert res["extraction_error"] and res["fact_id"]
    hits = store.fulltext_nodes("Wann startet Orion?")
    assert hits and hits[0].type == "FACT"
    assert json.loads(store.get_node(hits[0].id)["properties_json"])["source"] == "conversation"


def test_guard_against_prompt_leakage():
    from spaider_foldable.memory.ingest import guard_against_prompt_leakage

    text = "Mein Projekt heißt Orion. Der Projektleiter von Orion ist Idris Kane."
    orion = Node(label="Orion", type="ORGANIZATION", description="Electric vehicle manufacturer",
                 properties={"description": "Electric vehicle manufacturer", "source_text": "Orion was founded in 2003", "confidence": 1.0})  # fmt: skip  # noqa: E501
    idris = Node(label="Idris Kane", type="PERSON", properties={"description": "Projektleiter", "source_text": "Der Projektleiter von Orion ist Idris Kane", "confidence": 1.0})  # fmt: skip  # noqa: E501
    p = GraphPayload(nodes=[orion, idris], edges=[Edge(source_id=idris.id, target_id=orion.id, relation="LEADS",
                                                       properties={"source_text": "Elon Musk founded Tesla"})])  # fmt: skip  # noqa: E501
    out = guard_against_prompt_leakage(p, text)
    assert out.nodes[0].description is None and "source_text" not in out.nodes[0].properties
    assert out.nodes[0].properties["confidence"] == 0.4 and out.nodes[0].properties["unsupported_extraction"]
    assert out.nodes[1].properties["description"] == "Projektleiter" and out.nodes[1].properties["confidence"] == 1.0
    assert out.edges[0].properties["confidence"] == 0.4


@pytest.mark.asyncio
async def test_fuzzy_supersession_marks_fact_without_verbatim_quote(db, monkeypatch):
    """The judge marks an edge; the FACT that carried it mentions both endpoints
    but the stale text is not a verbatim substring -> fuzzy marking kicks in."""
    store = LocalMemoryStore(db)
    old_text = "Merk dir: Der Projektleiter von Orion ist Idris Kane. Das Budget beträgt 42000 Euro."
    idris = Node(label="Idris Kane", type="PERSON")
    orion = Node(label="Orion", type="PROJECT")  # noqa: E702
    old_edge = Edge(source_id=idris.id, target_id=orion.id, relation="PROJECT_LEADER",
                    properties={"source_text": "Projektleiter von Orion ist Idris Kane"})  # fmt: skip
    old = GraphPayload(nodes=[idris, orion], edges=[old_edge])
    attach_fact_node(old, old_text, "t")  # noqa: E702
    store.write_graph(old, Origin("conversation", "c1", "A"), embedding_model_id=None)
    priya = Node(label="Priya Nair", type="PERSON")
    new_edge = Edge(source_id=priya.id, target_id=orion.id, relation="PROJECT_LEADER",
                    properties={"source_text": "Projektleiter von Orion ist jetzt Priya Nair"})  # fmt: skip
    new = GraphPayload(nodes=[priya], edges=[new_edge])
    attach_fact_node(new, "Korrektur: Der Projektleiter von Orion ist jetzt Priya Nair.", "t")  # noqa: E702,E501
    store.write_graph(new, Origin("conversation", "c1", "A"), embedding_model_id=None)
    import spaider_foldable.memory.supersession_local as sl

    async def yes(text):
        return True

    async def judge(new_text, candidates):
        return {c["edge_id"] for c in candidates if "Idris" in c["text"]}

    monkeypatch.setattr(sl, "_is_state_update", yes)
    monkeypatch.setattr(sl, "_judge", judge)
    assert await resolve_supersession_local(store, new) == 1
    facts = {
        r["label"]: r["superseded"]
        for r in (dict(x) for x in db.query("SELECT label, superseded FROM nodes WHERE type='FACT'"))
    }
    assert [v for k, v in facts.items() if k.startswith("fact: Merk dir")] == [1]
    assert [v for k, v in facts.items() if k.startswith("fact: Korrektur")] == [0]
    retriever = MemoryRetriever(db, store, _NoEmbeddings())
    ctx = await retriever.retrieve("Wer leitet Orion?")
    assert "Priya Nair" in ctx.context_text and "[veraltet, ersetzt] Merk dir" in ctx.context_text
    assert any(s["status"] == "superseded" and s["excerpt"].startswith("Merk dir") for s in ctx.sources)
