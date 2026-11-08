"""Node ids per KB, clearance on every read path, erasure, tickets."""
from __future__ import annotations

import json

import pytest

from app.config import settings
from app.core.jobs import drain_background
from app.core.kv import cache_generation, get_kv
from app.services import purge_service
from app.services.graph_service import get_graph_service
from app.services.query_service import QueryService

_COUNT_NODES = "MATCH (n:SpaiderNode {agent_id: $kb}) RETURN count(n) AS c"


async def _seed(world, kb_id, node_id="n1", clearance=1, label="L"):
    async with world.driver.session() as session:
        await session.run(
            """
            CREATE (:SpaiderNode {id: $id, agent_id: $kb, label: $label, type: 'CONCEPT',
                                  clearance_level: $clearance, created_at: '2026-01-01T00:00:00+00:00'})
            """,
            id=node_id, kb=kb_id, clearance=clearance, label=label,
        )


async def _link(world, kb_id, source, target, edge_id="e1"):
    async with world.driver.session() as session:
        await session.run(
            """
            MATCH (a:SpaiderNode {id: $source, agent_id: $kb}), (b:SpaiderNode {id: $target, agent_id: $kb})
            CREATE (a)-[:RELATION {id: $edge, agent_id: $kb, relation: 'R',
                                   recorded_at: '2026-01-01T00:00:00+00:00'}]->(b)
            """,
            source=source, target=target, kb=kb_id, edge=edge_id,
        )


async def _low_reader(world, kb_id):
    user = await world.member(kb_id, "reader", clearance_level=1)
    return world.headers(user["api_key"])


# ── Node ids are per KB ───────────────────────────────────────────────────


async def test_same_id_in_two_kbs_stays_two_nodes(auth_world):
    first, second = await auth_world.create_kb(), await auth_world.create_kb()
    graph = get_graph_service()
    from app.models.schemas import GraphPayload, Node

    for kb, text in ((first, "from first"), (second, "from second")):
        payload = GraphPayload(nodes=[Node(id="shared", label=text, type="CONCEPT", agent_id=kb["id"])])
        await graph.write_graph(payload, kb["id"])

    assert (await graph.get_node_by_id("shared", first["id"])).label == "from first"
    assert (await graph.get_node_by_id("shared", second["id"])).label == "from second"


async def test_deleting_a_node_leaves_the_other_kbs_node_alone(auth_world):
    first, second = await auth_world.create_kb(), await auth_world.create_kb()
    await _seed(auth_world, first["id"], "shared")
    await _seed(auth_world, second["id"], "shared")
    response = await auth_world.client.delete(
        f"/api/v1/node/shared?agent_id={first['id']}", headers=auth_world.headers(first["api_key"])
    )
    assert response.status_code == 200, response.text
    graph = get_graph_service()
    assert await graph.get_node_by_id("shared", first["id"]) is None
    assert await graph.get_node_by_id("shared", second["id"]) is not None


# ── Clearance ─────────────────────────────────────────────────────────────


@pytest.fixture
async def secret_kb(auth_world):
    """A KB where n1 (public) connects to n3 (public) only through n2 (secret)."""
    kb = await auth_world.create_kb(clearance_level=5)
    await _seed(auth_world, kb["id"], "n1", 1, "public-one")
    await _seed(auth_world, kb["id"], "n2", 4, "secret-two")
    await _seed(auth_world, kb["id"], "n3", 1, "public-three")
    await _link(auth_world, kb["id"], "n1", "n2", "e1")
    await _link(auth_world, kb["id"], "n2", "n3", "e2")
    return kb


async def test_graph_views_hide_nodes_above_clearance(auth_world, secret_kb):
    headers = await _low_reader(auth_world, secret_kb["id"])
    kb = secret_kb["id"]
    body = (await auth_world.client.get(f"/api/v1/graph?agent_id={kb}", headers=headers)).json()
    assert "secret-two" not in json.dumps(body)
    stats = (await auth_world.client.get(f"/api/v1/graph/stats?agent_id={kb}", headers=headers)).json()
    assert stats["node_count"] == 2 and stats["edge_count"] == 0
    clusters = await auth_world.client.get(f"/api/v1/graph/clusters?agent_id={kb}", headers=headers)
    assert clusters.status_code == 200
    assert sum(c["node_count"] for c in clusters.json()["clusters"]) == 2


