"""Every ingest route ends in the same pipeline. LLM calls are stubbed."""
from __future__ import annotations

import asyncio

import pytest

from app.api.v1 import ingest as ingest_module
from app.core.jobs import drain_background
from app.core.kv import cache_generation
from app.models.schemas import Edge, GraphPayload, Node
from app.services import supersession
from app.services.compressor import ExtractionError
from app.services.graph_service import get_graph_service
from app.services.ingest_pipeline import IngestPipeline


class _Compressor:
    def __init__(self, fail=False):
        self.fail = fail

    async def extract(self, text, context=None):
        if self.fail:
            raise ExtractionError("provider down")
        return GraphPayload(nodes=[Node(label="Acme", type="ORG")])


class _Resolver:
    async def resolve(self, payload, agent_id, graph, caller_context="api"):
        return payload


@pytest.fixture
def stubs(monkeypatch):
    compressor = _Compressor()
    monkeypatch.setattr(ingest_module, "_compressor", compressor)
    monkeypatch.setattr(ingest_module, "_resolver", _Resolver())
    return compressor


async def _rows(world, cypher, **params):
    async with world.driver.session() as session:
        return await (await session.run(cypher, **params)).data()


async def _facts(world, kb_id):
    rows = await _rows(world, "MATCH (n:SpaiderNode {agent_id: $kb, type: 'FACT'}) RETURN n.description AS d", kb=kb_id)
    return [row["d"] for row in rows]


async def _episode_status(world, kb_id):
    rows = await _rows(world, "MATCH (e:Episode {agent_id: $kb}) RETURN e.status AS s", kb=kb_id)
    return [row["s"] for row in rows]


async def _post(world, kb, path, body):
    return await world.client.post(
        f"/api/v1/ingest{path}", json={**body, "agent_id": kb["id"]}, headers=world.headers(kb["api_key"])
    )


async def test_sync_ingest_runs_every_step(auth_world, stubs):
    kb = await auth_world.create_kb()
    before = await cache_generation(kb["id"])
    response = await _post(auth_world, kb, "/sync", {"text": "Acme moved to Berlin."})
    assert response.status_code == 200, response.text
    assert await _facts(auth_world, kb["id"]) == ["Acme moved to Berlin."]
    assert await _episode_status(auth_world, kb["id"]) == ["extracted"]
    assert await cache_generation(kb["id"]) > before


async def test_stream_ingest_keeps_the_original_text(auth_world, stubs, monkeypatch):
    """The streaming route used to drop FACT nodes and skip supersession."""
    from app.models.requests import IngestRequest

    kb = await auth_world.create_kb()
    seen = []

    async def supersede(driver, agent_id, payload):
        seen.append(agent_id)

    monkeypatch.setattr("app.services.ingest_pipeline.resolve_supersession", supersede)
    monkeypatch.setattr(asyncio, "sleep", _no_sleep())
    await ingest_module._run_stream_ingest(IngestRequest(text="Streamed fact.", agent_id=kb["id"]), "job-1")
    await drain_background()
    assert await _facts(auth_world, kb["id"]) == ["Streamed fact."]
    assert seen == [kb["id"]]


def _no_sleep():
    real = asyncio.sleep

    async def fast(_seconds, *args, **kwargs):
        await real(0)

    return fast


async def test_an_outage_leaves_the_episode_for_later(auth_world, stubs):
    kb = await auth_world.create_kb()
    stubs.fail = True
    response = await _post(auth_world, kb, "/sync", {"text": "Survives the outage."})
    assert response.status_code >= 400
    assert await _episode_status(auth_world, kb["id"]) == ["new"]


async def test_repeating_an_ingest_is_one_episode(auth_world, stubs):
    kb = await auth_world.create_kb()
    for _ in range(2):
        await _post(auth_world, kb, "/sync", {"text": "Same text."})
    assert len(await _episode_status(auth_world, kb["id"])) == 1


async def test_byov_and_batch_retire_cached_answers(auth_world, stubs):
    kb = await auth_world.create_kb()
    body = {"nodes": [{"label": "X", "type": "CONCEPT"}], "edges": []}
    before = await cache_generation(kb["id"])
    assert (await _post(auth_world, kb, "/graph", body)).status_code == 200
    after_single = await cache_generation(kb["id"])
    assert after_single > before
    response = await _post(auth_world, kb, "/graph/batch", {"payloads": [body, body]})
    assert response.status_code == 200, response.text
    assert await cache_generation(kb["id"]) > after_single


async def test_background_ingest_works_without_kafka(auth_world, stubs, monkeypatch):
    async def no_producer():
        return None

    monkeypatch.setattr(ingest_module, "_get_kafka_producer", no_producer)
    monkeypatch.setattr("app.services.compressor.SemanticCompressor", _Compressor)
    monkeypatch.setattr("app.services.entity_resolver.EntityResolver", _Resolver)
    from app.core.jobs import get_job_queue, reset_job_queue

    reset_job_queue()
    kb = await auth_world.create_kb()
    response = await _post(auth_world, kb, "", {"text": "Queued fact."})
    assert response.status_code == 202, response.text
    await get_job_queue().stop(drain=True, timeout=10)
    reset_job_queue()
    assert await _facts(auth_world, kb["id"]) == ["Queued fact."]


