"""RBAC: the role matrix, tenant isolation, and deny-by-default routing."""
from __future__ import annotations

import pytest
from fastapi.routing import APIRoute

from app.core.authz import KB_ROLE_ACTIONS, Action, KBRole, Principal, authorize, can
from app.core.security import hash_api_key


def _p(world, record: dict, role: str = "member") -> Principal:
    return Principal(id=record["id"], kind="user", org_id=world.org_id, org_role=role)


EXPECTED = {
    KBRole.READER: {Action.READ},
    KBRole.CONTRIBUTOR: {Action.READ, Action.WRITE},
    KBRole.EDITOR: {Action.READ, Action.WRITE, Action.EDIT},
    KBRole.STEWARD: {Action.READ, Action.WRITE, Action.EDIT, Action.MANAGE, Action.AUDIT},
    KBRole.AUDITOR: {Action.AUDIT},
}


def test_role_table_is_exactly_as_specified():
    assert {KBRole(k): set(v) for k, v in KB_ROLE_ACTIONS.items()} == EXPECTED


@pytest.mark.parametrize("role", list(KBRole))
async def test_each_role_gets_only_its_actions(auth_world, role):
    kb = await auth_world.create_kb()
    user = await auth_world.member(kb["id"], role.value)
    granted = {a for a in Action if await can(_p(auth_world, user), kb["id"], a)}
    assert granted == EXPECTED[role]


async def test_role_on_one_kb_grants_nothing_on_another(auth_world):
    kb_a, kb_b = await auth_world.create_kb("a"), await auth_world.create_kb("b")
    user = await auth_world.member(kb_a["id"], "steward")
    assert not [a for a in Action if await can(_p(auth_world, user), kb_b["id"], a)]


async def test_group_role_applies_to_members_and_stops_on_removal(auth_world):
    kb = await auth_world.create_kb()
    user = await auth_world.user()
    group = await auth_world.identity.create_group(auth_world.org_id, "team")
    await auth_world.identity.grant_role(auth_world.org_id, kb["id"], group["id"], "reader")
    principal = _p(auth_world, user)
    assert not await can(principal, kb["id"], Action.READ)
    await auth_world.identity.add_to_group(auth_world.org_id, group["id"], user["id"])
    assert await can(principal, kb["id"], Action.READ)
    assert not await can(principal, kb["id"], Action.WRITE)
    await auth_world.identity.remove_from_group(auth_world.org_id, group["id"], user["id"])
    assert not await can(principal, kb["id"], Action.READ)


async def test_org_admin_reaches_every_kb_in_the_org(auth_world):
    kb = await auth_world.create_kb()
    admin = await auth_world.user("a", org_role="admin")
    assert await can(_p(auth_world, admin, "admin"), kb["id"], Action.MANAGE)


async def test_org_auditor_can_audit_but_not_read(auth_world):
    kb = await auth_world.create_kb()
    auditor = await auth_world.user("a", org_role="auditor")
    principal = _p(auth_world, auditor, "auditor")
    assert await can(principal, kb["id"], Action.AUDIT)
    assert not await can(principal, kb["id"], Action.READ)


async def test_org_owner_of_another_org_is_a_stranger(auth_world):
    kb = await auth_world.create_kb()
    async with auth_world.driver.session() as session:
        await session.run("CREATE (:Org {id: 'other'})")
    foreign = Principal(id="x", kind="user", org_id="other", org_role="owner")
    assert not await can(foreign, kb["id"], Action.READ)


async def test_denial_does_not_reveal_whether_a_kb_exists(auth_world):
    from fastapi import HTTPException

    kb = await auth_world.create_kb()
    user = _p(auth_world, await auth_world.user())
    details = []
    for target in (kb["id"], "does-not-exist"):
        with pytest.raises(HTTPException) as exc:
            await authorize(user, target, Action.READ)
        details.append((exc.value.status_code, exc.value.detail))
    assert details[0] == details[1] and details[0][0] == 403


# ── Authentication ────────────────────────────────────────────────────────


