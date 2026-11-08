# ruff: noqa: E501
"""Episodes, recall, the indexed resolver, deadlines and job status."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.config import settings
from app.core import jobs
from app.core.jobs import job_status, mark_job_finished, mark_job_queued
from app.core.kb_lock import kb_write_lock
from app.models.schemas import GraphPayload, Node
from app.services.entity_resolver import EntityResolver
from app.services.graph_service import get_graph_service
from app.services.query_service import QueryDeadlineExceeded, QueryService
from app.services.recall_service import lucene_query


async def _write(world, kb, episodes, key=None):
    response = await world.client.post(
        "/api/v1/episodes", json={"agent_id": kb["id"], "episodes": episodes}, headers=world.headers(key or kb["api_key"])
    )
    return response


async def _recall(world, kb, key=None, **body):
    response = await world.client.post(
        "/api/v1/recall", json={"agent_id": kb["id"], **body}, headers=world.headers(key or kb["api_key"])
    )
    assert response.status_code == 200, response.text
    return response.json()["results"]


async def test_episode_writes_make_no_llm_or_embedding_calls(auth_world, openai_stub):
    kb = await auth_world.create_kb()
    response = await _write(auth_world, kb, [{"content": "robot saw a red door"}, {"content": "robot opened it"}])
    assert response.status_code == 201, response.text
    assert len(response.json()["episodes"]) == 2
    results = await _recall(auth_world, kb, query="red door")
    assert results[0]["text"] == "robot saw a red door"
    assert openai_stub.chat_calls == [] and openai_stub.embedding_calls == 0


async def test_retrying_with_an_idempotency_key_is_safe(auth_world):
    kb = await auth_world.create_kb()
    body = [{"content": "once", "idempotency_key": "k1"}]
    first = (await _write(auth_world, kb, body)).json()["episodes"][0]
    second = (await _write(auth_world, kb, body)).json()["episodes"][0]
    assert first["id"] == second["id"] and first["created"] is True and second["created"] is False


async def test_concurrent_retries_create_one_episode(auth_world):
    kb = await auth_world.create_kb()
    body = [{"content": "race", "idempotency_key": "k1"}]
    responses = await asyncio.gather(*[_write(auth_world, kb, body) for _ in range(8)])
    assert len({r.json()["episodes"][0]["id"] for r in responses}) == 1


@pytest.mark.parametrize("kind", ["query", "answer", "compaction"])
async def test_reserved_kinds_cannot_be_written(auth_world, kind):
    kb = await auth_world.create_kb()
    assert (await _write(auth_world, kb, [{"content": "x", "kind": kind}])).status_code == 422


async def test_episodes_cannot_exceed_the_writers_clearance(auth_world):
    kb = await auth_world.create_kb()
    user = await auth_world.member(kb["id"], "contributor", clearance_level=1)
    assert (await _write(auth_world, kb, [{"content": "x", "clearance_level": 3}], key=user["api_key"])).status_code == 403


async def test_reader_cannot_write_episodes(auth_world):
    kb = await auth_world.create_kb()
    user = await auth_world.member(kb["id"], "reader")
    assert (await _write(auth_world, kb, [{"content": "x"}], key=user["api_key"])).status_code == 403


async def test_a_location_must_be_complete(auth_world):
    kb = await auth_world.create_kb()
    assert (await _write(auth_world, kb, [{"content": "x", "location": {"x": 1.0}}])).status_code == 422


async def test_recall_hides_episodes_above_clearance(auth_world):
    kb = await auth_world.create_kb()
    await _write(auth_world, kb, [{"content": "public door note"}, {"content": "secret door note", "clearance_level": 4}], key=auth_world.owner_key)
    low = await auth_world.member(kb["id"], "reader", clearance_level=1)
    results = await _recall(auth_world, kb, key=low["api_key"], query="door note")
    assert [r["text"] for r in results] == ["public door note"]
    listing = await auth_world.client.get(f"/api/v1/episodes?agent_id={kb['id']}", headers=auth_world.headers(low["api_key"]))
    assert [e["content"] for e in listing.json()["episodes"]] == ["public door note"]


async def test_recall_filters(auth_world):
    kb = await auth_world.create_kb()
    now = datetime.now(timezone.utc)
    await _write(auth_world, kb, [
        {"content": "alpha one", "kind": "vision", "session_id": "s1", "occurred_at": (now - timedelta(days=5)).isoformat()},
        {"content": "alpha two", "kind": "audio", "session_id": "s2", "occurred_at": now.isoformat()},
    ])
    assert [r["text"] for r in await _recall(auth_world, kb, query="alpha", kinds=["vision"])] == ["alpha one"]
    assert [r["text"] for r in await _recall(auth_world, kb, query="alpha", session_id="s2")] == ["alpha two"]
    since = (now - timedelta(days=1)).isoformat()
    assert [r["text"] for r in await _recall(auth_world, kb, query="alpha", since=since)] == ["alpha two"]


async def test_recall_near_a_location(auth_world):
    kb = await auth_world.create_kb()
    await _write(auth_world, kb, [
        {"content": "at origin", "location": {"x": 0.0, "y": 0.0}},
        {"content": "far away", "location": {"x": 500.0, "y": 0.0}},
    ])
    results = await _recall(auth_world, kb, near={"x": 1.0, "y": 1.0}, radius=10)
    assert [r["text"] for r in results] == ["at origin"]


async def test_recall_finds_nodes_without_a_model(auth_world, openai_stub):
    kb = await auth_world.create_kb()
    async with auth_world.driver.session() as session:
        await session.run("CREATE (:SpaiderNode {id: 'n', agent_id: $kb, label: 'Zephyr Corp', type: 'ORG', description: 'A company'})", kb=kb["id"])
    results = await _recall(auth_world, kb, query="Zephyr")
    assert results[0]["type"] == "node" and openai_stub.embedding_calls == 0


async def test_recall_federated_uses_grants(auth_world):
    source, target = await auth_world.create_kb("s"), await auth_world.create_kb("t")
    async with auth_world.driver.session() as session:
        await session.run("CREATE (:SpaiderNode {id: 'n', agent_id: $kb, label: 'Shared Widget', type: 'PERSON'})", kb=source["id"])
    assert await _recall(auth_world, target, key=auth_world.owner_key, query="Widget", federated=True) == []

    headers = auth_world.headers(auth_world.owner_key)
    grant = await auth_world.client.post("/api/v1/federation/grants", json={"source_kb": source["id"], "target_kb": target["id"], "node_types": ["PERSON"]}, headers=headers)
    await auth_world.client.post(f"/api/v1/federation/grants/{grant.json()['id']}/approve", json={}, headers=headers)
    federated = await _recall(auth_world, target, key=auth_world.owner_key, query="Widget", federated=True)
    assert [r["id"] for r in federated] == ["n"]
    assert await _recall(auth_world, target, key=auth_world.owner_key, query="Widget") == []


async def test_recall_excludes_archived_and_skills(auth_world):
    kb = await auth_world.create_kb()
    async with auth_world.driver.session() as session:
        await session.run("CREATE (:SpaiderNode {id: 'a', agent_id: $kb, label: 'Quartz archived', type: 'X', status: 'archived'})", kb=kb["id"])
        await session.run("CREATE (:SpaiderNode {id: 's', agent_id: $kb, label: 'Quartz skill', type: 'SKILL'})", kb=kb["id"])
    assert await _recall(auth_world, kb, query="Quartz") == []


async def test_a_hostile_query_string_is_harmless(auth_world):
    kb = await auth_world.create_kb()
    assert await _recall(auth_world, kb, query='AND OR "(* ~ :/ \\') == []
    assert lucene_query("!!! ???") is None


async def test_mcp_remember_and_recall(auth_world, openai_stub):
    from app.api.v1 import mcp_server
    from app.core.authz import resolve_token

    kb = await auth_world.create_kb()
    mcp_server._AGENT_ID.set(kb["id"])
    mcp_server._PRINCIPAL.set(await resolve_token(kb["api_key"]))
    await mcp_server.call_tool("spaider.remember", {"content": "the charger is in bay four"})
    found = json.loads((await mcp_server.call_tool("spaider.recall", {"query": "charger bay"}))[0].text)
    assert found[0]["text"] == "the charger is in bay four"
    assert openai_stub.chat_calls == []


# ── Resolver ──────────────────────────────────────────────────────────────


def _payload(kb_id, *labels):
    return GraphPayload(nodes=[Node(label=label, type="CONCEPT", agent_id=kb_id) for label in labels], edges=[])


async def _seed(world, kb_id, node_id, label, **props):
    async with world.driver.session() as session:
        await session.run("CREATE (n:SpaiderNode {id: $id, agent_id: $kb, label: $label, type: 'CONCEPT'}) SET n += $p", id=node_id, kb=kb_id, label=label, p=props)


@pytest.fixture
def resolver():
    return EntityResolver(embedding_service=SimpleNamespace(embed_batch=_no_embed, embed=_no_embed))


async def _no_embed(*args, **kwargs):
    return []


async def _resolve(resolver, kb_id, *labels):
    return await resolver.resolve(_payload(kb_id, *labels), kb_id, get_graph_service(), caller_context="api")


async def test_resolver_matches_exact_case_insensitive(auth_world, resolver):
    kb = await auth_world.create_kb()
    await _seed(auth_world, kb["id"], "x", "Acme Corporation")
    result = await _resolve(resolver, kb["id"], "acme corporation")
    assert [n.id for n in result.nodes] == ["x"]


async def test_resolver_matches_a_typo(auth_world, resolver):
    kb = await auth_world.create_kb()
    await _seed(auth_world, kb["id"], "x", "Kubernetes Cluster")
    assert [n.id for n in (await _resolve(resolver, kb["id"], "Kubernetis Cluster")).nodes] == ["x"]


async def test_resolver_matches_an_alias(auth_world, resolver):
    kb = await auth_world.create_kb()
    await _seed(auth_world, kb["id"], "x", "International Business Machines", aliases_text="IBM", properties=json.dumps({"aliases": ["IBM"]}))
    assert [n.id for n in (await _resolve(resolver, kb["id"], "IBM")).nodes] == ["x"]


async def test_resolver_stays_in_its_kb(auth_world, resolver):
    first, second = await auth_world.create_kb(), await auth_world.create_kb()
    await _seed(auth_world, first["id"], "x", "Acme Corporation")
    result = await _resolve(resolver, second["id"], "Acme Corporation")
    assert result.nodes[0].id != "x"


async def test_two_mentions_in_one_document_are_one_entity(auth_world, resolver):
    kb = await auth_world.create_kb()
    result = await _resolve(resolver, kb["id"], "Nova Labs", "nova labs")
    assert len(result.nodes) == 1


async def test_resolver_cost_does_not_grow_with_the_graph(auth_world, resolver, monkeypatch):
    kb = await auth_world.create_kb()
    async with auth_world.driver.session() as session:
        await session.run("UNWIND range(1, 500) AS i CREATE (:SpaiderNode {id: 'n' + i, agent_id: $kb, label: 'Unrelated ' + i, type: 'CONCEPT'})", kb=kb["id"])
    graph = get_graph_service()
    candidates = await graph.resolver_candidates(kb["id"], ["Brand New Thing"], per_label=5)
    assert len(candidates) < 100


async def test_label_with_lucene_syntax_does_not_break_resolving(auth_world, resolver):
    kb = await auth_world.create_kb()
    result = await _resolve(resolver, kb["id"], 'C++ (lang): "x" AND ~y')
    assert len(result.nodes) == 1


# ── Writers, deadline, jobs ───────────────────────────────────────────────


async def test_concurrent_ingests_create_one_entity(auth_world, monkeypatch):
    from app.services.ingest_pipeline import IngestPipeline

    class Compressor:
        async def extract(self, text, context=None):
            return GraphPayload(nodes=[Node(label="Shared Entity", type="CONCEPT")])

    kb = await auth_world.create_kb()
    resolver = EntityResolver(embedding_service=SimpleNamespace(embed_batch=_fake_embed, embed=_fake_embed_one))
    pipeline = IngestPipeline(get_graph_service(), Compressor(), resolver)
    await asyncio.gather(*[pipeline.ingest_text(kb["id"], f"text {i}") for i in range(5)])
    async with auth_world.driver.session() as session:
        rows = await (await session.run("MATCH (n:SpaiderNode {agent_id: $kb, label: 'Shared Entity'}) RETURN n.id", kb=kb["id"])).data()
    assert len(rows) == 1


async def _fake_embed(texts):
    return [[0.1] * settings.embedding_dimensions for _ in texts]


async def _fake_embed_one(text):
    return [0.1] * settings.embedding_dimensions


def test_kb_locks_are_per_kb_and_reused():
    assert kb_write_lock("a") is kb_write_lock("a")
    assert kb_write_lock("a") is not kb_write_lock("b")


async def test_a_slow_query_hits_its_deadline(monkeypatch):
    service = QueryService.__new__(QueryService)

    async def slow(*args):
        await asyncio.sleep(5)

    monkeypatch.setattr(service, "_query_nl_impl", slow)
    monkeypatch.setattr(settings, "query_deadline_seconds", 0.01)
    with pytest.raises(QueryDeadlineExceeded):
        await service.query_nl("q", "kb")


async def test_queued_never_overwrites_a_finished_job(auth_world):
    await mark_job_finished("job-1", "kb", "done")
    await mark_job_queued("job-1", "kb")
    assert (await job_status("job-1"))["status"] == "done"


async def test_job_status_is_limited_to_readers_of_the_kb(auth_world):
    first, second = await auth_world.create_kb(), await auth_world.create_kb()
    await mark_job_queued("job-2", first["id"])
    url = "/api/v1/ingest/jobs/job-2"
    assert (await auth_world.client.get(url, headers=auth_world.headers(first["api_key"]))).json()["status"] == "queued"
    assert (await auth_world.client.get(url, headers=auth_world.headers(second["api_key"]))).status_code == 404


def test_fast_model_falls_back_to_the_main_model(monkeypatch):
    monkeypatch.setattr(settings, "llm_fast_model", "")
    assert settings.litellm_fast_model == settings.litellm_model
    monkeypatch.setattr(settings, "llm_fast_model", "gpt-4o-mini")
    assert "gpt-4o-mini" in settings.litellm_fast_model


def test_jobs_module_exposes_the_queue():
    assert isinstance(jobs.get_job_queue(), jobs.JobQueue) and MagicMock
