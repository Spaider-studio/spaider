"""Outbound-connection limits, raw-Cypher limits, and hardening middleware."""
from __future__ import annotations

import socket
from unittest.mock import AsyncMock

import httpx
import pytest
from cryptography.exceptions import InvalidTag
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.core import net_guard
from app.core.cypher_guard import CypherRejected, check_cypher
from app.core.net_guard import (
    BlockedTarget,
    PolicyRefused,
    UnresolvableHost,
    check_dsn,
    check_host,
    check_image_url,
    check_stdio_command,
    check_url,
    check_urls,
    guarded_get,
    is_blocked_ip,
)
from app.middleware.security_headers import SecurityHeadersMiddleware


@pytest.fixture
def strict(monkeypatch):
    monkeypatch.setattr(settings, "spaider_allow_private_net", False)
    monkeypatch.setattr(settings, "mcp_stdio_allowed_commands", "")


def _resolves_to(monkeypatch, *addresses):
    async def fake(host):
        return list(addresses)

    monkeypatch.setattr(net_guard, "_resolve", fake)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1", "10.0.0.1", "172.16.0.1", "192.168.1.1", "169.254.169.254",
        "100.64.0.1", "0.0.0.0", "198.18.0.1", "224.0.0.1",
        "::1", "fe80::1", "fc00::1", "fec0::1", "fec0:1::2", "::", "ff02::1",
        "::ffff:127.0.0.1", "::ffff:169.254.169.254", "2002:7f00:1::", "64:ff9b::a00:1",
    ],
)
def test_private_and_reserved_addresses_are_blocked(address):
    assert is_blocked_ip(address) is True


@pytest.mark.parametrize("address", ["1.1.1.1", "8.8.8.8", "2606:4700:4700::1111", "::ffff:8.8.8.8"])
def test_public_addresses_are_allowed(address):
    assert is_blocked_ip(address) is False


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "gopher://x", "ftp://example.com/a", "http://user:pw@example.com/"],
)
async def test_bad_url_forms_are_refused(strict, url):
    with pytest.raises(BlockedTarget):
        await check_url(url)


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1/", "http://[::1]/", "http://169.254.169.254/latest/meta-data", "http://[fec0::1]/"],
)
async def test_literal_private_urls_are_refused(strict, url):
    with pytest.raises(BlockedTarget):
        await check_url(url)


async def test_hostname_resolving_privately_is_refused(strict, monkeypatch):
    _resolves_to(monkeypatch, "10.1.2.3")
    with pytest.raises(BlockedTarget):
        await check_url("http://intranet.example/")


async def test_one_private_answer_among_public_ones_is_enough(strict, monkeypatch):
    _resolves_to(monkeypatch, "8.8.8.8", "10.0.0.1")
    with pytest.raises(BlockedTarget):
        await check_host("mixed.example")


async def test_real_localhost_resolution_is_refused(strict):
    with pytest.raises(BlockedTarget):
        await check_url("http://localhost:7687/")


async def test_unresolvable_host_is_not_treated_as_policy(strict, monkeypatch):
    async def gai(*a, **k):
        raise socket.gaierror

    monkeypatch.setattr("asyncio.BaseEventLoop.getaddrinfo", gai)
    with pytest.raises(UnresolvableHost):
        await check_url("http://nope.invalid/")
    await check_urls(["http://nope.invalid/"])


async def test_allow_private_net_opts_out(monkeypatch):
    monkeypatch.setattr(settings, "spaider_allow_private_net", True)
    await check_url("http://127.0.0.1/")


