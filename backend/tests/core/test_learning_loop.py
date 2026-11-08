# ruff: noqa: E501
"""Query learning and the sleep cycle. The LLM is a deterministic stub, so this
measures memory mechanics only, not answer quality."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.core.jobs import drain_background
from app.services import recall_ranker, sleep_service
from app.services.graph_service import get_graph_service
from app.services.sleep_service import LeaseLost, SleepBusy, SleepOrchestrator, is_outage


def _iso(days_ago: float = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


async def _rows(world, cypher, **params):
    async with world.driver.session() as session:
        return await (await session.run(cypher, **params)).data()


async def _ingest(world, kb, text):
    response = await world.client.post(
        "/api/v1/ingest/sync", json={"text": text, "agent_id": kb["id"]}, headers=world.headers(kb["api_key"])
    )
    assert response.status_code == 200, response.text
    await drain_background()


async def _ask(world, kb, question):
    response = await world.client.post(
        "/api/v1/query", json={"question": question, "agent_id": kb["id"]}, headers=world.headers(kb["api_key"])
    )
    assert response.status_code == 200, response.text
    await drain_background()
    return response.json()


async def _seed_node(world, kb_id, node_id, **props):
    body = {"label": node_id, "type": "CONCEPT", "created_at": _iso(), **props}
    async with world.driver.session() as session:
        await session.run("CREATE (n:SpaiderNode {id: $id, agent_id: $kb}) SET n += $props", id=node_id, kb=kb_id, props=body)


async def _seed_edge(world, kb_id, source, target, edge_id, **props):
    async with world.driver.session() as session:
        await session.run(
            """
            MATCH (a:SpaiderNode {id: $source, agent_id: $kb}), (b:SpaiderNode {id: $target, agent_id: $kb})
            CREATE (a)-[r:RELATION {id: $edge, agent_id: $kb, relation: 'R'}]->(b) SET r += $props
            """,
            source=source, target=target, kb=kb_id, edge=edge_id, props=props,
        )


async def _seed_episode(world, kb_id, episode_id, content="event", **props):
    body = {
        "content": content, "kind": "observation", "status": "new", "clearance_level": 1,
        "recorded_at": _iso(1), "occurred_at": _iso(1), "metadata": "{}", **props,
    }
    async with world.driver.session() as session:
        await session.run(
            "CREATE (e:Episode {id: $id, agent_id: $kb, idempotency_key: $id}) SET e += $props",
            id=episode_id, kb=kb_id, props=body,
        )


@pytest.fixture
def orchestrator(openai_stub, monkeypatch):
    monkeypatch.setattr(settings, "sleep_extract_min_age_seconds", 0)
    return SleepOrchestrator(get_graph_service())


async def _feedback(world, kb, query_id, success, node_ids=None):
    response = await world.client.post(
        "/api/v1/system/feedback",
        json={"query_id": query_id, "success": success, "agent_id": kb["id"], "used_node_ids": node_ids or []},
        headers=world.headers(kb["api_key"]),
    )
    assert response.status_code == 202, response.text


# ── Answering ─────────────────────────────────────────────────────────────


async def test_an_answer_has_an_id_citations_and_episodes(auth_world, openai_stub):
    kb = await auth_world.create_kb()
    await _ingest(auth_world, kb, "Acme is based in Berlin.")
    body = await _ask(auth_world, kb, "Where is Acme based?")
    assert body["query_id"]
    assert body["citations"] and body["citations"][0]["ref"] in body["answer"]
    kinds = await _rows(auth_world, "MATCH (e:Episode {agent_id: $kb}) RETURN e.kind AS k", kb=kb["id"])
    assert {"query", "answer"} <= {row["k"] for row in kinds}


async def test_a_citation_tag_outside_the_context_is_ignored():
    from types import SimpleNamespace

    from app.services.learning import parse_citations

    node = SimpleNamespace(id="a", label="A", agent_id="kb")
    assert [c["node_id"] for c in parse_citations("x [n1] y [n9] z [n1]", [node])] == ["a"]


async def test_one_query_boosts_each_node_once(auth_world, openai_stub):
    kb = await auth_world.create_kb()
    await _ingest(auth_world, kb, "Acme is based in Berlin.")
    body = await _ask(auth_world, kb, "Where is Acme based?")
    cited = [item["node_id"] for item in body["citations"]]
    rows = await _rows(
        auth_world,
        "MATCH (n:SpaiderNode {agent_id: $kb}) WHERE n.id IN $ids RETURN n.retrieval_count AS c",
        kb=kb["id"], ids=cited,
    )
    assert rows and all(row["c"] == 1 for row in rows)


async def test_nodes_cited_together_get_linked(auth_world):
    from app.services.learning import strengthen_co_activation

    kb = await auth_world.create_kb()
    await _seed_node(auth_world, kb["id"], "a")
    await _seed_node(auth_world, kb["id"], "b")
    cited = [{"node_id": "a", "agent_id": kb["id"]}, {"node_id": "b", "agent_id": kb["id"]}]
    await strengthen_co_activation(auth_world.driver, kb["id"], cited)
    await strengthen_co_activation(auth_world.driver, kb["id"], cited)
    rows = await _rows(auth_world, "MATCH (:SpaiderNode {id: 'a'})-[c:CO_ACTIVATED]->() RETURN c.count AS n")
    assert rows == [{"n": 2}]


async def test_memory_off_keeps_no_exchange(auth_world, openai_stub):
    kb = await auth_world.create_kb()
    await _ingest(auth_world, kb, "Acme is based in Berlin.")
    async with auth_world.driver.session() as session:
        await session.run("MATCH (a:SystemAgent {agent_id: $kb}) SET a.memory_mode = 'off'", kb=kb["id"])
    await _ask(auth_world, kb, "Where is Acme based?")
    rows = await _rows(auth_world, "MATCH (e:Episode {agent_id: $kb, kind: 'query'}) RETURN e", kb=kb["id"])
    assert rows == []


# ── Feedback ──────────────────────────────────────────────────────────────


@pytest.fixture
async def answered(auth_world):
    """A stored answer that cited a and b, which share an edge, plus a bystander edge."""
    from types import SimpleNamespace

    from app.services.learning import record_exchange

    kb = await auth_world.create_kb()
    for node in ("a", "b", "c"):
        await _seed_node(auth_world, kb["id"], node)
    await _seed_edge(auth_world, kb["id"], "a", "b", "ab", utility_weight=1.0)
    await _seed_edge(auth_world, kb["id"], "b", "c", "bc", utility_weight=1.0)
    retrieved = [SimpleNamespace(id=x, agent_id=kb["id"], clearance_level=1) for x in ("a", "b", "c")]
    cited = [{"node_id": x, "agent_id": kb["id"]} for x in ("a", "b")]
    await record_exchange(auth_world.driver, kb["id"], query_id="q1", question="Q", answer="A", retrieved=retrieved, cited=cited)
    return kb


async def _weight(world, edge_id):
    rows = await _rows(world, "MATCH ()-[r:RELATION {id: $id}]->() RETURN r.utility_weight AS w", id=edge_id)
    return round(rows[0]["w"], 3)


async def test_feedback_uses_what_the_answer_cited(auth_world, answered):
    await _feedback(auth_world, answered, "q1", True, node_ids=["b", "c"])
    assert await _weight(auth_world, "ab") == 1.1
    assert await _weight(auth_world, "bc") == 1.0


async def test_repeating_a_verdict_changes_nothing(auth_world, answered):
    await _feedback(auth_world, answered, "q1", True)
    await _feedback(auth_world, answered, "q1", True)
    assert await _weight(auth_world, "ab") == 1.1


async def test_the_latest_verdict_wins(auth_world, answered):
    await _feedback(auth_world, answered, "q1", True)
    await _feedback(auth_world, answered, "q1", False)
    assert await _weight(auth_world, "ab") == 0.9
    rows = await _rows(auth_world, "MATCH (f:Feedback {query_id: 'q1'}) RETURN f.verdict AS v")
    assert rows == [{"v": -1}]


async def test_concurrent_verdicts_leave_a_consistent_weight(auth_world, answered):
    from app.services.learning import apply_feedback

    await asyncio.gather(
        *[apply_feedback(auth_world.driver, answered["id"], "q1", index % 2 == 0, ["a", "b"]) for index in range(6)]
    )
    rows = await _rows(auth_world, "MATCH (f:Feedback {query_id: 'q1'}) RETURN f.verdict AS v")
    assert await _weight(auth_world, "ab") == round(1.0 + rows[0]["v"] * 0.1, 3)


async def test_feedback_without_a_stored_answer_needs_node_ids(auth_world):
    kb = await auth_world.create_kb()
    response = await auth_world.client.post(
        "/api/v1/system/feedback",
        json={"query_id": "nope", "success": True, "agent_id": kb["id"]},
        headers=auth_world.headers(kb["api_key"]),
    )
    assert response.status_code == 422


# ── Lease ─────────────────────────────────────────────────────────────────


async def test_a_full_run_reports_all_ten_passes(auth_world, orchestrator):
    kb = await auth_world.create_kb()
    report = await orchestrator.run(kb["id"])
    assert list(report["passes"]) == list(sleep_service.PASSES)
    assert all(p["status"] == "ok" for p in report["passes"].values()), report["passes"]
    assert report["status"] == "completed"


async def test_a_kb_sleeps_once_at_a_time(auth_world, orchestrator, monkeypatch):
    kb = await auth_world.create_kb()
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow(agent_id):
        entered.set()
        await release.wait()
        return {}

    monkeypatch.setattr(orchestrator, "_pass_extract", slow)
    first = asyncio.create_task(orchestrator.run(kb["id"], passes=("extract",)))
    await entered.wait()
    with pytest.raises(SleepBusy):
        await SleepOrchestrator(get_graph_service()).run(kb["id"], passes=("ranker",))
    release.set()
    await first
    await SleepOrchestrator(get_graph_service()).run(kb["id"], passes=("ranker",))


async def test_simultaneous_claims_have_one_winner(auth_world):
    kb = await auth_world.create_kb()
    leases = [sleep_service._Lease(auth_world.driver, kb["id"]) for _ in range(6)]
    results = await asyncio.gather(*[lease.acquire() for lease in leases])
    assert sum(results) == 1
    await leases[results.index(True)].release()


async def test_heartbeat_keeps_the_lease_past_its_ttl(auth_world, monkeypatch):
    monkeypatch.setattr(settings, "sleep_lease_ttl_seconds", 1)
    kb = await auth_world.create_kb()
    lease = sleep_service._Lease(auth_world.driver, kb["id"])
    assert await lease.acquire()
    await asyncio.sleep(1.6)
    assert not await sleep_service._Lease(auth_world.driver, kb["id"]).acquire()
    lease.ensure_held()
    await lease.release()


async def test_losing_the_lease_stops_the_run(auth_world, orchestrator, monkeypatch):
    kb = await auth_world.create_kb()
    ran = []

    async def steal(agent_id):
        ran.append("first")
        orchestrator._lease._lost = True
        return {}

    async def never(agent_id):
        ran.append("second")
        return {}

    monkeypatch.setattr(orchestrator, "_pass_extract", steal)
    monkeypatch.setattr(orchestrator, "_pass_prune", never)
    report = await orchestrator.run(kb["id"], passes=("extract", "prune"))
    assert report["status"] == "lease_lost" and ran == ["first"]


async def test_lease_check_raises():
    lease = sleep_service._Lease(None, "kb")
    lease._lost = True
    with pytest.raises(LeaseLost):
        lease.ensure_held()


async def test_a_failing_pass_does_not_stop_the_rest(auth_world, orchestrator, monkeypatch):
    kb = await auth_world.create_kb()

    async def boom(agent_id):
        raise RuntimeError("secret detail")

    monkeypatch.setattr(orchestrator, "_pass_fusion", boom)
    report = await orchestrator.run(kb["id"], passes=("fusion", "ranker"))
    assert report["passes"]["fusion"] == {"status": "failed", "ms": report["passes"]["fusion"]["ms"], "error": "RuntimeError"}
    assert report["passes"]["ranker"]["status"] == "ok"


# ── Extract ───────────────────────────────────────────────────────────────


async def test_sleep_extracts_waiting_episodes(auth_world, orchestrator):
    kb = await auth_world.create_kb()
    await _seed_episode(auth_world, kb["id"], "ep1", "Acme is based in Berlin.")
    report = await orchestrator.run(kb["id"], passes=("extract",))
    assert report["passes"]["extract"]["extracted"] == 1
    rows = await _rows(auth_world, "MATCH (e:Episode {id: 'ep1'}) RETURN e.status AS s")
    assert rows == [{"s": "extracted"}]
    facts = await _rows(auth_world, "MATCH (n:SpaiderNode {agent_id: $kb, type: 'FACT'}) RETURN n.id", kb=kb["id"])
    assert len(facts) == 1


async def test_extracted_knowledge_keeps_the_episode_clearance(auth_world, orchestrator):
    kb = await auth_world.create_kb()
    await _seed_episode(auth_world, kb["id"], "ep1", "Acme is based in Berlin.", clearance_level=4)
    await orchestrator.run(kb["id"], passes=("extract",))
    rows = await _rows(auth_world, "MATCH (n:SpaiderNode {agent_id: $kb}) RETURN n.clearance_level AS c", kb=kb["id"])
    assert rows and all(row["c"] == 4 for row in rows)


async def test_an_outage_does_not_use_up_attempts(auth_world, orchestrator, openai_stub):
    kb = await auth_world.create_kb()
    # Unique text: extraction results are cached across tests.
    await _seed_episode(auth_world, kb["id"], "ep1", f"Gamma {kb['id']} is based in Rome.")
    await _seed_episode(auth_world, kb["id"], "ep2", f"Delta {kb['id']} is based in Oslo.")
    openai_stub.fail_with = 503
    report = await orchestrator.run(kb["id"], passes=("extract",))
    assert report["passes"]["extract"]["outage"] is True
    rows = await _rows(auth_world, "MATCH (e:Episode {agent_id: $kb}) RETURN e.status AS s, e.extract_attempts AS a", kb=kb["id"])
    assert all(row == {"s": "new", "a": None} for row in rows), rows


async def test_a_bad_episode_backs_off_then_fails(auth_world, monkeypatch):
    class Refuses:
        async def extract(self, text, context=None):
            raise ValueError("cannot parse this")

    class Resolver:
        async def resolve(self, payload, agent_id, graph, caller_context="api"):
            return payload

    from app.services.ingest_pipeline import IngestPipeline

    monkeypatch.setattr(settings, "sleep_extract_min_age_seconds", 0)
    graph = get_graph_service()
    orchestrator = SleepOrchestrator(graph, pipeline=IngestPipeline(graph, Refuses(), Resolver()))
    kb = await auth_world.create_kb()
    await _seed_episode(auth_world, kb["id"], "ep1")
    await orchestrator.run(kb["id"], passes=("extract",))
    rows = await _rows(auth_world, "MATCH (e:Episode {id: 'ep1'}) RETURN e.status AS s, e.extract_attempts AS a, e.next_attempt_at AS n")
    assert rows[0]["s"] == "new" and rows[0]["a"] == 1 and rows[0]["n"] > _iso()

    report = await orchestrator.run(kb["id"], passes=("extract",))
    assert report["passes"]["extract"]["candidates"] == 0

    async with auth_world.driver.session() as session:
        await session.run("MATCH (e:Episode {id: 'ep1'}) SET e.extract_attempts = 2, e.next_attempt_at = ''")
    await orchestrator.run(kb["id"], passes=("extract",))
    rows = await _rows(auth_world, "MATCH (e:Episode {id: 'ep1'}) RETURN e.status AS s")
    assert rows == [{"s": "failed"}]


@pytest.mark.parametrize(
    ("message", "expected"),
    [("Connection refused", True), ("Error code: 503", True), ("rate limit reached", True), ("JSON parse error", False)],
)
def test_outage_classification(message, expected):
    assert is_outage(RuntimeError(message)) is expected


# ── Contradictions ────────────────────────────────────────────────────────


async def test_updates_settle_oldest_first(auth_world, orchestrator, monkeypatch):
    async def is_state(_text):
        return True

    async def judge(_text, candidates):
        return {candidate["edge_id"] for candidate in candidates}

    monkeypatch.setattr(sleep_service.settings, "default_supersession", True)
    monkeypatch.setattr("app.services.supersession._is_state_update", is_state)
    monkeypatch.setattr("app.services.supersession._judge", judge)
    kb = await auth_world.create_kb()
    await _seed_node(auth_world, kb["id"], "company")
    for index, days in (("1", 3), ("2", 2), ("3", 1)):
        await _seed_node(auth_world, kb["id"], f"city{index}")
        await _seed_edge(auth_world, kb["id"], "company", f"city{index}", f"edge{index}", recorded_at=_iso(days))
    await orchestrator.run(kb["id"], passes=("contradictions",))
    rows = await _rows(auth_world, "MATCH ()-[r:RELATION {agent_id: $kb}]->() WHERE NOT coalesce(r.superseded, false) RETURN r.id AS id", kb=kb["id"])
    assert rows == [{"id": "edge3"}]


async def test_an_llm_outage_leaves_edges_for_a_retry(auth_world, orchestrator, openai_stub, monkeypatch):
    monkeypatch.setattr(sleep_service.settings, "default_supersession", True)
    kb = await auth_world.create_kb()
    await _seed_node(auth_world, kb["id"], "a")
    await _seed_node(auth_world, kb["id"], "b")
    await _seed_edge(auth_world, kb["id"], "a", "b", "edge1", recorded_at=_iso(1))
    openai_stub.fail_with = 503
    await orchestrator.run(kb["id"], passes=("contradictions",))
    rows = await _rows(auth_world, "MATCH ()-[r:RELATION {id: 'edge1'}]->() RETURN r.contradiction_checked AS c")
    assert rows == [{"c": None}]


async def test_a_superseded_fact_disappears_from_as_of_after_its_time(auth_world):
    kb = await auth_world.create_kb()
    await _seed_node(auth_world, kb["id"], "f", created_at=_iso(5), superseded=True, superseded_at=_iso(2))
    graph = get_graph_service()
    before = await graph.get_full_graph(kb["id"], as_of=_iso(3))
    after = await graph.get_full_graph(kb["id"], as_of=_iso(1))
    assert [node.id for node in before.nodes] == ["f"] and after.nodes == []


# ── Fusion, decay, archive, prune ─────────────────────────────────────────


async def test_fusion_keeps_knowledge_edges_and_raises_clearance(auth_world, orchestrator):
    kb = await auth_world.create_kb()
    vector = [1.0] + [0.0] * (settings.embedding_dimensions - 1)
    await _seed_node(auth_world, kb["id"], "keep", embedding=vector)
    await _seed_node(auth_world, kb["id"], "drop", embedding=vector, clearance_level=4)
    await _seed_node(auth_world, kb["id"], "target")
    await _seed_edge(auth_world, kb["id"], "keep", "target", "e0")
    await _seed_edge(auth_world, kb["id"], "drop", "target", "e1")
    await orchestrator.run(kb["id"], passes=("fusion",))
    survivors = await _rows(auth_world, "MATCH (n:SpaiderNode {agent_id: $kb}) WHERE n.embedding IS NOT NULL RETURN n.id AS id, n.clearance_level AS c", kb=kb["id"])
    assert len(survivors) == 1 and survivors[0]["c"] == 4
    assert await _rows(auth_world, "MATCH ()-[r:RELATED_TO]->() RETURN r") == []
    assert len(await _rows(auth_world, "MATCH (:SpaiderNode {id: $id})-[r:RELATION]->(:SpaiderNode {id: 'target'}) RETURN r", id=survivors[0]["id"])) == 2


async def test_fusion_leaves_facts_alone(auth_world, orchestrator):
    kb = await auth_world.create_kb()
    vector = [1.0] + [0.0] * (settings.embedding_dimensions - 1)
    await _seed_node(auth_world, kb["id"], "f1", embedding=vector, type="FACT")
    await _seed_node(auth_world, kb["id"], "f2", embedding=vector, type="FACT")
    report = await orchestrator.run(kb["id"], passes=("fusion",))
    assert report["passes"]["fusion"]["merged"] == 0


async def test_decay_persists_and_archive_hides_unused_nodes(auth_world, orchestrator, monkeypatch):
    monkeypatch.setattr(settings, "cognitive_base_decay", 0.1)
    kb = await auth_world.create_kb()
    await _seed_node(auth_world, kb["id"], "old", created_at=_iso(60))
    await _seed_node(auth_world, kb["id"], "old-fact", created_at=_iso(60), type="FACT")
    await _seed_node(auth_world, kb["id"], "fresh")
    await orchestrator.run(kb["id"], passes=("decay", "archive"))
    rows = await _rows(auth_world, "MATCH (n:SpaiderNode {agent_id: $kb}) RETURN n.id AS id, n.energy_level AS e, n.status AS s", kb=kb["id"])
    by_id = {row["id"]: row for row in rows}
    assert by_id["old"]["e"] < 0.05 and by_id["old"]["s"] == "archived"
    assert by_id["old-fact"]["s"] is None
    assert by_id["fresh"]["e"] > 0.9 and by_id["fresh"]["s"] is None


async def test_decay_does_not_compound_across_runs(auth_world, orchestrator, monkeypatch):
    monkeypatch.setattr(settings, "cognitive_base_decay", 0.01)
    kb = await auth_world.create_kb()
    await _seed_node(auth_world, kb["id"], "n", created_at=_iso(2))
    await orchestrator.run(kb["id"], passes=("decay",))
    first = (await _rows(auth_world, "MATCH (n:SpaiderNode {id: 'n'}) RETURN n.energy_level AS e"))[0]["e"]
    await orchestrator.run(kb["id"], passes=("decay",))
    second = (await _rows(auth_world, "MATCH (n:SpaiderNode {id: 'n'}) RETURN n.energy_level AS e"))[0]["e"]
    assert second == pytest.approx(first, abs=0.001)


async def test_prune_removes_orphans_but_never_verbatim_records(auth_world, orchestrator):
    kb = await auth_world.create_kb()
    await _seed_node(auth_world, kb["id"], "orphan", created_at=_iso(400))
    await _seed_node(auth_world, kb["id"], "fact", created_at=_iso(400), type="FACT")
    await _seed_node(auth_world, kb["id"], "image", created_at=_iso(400), type="IMAGE")
    await _seed_node(auth_world, kb["id"], "young", created_at=_iso())
    await orchestrator.run(kb["id"], passes=("prune",))
    rows = await _rows(auth_world, "MATCH (n:SpaiderNode {agent_id: $kb}) RETURN n.id AS id", kb=kb["id"])
    assert {row["id"] for row in rows} == {"fact", "image", "young"}


# ── Lessons, skills, compaction ───────────────────────────────────────────


async def _exchange(world, kb_id, query_id, question="Where is it?", clearance=1, cited=("a",)):
    from types import SimpleNamespace

    from app.services.learning import record_exchange

    retrieved = [SimpleNamespace(id=x, agent_id=kb_id, clearance_level=clearance) for x in cited]
    await record_exchange(
        world.driver, kb_id, query_id=query_id, question=question, answer="Answer",
        retrieved=retrieved, cited=[{"node_id": x, "agent_id": kb_id} for x in cited],
    )


async def test_a_wrong_answer_becomes_a_lesson_at_its_clearance(auth_world, orchestrator):
    from app.services.learning import apply_feedback

    kb = await auth_world.create_kb()
    await _seed_node(auth_world, kb["id"], "a", clearance_level=4)
    await _exchange(auth_world, kb["id"], "q1", clearance=2)
    await apply_feedback(auth_world.driver, kb["id"], "q1", False, ["a"])
    await orchestrator.run(kb["id"], passes=("lessons",))
    rows = await _rows(auth_world, "MATCH (n:SpaiderNode {agent_id: $kb, type: 'LESSON'}) RETURN n.clearance_level AS c", kb=kb["id"])
    assert rows == [{"c": 4}]
    report = await orchestrator.run(kb["id"], passes=("lessons",))
    assert report["passes"]["lessons"]["created"] == 0


async def test_flipping_a_verdict_retires_its_lesson(auth_world, orchestrator):
    from app.services.learning import apply_feedback

    kb = await auth_world.create_kb()
    await _seed_node(auth_world, kb["id"], "a")
    await _exchange(auth_world, kb["id"], "q1")
    await apply_feedback(auth_world.driver, kb["id"], "q1", False, ["a"])
    await orchestrator.run(kb["id"], passes=("lessons",))
    await apply_feedback(auth_world.driver, kb["id"], "q1", True, ["a"])
    await orchestrator.run(kb["id"], passes=("lessons",))
    assert await _rows(auth_world, "MATCH (n:SpaiderNode {agent_id: $kb, type: 'LESSON'}) RETURN n", kb=kb["id"]) == []


async def test_repeated_success_becomes_a_skill_kept_out_of_retrieval(auth_world, orchestrator):
    from app.services.learning import apply_feedback

    kb = await auth_world.create_kb()
    await _seed_node(auth_world, kb["id"], "a")
    for index in range(3):
        await _exchange(auth_world, kb["id"], f"q{index}", question="Where is the Acme office located?", clearance=index + 1)
        await apply_feedback(auth_world.driver, kb["id"], f"q{index}", True, ["a"])
    await orchestrator.run(kb["id"], passes=("skills",))
    rows = await _rows(auth_world, "MATCH (n:SpaiderNode {agent_id: $kb, type: 'SKILL'}) RETURN n.clearance_level AS c, n.embedding AS e", kb=kb["id"])
    assert rows == [{"c": 3, "e": None}]


async def test_compaction_folds_old_episodes_and_hides_them(auth_world, orchestrator, monkeypatch):
    from app.services.episode_store import EpisodeStore

    monkeypatch.setattr(settings, "sleep_compact_after_days", 1)
    monkeypatch.setattr(settings, "sleep_compact_min_group", 2)
    kb = await auth_world.create_kb()
    await _seed_episode(auth_world, kb["id"], "e1", status="extracted", occurred_at=_iso(5), session_id="s", clearance_level=1)
    await _seed_episode(auth_world, kb["id"], "e2", status="extracted", occurred_at=_iso(5), session_id="s", clearance_level=3)
    await _seed_episode(auth_world, kb["id"], "recent", status="extracted", occurred_at=_iso(), session_id="s")
    report = await orchestrator.run(kb["id"], passes=("compaction",))
    assert report["passes"]["compaction"] == {**report["passes"]["compaction"], "summaries": 1, "episodes_compacted": 2}
    rows = await _rows(auth_world, "MATCH (c:Episode {kind: 'compaction'})-[:COMPACTS]->(e) RETURN c.clearance_level AS c, e.status AS s")
    assert len(rows) == 2 and all(row == {"c": 3, "s": "compacted"} for row in rows)
    recent = await EpisodeStore(auth_world.driver).recent(kb["id"])
    assert {row["id"] for row in recent if row["kind"] != "compaction"} == {"recent"}


async def test_compaction_clearance_only_rises(auth_world, orchestrator, monkeypatch):
    monkeypatch.setattr(settings, "sleep_compact_after_days", 1)
    monkeypatch.setattr(settings, "sleep_compact_min_group", 2)
    kb = await auth_world.create_kb()
    for episode_id, clearance in (("e1", 4), ("e2", 1)):
        await _seed_episode(auth_world, kb["id"], episode_id, status="extracted", occurred_at=_iso(5), session_id="s", clearance_level=clearance)
    await orchestrator.run(kb["id"], passes=("compaction",))
    for episode_id in ("e3", "e4"):
        await _seed_episode(auth_world, kb["id"], episode_id, status="extracted", occurred_at=_iso(4), session_id="s")
    await orchestrator.run(kb["id"], passes=("compaction",))
    rows = await _rows(auth_world, "MATCH (c:Episode {kind: 'compaction'}) RETURN c.clearance_level AS c")
    assert rows == [{"c": 4}]


# ── Ranker ────────────────────────────────────────────────────────────────


def test_fit_is_deterministic_and_learns():
    positive = [1.0, 1.0, 0.8, 0.5, 0.6, 1.0, 0.1]
    negative = [1.0, 0.1, 0.0, 0.5, 0.0, 0.0, 0.9]
    examples = [(positive, 1), (negative, 0)] * 10
    weights = recall_ranker.fit(examples)
    assert weights == recall_ranker.fit(examples)
    assert recall_ranker.predict(weights, positive) > 0.8 > 0.2 > recall_ranker.predict(weights, negative)


async def _train_data(world, kb_id):
    from types import SimpleNamespace

    from app.services.learning import apply_feedback, record_exchange

    await _seed_node(world, kb_id, "useful", energy_level=1.0, retrieval_count=50)
    await _seed_node(world, kb_id, "noise", energy_level=0.05, retrieval_count=0)
    nodes = [SimpleNamespace(id=x, agent_id=kb_id, clearance_level=1) for x in ("noise", "useful")]
    for index in range(12):
        query_id = f"q{index}"
        await record_exchange(
            world.driver, kb_id, query_id=query_id, question=f"Q{index}", answer="A",
            retrieved=nodes, cited=[{"node_id": "useful", "agent_id": kb_id}],
        )
        await apply_feedback(world.driver, kb_id, query_id, True, ["useful"])


async def test_ranker_learns_from_feedback_and_changes_the_order(auth_world, orchestrator, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(settings, "ranker_min_examples", 10)
    kb = await auth_world.create_kb()
    await _train_data(auth_world, kb["id"])
    candidates = [SimpleNamespace(id=x, agent_id=kb["id"]) for x in ("noise", "useful")]
    assert [n.id for n in await recall_ranker.rerank(auth_world.driver, kb["id"], candidates)] == ["noise", "useful"]
    report = await orchestrator.run(kb["id"], passes=("ranker",))
    assert report["passes"]["ranker"]["trained"] is True
    assert [n.id for n in await recall_ranker.rerank(auth_world.driver, kb["id"], candidates)] == ["useful", "noise"]


async def test_ranker_waits_for_enough_examples(auth_world, orchestrator):
    kb = await auth_world.create_kb()
    report = await orchestrator.run(kb["id"], passes=("ranker",))
    assert report["passes"]["ranker"]["trained"] is False


async def test_ranker_never_changes_the_candidate_set(auth_world, orchestrator, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(settings, "ranker_min_examples", 10)
    kb = await auth_world.create_kb()
    await _train_data(auth_world, kb["id"])
    await orchestrator.run(kb["id"], passes=("ranker",))
    candidates = [SimpleNamespace(id=x, agent_id=kb["id"]) for x in ("useful", "noise", "missing")]
    ranked = await recall_ranker.rerank(auth_world.driver, kb["id"], candidates)
    assert sorted(n.id for n in ranked) == ["missing", "noise", "useful"]


async def test_ranker_failure_keeps_the_original_order():
    from types import SimpleNamespace

    class Broken:
        def session(self):
            raise ConnectionError

    candidates = [SimpleNamespace(id=x, agent_id="kb") for x in ("a", "b")]
    assert await recall_ranker.rerank(Broken(), "kb", candidates) == candidates


# ── Routes and MCP ────────────────────────────────────────────────────────


async def test_sleep_route_needs_steward_and_saves_a_report(auth_world, openai_stub):
    kb = await auth_world.create_kb()
    reader = await auth_world.member(kb["id"], "reader")
    url = f"/api/v1/agents/{kb['id']}/sleep"
    assert (await auth_world.client.post(url, headers=auth_world.headers(reader["api_key"]))).status_code == 403
    response = await auth_world.client.post(url, headers=auth_world.headers(auth_world.owner_key))
    assert response.status_code == 200, response.text
    saved = await auth_world.client.get(url + "/report", headers=auth_world.headers(reader["api_key"]))
    assert saved.status_code == 200 and saved.json()["id"] == response.json()["id"]
    assert set(saved.json()) >= {"passes", "tokens", "status"}


def _mcp(principal, kb_id):
    from app.api.v1 import mcp_server

    return mcp_server._AGENT_ID.set(kb_id), mcp_server._PRINCIPAL.set(principal)


async def _principal(world, key):
    from app.core.authz import resolve_token

    return await resolve_token(key)


async def test_mcp_resources_respect_clearance(auth_world, orchestrator):
    from app.api.v1 import mcp_server

    kb = await auth_world.create_kb()
    await _seed_node(auth_world, kb["id"], "skill-low", type="SKILL", description="open procedure", clearance_level=1, updated_at=_iso())
    await _seed_node(auth_world, kb["id"], "lesson-high", type="LESSON", description="secret lesson", clearance_level=4, updated_at=_iso())
    low = await auth_world.member(kb["id"], "reader", clearance_level=1)
    _mcp(await _principal(auth_world, low["api_key"]), kb["id"])

    listing = await mcp_server._on_list_resources(None)
    assert [str(resource.uri) for resource in listing.resources] == ["spaider://skills/skill-low"]
    assert await mcp_server.read_resource("spaider://skills/skill-low") == "open procedure"
    with pytest.raises(ValueError, match="Unknown resource"):
        await mcp_server.read_resource("spaider://lessons/lesson-high")

    high = await auth_world.member(kb["id"], "reader", clearance_level=4)
    _mcp(await _principal(auth_world, high["api_key"]), kb["id"])
    assert await mcp_server.read_resource("spaider://lessons/lesson-high") == "secret lesson"


async def test_mcp_tools_for_sleep(auth_world, orchestrator):
    from app.api.v1 import mcp_server

    kb = await auth_world.create_kb()
    reader = await auth_world.member(kb["id"], "reader")
    _mcp(await _principal(auth_world, reader["api_key"]), kb["id"])
    text = (await mcp_server.call_tool("spaider.sleep_report", {}))[0].text
    assert "not slept" in text
    with pytest.raises(Exception):
        await mcp_server.call_tool("spaider.compact", {})

    _mcp(await _principal(auth_world, auth_world.owner_key), kb["id"])
    result = (await mcp_server.call_tool("spaider.compact", {}))[0].text
    assert json.loads(result)["status"] == "ok"
    assert "compaction" in (await mcp_server.call_tool("spaider.sleep_report", {}))[0].text
