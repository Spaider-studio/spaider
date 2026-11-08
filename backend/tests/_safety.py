"""
Keeps the test suite away from real databases.

``app.config`` defaults to ``localhost:7687`` and ``localhost:6379`` with the
dev password, which on a contributor's machine is their actual running stack.
Integration tests wipe what they connect to, so one unmocked code path is
enough to lose real data. Three independent layers prevent that:

1. ``install_test_environment`` points every service setting at the throwaway
   test ports. It must run before anything imports ``app.config``.
2. ``block_real_services`` makes loopback connections to the standard service
   ports fail as if the service were down.
3. ``assert_test_database`` refuses to touch a Neo4j instance that holds data
   but carries no test marker.
"""
from __future__ import annotations

import os
import socket
import tempfile
from collections.abc import Callable
from urllib.parse import urlsplit

TEST_NEO4J_URI = "bolt://localhost:17687"
TEST_NEO4J_PASSWORD = "testpassword123"
TEST_REDIS_URL = "redis://localhost:16379/0"

# Neo4j, Redis, Kafka, ZooKeeper, Postgres, ClickHouse, and the app + gateway.
BLOCKED_LOOPBACK_PORTS = frozenset(
    {7687, 7474, 6379, 9092, 29092, 2181, 5432, 8123, 9000, 8000, 8001, 8080, 3000}
)
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0", "::"})

MARKER_LABEL = "SpaiderTestMarker"
MARKER_ID = "spaider-test-db"


class RealServiceBlocked(ConnectionRefusedError):
    """A test tried to reach a standard service port on this machine.

    Subclasses ConnectionRefusedError so code that tolerates a service being
    down treats the blocked connection the same way.
    """


class NotATestDatabase(RuntimeError):
    """The connected database holds data and has no test marker."""


def neo4j_uri_for_tests() -> str:
    return os.environ.get("SPAIDER_TEST_NEO4J_URI", TEST_NEO4J_URI)


def neo4j_password_for_tests() -> str:
    return os.environ.get("SPAIDER_TEST_NEO4J_PASSWORD", TEST_NEO4J_PASSWORD)


def redis_url_for_tests() -> str:
    return os.environ.get("SPAIDER_TEST_REDIS_URL", TEST_REDIS_URL)


def is_blocked_address(host: str, port: int) -> bool:
    return host in _LOOPBACK_HOSTS and port in BLOCKED_LOOPBACK_PORTS


def assert_safe_url(url: str) -> None:
    """Reject a configured test URL that points at a standard local port."""
    parts = urlsplit(url)
    if parts.hostname and parts.port and is_blocked_address(parts.hostname, parts.port):
        raise RealServiceBlocked(
            f"Test service URL {parts.hostname}:{parts.port} is a standard local "
            "port. Use backend/tests/docker-compose.test.yml (17687 / 16379)."
        )


def install_test_environment() -> None:
    """Point every service setting at throwaway test targets."""
    assert_safe_url(neo4j_uri_for_tests())
    assert_safe_url(redis_url_for_tests())
    os.environ["NEO4J_URI"] = neo4j_uri_for_tests()
    os.environ["NEO4J_USER"] = "neo4j"
    os.environ["NEO4J_PASSWORD"] = neo4j_password_for_tests()
    os.environ["REDIS_URL"] = redis_url_for_tests()
    # Absent by default in tests; the code falls back to its Lite-mode stores.
    os.environ["KAFKA_BOOTSTRAP_SERVERS"] = ""
    os.environ["CLICKHOUSE_HOST"] = ""
    os.environ["POSTGRES_URL"] = ""
    os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="spaider-test-data-")
    # Most tests are not about auth. The ones that are clear this flag.
    os.environ["SPAIDER_INSECURE_DEV"] = "true"
    # An unmocked LLM call must fail rather than spend money.
    for key in ("LLM_API_KEY", "EMBEDDING_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        os.environ[key] = ""


def _guarded(original: Callable) -> Callable:
    def connect(self: socket.socket, address, *args, **kwargs):
        if (
            isinstance(address, tuple)
            and len(address) >= 2
            and isinstance(address[1], int)
            and is_blocked_address(address[0], address[1])
        ):
            raise RealServiceBlocked(f"Blocked test connection to {address[0]}:{address[1]}")
        return original(self, address, *args, **kwargs)

    return connect


def block_real_services() -> None:
    """Patch socket connects, once per process."""
    if getattr(socket.socket, "_spaider_guarded", False):
        return
    socket.socket.connect = _guarded(socket.socket.connect)  # type: ignore[method-assign]
    socket.socket.connect_ex = _guarded(socket.socket.connect_ex)  # type: ignore[method-assign]
    socket.socket._spaider_guarded = True  # type: ignore[attr-defined]


async def assert_test_database(driver) -> None:
    """Stamp an empty database as a test database, or refuse a non-test one.

    A real database is never empty and never carries the marker, so a wrong
    connection string stops here instead of being wiped.
    """
    async with driver.session() as session:
        result = await session.run(f"MATCH (m:{MARKER_LABEL}) RETURN count(m) AS c")
        record = await result.single()
        if record["c"] > 0:
            return
        result = await session.run("MATCH (n) RETURN count(n) AS c")
        record = await result.single()
        if record["c"] > 0:
            raise NotATestDatabase(
                f"Database holds {record['c']} nodes and has no {MARKER_LABEL} node; "
                "refusing to run integration tests against it."
            )
        await session.run(f"CREATE (:{MARKER_LABEL} {{id: $id}})", id=MARKER_ID)


async def wipe_test_database(driver) -> None:
    """Delete everything except the marker. Call assert_test_database first."""
    async with driver.session() as session:
        await session.run(f"MATCH (n) WHERE NOT n:{MARKER_LABEL} DETACH DELETE n")
