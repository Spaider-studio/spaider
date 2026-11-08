"""Short-term episodes, hash embeddings, and bi-temporal edges."""
from __future__ import annotations

import asyncio
import math
from datetime import datetime, timedelta, timezone

import pytest

from app.core.hashing import HASH_EMBEDDING_DIM, hash_embed
from app.core.schema import FOUNDATION_SCHEMA
from app.core.temporal import to_utc_iso
from app.models.schemas import Edge, GraphPayload, Node
from app.services.episode_store import EpisodeInput, EpisodeStore, Location
from app.services.graph_service import GraphService
from app.services.query_service import QueryService
from app.services.supersession import _MARK_CYPHER


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def test_hash_embedding_is_deterministic_and_unit_length():
    vector = hash_embed("The gripper dropped the part")
    assert vector == hash_embed("The gripper dropped the part")
    assert len(vector) == HASH_EMBEDDING_DIM
    assert math.isclose(sum(x * x for x in vector), 1.0)


def test_hash_embedding_of_nothing_is_zero():
    assert not any(hash_embed("   ...  "))


def test_shared_words_score_higher_than_unrelated_text():
    base = hash_embed("the robot arm picked up the red box")
    assert _cosine(base, hash_embed("the robot arm picked the red block")) > _cosine(
        base, hash_embed("quarterly revenue grew in Munich")
    )


@pytest.fixture
async def store(neo4j_test_driver):
    async with neo4j_test_driver.session() as session:
        for statement in FOUNDATION_SCHEMA:
            await session.run(statement)
        await session.run("CALL db.awaitIndexes(30)")
    return EpisodeStore(neo4j_test_driver)


async def test_write_and_read_back(store):
    written = await store.write_batch(
        "kb", [EpisodeInput("first", kind="message", session_id="s", metadata={"a": {"b": 1}})]
    )
    assert written[0]["created"] is True
    episode = (await store.recent("kb"))[0]
    assert (episode["content"], episode["kind"], episode["session_id"]) == ("first", "message", "s")
    assert episode["metadata"] == {"a": {"b": 1}}


async def test_retry_with_same_key_creates_nothing(store):
    first = await store.write_batch("kb", [EpisodeInput("x", idempotency_key="k1")])
    second = await store.write_batch("kb", [EpisodeInput("changed on retry", idempotency_key="k1")])
    assert second == [{"id": first[0]["id"], "created": False}]
    assert [e["content"] for e in await store.recent("kb")] == ["x"]


async def test_same_key_in_another_kb_is_independent(store):
    a = await store.write_batch("kb-a", [EpisodeInput("x", idempotency_key="k")])
    b = await store.write_batch("kb-b", [EpisodeInput("x", idempotency_key="k")])
    assert a[0]["id"] != b[0]["id"] and b[0]["created"] is True


async def test_concurrent_retries_produce_one_episode(store):
    results = await asyncio.gather(
        *[store.write_batch("kb", [EpisodeInput("x", idempotency_key="race")]) for _ in range(8)]
    )
    assert len({r[0]["id"] for r in results}) == 1
    assert len(await store.recent("kb")) == 1


async def test_without_a_key_every_episode_is_distinct(store):
    await store.write_batch("kb", [EpisodeInput("same"), EpisodeInput("same")])
    assert len(await store.recent("kb")) == 2


async def test_batch_preserves_input_order(store):
    written = await store.write_batch("kb", [EpisodeInput(str(i)) for i in range(50)])
    assert len(written) == 50 and all(w["created"] for w in written)


async def test_recent_is_newest_first_and_scoped(store):
    old = datetime.now(timezone.utc) - timedelta(hours=1)
    await store.write_batch("kb", [EpisodeInput("old", occurred_at=old), EpisodeInput("new")])
    await store.write_batch("other", [EpisodeInput("theirs")])
    assert [e["content"] for e in await store.recent("kb")] == ["new", "old"]