# ── Supersession ordering ─────────────────────────────────────────────────


@pytest.fixture
def judge_replaces_everything(monkeypatch):
    async def is_state(_text):
        return True

    async def judge(_text, candidates):
        return {candidate["edge_id"] for candidate in candidates}

    monkeypatch.setattr(supersession, "_is_state_update", is_state)
    monkeypatch.setattr(supersession, "_judge", judge)
    monkeypatch.setattr(supersession.settings, "default_supersession", True)


def _payload(kb_id, edge_ids, target_prefix="t"):
    source = Node(id="company", label="Acme", type="ORG", agent_id=kb_id)
    targets = [Node(id=f"{target_prefix}{i}", label=f"City {i}", type="PLACE", agent_id=kb_id) for i in edge_ids]
    edges = [
        Edge(
            id=f"edge-{i}",
            source_id="company",
            target_id=f"{target_prefix}{i}",
            relation="LOCATED_IN",
            agent_id=kb_id,
            properties={"source_text": f"Acme is in city {i}"},
        )
        for i in edge_ids
    ]
    return GraphPayload(nodes=[source, *targets], edges=edges)


async def _live_edges(world, kb_id):
    rows = await _rows(
        world,
        "MATCH ()-[r:RELATION {agent_id: $kb}]->() WHERE NOT coalesce(r.superseded, false) RETURN r.id AS id",
        kb=kb_id,
    )
    return {row["id"] for row in rows}


async def test_edges_from_one_document_cannot_supersede_each_other(auth_world, judge_replaces_everything):
    kb = await auth_world.create_kb()
    graph = get_graph_service()
    payload = _payload(kb["id"], [1, 2])
    await graph.write_graph(payload, kb["id"])
    await supersession.resolve_supersession(graph._driver, kb["id"], payload)
    assert await _live_edges(auth_world, kb["id"]) == {"edge-1", "edge-2"}


async def test_a_new_fact_supersedes_only_older_ones(auth_world, judge_replaces_everything):
    kb = await auth_world.create_kb()
    graph = get_graph_service()
    older, newer = _payload(kb["id"], [1]), _payload(kb["id"], [2])
    await graph.write_graph(older, kb["id"])
    await graph.write_graph(newer, kb["id"])

    # The older edge's pass runs late and must not retire the newer one.
    await supersession.resolve_supersession(graph._driver, kb["id"], older)
    assert await _live_edges(auth_world, kb["id"]) == {"edge-1", "edge-2"}

    await supersession.resolve_supersession(graph._driver, kb["id"], newer)
    assert await _live_edges(auth_world, kb["id"]) == {"edge-2"}
    rows = await _rows(auth_world, "MATCH ()-[r:RELATION {id: 'edge-1'}]->() RETURN r.valid_to AS v, r.expired_at AS e")
    assert rows[0]["v"] and rows[0]["e"]


async def test_supersession_stays_inside_the_kb(auth_world, judge_replaces_everything):
    first, second = await auth_world.create_kb(), await auth_world.create_kb()
    graph = get_graph_service()
    other = _payload(second["id"], [1])
    await graph.write_graph(other, second["id"])
    await graph.write_graph(_payload(first["id"], [2]), first["id"])
    newer = _payload(first["id"], [3])
    await graph.write_graph(newer, first["id"])
    await supersession.resolve_supersession(graph._driver, first["id"], newer)
    assert await _live_edges(auth_world, second["id"]) == {"edge-1"}


async def test_supersession_llm_calls_are_bounded(auth_world, monkeypatch):
    running = peak = 0

    async def is_state(_text):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        running -= 1
        return False

    monkeypatch.setattr(supersession, "_is_state_update", is_state)
    monkeypatch.setattr(supersession.settings, "default_supersession", True)
    monkeypatch.setattr(supersession.settings, "supersession_max_concurrency", 2)
    kb = await auth_world.create_kb()
    graph = get_graph_service()
    await supersession.resolve_supersession(graph._driver, kb["id"], _payload(kb["id"], list(range(8))))
    assert peak == 2


async def test_commit_many_writes_together_and_finishes_each(auth_world, stubs):
    kb = await auth_world.create_kb()
    pipeline = IngestPipeline(get_graph_service(), _Compressor(), _Resolver())
    items = [await pipeline.prepare_text(kb["id"], f"Fact {i}") for i in range(3)]
    await pipeline.commit_many(items)
    assert sorted(await _facts(auth_world, kb["id"])) == ["Fact 0", "Fact 1", "Fact 2"]
    assert await _episode_status(auth_world, kb["id"]) == ["extracted"] * 3