async def _whoami(world, key: str):
    return await world.client.get("/api/v1/auth/whoami", headers=world.headers(key))


async def test_bearer_header_also_works(auth_world):
    response = await auth_world.client.get(
        "/api/v1/auth/whoami", headers={"Authorization": f"Bearer {auth_world.owner_key}"}
    )
    assert response.status_code == 200 and response.json()["org_role"] == "owner"


async def test_unknown_key_is_401(auth_world):
    assert (await _whoami(auth_world, "sk-nope")).status_code == 401


async def test_revoked_key_is_401(auth_world):
    user = await auth_world.user()
    await auth_world.identity.revoke_keys(user["id"])
    assert (await _whoami(auth_world, user["api_key"])).status_code == 401


async def test_expired_key_is_401(auth_world):
    user = await auth_world.user()
    async with auth_world.driver.session() as session:
        await session.run(
            "MATCH (k:ApiKey {hash: $h}) SET k.expires_at = '2000-01-01T00:00:00+00:00'",
            h=hash_api_key(user["api_key"]),
        )
    assert (await _whoami(auth_world, user["api_key"])).status_code == 401


async def test_disabled_principal_is_401(auth_world):
    user = await auth_world.user()
    await auth_world.identity.disable_principal(auth_world.org_id, user["id"])
    assert (await _whoami(auth_world, user["api_key"])).status_code == 401


async def test_rotate_own_key(auth_world):
    user = await auth_world.user()
    response = await auth_world.client.post("/api/v1/auth/rotate", headers=auth_world.headers(user["api_key"]))
    assert (await _whoami(auth_world, response.json()["api_key"])).status_code == 200
    assert (await _whoami(auth_world, user["api_key"])).status_code == 401


# ── Bootstrap ─────────────────────────────────────────────────────────────


BOOTSTRAP = {"org_name": "Second", "owner_name": "O"}


async def test_bootstrap_works_only_once(auth_world):
    response = await auth_world.client.post(
        "/api/v1/auth/bootstrap", json=BOOTSTRAP, headers={"X-Bootstrap-Token": "bootstrap-secret"}
    )
    assert response.status_code == 409


@pytest.mark.parametrize("headers", [{}, {"X-Bootstrap-Token": "wrong"}])
async def test_bootstrap_needs_the_token(auth_world, headers):
    response = await auth_world.client.post("/api/v1/auth/bootstrap", json=BOOTSTRAP, headers=headers)
    assert response.status_code == 403


async def test_bootstrap_is_closed_when_no_token_is_configured(auth_world, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "spaider_bootstrap_token", "")
    response = await auth_world.client.post(
        "/api/v1/auth/bootstrap", json=BOOTSTRAP, headers={"X-Bootstrap-Token": ""}
    )
    assert response.status_code == 403


# ── Escalation ────────────────────────────────────────────────────────────


async def test_member_cannot_create_users(auth_world):
    member = await auth_world.user()
    response = await auth_world.client.post(
        "/api/v1/users", json={"name": "x"}, headers=auth_world.headers(member["api_key"])
    )
    assert response.status_code == 403


async def test_admin_cannot_mint_an_owner(auth_world):
    admin = await auth_world.user("a", org_role="admin", clearance_level=5)
    response = await auth_world.client.post(
        "/api/v1/users", json={"name": "x", "org_role": "owner"}, headers=auth_world.headers(admin["api_key"])
    )
    assert response.status_code == 403


async def test_admin_cannot_disable_an_owner(auth_world):
    admin = await auth_world.user("a", org_role="admin")
    principals = await auth_world.identity.list_principals(auth_world.org_id)
    owner = next(p for p in principals if p["org_role"] == "owner")
    response = await auth_world.client.delete(
        f"/api/v1/principals/{owner['id']}", headers=auth_world.headers(admin["api_key"])
    )
    assert response.status_code == 403