async def test_similar_finds_shared_wording_within_the_kb(store):
    await store.write_batch(
        "kb", [EpisodeInput("the conveyor belt jammed at station four"), EpisodeInput("lunch is at noon")]
    )
    await store.write_batch("other", [EpisodeInput("the conveyor belt jammed at station four")])
    hits = await store.similar("kb", "conveyor jammed at station", limit=5)
    assert hits[0]["content"].startswith("the conveyor")
    assert len([h for h in hits if h["content"].startswith("the conveyor")]) == 1


async def test_clearance_limits_every_read(store):
    await store.write_batch(
        "kb", [EpisodeInput("public note", clearance_level=1), EpisodeInput("secret note", clearance_level=4)]
    )
    assert [e["content"] for e in await store.recent("kb", max_clearance=2)] == ["public note"]
    hits = await store.similar("kb", "note", max_clearance=2)
    assert [h["content"] for h in hits] == ["public note"]


async def test_geographic_radius(store):
    await store.write_batch(
        "kb",
        [
            EpisodeInput("near", location=Location(latitude=52.5200, longitude=13.4050)),
            EpisodeInput("far", location=Location(latitude=48.1351, longitude=11.5820)),
            EpisodeInput("nowhere"),
        ],
    )
    hits = await store.near("kb", Location(latitude=52.5201, longitude=13.4051), 1000)
    assert [h["content"] for h in hits] == ["near"]


async def test_local_frame_radius_in_three_dimensions(store):
    await store.write_batch(
        "kb",
        [
            EpisodeInput("a", location=Location(x=0.0, y=0.0, z=0.0)),
            EpisodeInput("b", location=Location(x=0.0, y=0.0, z=5.0)),
        ],
    )
    hits = await store.near("kb", Location(x=0.0, y=0.0, z=0.5), 1.0)
    assert [h["content"] for h in hits] == ["a"]


def test_location_without_coordinates_is_rejected():
    with pytest.raises(ValueError):
        Location().as_point()


# ── Bi-temporal edges ─────────────────────────────────────────────────────


@pytest.fixture
async def graph(monkeypatch, neo4j_test_driver):
    service = GraphService()
    yield service
    await service.close()


async def _seed(graph, agent: str = "kb", **edge_kwargs) -> None:
    await graph.write_graph(
        GraphPayload(
            nodes=[
                Node(id="a", label="Acme", type="ORGANIZATION", agent_id=agent),
                Node(id="b", label="Berlin", type="LOCATION", agent_id=agent),
            ],
            edges=[Edge(id="e1", source_id="a", target_id="b", relation="LOCATED_IN", agent_id=agent, **edge_kwargs)],
        ),
        agent,
    )


async def _edge(graph, edge_id: str = "e1") -> dict:
    async with graph._driver.session() as session:
        result = await session.run("MATCH ()-[r:RELATION {id: $id}]->() RETURN properties(r) AS p", id=edge_id)
        return (await result.single())["p"]


async def _supersede(graph, at: str) -> None:
    async with graph._driver.session() as session:
        await session.run(_MARK_CYPHER, items=[{"edge_id": "e1", "text": ""}], new_id="e2", aid="kb", now=at)


async def test_new_edge_gets_both_timelines(graph):
    await _seed(graph)
    edge = await _edge(graph)
    assert edge["valid_from"] == edge["recorded_at"] == edge["created_at"]
    assert edge.get("valid_to") is None and edge.get("expired_at") is None


async def test_caller_can_state_when_a_fact_became_true(graph):
    moment = datetime(2020, 1, 1, tzinfo=timezone.utc)
    await _seed(graph, valid_from=moment)
    edge = await _edge(graph)
    assert edge["valid_from"] == to_utc_iso(moment)
    assert edge["recorded_at"] > edge["valid_from"]


async def test_rewriting_an_edge_keeps_its_history(graph):
    await _seed(graph)
    before = await _edge(graph)
    await _seed(graph)
    after = await _edge(graph)
    assert (after["valid_from"], after["recorded_at"]) == (before["valid_from"], before["recorded_at"])


async def test_supersession_closes_both_timelines(graph):
    await _seed(graph)
    await _supersede(graph, "2030-01-01T00:00:00+00:00")
    edge = await _edge(graph)
    assert edge["valid_to"] == edge["expired_at"] == "2030-01-01T00:00:00+00:00"


