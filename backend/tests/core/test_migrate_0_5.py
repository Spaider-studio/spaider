# ruff: noqa: E501
"""The upgrade script against data shaped like a 0.4 installation."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import fakeredis.aioredis
import pytest

from app.core.security import hash_api_key

_path = Path(__file__).resolve().parents[2] / "scripts" / "migrate_0_5.py"
_spec = importlib.util.spec_from_file_location("migrate_0_5", _path)
migrate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migrate)


@pytest.fixture
async def world(auth_world):
    """Starts empty of identity, like a 0.4 database."""
    async with auth_world.driver.session() as session:
        await session.run("MATCH (n) WHERE n:Org OR n:Workspace OR n:Principal OR n:ApiKey OR n:BootstrapLock DETACH DELETE n")
    return auth_world


@pytest.fixture
async def redis():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


LEGACY_KEY = "sk-legacy-key-value"


async def _seed(world, redis):
    async with world.driver.session() as session:
        await session.run(
            """
            CREATE (:SystemAgent {agent_id: 'kb1', name: 'Kept name'})
            CREATE (:SystemAgent {agent_id: 'kb2', name: 'Two'})-[:SHARES_KNOWLEDGE_WITH]->(:SystemAgent {agent_id: 'kb3', name: 'Three'})
            CREATE (a:SpaiderNode {id: 'a', agent_id: 'kb1', label: 'A', type: 'X', created_at: '2026-01-01T00:00:00+00:00', properties: '{"aliases": ["Alpha", "Ay"]}'})
            CREATE (b:SpaiderNode {id: 'b', agent_id: 'kb1', label: 'B', type: 'X', created_at: 1767225600000})
            CREATE (a)-[:RELATION {id: 'e1', agent_id: 'kb1', relation: 'R', created_at: '2026-01-02T00:00:00+00:00'}]->(b)
            CREATE (a)-[:RELATION {id: 'e2', agent_id: 'kb1', relation: 'OLD', created_at: '2026-01-03T00:00:00+00:00', superseded: true, updated_at: '2026-02-01T00:00:00+00:00'}]->(b)
            CREATE (b)-[:RELATED_TO {id: 'r1', agent_id: 'kb1', relation: 'FUSED', created_at: '2026-01-04T00:00:00+00:00'}]->(a)
            CREATE (:SpaiderNode {id: 'f', agent_id: 'kb1', label: 'F', type: 'FACT', created_at: '2026-01-05T00:00:00+00:00', superseded: true, updated_at: '2026-03-01T00:00:00+00:00'})
            CREATE (:InteractionNode {id: 'i1', agent_id: 'kb1', question: 'Q?', answer_summary: 'A.', session_id: 's', timestamp: datetime('2026-01-06T00:00:00Z')})
            """
        )
    await redis.set("spaider:agent:kb1", json.dumps({"id": "kb1", "name": "Overwritten?", "clearance_level": 3}))
    await redis.set("spaider:agent:only-redis", json.dumps({"id": "only-redis", "name": "Redis Only", "clearance_level": 2}))
    await redis.set(f"spaider:apikey:{hash_api_key(LEGACY_KEY)}", json.dumps({"agent_id": "kb1", "permissions": ["read", "write"], "created_at": "2026-01-01T00:00:00+00:00"}))
    await redis.set("spaider:apikey:orphan", json.dumps({"agent_id": "gone", "permissions": ["read"]}))
    await redis.set("spaider:swarm:connection:c1", json.dumps({"id": "c1", "source_agent_id": "kb1", "target_agent_id": "kb2", "allowed_node_types": ["PERSON"], "expires_at": None}))


async def _rows(world, cypher, **params):
    async with world.driver.session() as session:
        return await (await session.run(cypher, **params)).data()


async def _snapshot(world):
    nodes = await _rows(world, "MATCH (n) WHERE NOT n:SpaiderTestMarker RETURN labels(n) AS l, properties(n) AS p")
    edges = await _rows(world, "MATCH (a)-[r]->(b) RETURN type(r) AS t, properties(r) AS p")
    return sorted(nodes, key=lambda row: json.dumps(row, default=str)), sorted(edges, key=lambda row: json.dumps(row, default=str))


async def _run(world, redis, tmp_path, apply=True):
    tool = migrate.Migration(world.driver, redis, apply=apply, owner_key_file=tmp_path / "owner.txt")
    return await tool.run()


async def test_dry_run_changes_nothing(world, redis, tmp_path):
    await _seed(world, redis)
    before = await _snapshot(world)
    report = await _run(world, redis, tmp_path, apply=False)
    assert report["mode"] == "dry-run" and await _snapshot(world) == before
    assert report["steps"]["related_to_edges"]["pending"] == 1
    assert report["steps"]["api_keys"]["to_create"] == 1
    assert report["steps"]["api_keys"]["without_a_kb"] == 1
    assert not (tmp_path / "owner.txt").exists()


async def test_running_twice_changes_nothing_more(world, redis, tmp_path):
    await _seed(world, redis)
    await _run(world, redis, tmp_path)
    first = await _snapshot(world)
    report = await _run(world, redis, tmp_path)
    second = await _snapshot(world)

    def stable(snapshot):
        text = json.dumps(snapshot, default=str)
        return text

    assert len(first[0]) == len(second[0]) and len(first[1]) == len(second[1])
    assert stable(first) == stable(second)
    assert all(step.get("changed", 0) == 0 for step in report["steps"].values())


async def test_an_edit_made_after_migrating_survives_a_rerun(world, redis, tmp_path):
    await _seed(world, redis)
    await _run(world, redis, tmp_path)
    async with world.driver.session() as session:
        await session.run("MATCH (a:SystemAgent {agent_id: 'kb1'}) SET a.name = 'Renamed later', a.clearance_level = 5")
        await session.run("MATCH ()-[r:RELATION {id: 'e1'}]->() SET r.utility_weight = 1.7, r.valid_from = '2020-01-01T00:00:00+00:00'")
        await session.run("MATCH (n:SpaiderNode {id: 'a'}) SET n.energy_level = 0.3, n.aliases_text = 'edited'")
        await session.run("MATCH (:Principal)-[r:HAS_ROLE]->(:SystemAgent {agent_id: 'kb1'}) SET r.role = 'reader'")
        await session.run("MATCH (g:FederationGrant) SET g.status = 'revoked'")
    await _run(world, redis, tmp_path)
    agent = (await _rows(world, "MATCH (a:SystemAgent {agent_id: 'kb1'}) RETURN a.name AS n, a.clearance_level AS c"))[0]
    edge = (await _rows(world, "MATCH ()-[r:RELATION {id: 'e1'}]->() RETURN r.utility_weight AS w, r.valid_from AS v"))[0]
    node = (await _rows(world, "MATCH (n:SpaiderNode {id: 'a'}) RETURN n.energy_level AS e, n.aliases_text AS t"))[0]
    assert agent == {"n": "Renamed later", "c": 5}
    assert edge == {"w": 1.7, "v": "2020-01-01T00:00:00+00:00"}
    assert node == {"e": 0.3, "t": "edited"}
    assert await _rows(world, "MATCH (:Principal)-[r:HAS_ROLE]->(:SystemAgent {agent_id: 'kb1'}) RETURN r.role AS r") == [{"r": "reader"}]
    assert {row["s"] for row in await _rows(world, "MATCH (g:FederationGrant) RETURN g.status AS s")} == {"revoked"}


async def test_existing_keys_keep_working(world, redis, tmp_path):
    from app.services.identity_service import get_identity_service

    await _seed(world, redis)
    await _run(world, redis, tmp_path)
    principal = await get_identity_service().resolve_key(LEGACY_KEY)
    assert principal.default_kb == "kb1" and principal.kind == "service_account"
    rows = await _rows(world, "MATCH (:Principal {id: $id})-[r:HAS_ROLE]->(:SystemAgent {agent_id: 'kb1'}) RETURN r.role AS r", id=principal.id)
    assert rows == [{"r": "editor"}]


async def test_a_migrated_key_can_use_the_api(world, redis, tmp_path):
    await _seed(world, redis)
    await _run(world, redis, tmp_path)
    response = await world.client.get("/api/v1/graph/stats?agent_id=kb1", headers=world.headers(LEGACY_KEY))
    assert response.status_code == 200, response.text
    denied = await world.client.get("/api/v1/graph/stats?agent_id=kb2", headers=world.headers(LEGACY_KEY))
    assert denied.status_code == 403


async def test_an_owner_is_created_once_with_its_key_only_in_the_file(world, redis, tmp_path, capsys):
    from app.services.identity_service import get_identity_service

    await _seed(world, redis)
    await _run(world, redis, tmp_path)
    key = (tmp_path / "owner.txt").read_text().strip()
    assert key not in capsys.readouterr().out
    assert oct((tmp_path / "owner.txt").stat().st_mode)[-3:] == "600"
    principal = await get_identity_service().resolve_key(key)
    assert principal.org_role == "owner"
    report = await _run(world, redis, tmp_path)
    assert report["steps"]["owner"]["to_create"] == 0


async def test_redis_agents_are_added_without_overwriting(world, redis, tmp_path):
    await _seed(world, redis)
    await _run(world, redis, tmp_path)
    rows = await _rows(world, "MATCH (a:SystemAgent) WHERE a.agent_id IN ['kb1', 'only-redis'] RETURN a.agent_id AS id, a.name AS n, a.clearance_level AS c ORDER BY id")
    assert rows == [{"id": "kb1", "n": "Kept name", "c": 3}, {"id": "only-redis", "n": "Redis Only", "c": 2}]


async def test_every_kb_joins_the_org(world, redis, tmp_path):
    await _seed(world, redis)
    await _run(world, redis, tmp_path)
    assert await _rows(world, "MATCH (a:SystemAgent) WHERE NOT (a)-[:IN_WORKSPACE]->(:Workspace)-[:IN_ORG]->(:Org) RETURN a") == []


async def test_related_to_edges_become_knowledge_edges(world, redis, tmp_path):
    await _seed(world, redis)
    await _run(world, redis, tmp_path)
    assert await _rows(world, "MATCH ()-[r:RELATED_TO]->() RETURN r") == []
    rows = await _rows(world, "MATCH (:SpaiderNode {id: 'b'})-[r:RELATION {id: 'r1'}]->(:SpaiderNode {id: 'a'}) RETURN r.relation AS n, r.recorded_at AS t")
    assert rows == [{"n": "FUSED", "t": "2026-01-04T00:00:00+00:00"}]


async def test_legacy_superseded_edges_get_their_end_time(world, redis, tmp_path):
    await _seed(world, redis)
    await _run(world, redis, tmp_path)
    rows = await _rows(world, "MATCH ()-[r:RELATION {id: 'e2'}]->() RETURN r.valid_to AS v, r.expired_at AS e")
    assert rows == [{"v": "2026-02-01T00:00:00+00:00", "e": "2026-02-01T00:00:00+00:00"}]
    facts = await _rows(world, "MATCH (n:SpaiderNode {id: 'f'}) RETURN n.superseded_at AS s")
    assert facts == [{"s": "2026-03-01T00:00:00+00:00"}]


async def test_backfills_and_normalises_time(world, redis, tmp_path):
    await _seed(world, redis)
    await _run(world, redis, tmp_path)
    node = (await _rows(world, "MATCH (n:SpaiderNode {id: 'b'}) RETURN n.created_at AS c, n.energy_level AS e, n.retrieval_count AS r"))[0]
    assert node["c"].startswith("2026-01-01T00:00:00") and node["e"] == 1.0 and node["r"] == 0
    edge = (await _rows(world, "MATCH ()-[r:RELATION {id: 'e1'}]->() RETURN r.recorded_at AS t, r.valid_from AS f, r.utility_weight AS w, r.contradiction_checked AS c"))[0]
    assert edge == {"t": "2026-01-02T00:00:00+00:00", "f": "2026-01-02T00:00:00+00:00", "w": 1.0, "c": True}


async def test_aliases_become_searchable(world, redis, tmp_path):
    await _seed(world, redis)
    await _run(world, redis, tmp_path)
    assert await _rows(world, "MATCH (n:SpaiderNode {id: 'a'}) RETURN n.aliases_text AS t") == [{"t": "Alpha Ay"}]


async def test_interactions_become_episodes(world, redis, tmp_path):
    await _seed(world, redis)
    await _run(world, redis, tmp_path)
    rows = await _rows(world, "MATCH (e:Episode {agent_id: 'kb1'}) RETURN e.kind AS k, e.content AS c, e.session_id AS s ORDER BY k")
    assert rows == [{"k": "answer", "c": "A.", "s": "s"}, {"k": "query", "c": "Q?", "s": "s"}]
    assert await _rows(world, "MATCH (i:InteractionNode) RETURN i.id AS id") == [{"id": "i1"}]


async def test_sharing_becomes_grants(world, redis, tmp_path):
    from app.services.federation import FederationService

    await _seed(world, redis)
    await _run(world, redis, tmp_path)
    service = FederationService(world.driver)
    reads_for_kb2 = {source.kb: source for source in await service.sources_for("kb2", 5)}
    assert "kb3" in reads_for_kb2
    reads_for_kb1 = {source.kb: source for source in await service.sources_for("kb1", 5)}
    assert reads_for_kb1["kb2"].node_types == ["PERSON"]


async def test_works_without_redis(world, tmp_path):
    report = await migrate.Migration(world.driver, None, apply=True, owner_key_file=tmp_path / "o.txt").run()
    assert report["steps"]["api_keys"]["to_create"] == 0
