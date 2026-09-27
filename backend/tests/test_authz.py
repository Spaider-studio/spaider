"""
Authorization regression tests for the v0.3.1 security patch.

Covers the pure-logic core that gates every protected route:
  - _is_admin / _check_idor / _require_admin (identity + admin gating)
  - optional_auth (non-blocking soft auth used by agent self-registration)
  - the read-only Cypher keyword filter (blocks CALL / APOC / LOAD CSV)
  - traverse namespace scoping (start node pinned to the caller's agent_id)

These are the guards that were missing or bypassable in the reported
vulnerabilities; they run without a live Neo4j/Redis so they stay fast and
deterministic in CI.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

import app.services.auth_service as auth_mod
from app.services.auth_service import (
    _check_idor,
    _is_admin,
    _require_admin,
    optional_auth,
)

BYPASS = {"agent_id": None, "auth_bypassed": True}
AGENT_A = {"agent_id": "A", "permissions": ["read", "write"]}
AGENT_B = {"agent_id": "B", "permissions": ["read", "write"]}
ADMIN = {"agent_id": "ops", "permissions": ["read", "write", "admin"]}


# ---------------------------------------------------------------------------
# _is_admin
# ---------------------------------------------------------------------------

def test_is_admin_true_only_with_admin_permission():
    assert _is_admin(ADMIN) is True
    assert _is_admin(AGENT_A) is False
    assert _is_admin({"agent_id": "x"}) is False
    assert _is_admin({}) is False


# ---------------------------------------------------------------------------
# _check_idor
# ---------------------------------------------------------------------------

def test_check_idor_noop_when_flag_off():
    """With auth disabled the guard is a no-op regardless of identity."""
    with patch.object(auth_mod, "_REQUIRE_API_KEY_AUTH", False):
        # Even a mismatched, non-bypass record passes when the flag is off.
        _check_idor({"agent_id": "A", "permissions": []}, "B")


def test_check_idor_noop_for_bypass_sentinel():
    with patch.object(auth_mod, "_REQUIRE_API_KEY_AUTH", True):
        _check_idor(BYPASS, "anything")


def test_check_idor_same_agent_allowed():
    with patch.object(auth_mod, "_REQUIRE_API_KEY_AUTH", True):
        _check_idor(AGENT_A, "A")


def test_check_idor_cross_agent_blocked():
    with patch.object(auth_mod, "_REQUIRE_API_KEY_AUTH", True):
        with pytest.raises(HTTPException) as exc:
            _check_idor(AGENT_A, "B")
        assert exc.value.status_code == 403


def test_check_idor_admin_crosses_namespaces():
    with patch.object(auth_mod, "_REQUIRE_API_KEY_AUTH", True):
        _check_idor(ADMIN, "A")
        _check_idor(ADMIN, "B")


# ---------------------------------------------------------------------------
# _require_admin
# ---------------------------------------------------------------------------

def test_require_admin_noop_when_flag_off():
    with patch.object(auth_mod, "_REQUIRE_API_KEY_AUTH", False):
        _require_admin(AGENT_A)  # no raise


def test_require_admin_noop_for_bypass_sentinel():
    with patch.object(auth_mod, "_REQUIRE_API_KEY_AUTH", True):
        _require_admin(BYPASS)


def test_require_admin_allows_admin():
    with patch.object(auth_mod, "_REQUIRE_API_KEY_AUTH", True):
        _require_admin(ADMIN)


def test_require_admin_blocks_non_admin():
    with patch.object(auth_mod, "_REQUIRE_API_KEY_AUTH", True):
        with pytest.raises(HTTPException) as exc:
            _require_admin(AGENT_A)
        assert exc.value.status_code == 403


def test_require_admin_blocks_anonymous():
    """Flag on + no valid key (anonymous soft-auth record) is not admin."""
    with patch.object(auth_mod, "_REQUIRE_API_KEY_AUTH", True):
        with pytest.raises(HTTPException):
            _require_admin({"agent_id": None, "auth_bypassed": False, "permissions": []})


# ---------------------------------------------------------------------------
# optional_auth — never raises
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_optional_auth_bypass_when_flag_off():
    with patch.object(auth_mod, "_REQUIRE_API_KEY_AUTH", False):
        result = await optional_auth(x_api_key=None, authorization=None)
    assert result.get("auth_bypassed") is True


@pytest.mark.asyncio
async def test_optional_auth_anonymous_when_no_key():
    with patch.object(auth_mod, "_REQUIRE_API_KEY_AUTH", True):
        result = await optional_auth(x_api_key=None, authorization=None)
    assert result["agent_id"] is None
    assert result.get("auth_bypassed") is False
    assert result["permissions"] == []


@pytest.mark.asyncio
async def test_optional_auth_returns_record_for_valid_key():
    record = {"agent_id": "A", "permissions": ["read", "write", "admin"]}
    with patch.object(auth_mod, "_REQUIRE_API_KEY_AUTH", True), \
         patch.object(auth_mod.AuthService, "get_agent_by_api_key",
                      new=AsyncMock(return_value=record)):
        result = await optional_auth(x_api_key="sk-valid", authorization=None)
    assert result == record
    assert _is_admin(result) is True


@pytest.mark.asyncio
async def test_optional_auth_anonymous_for_invalid_key():
    with patch.object(auth_mod, "_REQUIRE_API_KEY_AUTH", True), \
         patch.object(auth_mod.AuthService, "get_agent_by_api_key",
                      new=AsyncMock(return_value=None)):
        result = await optional_auth(x_api_key="sk-bogus", authorization=None)
    assert result["agent_id"] is None
    assert result.get("auth_bypassed") is False


@pytest.mark.asyncio
async def test_optional_auth_reads_bearer_header():
    record = {"agent_id": "A", "permissions": ["read"]}
    with patch.object(auth_mod, "_REQUIRE_API_KEY_AUTH", True), \
         patch.object(auth_mod.AuthService, "get_agent_by_api_key",
                      new=AsyncMock(return_value=record)) as m:
        result = await optional_auth(x_api_key=None, authorization="Bearer sk-abc")
    assert result == record
    m.assert_awaited_once_with("sk-abc")


# ---------------------------------------------------------------------------
# Read-only Cypher keyword filter (endpoint + service regexes)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "cypher",
    [
        "MATCH (n) CREATE (m)",
        "MATCH (n) SET n.x = 1",
        "MATCH (n) DETACH DELETE n",
        "MERGE (n:X)",
        "CALL apoc.create.node(['L'], {})",       # APOC write without a write keyword
        "CALL apoc.cypher.doIt('MATCH (n) RETURN n', {})",
        "LOAD CSV FROM 'file:///x.csv' AS row RETURN row",
        "CALL db.labels()",
    ],
)
def test_write_pattern_blocks_writes_and_calls(cypher):
    from app.api.v1.query import _WRITE_PATTERN
    assert _WRITE_PATTERN.search(cypher) is not None, cypher


@pytest.mark.parametrize(
    "cypher",
    [
        "MATCH (n:SpaiderNode {agent_id: $agent_id}) RETURN n",
        "MATCH (n) WHERE n.agent_id = $agent_id RETURN n.label",
    ],
)
def test_write_pattern_allows_plain_reads(cypher):
    from app.api.v1.query import _WRITE_PATTERN
    assert _WRITE_PATTERN.search(cypher) is None, cypher


def test_service_read_only_validator_blocks_apoc():
    from app.services.query_service import QueryService
    svc = QueryService.__new__(QueryService)  # no __init__ / no driver needed
    with pytest.raises(ValueError):
        svc._validate_read_only("CALL apoc.create.node(['L'], {})")
    # A plain read must pass.
    svc._validate_read_only("MATCH (n {agent_id: $agent_id}) RETURN n")


# ---------------------------------------------------------------------------
# traverse() namespace scoping
# ---------------------------------------------------------------------------

def _mock_query_service_capturing_cypher():
    """A QueryService whose driver session records the last cypher/params."""
    from app.services.query_service import QueryService

    captured: dict = {}

    class _Result:
        async def single(self):
            return None  # empty traversal → GraphPayload()

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def run(self, cypher, **params):
            captured["cypher"] = cypher
            captured["params"] = params
            return _Result()

    class _Driver:
        def session(self):
            return _Session()

    svc = QueryService.__new__(QueryService)
    svc._graph = type("G", (), {"_driver": _Driver()})()
    return svc, captured


@pytest.mark.asyncio
async def test_traverse_scopes_start_node_to_agent():
    svc, captured = _mock_query_service_capturing_cypher()
    await svc.traverse(start_node_id="n1", depth=2, agent_id="A")
    assert "agent_id: $agent_id" in captured["cypher"]
    assert captured["params"].get("agent_id") == "A"


@pytest.mark.asyncio
async def test_traverse_unscoped_when_no_agent():
    svc, captured = _mock_query_service_capturing_cypher()
    await svc.traverse(start_node_id="n1", depth=2, agent_id=None)
    assert "agent_id" not in captured["cypher"]
    assert "agent_id" not in captured["params"]
