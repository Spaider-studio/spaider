"""GET /graph/stats reports real counts, within what the caller may see."""
from __future__ import annotations

import pytest


async def _seed(world, kb_id: str) -> None:
    async with world.driver.session() as session:
        await session.run(
            """
            UNWIND $nodes AS n
            CREATE (:SpaiderNode {id: n.id, agent_id: $kb, label: n.id, type: n.type, clearance_level: n.level})
            """,
            kb=kb_id,
            nodes=[
                {"id": "ada", "type": "PERSON", "level": 1},
                {"id": "bob", "type": "PERSON", "level": 1},
                {"id": "acme", "type": "ORGANIZATION", "level": 1},
                {"id": "secret", "type": "PERSON", "level": 5},
            ],
        )
        await session.run(
            """
            MATCH (a:SpaiderNode {id: 'ada', agent_id: $kb}), (b:SpaiderNode {id: 'acme', agent_id: $kb}),
                  (c:SpaiderNode {id: 'bob', agent_id: $kb}), (s:SpaiderNode {id: 'secret', agent_id: $kb})
            CREATE (a)-[:RELATION {relation: 'WORKS_AT'}]->(b),
                   (c)-[:RELATION {relation: 'WORKS_AT'}]->(b),
                   (a)-[:RELATION {relation: 'KNOWS'}]->(c),
                   (s)-[:RELATION {relation: 'KNOWS'}]->(a)
            """,
            kb=kb_id,
        )


@pytest.mark.asyncio
async def test_the_stats_count_nodes_and_relations_by_type(auth_world):
    kb = await auth_world.create_kb("stats-kb", clearance_level=5)
    await _seed(auth_world, kb["id"])

    response = await auth_world.client.get(
        "/api/v1/graph/stats", params={"agent_id": kb["id"]}, headers=auth_world.headers(auth_world.owner_key)
    )

    body = response.json()
    assert response.status_code == 200, response.text
    assert body["node_count"] == 4
    assert body["type_distribution"] == {"PERSON": 3, "ORGANIZATION": 1}
    assert body["relation_distribution"] == {"WORKS_AT": 2, "KNOWS": 2}


@pytest.mark.asyncio
async def test_the_stats_leave_out_what_the_caller_may_not_see(auth_world):
    kb = await auth_world.create_kb("stats-kb", clearance_level=5)
    await _seed(auth_world, kb["id"])
    reader = await auth_world.member(kb["id"], "reader", clearance_level=1)

    response = await auth_world.client.get(
        "/api/v1/graph/stats", params={"agent_id": kb["id"]}, headers=auth_world.headers(reader["api_key"])
    )

    body = response.json()
    assert response.status_code == 200, response.text
    assert body["node_count"] == 3
    assert body["type_distribution"] == {"PERSON": 2, "ORGANIZATION": 1}
    assert body["relation_distribution"] == {"WORKS_AT": 2, "KNOWS": 1}
