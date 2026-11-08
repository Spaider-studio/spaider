"""
The guard in tests/_safety.py is what stands between the suite and a
developer's real databases, so it gets tests of its own.
"""
from __future__ import annotations

import socket

import pytest

from app.config import settings
from tests import _safety


@pytest.mark.parametrize("port", [7687, 6379, 5432, 9092, 8123, 8000])
@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_connect_to_standard_port_is_blocked(host, port):
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        with pytest.raises(_safety.RealServiceBlocked):
            sock.connect((host, port))


def test_connect_elsewhere_is_allowed():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
            client.connect(server.getsockname())


async def test_asyncio_connections_are_blocked_too():
    import asyncio

    with pytest.raises(_safety.RealServiceBlocked):
        await asyncio.open_connection("127.0.0.1", 7687)


def test_settings_point_away_from_real_services():
    for url in (settings.neo4j_uri, settings.redis_url):
        _safety.assert_safe_url(url)
    assert settings.llm_api_key == ""


@pytest.mark.parametrize(
    "url", ["bolt://localhost:7687", "redis://127.0.0.1:6379/0"]
)
def test_standard_port_rejected_as_test_target(url):
    with pytest.raises(_safety.RealServiceBlocked):
        _safety.assert_safe_url(url)


def test_remote_service_host_is_allowed():
    # CI runs the suite inside compose, where these are ephemeral services.
    _safety.assert_safe_url("bolt://neo4j:7687")


async def test_empty_database_gets_stamped(neo4j_test_driver):
    async with neo4j_test_driver.session() as session:
        await session.run(f"MATCH (m:{_safety.MARKER_LABEL}) DELETE m")
    await _safety.assert_test_database(neo4j_test_driver)
    async with neo4j_test_driver.session() as session:
        result = await session.run(f"MATCH (m:{_safety.MARKER_LABEL}) RETURN count(m) AS c")
        assert (await result.single())["c"] == 1


async def test_populated_unmarked_database_is_refused(neo4j_test_driver):
    async with neo4j_test_driver.session() as session:
        await session.run(f"MATCH (m:{_safety.MARKER_LABEL}) DELETE m")
        await session.run("CREATE (:SpaiderNode {id: 'looks-real'})")
    with pytest.raises(_safety.NotATestDatabase):
        await _safety.assert_test_database(neo4j_test_driver)
    async with neo4j_test_driver.session() as session:
        result = await session.run("MATCH (n:SpaiderNode {id: 'looks-real'}) RETURN count(n) AS c")
        assert (await result.single())["c"] == 1
