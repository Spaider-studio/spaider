# ruff: noqa: E501
"""Grants, scope enforcement and Knowledge PRs. LLM calls use the deterministic stub."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.core.jobs import drain_background
from app.services.federation import FederationService, policy_predicate
from app.services.query_service import QueryService


def _iso(days: float = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


async def _rows(world, cypher, **params):
    async with world.driver.session() as session:
        return await (await session.run(cypher, **params)).data()


async def _node(world, kb_id, node_id, **props):
    body = {"label": node_id, "type": "CONCEPT", "created_at": _iso(), **props}
    async with world.driver.session() as session:
        await session.run("CREATE (n:SpaiderNode {id: $id, agent_id: $kb}) SET n += $p", id=node_id, kb=kb_id, p=body)


async def _edge(world, kb_id, a, b, edge_id, relation="R"):
    async with world.driver.session() as session:
        await session.run(
            """
            MATCH (a:SpaiderNode {id: $a, agent_id: $kb}), (b:SpaiderNode {id: $b, agent_id: $kb})
            CREATE (a)-[:RELATION {id: $id, agent_id: $kb, relation: $rel, recorded_at: $now}]->(b)
            """,
            a=a, b=b, kb=kb_id, id=edge_id, rel=relation, now=_iso(-1),
        )


async def _post(world, path, key, body=None):
    return await world.client.post(f"/api/v1/federation{path}", json=body or {}, headers=world.headers(key))


@pytest.fixture
async def kbs(auth_world):
    return SimpleNamespace(
        source=await auth_world.create_kb("source", clearance_level=5),
        target=await auth_world.create_kb("target", clearance_level=5),
    )


async def _grant(world, kbs, approve=True, **scope):
    created = await _post(world, "/grants", kbs.target["api_key"] if False else world.owner_key, {"source_kb": kbs.source["id"], "target_kb": kbs.target["id"], **scope})
    assert created.status_code == 201, created.text
    grant = created.json()
    if approve:
        approved = await _post(world, f"/grants/{grant['id']}/approve", world.owner_key)
        assert approved.status_code == 200, approved.text
    return grant


# ── Grant lifecycle and authority ─────────────────────────────────────────


async def test_a_request_alone_gives_no_access(auth_world, kbs):
    await _grant(auth_world, kbs, approve=False)
    sources = await FederationService(auth_world.driver).sources_for(kbs.target["id"], 5)
    assert [source.kb for source in sources] == [kbs.target["id"]]


async def test_only_a_steward_of_the_source_can_approve(auth_world, kbs):
    grant = await _grant(auth_world, kbs, approve=False)
    target_steward = await auth_world.member(kbs.target["id"], "steward")
    source_editor = await auth_world.member(kbs.source["id"], "editor")
    for user in (target_steward, source_editor):
        response = await _post(auth_world, f"/grants/{grant['id']}/approve", user["api_key"])
        assert response.status_code == 403
    source_steward = await auth_world.member(kbs.source["id"], "steward", clearance_level=1)
    response = await _post(auth_world, f"/grants/{grant['id']}/approve", source_steward["api_key"])
    assert response.status_code == 200, response.text


async def test_requesting_needs_steward_on_the_target(auth_world, kbs):
    editor = await auth_world.member(kbs.target["id"], "editor")
    response = await _post(auth_world, "/grants", editor["api_key"], {"source_kb": kbs.source["id"], "target_kb": kbs.target["id"]})
    assert response.status_code == 403


async def test_a_steward_cannot_grant_above_their_clearance(auth_world, kbs):
    grant = await _grant(auth_world, kbs, approve=False, max_clearance=4)
    steward = await auth_world.member(kbs.source["id"], "steward", clearance_level=2)
    response = await _post(auth_world, f"/grants/{grant['id']}/approve", steward["api_key"])
    assert response.status_code == 403


async def test_the_approver_can_narrow_the_request(auth_world, kbs):
    grant = await _grant(auth_world, kbs, approve=False, max_clearance=3)
    response = await _post(auth_world, f"/grants/{grant['id']}/approve", auth_world.owner_key, {"max_clearance": 1, "node_types": ["PERSON"]})
    assert response.json()["max_clearance"] == 1
    sources = await FederationService(auth_world.driver).sources_for(kbs.target["id"], 5)
    assert sources[1].node_types == ["PERSON"] and sources[1].max_clearance == 1


async def test_two_decisions_cannot_both_win(auth_world, kbs):
    grant = await _grant(auth_world, kbs, approve=False)
    first, second = await asyncio.gather(
        _post(auth_world, f"/grants/{grant['id']}/approve", auth_world.owner_key),
        _post(auth_world, f"/grants/{grant['id']}/deny", auth_world.owner_key),
    )
    assert sorted((first.status_code, second.status_code)) == [200, 409]


async def test_grants_stay_inside_the_org(auth_world, kbs):
    async with auth_world.driver.session() as session:
        await session.run(
            "CREATE (:SystemAgent {agent_id: 'foreign'})-[:IN_WORKSPACE]->(:Workspace {id: 'w2'})-[:IN_ORG]->(:Org {id: 'o2'})"
        )
    response = await _post(auth_world, "/grants", auth_world.owner_key, {"source_kb": "foreign", "target_kb": kbs.target["id"]})
    assert response.status_code == 404


async def test_a_kb_cannot_grant_to_itself(auth_world, kbs):
    response = await _post(auth_world, "/grants", auth_world.owner_key, {"source_kb": kbs.target["id"], "target_kb": kbs.target["id"]})
    assert response.status_code == 422


async def test_an_expired_grant_stops_working(auth_world, kbs):
    await _grant(auth_world, kbs, expires_at=_iso(-1))
    sources = await FederationService(auth_world.driver).sources_for(kbs.target["id"], 5)
    assert len(sources) == 1


async def test_clearance_is_the_lowest_of_caller_and_grant(auth_world, kbs):
    await _grant(auth_world, kbs, max_clearance=2)
    service = FederationService(auth_world.driver)
    assert (await service.sources_for(kbs.target["id"], 5))[1].max_clearance == 2
    assert (await service.sources_for(kbs.target["id"], 1))[1].max_clearance == 1


# ── Scope enforced in Cypher ──────────────────────────────────────────────


async def _visible(world, kbs, clearance=5):
    from app.services.graph_service import get_graph_service

    service = QueryService(get_graph_service())
    context, _ = await service.retrieve_swarm_context(kbs.target["id"], "alpha beta gamma delta", agent_clearance=clearance)
    return context


@pytest.fixture
async def shared(auth_world, kbs):
    await _node(auth_world, kbs.source["id"], "p", label="alpha person", type="PERSON")
    await _node(auth_world, kbs.source["id"], "c", label="beta concept", type="CONCEPT")
    await _node(auth_world, kbs.source["id"], "s", label="gamma secret", type="PERSON", clearance_level=4)
    await _node(auth_world, kbs.target["id"], "own", label="delta own", type="CONCEPT")
    return kbs


async def _search_ready(world):
    async with world.driver.session() as session:
        await session.run("CALL db.awaitIndexes(30)")
        await session.run("CALL db.index.fulltext.awaitEventuallyConsistentIndexRefresh()")


async def test_grant_lifecycle_controls_what_retrieval_returns(auth_world, shared):
    """request, approve, federated query sees in-scope only, revoke, excluded."""
    await _search_ready(auth_world)
    assert "alpha person" not in await _visible(auth_world, shared)
    grant = await _grant(auth_world, shared, node_types=["PERSON"], max_clearance=1)
    await _search_ready(auth_world)
    context = await _visible(auth_world, shared)
    assert "alpha person" in context and "delta own" in context
    assert "beta concept" not in context and "gamma secret" not in context
    assert "[Source: source]" in context

    response = await auth_world.client.delete(f"/api/v1/federation/grants/{grant['id']}", headers=auth_world.headers(auth_world.owner_key))
    assert response.status_code == 200, response.text
    context = await _visible(auth_world, shared)
    assert "alpha person" not in context and "delta own" in context


async def test_relationship_lines_respect_scope(auth_world, kbs):
    from app.services.graph_service import get_graph_service

    await _node(auth_world, kbs.source["id"], "a", label="A1", type="PERSON")
    await _node(auth_world, kbs.source["id"], "b", label="B1", type="PERSON")
    await _edge(auth_world, kbs.source["id"], "a", "b", "e1", "ALLOWED")
    await _edge(auth_world, kbs.source["id"], "b", "a", "e2", "HIDDEN")
    service = QueryService(get_graph_service())
    scope = [{"kb": kbs.source["id"], "max_clearance": 1, "node_types": None, "relation_types": ["ALLOWED"]}]
    lines = await service._fetch_relationship_lines(["a", "b"], [kbs.source["id"]], 1, fed_sources=scope)
    assert lines == ["A1 -[ALLOWED]-> B1"]


def test_policy_predicate_names_every_limit():
    predicate = policy_predicate("n")
    assert "s.kb = n.agent_id" in predicate and "s.max_clearance" in predicate and "s.node_types" in predicate


async def test_federated_answer_cites_and_names_the_source(auth_world, shared, openai_stub):
    await _grant(auth_world, shared, node_types=["PERSON"], max_clearance=1)
    await _search_ready(auth_world)
    response = await auth_world.client.post(
        "/api/v1/query", json={"question": "Who is the alpha person?", "agent_id": shared.target["id"]},
        headers=auth_world.headers(auth_world.owner_key),
    )
    assert response.status_code == 200, response.text
    await drain_background()
    body = response.json()
    assert any(item["agent_id"] == shared.source["id"] for item in body["citations"])
    prompts = " ".join(message.get("content") or "" for call in openai_stub.chat_calls for message in call)
    assert "[Source: source]" in prompts and "beta concept" not in prompts and "gamma secret" not in prompts


async def test_reading_through_a_grant_leaves_the_source_unchanged(auth_world, shared, openai_stub):
    await _grant(auth_world, shared, node_types=["PERSON"], max_clearance=1)
    await _search_ready(auth_world)
    await auth_world.client.post(
        "/api/v1/query", json={"question": "Who is the alpha person?", "agent_id": shared.target["id"]},
        headers=auth_world.headers(auth_world.owner_key),
    )
    await drain_background()
    rows = await _rows(auth_world, "MATCH (n:SpaiderNode {id: 'p'}) RETURN n.retrieval_count AS c")
    assert rows == [{"c": None}]


async def test_revoking_retires_cached_answers(auth_world, kbs):
    from app.core.kv import cache_generation

    grant = await _grant(auth_world, kbs)
    before = await cache_generation(kbs.target["id"])
    await auth_world.client.delete(f"/api/v1/federation/grants/{grant['id']}", headers=auth_world.headers(auth_world.owner_key))
    assert await cache_generation(kbs.target["id"]) > before


async def test_deleting_a_kb_removes_its_grants(auth_world, kbs, monkeypatch):
    from app.services import purge_service

    monkeypatch.setattr(purge_service, "SETTLE_SECONDS", 0)
    await _grant(auth_world, kbs)
    response = await auth_world.client.delete(f"/api/v1/agents/{kbs.source['id']}", headers=auth_world.headers(auth_world.owner_key))
    assert response.status_code == 200
    assert await _rows(auth_world, "MATCH (g:FederationGrant) RETURN g") == []


async def test_old_sharing_routes_are_gone(auth_world):
    for method, path in (("post", "/api/v1/swarm/connections"), ("post", "/api/v1/agents/connect"), ("get", "/api/v1/agents/links")):
        response = await getattr(auth_world.client, method)(path, headers=auth_world.headers(auth_world.owner_key))
        assert response.status_code in (404, 405, 422), path
        if method == "get":
            assert response.status_code != 200


# ── Knowledge PRs ─────────────────────────────────────────────────────────


async def _snapshot(world, kb_id):
    nodes = await _rows(world, "MATCH (n:SpaiderNode {agent_id: $kb}) RETURN n.id AS id, properties(n) AS p ORDER BY id", kb=kb_id)
    edges = await _rows(world, "MATCH ()-[r:RELATION {agent_id: $kb}]->() RETURN r.id AS id, properties(r) AS p ORDER BY id", kb=kb_id)
    return nodes, edges


async def _kpr(world, kbs, node_ids, key=None, title="merge"):
    response = await _post(world, "/kpr", key or world.owner_key, {"source_kb": kbs.source["id"], "target_kb": kbs.target["id"], "node_ids": node_ids, "title": title})
    assert response.status_code == 201, response.text
    return response.json()


async def _apply(world, kpr, key=None):
    response = await _post(world, f"/kpr/{kpr['id']}/approve", key or world.owner_key)
    assert response.status_code == 200, response.text
    return response.json()


async def _revert(world, kpr):
    response = await _post(world, f"/kpr/{kpr['id']}/revert", world.owner_key)
    assert response.status_code == 200, response.text
    return response.json()


async def _set(world, kb_id, node_id, **props):
    async with world.driver.session() as session:
        await session.run("MATCH (n:SpaiderNode {id: $id, agent_id: $kb}) SET n += $p", id=node_id, kb=kb_id, p=props)


async def test_diff_shows_adds_and_updates(auth_world, kbs):
    await _node(auth_world, kbs.source["id"], "new1", description="fresh")
    await _node(auth_world, kbs.source["id"], "shared", description="better")
    await _node(auth_world, kbs.target["id"], "shared", description="old")
    kpr = await _kpr(auth_world, kbs, ["new1", "shared"])
    actions = {item["target_id"]: item for item in kpr["diff"]["nodes"]}
    assert actions["new1"]["action"] == "add"
    assert actions["shared"]["changes"]["description"] == {"before": "old", "after": "better"}
    assert await _rows(auth_world, "MATCH (n:SpaiderNode {id: 'new1', agent_id: $kb}) RETURN n", kb=kbs.target["id"]) == []


async def test_a_matching_label_merges_into_the_existing_node(auth_world, kbs):
    await _node(auth_world, kbs.source["id"], "src-id", label="Acme", description="better")
    await _node(auth_world, kbs.target["id"], "tgt-id", label="Acme", description="old")
    kpr = await _kpr(auth_world, kbs, ["src-id"])
    assert kpr["diff"]["nodes"][0]["target_id"] == "tgt-id"


async def test_apply_then_revert_restores_the_kb_exactly(auth_world, kbs):
    await _node(auth_world, kbs.source["id"], "a", description="from source")
    await _node(auth_world, kbs.source["id"], "b")
    await _edge(auth_world, kbs.source["id"], "a", "b", "e1")
    await _node(auth_world, kbs.source["id"], "shared", description="better", clearance_level=2)
    await _node(auth_world, kbs.target["id"], "shared", description="old")
    await _node(auth_world, kbs.target["id"], "untouched")
    before = await _snapshot(auth_world, kbs.target["id"])

    kpr = await _kpr(auth_world, kbs, ["a", "b", "shared"])
    await _apply(auth_world, kpr)
    nodes, edges = await _snapshot(auth_world, kbs.target["id"])
    assert len(nodes) == 4 and len(edges) == 1

    await _revert(auth_world, kpr)
    assert await _snapshot(auth_world, kbs.target["id"]) == before


async def test_reverting_the_older_of_two_on_one_field_keeps_the_newer(auth_world, kbs):
    await _node(auth_world, kbs.target["id"], "n", description="original")
    await _node(auth_world, kbs.source["id"], "n", description="first")
    original = await _snapshot(auth_world, kbs.target["id"])
    first = await _kpr(auth_world, kbs, ["n"])
    await _apply(auth_world, first)
    await _set(auth_world, kbs.source["id"], "n", description="second")
    second = await _kpr(auth_world, kbs, ["n"])
    await _apply(auth_world, second)

    await _revert(auth_world, first)
    rows = await _rows(auth_world, "MATCH (n:SpaiderNode {id: 'n', agent_id: $kb}) RETURN n.description AS d", kb=kbs.target["id"])
    assert rows == [{"d": "second"}]

    await _revert(auth_world, second)
    assert await _snapshot(auth_world, kbs.target["id"]) == original


async def test_reverting_in_either_order_ends_at_the_original(auth_world, kbs):
    await _node(auth_world, kbs.target["id"], "n", description="original")
    await _node(auth_world, kbs.source["id"], "n", description="first", source_text="kept")
    original = await _snapshot(auth_world, kbs.target["id"])
    first = await _kpr(auth_world, kbs, ["n"])
    await _apply(auth_world, first)
    await _set(auth_world, kbs.source["id"], "n", description="second")
    second = await _kpr(auth_world, kbs, ["n"])
    await _apply(auth_world, second)
    await _revert(auth_world, second)
    rows = await _rows(auth_world, "MATCH (n:SpaiderNode {id: 'n', agent_id: $kb}) RETURN n.description AS d, n.source_text AS s", kb=kbs.target["id"])
    assert rows == [{"d": "first", "s": "kept"}]
    await _revert(auth_world, first)
    assert await _snapshot(auth_world, kbs.target["id"]) == original


async def test_a_different_field_changed_later_is_left_alone(auth_world, kbs):
    await _node(auth_world, kbs.target["id"], "n", description="original", source_text="orig text")
    await _node(auth_world, kbs.source["id"], "n", description="first", source_text="orig text")
    first = await _kpr(auth_world, kbs, ["n"])
    await _apply(auth_world, first)
    await _set(auth_world, kbs.source["id"], "n", description="first", source_text="new text")
    second = await _kpr(auth_world, kbs, ["n"])
    await _apply(auth_world, second)
    await _revert(auth_world, first)
    rows = await _rows(auth_world, "MATCH (n:SpaiderNode {id: 'n', agent_id: $kb}) RETURN n.description AS d, n.source_text AS s", kb=kbs.target["id"])
    assert rows == [{"d": "original", "s": "new text"}]


async def test_a_node_a_later_kpr_relies_on_survives_until_that_kpr_goes(auth_world, kbs):
    await _node(auth_world, kbs.target["id"], "base")
    original = await _snapshot(auth_world, kbs.target["id"])
    await _node(auth_world, kbs.source["id"], "n", description="first")
    first = await _kpr(auth_world, kbs, ["n"])
    await _apply(auth_world, first)
    await _set(auth_world, kbs.source["id"], "n", description="second")
    second = await _kpr(auth_world, kbs, ["n"])
    await _apply(auth_world, second)

    await _revert(auth_world, first)
    rows = await _rows(auth_world, "MATCH (n:SpaiderNode {id: 'n', agent_id: $kb}) RETURN n.description AS d", kb=kbs.target["id"])
    assert rows == [{"d": "second"}]

    await _revert(auth_world, second)
    assert await _snapshot(auth_world, kbs.target["id"]) == original


async def test_a_shared_edge_survives_reverting_its_creator(auth_world, kbs):
    await _node(auth_world, kbs.source["id"], "a")
    await _node(auth_world, kbs.source["id"], "b")
    await _edge(auth_world, kbs.source["id"], "a", "b", "e1")
    original = await _snapshot(auth_world, kbs.target["id"])
    first = await _kpr(auth_world, kbs, ["a", "b"])
    await _apply(auth_world, first)
    second = await _kpr(auth_world, kbs, ["a", "b"])
    await _apply(auth_world, second)
    await _revert(auth_world, first)
    _, edges = await _snapshot(auth_world, kbs.target["id"])
    assert len(edges) == 1
    await _revert(auth_world, second)
    assert await _snapshot(auth_world, kbs.target["id"]) == original


async def test_a_manual_edit_is_not_overwritten_by_revert(auth_world, kbs):
    await _node(auth_world, kbs.target["id"], "n", description="original")
    await _node(auth_world, kbs.source["id"], "n", description="first")
    kpr = await _kpr(auth_world, kbs, ["n"])
    await _apply(auth_world, kpr)
    await _set(auth_world, kbs.target["id"], "n", description="hand edited")
    result = await _revert(auth_world, kpr)
    assert result["skipped_fields"] == ["n.description"]
    rows = await _rows(auth_world, "MATCH (n:SpaiderNode {id: 'n', agent_id: $kb}) RETURN n.description AS d", kb=kbs.target["id"])
    assert rows == [{"d": "hand edited"}]


async def test_a_stale_diff_is_refused(auth_world, kbs):
    await _node(auth_world, kbs.target["id"], "n", description="original")
    await _node(auth_world, kbs.source["id"], "n", description="proposed")
    kpr = await _kpr(auth_world, kbs, ["n"])
    await _set(auth_world, kbs.target["id"], "n", description="changed meanwhile")
    response = await _post(auth_world, f"/kpr/{kpr['id']}/approve", auth_world.owner_key)
    assert response.status_code == 409


async def test_applying_never_lowers_a_nodes_clearance(auth_world, kbs):
    await _node(auth_world, kbs.target["id"], "n", clearance_level=4, description="old")
    await _node(auth_world, kbs.source["id"], "n", clearance_level=1, description="new")
    kpr = await _kpr(auth_world, kbs, ["n"])
    await _apply(auth_world, kpr)
    rows = await _rows(auth_world, "MATCH (n:SpaiderNode {id: 'n', agent_id: $kb}) RETURN n.clearance_level AS c", kb=kbs.target["id"])
    assert rows == [{"c": 4}]


async def test_an_author_cannot_approve_their_own_proposal(auth_world, kbs):
    await _node(auth_world, kbs.source["id"], "n")
    steward = await auth_world.member(kbs.target["id"], "steward")
    await auth_world.identity.grant_role(auth_world.org_id, kbs.source["id"], steward["id"], "reader")
    kpr = await _kpr(auth_world, kbs, ["n"], key=steward["api_key"])
    response = await _post(auth_world, f"/kpr/{kpr['id']}/approve", steward["api_key"])
    assert response.status_code == 403


async def test_kpr_permissions(auth_world, kbs):
    await _node(auth_world, kbs.source["id"], "n")
    outsider = await auth_world.member(kbs.target["id"], "contributor")
    response = await _post(auth_world, "/kpr", outsider["api_key"], {"source_kb": kbs.source["id"], "target_kb": kbs.target["id"], "node_ids": ["n"], "title": "t"})
    assert response.status_code == 403

    kpr = await _kpr(auth_world, kbs, ["n"])
    editor = await auth_world.member(kbs.target["id"], "editor")
    assert (await _post(auth_world, f"/kpr/{kpr['id']}/approve", editor["api_key"])).status_code == 403


async def test_a_diff_hides_above_the_authors_clearance(auth_world, kbs):
    await _node(auth_world, kbs.source["id"], "open")
    await _node(auth_world, kbs.source["id"], "secret", clearance_level=4)
    author = await auth_world.member(kbs.target["id"], "contributor", clearance_level=1)
    await auth_world.identity.grant_role(auth_world.org_id, kbs.source["id"], author["id"], "reader")
    kpr = await _kpr(auth_world, kbs, ["open", "secret"], key=author["api_key"])
    assert [item["source_id"] for item in kpr["diff"]["nodes"]] == ["open"]


async def test_viewing_a_diff_needs_its_clearance(auth_world, kbs):
    await _node(auth_world, kbs.source["id"], "secret", clearance_level=4)
    kpr = await _kpr(auth_world, kbs, ["secret"])
    reader = await auth_world.member(kbs.target["id"], "reader", clearance_level=1)
    response = await auth_world.client.get(f"/api/v1/federation/kpr/{kpr['id']}", headers=auth_world.headers(reader["api_key"]))
    assert response.status_code == 403


async def test_a_kpr_is_applied_once_and_reverted_once(auth_world, kbs):
    await _node(auth_world, kbs.source["id"], "n")
    kpr = await _kpr(auth_world, kbs, ["n"])
    await _apply(auth_world, kpr)
    assert (await _post(auth_world, f"/kpr/{kpr['id']}/approve", auth_world.owner_key)).status_code == 409
    await _revert(auth_world, kpr)
    assert (await _post(auth_world, f"/kpr/{kpr['id']}/revert", auth_world.owner_key)).status_code == 409


async def test_concurrent_approvals_apply_once(auth_world, kbs):
    await _node(auth_world, kbs.source["id"], "n")
    kpr = await _kpr(auth_world, kbs, ["n"])
    first, second = await asyncio.gather(
        _post(auth_world, f"/kpr/{kpr['id']}/approve", auth_world.owner_key),
        _post(auth_world, f"/kpr/{kpr['id']}/approve", auth_world.owner_key),
    )
    assert sorted((first.status_code, second.status_code)) == [200, 409]
    rows = await _rows(auth_world, "MATCH (n:SpaiderNode {id: 'n', agent_id: $kb}) RETURN n.id", kb=kbs.target["id"])
    assert len(rows) == 1