async def test_service_account_cannot_exceed_steward_clearance(auth_world):
    kb = await auth_world.create_kb()
    steward = await auth_world.member(kb["id"], "steward", clearance_level=2)
    response = await auth_world.client.post(
        f"/api/v1/agents/{kb['id']}/service-accounts",
        json={"name": "s", "clearance_level": 3},
        headers=auth_world.headers(steward["api_key"]),
    )
    assert response.status_code == 403


async def test_service_account_holds_no_org_powers(auth_world):
    kb = await auth_world.create_kb()
    response = await auth_world.client.post(
        "/api/v1/agents", json={"name": "x"}, headers=auth_world.headers(kb["api_key"])
    )
    assert response.status_code == 403


async def test_editor_cannot_raise_kb_clearance(auth_world):
    kb = await auth_world.create_kb(clearance_level=1)
    editor = await auth_world.member(kb["id"], "editor", clearance_level=5)
    response = await auth_world.client.put(
        f"/api/v1/agents/{kb['id']}",
        json={"name": kb["name"], "clearance_level": 5},
        headers=auth_world.headers(editor["api_key"]),
    )
    assert response.status_code == 200 and response.json()["agent"]["clearance_level"] == 1


# ── Membership over HTTP ──────────────────────────────────────────────────


async def test_steward_manages_members_and_reader_cannot(auth_world):
    kb = await auth_world.create_kb()
    steward, reader = await auth_world.member(kb["id"], "steward"), await auth_world.member(kb["id"], "reader")
    target = await auth_world.user("t")
    url = f"/api/v1/agents/{kb['id']}/members/{target['id']}"
    denied = await auth_world.client.put(url, json={"role": "reader"}, headers=auth_world.headers(reader["api_key"]))
    assert denied.status_code == 403
    allowed = await auth_world.client.put(url, json={"role": "reader"}, headers=auth_world.headers(steward["api_key"]))
    assert allowed.status_code == 200
    assert await can(_p(auth_world, target), kb["id"], Action.READ)
    await auth_world.client.delete(url, headers=auth_world.headers(steward["api_key"]))
    assert not await can(_p(auth_world, target), kb["id"], Action.READ)


async def test_list_shows_only_accessible_kbs(auth_world):
    kb_a, _ = await auth_world.create_kb("a"), await auth_world.create_kb("b")
    reader = await auth_world.member(kb_a["id"], "reader")
    agents = (await auth_world.client.get("/api/v1/agents", headers=auth_world.headers(reader["api_key"]))).json()
    assert [a["id"] for a in agents["agents"]] == [kb_a["id"]]


async def test_deleting_a_kb_revokes_its_keys_and_roles(auth_world):
    kb = await auth_world.create_kb()
    user = await auth_world.member(kb["id"], "reader")
    response = await auth_world.client.delete(
        f"/api/v1/agents/{kb['id']}", headers=auth_world.headers(auth_world.owner_key)
    )
    assert response.status_code == 200
    assert (await _whoami(auth_world, kb["api_key"])).status_code == 401
    assert not await can(_p(auth_world, user), kb["id"], Action.READ)


# ── Deny by default ───────────────────────────────────────────────────────

_PUBLIC = {("POST", "/api/v1/auth/bootstrap")}


def _routes():
    from app.api.router import api_router

    for route in api_router.routes:
        if isinstance(route, APIRoute):
            for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
                yield method, f"/api/v1{route.path}"


@pytest.mark.parametrize(("method", "path"), sorted(set(_routes())))
async def test_every_route_rejects_anonymous_callers(auth_world, method, path):
    if (method, path) in _PUBLIC:
        pytest.skip("deliberately public")
    url = path.replace("{", "").replace("}", "")
    response = await auth_world.client.request(method, url)
    assert response.status_code == 401, f"{method} {path} -> {response.status_code}"