async def test_a_higher_clearance_reader_sees_the_node(auth_world, secret_kb):
    key = await auth_world.member(secret_kb["id"], "reader", clearance_level=4)
    body = await auth_world.client.get(
        f"/api/v1/graph?agent_id={secret_kb['id']}", headers=auth_world.headers(key["api_key"])
    )
    assert "secret-two" in json.dumps(body.json())


async def test_traverse_cannot_walk_through_a_hidden_node(auth_world, secret_kb):
    headers = await _low_reader(auth_world, secret_kb["id"])
    response = await auth_world.client.post(
        "/api/v1/query/traverse",
        json={"start_node_id": "n1", "depth": 3, "agent_id": secret_kb["id"]},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert "public-three" not in response.text and "secret-two" not in response.text


async def test_subgraph_drops_paths_through_hidden_nodes(auth_world, secret_kb):
    payload = await get_graph_service().get_subgraph("n1", depth=2, agent_id=secret_kb["id"], max_clearance=1)
    assert {node.id for node in payload.nodes} == {"n1"}


async def test_export_hides_nodes_above_clearance(auth_world, secret_kb):
    key = await auth_world.member(secret_kb["id"], "steward", clearance_level=1)
    response = await auth_world.client.get(
        f"/api/v1/agents/{secret_kb['id']}/export", headers=auth_world.headers(key["api_key"])
    )
    assert response.status_code == 200, response.text
    assert "public-one" in response.text and "secret-two" not in response.text


async def test_answer_cache_is_separate_per_clearance():
    assert QueryService._cache_key("q", "kb", 1) != QueryService._cache_key("q", "kb", 5)


async def test_answer_cache_retires_with_the_generation():
    assert QueryService._cache_key("q", "kb", 1, 0) != QueryService._cache_key("q", "kb", 1, 1)


async def test_effective_clearance_is_the_lower_of_kb_and_caller(monkeypatch):
    service = QueryService.__new__(QueryService)

    async def kb_level(agent_id):
        return 3

    monkeypatch.setattr(service, "_get_agent_clearance", kb_level)
    assert await service._effective_clearance("kb", 5) == 3
    assert await service._effective_clearance("kb", 1) == 1
    assert await service._effective_clearance("kb", None) == 3


# ── Datasets ──────────────────────────────────────────────────────────────


async def _dataset(tmp_path, monkeypatch, kb_id, clearance):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    folder = purge_service.dataset_root() / kb_id
    folder.mkdir(parents=True)
    (folder / "20260101.jsonl").write_text("{}", encoding="utf-8")
    if clearance is not None:
        (folder / "20260101.meta.json").write_text(json.dumps({"clearance": clearance}), encoding="utf-8")


async def test_dataset_download_checks_generation_clearance(auth_world, tmp_path, monkeypatch):
    kb = await auth_world.create_kb()
    await _dataset(tmp_path, monkeypatch, kb["id"], 4)
    url = "/api/v1/synthesize/download/20260101"
    low = await auth_world.member(kb["id"], "reader", clearance_level=1)
    high = await auth_world.member(kb["id"], "reader", clearance_level=4)
    assert (await auth_world.client.get(url, headers=auth_world.headers(low["api_key"]))).status_code == 404
    assert (await auth_world.client.get(url, headers=auth_world.headers(high["api_key"]))).status_code == 200


async def test_dataset_without_a_record_counts_as_top_secret(auth_world, tmp_path, monkeypatch):
    kb = await auth_world.create_kb()
    await _dataset(tmp_path, monkeypatch, kb["id"], None)
    key = await auth_world.member(kb["id"], "reader", clearance_level=4)
    response = await auth_world.client.get(
        "/api/v1/synthesize/download/20260101", headers=auth_world.headers(key["api_key"])
    )
    assert response.status_code == 404


# ── Erasure ───────────────────────────────────────────────────────────────


async def test_deleting_a_kb_erases_everything_and_leaves_one_tombstone(auth_world, tmp_path, monkeypatch):
    monkeypatch.setattr(purge_service, "SETTLE_SECONDS", 0)
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    kb, other = await auth_world.create_kb(), await auth_world.create_kb()
    await _seed(auth_world, kb["id"])
    await _seed(auth_world, other["id"])
    async with auth_world.driver.session() as session:
        for label in ("Episode", "InteractionNode", "NodeVersion", "AuditEvent"):
            await session.run(f"CREATE (:{label} {{id: 'x', agent_id: $kb}})", kb=kb["id"])
    folder = purge_service.dataset_root() / kb["id"]
    folder.mkdir(parents=True)
    (folder / "d.jsonl").write_text("{}", encoding="utf-8")

    response = await auth_world.client.delete(
        f"/api/v1/agents/{kb['id']}", headers=auth_world.headers(auth_world.owner_key)
    )
    assert response.status_code == 200, response.text

    async with auth_world.driver.session() as session:
        rows = await (
            await session.run("MATCH (n {agent_id: $kb}) RETURN labels(n) AS l, n.event_type AS e", kb=kb["id"])
        ).data()
        survivor = await (await session.run(_COUNT_NODES, kb=other["id"])).single()
    assert rows == [{"l": ["AuditEvent"], "e": "kb_purged"}]
    assert survivor["c"] == 1
    assert not folder.exists()


async def test_a_write_landing_during_the_purge_is_removed(auth_world, monkeypatch):
    kb = await auth_world.create_kb()
    await _seed(auth_world, kb["id"], "before")

    async def late_write(_seconds):
        await _seed(auth_world, kb["id"], "in-flight")

    monkeypatch.setattr(purge_service.asyncio, "sleep", late_write)
    await purge_service.purge_kb(get_graph_service(), kb["id"])
    async with auth_world.driver.session() as session:
        record = await (await session.run(_COUNT_NODES, kb=kb["id"])).single()
    assert record["c"] == 0


async def test_purge_retires_cached_answers(auth_world, monkeypatch):
    monkeypatch.setattr(purge_service, "SETTLE_SECONDS", 0)
    kb = await auth_world.create_kb()
    before = await cache_generation(kb["id"])
    await purge_service.purge_kb(get_graph_service(), kb["id"])
    assert await cache_generation(kb["id"]) == before + 1


# ── Tickets and the live feed ─────────────────────────────────────────────


async def _ticket(world, key):
    response = await world.client.post("/api/v1/auth/ticket", headers=world.headers(key))
    assert response.status_code == 201, response.text
    return response.json()["ticket"]


async def test_ticket_holds_no_key_and_works_once(auth_world):
    from app.core.tickets import redeem_ticket

    kb = await auth_world.create_kb()
    ticket = await _ticket(auth_world, kb["api_key"])
    assert kb["api_key"] not in ticket
    assert (await redeem_ticket(ticket)) is not None
    assert (await redeem_ticket(ticket)) is None


async def test_ticket_expires_and_is_stored_hashed(auth_world):
    kb = await auth_world.create_kb()
    ticket = await _ticket(auth_world, kb["api_key"])
    keys = [k async for k in get_kv().scan_iter("spaider:ticket:*")]
    assert keys and ticket not in keys[0]
    assert 0 < await get_kv().ttl(keys[0]) <= 30


async def test_ticket_of_a_disabled_principal_is_refused(auth_world):
    from app.core.tickets import redeem_ticket

    kb = await auth_world.create_kb()
    ticket = await _ticket(auth_world, kb["api_key"])
    async with auth_world.driver.session() as session:
        await session.run("MATCH (p:Principal)<-[:AUTHENTICATES]-(:ApiKey) SET p.disabled = true")
    assert await redeem_ticket(ticket) is None


async def test_event_stream_rejects_raw_api_keys(auth_world):
    kb = await auth_world.create_kb()
    response = await auth_world.client.get(
        f"/api/v1/swarm/events/stream?token={kb['api_key']}", headers=auth_world.headers(kb["api_key"])
    )
    assert response.status_code == 403


def _principal(role):
    from app.core.authz import Principal

    return Principal(id="p", kind="user", org_id="o", org_role=role)


@pytest.mark.parametrize(
    ("event", "role", "visible"),
    [
        ({"kb_id": "mine"}, "member", True),
        ({"kb_id": "theirs"}, "member", False),
        ({"type": "lock"}, "member", False),
        ({"type": "lock"}, "owner", True),
    ],
)
def test_feed_filter(event, role, visible):
    from app.api.v1.swarm import _event_visible

    assert _event_visible(json.dumps(event), _principal(role), {"mine"}) is visible


def test_feed_filter_refuses_unparsable_events():
    from app.api.v1.swarm import _event_visible

    assert _event_visible("not json", _principal("member"), {"mine"}) is False


async def test_background_tasks_drain():
    await drain_background()
