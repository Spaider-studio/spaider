"""POST /agents and rotation, against the real router and a real Neo4j."""
from __future__ import annotations

from app.core.security import hash_api_key


async def _key_nodes(world) -> list[dict]:
    async with world.driver.session() as session:
        result = await session.run("MATCH (k:ApiKey) RETURN properties(k) AS p")
        return [record["p"] async for record in result]


class TestCreate:
    async def test_key_is_a_service_key_shown_once(self, auth_world) -> None:
        agent = await auth_world.create_kb()
        assert agent["api_key"].startswith("ss-")

    async def test_key_authenticates_immediately(self, auth_world) -> None:
        agent = await auth_world.create_kb()
        response = await auth_world.client.get(
            f"/api/v1/agents/{agent['id']}", headers=auth_world.headers(agent["api_key"])
        )
        assert response.status_code == 200

    async def test_only_the_hash_is_stored(self, auth_world) -> None:
        agent = await auth_world.create_kb()
        stored = await _key_nodes(auth_world)
        assert any(k["hash"] == hash_api_key(agent["api_key"]) for k in stored)
        assert all(agent["api_key"] not in str(k) for k in stored)

    async def test_creator_becomes_steward(self, auth_world) -> None:
        agent = await auth_world.create_kb()
        members = await auth_world.identity.list_members(agent["id"])
        assert {"steward", "editor"} == {m["role"] for m in members}

    async def test_above_own_clearance_is_refused(self, auth_world) -> None:
        member = await auth_world.user("m", clearance_level=2)
        response = await auth_world.client.post(
            "/api/v1/agents",
            json={"name": "x", "clearance_level": 3},
            headers=auth_world.headers(member["api_key"]),
        )
        assert response.status_code == 403


class TestRotate:
    async def _rotate(self, world, agent_id: str):
        return await world.client.post(
            f"/api/v1/agents/{agent_id}/rotate-key", headers=world.headers(world.owner_key)
        )

    async def test_new_key_differs_and_works(self, auth_world) -> None:
        agent = await auth_world.create_kb()
        new_key = (await self._rotate(auth_world, agent["id"])).json()["api_key"]
        assert new_key != agent["api_key"] and new_key.startswith("ss-")
        response = await auth_world.client.get(
            f"/api/v1/agents/{agent['id']}", headers=auth_world.headers(new_key)
        )
        assert response.status_code == 200

    async def test_old_key_stops_working(self, auth_world) -> None:
        agent = await auth_world.create_kb()
        await self._rotate(auth_world, agent["id"])
        response = await auth_world.client.get(
            f"/api/v1/agents/{agent['id']}", headers=auth_world.headers(agent["api_key"])
        )
        assert response.status_code == 401

    async def test_unknown_kb_is_404_for_an_admin(self, auth_world) -> None:
        assert (await self._rotate(auth_world, "nope")).status_code == 404

    async def test_rotation_leaves_the_graph_alone(self, auth_world) -> None:
        agent = await auth_world.create_kb()
        async with auth_world.driver.session() as session:
            await session.run("CREATE (:SpaiderNode {id: 'n1', agent_id: $a})", a=agent["id"])
        await self._rotate(auth_world, agent["id"])
        async with auth_world.driver.session() as session:
            result = await session.run("MATCH (n:SpaiderNode {agent_id: $a}) RETURN count(n) AS c", a=agent["id"])
            assert (await result.single())["c"] == 1

    async def test_metadata_is_unchanged(self, auth_world) -> None:
        agent = await auth_world.create_kb(name="keep", description="d", clearance_level=2)
        await self._rotate(auth_world, agent["id"])
        after = (
            await auth_world.client.get(
                f"/api/v1/agents/{agent['id']}", headers=auth_world.headers(auth_world.owner_key)
            )
        ).json()["agent"]
        assert (after["name"], after["description"], after["clearance_level"]) == ("keep", "d", 2)


async def test_list_and_get_never_expose_a_key(auth_world) -> None:
    agent = await auth_world.create_kb()
    headers = auth_world.headers(auth_world.owner_key)
    listed = (await auth_world.client.get("/api/v1/agents", headers=headers)).json()["agents"]
    fetched = (await auth_world.client.get(f"/api/v1/agents/{agent['id']}", headers=headers)).json()["agent"]
    assert [a["api_key"] for a in listed] == [None]
    assert fetched["api_key"] is None