class _Recorder:
    """Stands in for httpx.AsyncClient and scripts responses by call."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    async def get(self, url, headers=None, extensions=None, follow_redirects=None):
        self.calls.append({"url": url, "headers": headers, "extensions": extensions, "follow": follow_redirects})
        return self.responses.pop(0)


async def test_connection_is_pinned_to_the_checked_address(strict, monkeypatch):
    _resolves_to(monkeypatch, "93.184.216.34")
    client = _Recorder(httpx.Response(200, text="ok"))
    await guarded_get(client, "https://example.com/a?b=1", headers={"If-None-Match": "x"})
    call = client.calls[0]
    assert str(call["url"]) == "https://93.184.216.34/a?b=1"
    assert call["headers"]["Host"] == "example.com"
    assert call["headers"]["If-None-Match"] == "x"
    assert call["extensions"] == {"sni_hostname": "example.com"}
    assert call["follow"] is False


async def test_redirect_to_a_private_address_is_refused(strict, monkeypatch):
    answers = {"public.example": ["8.8.8.8"], "169.254.169.254": ["169.254.169.254"]}

    async def fake(host):
        return answers[host]

    monkeypatch.setattr(net_guard, "_resolve", fake)
    redirect = httpx.Response(302, headers={"location": "http://169.254.169.254/latest"})
    client = _Recorder(redirect)
    with pytest.raises(BlockedTarget):
        await guarded_get(client, "http://public.example/")
    assert len(client.calls) == 1


async def test_redirect_loops_stop(strict, monkeypatch):
    _resolves_to(monkeypatch, "8.8.8.8")
    client = _Recorder(*[httpx.Response(302, headers={"location": "/again"}) for _ in range(10)])
    with pytest.raises(BlockedTarget):
        await guarded_get(client, "http://public.example/")


def test_stdio_is_off_by_default(strict):
    with pytest.raises(PolicyRefused):
        check_stdio_command("/bin/sh")


def test_stdio_allowlist_is_exact(strict, monkeypatch):
    monkeypatch.setattr(settings, "mcp_stdio_allowed_commands", "uvx, node")
    check_stdio_command("uvx")
    with pytest.raises(PolicyRefused):
        check_stdio_command("/tmp/uvx")


@pytest.mark.parametrize(
    "dsn",
    [
        "sqlite+aiosqlite:////app/data/connectors.db",
        "postgresql+psycopg2://u:p@example.com/db",
        "postgresql+asyncpg://u:p@127.0.0.1/db",
        "postgresql+asyncpg://u:p@example.com/db?host=/var/run/postgresql",
        "not a dsn",
    ],
)
async def test_bad_dsns_are_refused(strict, dsn, monkeypatch):
    _resolves_to(monkeypatch, "8.8.8.8") if "example.com" in dsn else None
    with pytest.raises(BlockedTarget):
        await check_dsn(dsn)


async def test_public_dsn_is_allowed(strict, monkeypatch):
    _resolves_to(monkeypatch, "8.8.8.8")
    await check_dsn("postgresql+asyncpg://u:p@db.example.com:5432/db")


async def test_image_urls(strict, monkeypatch):
    await check_image_url("data:image/png;base64,AAAA")
    with pytest.raises(BlockedTarget):
        await check_image_url("data:text/html;base64,AAAA")
    with pytest.raises(BlockedTarget):
        await check_image_url("http://169.254.169.254/x.png")


# ── Connectors apply the guards themselves ────────────────────────────────


async def test_url_connector_refuses_before_any_fetch(strict):
    from app.connectors import RunState
    from app.connectors.trafilatura_url_connector import URLConnector

    connector = URLConnector()
    with pytest.raises(BlockedTarget):
        async for _ in connector.run("kb", RunState(connector_id="url"), urls=["http://127.0.0.1/"]):
            pass


async def test_sql_connector_refuses_local_files(strict):
    from app.connectors import RunState
    from app.connectors.sql_connector import SQLConnector

    with pytest.raises(BlockedTarget):
        async for _ in SQLConnector().run(
            "kb", RunState(connector_id="sql"), dsn="sqlite+aiosqlite:////etc/passwd", query="SELECT 1"
        ):
            pass


async def test_mcp_connector_refuses_commands(strict):
    from app.connectors import RunState
    from app.connectors.mcp_connector import MCPConnector

    with pytest.raises(PolicyRefused):
        async for _ in MCPConnector().run("kb", RunState(connector_id="mcp"), command="/bin/sh"):
            pass


@pytest.mark.parametrize(
    ("route", "body", "status"),
    [
        ("/api/v1/ingest/url", {"urls": ["http://169.254.169.254/"]}, 422),
        ("/api/v1/ingest/mcp", {"command": "/bin/sh"}, 403),
        ("/api/v1/ingest/sql", {"dsn": "sqlite+aiosqlite:////etc/passwd", "query": "SELECT 1"}, 422),
        ("/api/v1/ingest/image", {"image_url": "http://10.0.0.1/a.png"}, 422),
    ],
)
async def test_routes_map_refusals_to_the_right_status(auth_world, strict, route, body, status):
    kb = await auth_world.create_kb()
    response = await auth_world.client.post(
        route, json={**body, "agent_id": kb["id"]}, headers=auth_world.headers(kb["api_key"])
    )
    assert response.status_code == status, response.text


# ── Raw Cypher ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "cypher",
    [
        "MATCH (n:SpaiderNode {agent_id: $agent_id}) RETURN n.label LIMIT 5",
        "MATCH (a:SpaiderNode)-[r:RELATION]->(b:SpaiderNode) WHERE (a.x = 1 OR b.x = 2) RETURN a.id, count(r)",
        "MATCH (n:SpaiderNode) WHERE n.label = 'MATCH (p:Principal) // not code' RETURN n",
        "MATCH (e:Episode) RETURN e.content;",
        "UNWIND [1, 2] AS x RETURN x",
        "RETURN toUpper('a') AS a, size([1]) AS b",
    ],
)
def test_allowed_queries(cypher):
    check_cypher(cypher)


@pytest.mark.parametrize(
    "cypher",
    [
        "MATCH (k:ApiKey) RETURN k.hash",
        "MATCH (p:Principal) RETURN p",
        "MATCH (o:Org) RETURN o",
        "MATCH (a:SystemAgent) RETURN a",
        "MATCH (n:SpaiderNode|ApiKey) RETURN n",
        "MATCH (n:!SpaiderNode) RETURN n",
        "MATCH (n:%) RETURN n",
        "MATCH (n:`ApiKey`) RETURN n",
        "MATCH (n) RETURN n",
        "MATCH () RETURN 1",
        "MATCH (n:SpaiderNode)--(m) RETURN m",
        "MATCH (n {id: 'x'}) RETURN n",
        "MATCH (n:SpaiderNode) // hidden\nRETURN n",
        "MATCH (n:SpaiderNode) /* x */ RETURN n",
        "MATCH (n:SpaiderNode) RETURN n; MATCH (k:ApiKey) RETURN k",
        "CALL db.labels()",
        "MATCH (n:SpaiderNode) CALL { MATCH (k:ApiKey) RETURN k } RETURN k",
        "MATCH (n:SpaiderNode) RETURN apoc.cypher.runFirstColumnSingle('MATCH (k:ApiKey) RETURN k', {})",
        "MATCH (n:SpaiderNode)-[r]-(m:SpaiderNode) RETURN startNode(r)",
        "MATCH p = (n:SpaiderNode)-[*]-(m:SpaiderNode) RETURN nodes(p)",
        "SHOW USERS",
        "LOAD CSV FROM 'http://x' AS r RETURN r",
        "MATCH (n:SpaiderNode) SET n.x = 1",
        "MATCH (n:SpaiderNode) WHERE n.a = \\u0027 RETURN n",
        "MATCH (n:SpaiderNode) WHERE n.a = 'unterminated RETURN n",
        "USE system MATCH (n:SpaiderNode) RETURN n",
        "",
    ],
)
def test_rejected_queries(cypher):
    with pytest.raises(CypherRejected):
        check_cypher(cypher)


async def _cypher(world, kb, query, key=None):
    return await world.client.post(
        "/api/v1/query/cypher",
        json={"cypher": query, "agent_id": kb["id"]},
        headers=world.headers(key or world.owner_key),
    )


async def test_raw_cypher_cannot_read_api_keys(auth_world):
    kb = await auth_world.create_kb()
    response = await _cypher(auth_world, kb, "MATCH (k:ApiKey) RETURN k.hash AS h")
    assert response.status_code == 403


async def test_raw_cypher_works_for_an_org_admin(auth_world):
    kb = await auth_world.create_kb()
    response = await _cypher(auth_world, kb, "MATCH (n:SpaiderNode) RETURN count(n) AS c")
    assert response.status_code == 200, response.text


async def test_raw_cypher_needs_org_admin(auth_world):
    kb = await auth_world.create_kb()
    response = await _cypher(auth_world, kb, "MATCH (n:SpaiderNode) RETURN count(n) AS c", key=kb["api_key"])
    assert response.status_code == 403


async def test_raw_cypher_is_dev_only_with_several_orgs(auth_world, monkeypatch):
    kb = await auth_world.create_kb()
    async with auth_world.driver.session() as session:
        await session.run("CREATE (:Org {id: 'second'})")
    monkeypatch.setattr(settings, "environment", "staging")
    assert (await _cypher(auth_world, kb, "MATCH (n:SpaiderNode) RETURN n")).status_code == 403
    monkeypatch.setattr(settings, "environment", "development")
    assert (await _cypher(auth_world, kb, "MATCH (n:SpaiderNode) RETURN n")).status_code == 200


# ── Secrets, headers, rate limits, startup ────────────────────────────────


@pytest.fixture
def secrets_service(monkeypatch):
    import base64

    from app.services import secrets_service as module

    monkeypatch.setattr(settings, "connector_secret_key", base64.b64encode(b"k" * 32).decode())
    return module.SecretsService()


def test_secret_round_trip(secrets_service):
    cipher, nonce = secrets_service.encrypt({"k": "v"}, context="c1:token")
    assert secrets_service.decrypt(cipher, nonce, context="c1:token") == {"k": "v"}


def test_secret_cannot_move_to_another_connector(secrets_service):
    cipher, nonce = secrets_service.encrypt({"k": "v"}, context="c1:token")
    with pytest.raises(InvalidTag):
        secrets_service.decrypt(cipher, nonce, context="c2:token")


async def test_security_headers_are_set():
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/x")
    async def x():
        return {}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        headers = (await client.get("/x")).headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"


def _limited_app(limit: int) -> FastAPI:
    from app.middleware.rate_limiter import RateLimiterMiddleware

    app = FastAPI()
    app.add_middleware(RateLimiterMiddleware, limit=limit)

    @app.get("/api/x")
    async def x():
        return {}

    @app.get("/health")
    async def health():
        return {}

    return app


async def test_rate_limit_applies_per_key_without_redis(monkeypatch):
    monkeypatch.setattr(settings, "redis_url", "")
    async with AsyncClient(transport=ASGITransport(app=_limited_app(2)), base_url="http://t") as client:
        codes = [(await client.get("/api/x", headers={"X-API-Key": "a"})).status_code for _ in range(3)]
        other = (await client.get("/api/x", headers={"X-API-Key": "b"})).status_code
        health = (await client.get("/health", headers={"X-API-Key": "a"})).status_code
    assert codes == [200, 200, 429] and other == 200 and health == 200


async def test_rate_limit_stores_no_raw_key(monkeypatch):
    from app.core.kv import get_kv

    monkeypatch.setattr(settings, "redis_url", "")
    async with AsyncClient(transport=ASGITransport(app=_limited_app(5)), base_url="http://t") as client:
        await client.get("/api/x", headers={"X-API-Key": "sk-very-secret"})
    assert not [k async for k in get_kv().scan_iter("*") if "sk-very-secret" in k]


async def test_a_limiter_outage_does_not_block_requests(monkeypatch):
    from app.middleware import rate_limiter

    monkeypatch.setattr(rate_limiter, "check_rate_limit", AsyncMock(side_effect=ConnectionError))
    async with AsyncClient(transport=ASGITransport(app=_limited_app(1)), base_url="http://t") as client:
        assert (await client.get("/api/x")).status_code == 200


async def test_default_password_is_refused_outside_development(monkeypatch):
    from app.main import app, lifespan

    monkeypatch.setattr(settings, "spaider_insecure_dev", False)
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "neo4j_password", "spaider-dev-2024")
    with pytest.raises(RuntimeError, match="NEO4J_PASSWORD"):
        async with lifespan(app):
            pass