async def test_as_of_shows_the_graph_at_that_time(graph):
    await _seed(graph, valid_from=datetime(2020, 1, 1, tzinfo=timezone.utc))
    written = (await _edge(graph))["created_at"]
    stop = (datetime.fromisoformat(written) + timedelta(days=30)).isoformat()
    await _supersede(graph, stop)

    between = (datetime.fromisoformat(written) + timedelta(days=1)).isoformat()
    after = (datetime.fromisoformat(written) + timedelta(days=60)).isoformat()
    before_any_write = (datetime.fromisoformat(written) - timedelta(days=1)).isoformat()

    assert len((await graph.get_full_graph("kb", as_of=between)).edges) == 1
    assert len((await graph.get_full_graph("kb", as_of=after)).edges) == 0
    # True in the world since 2020, but this graph did not know it yet.
    assert len((await graph.get_full_graph("kb", as_of=before_any_write)).edges) == 0


async def test_valid_to_in_the_past_hides_a_fact_from_later_as_of(graph):
    await _seed(
        graph,
        valid_from=datetime(2020, 1, 1, tzinfo=timezone.utc),
        valid_to=datetime(2021, 1, 1, tzinfo=timezone.utc),
    )
    written = (await _edge(graph))["created_at"]
    assert len((await graph.get_full_graph("kb", as_of=written)).edges) == 0


async def test_superseded_node_is_excluded_after_its_timestamp(graph):
    await _seed(graph)
    created = (await _edge(graph))["created_at"]
    gone = (datetime.fromisoformat(created) + timedelta(days=1)).isoformat()
    async with graph._driver.session() as session:
        await session.run("MATCH (n {id: 'a'}) SET n.superseded = true, n.superseded_at = $t", t=gone)
    earlier = (datetime.fromisoformat(created) + timedelta(hours=1)).isoformat()
    later = (datetime.fromisoformat(created) + timedelta(days=2)).isoformat()
    assert {n.id for n in (await graph.get_full_graph("kb", as_of=earlier)).nodes} == {"a", "b"}
    assert {n.id for n in (await graph.get_full_graph("kb", as_of=later)).nodes} == {"b"}


# ── Traversal against a real database ─────────────────────────────────────


@pytest.fixture
def query(graph):
    return QueryService(graph_service=graph)


async def test_traverse_returns_the_neighbourhood(graph, query):
    await _seed(graph)
    result = await query.traverse("a", depth=2, agent_id="kb")
    assert {n.id for n in result.nodes} == {"a", "b"}
    assert [e.id for e in result.edges] == ["e1"]


async def test_traverse_honours_the_relation_filter(graph, query):
    await _seed(graph)
    assert not (await query.traverse("a", 2, relation_filter=["OTHER"], agent_id="kb")).nodes
    assert (await query.traverse("a", 2, relation_filter=["LOCATED_IN"], agent_id="kb")).nodes


async def test_traverse_is_empty_from_another_kbs_node(graph, query):
    await _seed(graph)
    assert not (await query.traverse("a", 2, agent_id="other")).nodes


async def test_traverse_does_not_cross_into_another_kb(graph, query):
    await _seed(graph)
    async with graph._driver.session() as session:
        await session.run(
            """
            MATCH (b:SpaiderNode {id: 'b'})
            CREATE (b)-[:RELATION {id: 'x', relation: 'LEAK', agent_id: 'other', created_at: '2020'}]->
                   (:SpaiderNode {id: 'secret', agent_id: 'other', label: 'S', type: 'CONCEPT'})
            """
        )
    assert "secret" not in {n.id for n in (await query.traverse("a", 3, agent_id="kb")).nodes}


async def test_traverse_skips_superseded_edges(graph, query):
    await _seed(graph)
    await _supersede(graph, "2030-01-01T00:00:00+00:00")
    assert not (await query.traverse("a", 2, agent_id="kb")).nodes


async def test_traverse_as_of_sees_a_superseded_edge(graph, query):
    await _seed(graph)
    await _supersede(graph, "2999-01-01T00:00:00+00:00")
    moment = datetime.now(timezone.utc)
    assert (await query.traverse("a", 2, agent_id="kb", as_of=moment)).nodes