# A valid key for KB A against KB B, across every per-KB read and write surface.
_CROSS_KB = [
    ("GET", "/api/v1/agents/{b}", None),
    ("PUT", "/api/v1/agents/{b}", {"name": "x"}),
    ("DELETE", "/api/v1/agents/{b}", None),
    ("POST", "/api/v1/agents/{b}/rotate-key", None),
    ("GET", "/api/v1/agents/{b}/export", None),
    ("GET", "/api/v1/agents/{b}/members", None),
    ("POST", "/api/v1/agents/{b}/memory-mode", {"memory_mode": "on"}),
    ("POST", "/api/v1/agents/{b}/consolidate-now", None),
    ("DELETE", "/api/v1/agents/{b}/interactions", None),
    ("GET", "/api/v1/graph?agent_id={b}", None),
    ("GET", "/api/v1/graph/stats?agent_id={b}", None),
    ("GET", "/api/v1/graph/clusters?agent_id={b}", None),
    ("GET", "/api/v1/analytics/overview?agent_id={b}", None),
    ("GET", "/api/v1/replay/workflows?agent_id={b}", None),
    ("POST", "/api/v1/query", {"question": "q", "agent_id": "{b}"}),
    ("POST", "/api/v1/query/stream", {"question": "q", "agent_id": "{b}"}),
    ("POST", "/api/v1/query/traverse", {"start_node_id": "n", "agent_id": "{b}"}),
    ("POST", "/api/v1/ingest/sync", {"text": "t", "agent_id": "{b}"}),
    ("POST", "/api/v1/ingest/graph", {"agent_id": "{b}", "nodes": [], "edges": []}),
    ("POST", "/api/v1/synthesize", {"agent_id": "{b}"}),
    ("GET", "/api/v1/synthesize/dpo?agent_id={b}", None),
    ("POST", "/api/v1/system/feedback", {"query_id": "q", "used_node_ids": ["n"], "success": True, "agent_id": "{b}"}),
    ("POST", "/api/v1/swarm/query", {"query": "q", "agent_ids": ["{b}"]}),
]


@pytest.mark.parametrize(("method", "path", "body"), _CROSS_KB)
async def test_key_for_one_kb_cannot_touch_another(auth_world, method, path, body):
    kb_a, kb_b = await auth_world.create_kb("a"), await auth_world.create_kb("b")
    payload = None
    if body is not None:
        import json

        payload = json.loads(json.dumps(body).replace("{b}", kb_b["id"]))
    response = await auth_world.client.request(
        method, path.replace("{b}", kb_b["id"]), json=payload, headers=auth_world.headers(kb_a["api_key"])
    )
    assert response.status_code == 403, f"{method} {path} -> {response.status_code}: {response.text}"


async def test_fleet_views_need_org_admin(auth_world):
    kb = await auth_world.create_kb()
    headers = auth_world.headers(kb["api_key"])
    paths = ("/api/v1/graph/multiverse", "/api/v1/analytics/top-agents", "/api/v1/swarm/health", "/api/v1/agents/links")
    for path in paths:
        assert (await auth_world.client.get(path, headers=headers)).status_code == 403, path


async def test_insecure_mode_is_refused_in_production(monkeypatch):
    from app.config import settings
    from app.main import app, lifespan

    monkeypatch.setattr(settings, "spaider_insecure_dev", True)
    monkeypatch.setattr(settings, "environment", "production")
    with pytest.raises(RuntimeError, match="SPAIDER_INSECURE_DEV"):
        async with lifespan(app):
            pass


async def test_node_delete_needs_editor_on_the_owning_kb(auth_world):
    kb = await auth_world.create_kb()
    reader, editor = await auth_world.member(kb["id"], "reader"), await auth_world.member(kb["id"], "editor")
    async with auth_world.driver.session() as session:
        await session.run(
            "CREATE (:SpaiderNode {id: 'n1', agent_id: $a, label: 'L', type: 'CONCEPT'})", a=kb["id"]
        )
    denied = await auth_world.client.delete("/api/v1/node/n1", headers=auth_world.headers(reader["api_key"]))
    assert denied.status_code == 403
    allowed = await auth_world.client.delete("/api/v1/node/n1", headers=auth_world.headers(editor["api_key"]))
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["audit_entry"]["agent_id"] == kb["id"]
    assert allowed.json()["audit_entry"]["node_label"] == "L"
