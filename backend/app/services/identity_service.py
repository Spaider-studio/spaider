"""
Organizations, principals, roles and API keys, stored in Neo4j.

    (:Org)<-[:IN_ORG]-(:Workspace)<-[:IN_WORKSPACE]-(:SystemAgent)     the KB
    (:Principal)-[:IN_ORG {role}]->(:Org)
    (:Principal)-[:HAS_ROLE {role}]->(:SystemAgent)
    (:Principal)-[:IN_GROUP]->(:Principal:Group)
    (:ApiKey)-[:AUTHENTICATES]->(:Principal)

Only the SHA-256 of a key is stored. Raw keys are returned once, at creation.
"""
from __future__ import annotations

import logging
import secrets
import uuid
from datetime import datetime, timezone
from typing import Optional

from neo4j.exceptions import ConstraintError

from app.core.authz import KBRole, OrgRole, Principal
from app.core.security import hash_api_key

logger = logging.getLogger(__name__)

USER_KEY_PREFIX = "sk-"
SERVICE_KEY_PREFIX = "ss-"
DEFAULT_WORKSPACE = "default"


class AlreadyBootstrapped(RuntimeError):
    """An organization already exists."""


class UnknownPrincipal(LookupError):
    """The principal does not exist in this organization."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_key(prefix: str) -> str:
    return f"{prefix}{secrets.token_hex(24)}"


class IdentityService:
    def __init__(self, driver) -> None:
        self._driver = driver

    # ── Bootstrap ─────────────────────────────────────────────────────────

    async def bootstrap(self, org_name: str, owner_name: str, email: Optional[str] = None) -> dict:
        """Create the first org and its owner. Works exactly once."""
        org_id, workspace_id, user_id = (str(uuid.uuid4()) for _ in range(3))
        raw_key = _new_key(USER_KEY_PREFIX)
        now = _now()
        async with self._driver.session() as session:
            try:
                # The unique constraint makes concurrent bootstraps race safely.
                await session.run(
                    "CREATE (:BootstrapLock {id: 'singleton', created_at: $now})", now=now
                )
            except ConstraintError as exc:
                raise AlreadyBootstrapped("This instance is already bootstrapped.") from exc
            await session.run(
                """
                CREATE (o:Org {id: $org, name: $org_name, created_at: $now})
                CREATE (w:Workspace {id: $ws, name: $ws_name, org_id: $org, created_at: $now})-[:IN_ORG]->(o)
                CREATE (u:Principal:User {
                    id: $user, name: $owner, email: $email, org_id: $org,
                    clearance_level: 5, created_at: $now
                })-[:IN_ORG {role: $role}]->(o)
                CREATE (:ApiKey {hash: $hash, prefix: $prefix, created_at: $now})-[:AUTHENTICATES]->(u)
                """,
                org=org_id, org_name=org_name, ws=workspace_id, ws_name=DEFAULT_WORKSPACE,
                user=user_id, owner=owner_name, email=email, role=OrgRole.OWNER.value,
                hash=hash_api_key(raw_key), prefix=raw_key[:10], now=now,
            )
        return {"org_id": org_id, "workspace_id": workspace_id, "user_id": user_id, "api_key": raw_key}

    async def ensure_org(self, org_id: str) -> None:
        """Create an org and its default workspace if absent (insecure dev only)."""
        async with self._driver.session() as session:
            await session.run(
                """
                MERGE (o:Org {id: $org}) ON CREATE SET o.name = $org, o.created_at = $now
                MERGE (w:Workspace {name: $ws, org_id: $org})
                    ON CREATE SET w.id = randomUUID(), w.created_at = $now
                MERGE (w)-[:IN_ORG]->(o)
                """,
                org=org_id, ws=DEFAULT_WORKSPACE, now=_now(),
            )

    async def is_bootstrapped(self) -> bool:
        async with self._driver.session() as session:
            result = await session.run("MATCH (o:Org) RETURN count(o) > 0 AS present")
            return bool((await result.single())["present"])

    # ── Authentication ────────────────────────────────────────────────────

    async def resolve_key(self, raw_key: str) -> Optional[Principal]:
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (k:ApiKey {hash: $hash})-[:AUTHENTICATES]->(p:Principal)-[m:IN_ORG]->(o:Org)
                WHERE k.revoked_at IS NULL
                  AND (k.expires_at IS NULL OR k.expires_at > $now)
                  AND NOT coalesce(p.disabled, false)
                RETURN p.id AS id, p:ServiceAccount AS service, o.id AS org, m.role AS role,
                       coalesce(p.clearance_level, 1) AS clearance,
                       p.default_kb AS default_kb, p.name AS name
                """,
                hash=hash_api_key(raw_key),
                now=_now(),
            )
            record = await result.single()
        if record is None:
            return None
        return Principal(
            id=record["id"],
            kind="service_account" if record["service"] else "user",
            org_id=record["org"],
            org_role=record["role"],
            clearance_level=record["clearance"],
            default_kb=record["default_kb"],
            name=record["name"] or "",
        )

    async def _issue_key(self, session, principal_id: str, prefix: str, name: Optional[str]) -> str:
        raw_key = _new_key(prefix)
        await session.run(
            """
            MATCH (p:Principal {id: $pid})
            CREATE (:ApiKey {hash: $hash, prefix: $short, name: $name, created_at: $now})
                   -[:AUTHENTICATES]->(p)
            """,
            pid=principal_id, hash=hash_api_key(raw_key), short=raw_key[:10], name=name, now=_now(),
        )
        return raw_key

    async def revoke_keys(self, principal_id: str) -> int:
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (k:ApiKey)-[:AUTHENTICATES]->(:Principal {id: $pid})
                WHERE k.revoked_at IS NULL
                SET k.revoked_at = $now
                RETURN count(k) AS revoked
                """,
                pid=principal_id, now=_now(),
            )
            return (await result.single())["revoked"]

    async def rotate_key(self, principal_id: str) -> str:
        """Revoke every key the principal holds and issue a fresh one."""
        await self.revoke_keys(principal_id)
        async with self._driver.session() as session:
            result = await session.run(
                "MATCH (p:Principal {id: $pid}) RETURN p:ServiceAccount AS service", pid=principal_id
            )
            record = await result.single()
            if record is None:
                raise UnknownPrincipal(principal_id)
            prefix = SERVICE_KEY_PREFIX if record["service"] else USER_KEY_PREFIX
            return await self._issue_key(session, principal_id, prefix, None)

    # ── Users and groups ──────────────────────────────────────────────────

    async def create_user(
        self,
        org_id: str,
        name: str,
        *,
        email: Optional[str] = None,
        org_role: str = OrgRole.MEMBER.value,
        clearance_level: int = 1,
        created_by: Optional[str] = None,
    ) -> dict:
        user_id = str(uuid.uuid4())
        async with self._driver.session() as session:
            await session.run(
                """
                MATCH (o:Org {id: $org})
                CREATE (:Principal:User {
                    id: $id, name: $name, email: $email, org_id: $org,
                    clearance_level: $clearance, created_by: $by, created_at: $now
                })-[:IN_ORG {role: $role}]->(o)
                """,
                org=org_id, id=user_id, name=name, email=email, clearance=clearance_level,
                by=created_by, role=org_role, now=_now(),
            )
            raw_key = await self._issue_key(session, user_id, USER_KEY_PREFIX, None)
        return {"id": user_id, "name": name, "org_role": org_role, "api_key": raw_key}

    async def list_principals(self, org_id: str) -> list[dict]:
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (p:Principal)-[m:IN_ORG]->(:Org {id: $org})
                RETURN p.id AS id, p.name AS name, p.email AS email, m.role AS org_role,
                       CASE WHEN p:Group THEN 'group'
                            WHEN p:ServiceAccount THEN 'service_account' ELSE 'user' END AS kind,
                       coalesce(p.clearance_level, 1) AS clearance_level,
                       coalesce(p.disabled, false) AS disabled
                ORDER BY name
                """,
                org=org_id,
            )
            return [dict(record) async for record in result]

    async def disable_principal(self, org_id: str, principal_id: str) -> None:
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (p:Principal {id: $pid})-[:IN_ORG]->(:Org {id: $org})
                SET p.disabled = true, p.disabled_at = $now
                RETURN p.id AS id
                """,
                pid=principal_id, org=org_id, now=_now(),
            )
            if await result.single() is None:
                raise UnknownPrincipal(principal_id)
        await self.revoke_keys(principal_id)

    async def org_role_of(self, org_id: str, principal_id: str) -> Optional[str]:
        async with self._driver.session() as session:
            result = await session.run(
                "MATCH (:Principal {id: $pid})-[m:IN_ORG]->(:Org {id: $org}) RETURN m.role AS role",
                pid=principal_id, org=org_id,
            )
            record = await result.single()
            return record["role"] if record else None

    async def create_group(self, org_id: str, name: str, created_by: Optional[str] = None) -> dict:
        group_id = str(uuid.uuid4())
        async with self._driver.session() as session:
            await session.run(
                """
                MATCH (o:Org {id: $org})
                CREATE (:Principal:Group {
                    id: $id, name: $name, org_id: $org, created_by: $by, created_at: $now
                })-[:IN_ORG {role: $role}]->(o)
                """,
                org=org_id, id=group_id, name=name, by=created_by,
                role=OrgRole.MEMBER.value, now=_now(),
            )
        return {"id": group_id, "name": name}

    async def add_to_group(self, org_id: str, group_id: str, principal_id: str) -> None:
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (p:Principal {id: $pid})-[:IN_ORG]->(o:Org {id: $org})
                MATCH (g:Group {id: $gid})-[:IN_ORG]->(o)
                WHERE NOT p:Group
                MERGE (p)-[:IN_GROUP]->(g)
                RETURN p.id AS id
                """,
                pid=principal_id, gid=group_id, org=org_id,
            )
            if await result.single() is None:
                raise UnknownPrincipal(principal_id)

    async def remove_from_group(self, org_id: str, group_id: str, principal_id: str) -> None:
        async with self._driver.session() as session:
            await session.run(
                """
                MATCH (:Principal {id: $pid})-[r:IN_GROUP]->(g:Group {id: $gid})
                      -[:IN_ORG]->(:Org {id: $org})
                DELETE r
                """,
                pid=principal_id, gid=group_id, org=org_id,
            )

    # ── Knowledge bases ───────────────────────────────────────────────────

    async def attach_kb(
        self, org_id: str, agent_id: str, *, creator_id: Optional[str] = None
    ) -> None:
        """Place a SystemAgent in the org's default workspace; creator becomes steward."""
        async with self._driver.session() as session:
            await session.run(
                """
                MATCH (o:Org {id: $org})<-[:IN_ORG]-(w:Workspace {name: $ws})
                MATCH (kb:SystemAgent {agent_id: $kb})
                MERGE (kb)-[:IN_WORKSPACE]->(w)
                """,
                org=org_id, ws=DEFAULT_WORKSPACE, kb=agent_id,
            )
            if creator_id:
                await session.run(
                    """
                    MATCH (p:Principal {id: $pid}), (kb:SystemAgent {agent_id: $kb})
                    MERGE (p)-[r:HAS_ROLE]->(kb)
                    SET r.role = $role, r.granted_at = $now
                    """,
                    pid=creator_id, kb=agent_id, role=KBRole.STEWARD.value, now=_now(),
                )

    async def detach_kb(self, agent_id: str) -> None:
        """Remove every role on the KB and disable the service accounts bound to it."""
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (sa:ServiceAccount {default_kb: $kb})
                SET sa.disabled = true, sa.disabled_at = $now
                RETURN sa.id AS id
                """,
                kb=agent_id, now=_now(),
            )
            service_accounts = [record["id"] async for record in result]
            await session.run(
                "MATCH (:Principal)-[r:HAS_ROLE]->(:SystemAgent {agent_id: $kb}) DELETE r", kb=agent_id
            )
        for principal_id in service_accounts:
            await self.revoke_keys(principal_id)

    async def grant_role(
        self, org_id: str, agent_id: str, principal_id: str, role: str, granted_by: Optional[str] = None
    ) -> None:
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (p:Principal {id: $pid})-[:IN_ORG]->(o:Org {id: $org})
                MATCH (kb:SystemAgent {agent_id: $kb})-[:IN_WORKSPACE]->(:Workspace)-[:IN_ORG]->(o)
                MERGE (p)-[r:HAS_ROLE]->(kb)
                SET r.role = $role, r.granted_by = $by, r.granted_at = $now
                RETURN p.id AS id
                """,
                pid=principal_id, org=org_id, kb=agent_id, role=role, by=granted_by, now=_now(),
            )
            if await result.single() is None:
                raise UnknownPrincipal(principal_id)

    async def revoke_role(self, agent_id: str, principal_id: str) -> None:
        async with self._driver.session() as session:
            await session.run(
                "MATCH (:Principal {id: $pid})-[r:HAS_ROLE]->(:SystemAgent {agent_id: $kb}) DELETE r",
                pid=principal_id, kb=agent_id,
            )

    async def list_members(self, agent_id: str) -> list[dict]:
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (p:Principal)-[r:HAS_ROLE]->(:SystemAgent {agent_id: $kb})
                RETURN p.id AS id, p.name AS name, r.role AS role,
                       CASE WHEN p:Group THEN 'group'
                            WHEN p:ServiceAccount THEN 'service_account' ELSE 'user' END AS kind
                ORDER BY name
                """,
                kb=agent_id,
            )
            return [dict(record) async for record in result]

    # ── Service accounts ──────────────────────────────────────────────────

    async def create_service_account(
        self,
        org_id: str,
        agent_id: str,
        name: str,
        *,
        role: str = KBRole.EDITOR.value,
        clearance_level: int = 1,
        created_by: Optional[str] = None,
        kb_default: bool = False,
    ) -> dict:
        """Create a service account bound to one KB and return its key once.

        ``kb_default`` marks the account whose key ``POST /agents/{id}/rotate-key``
        rotates.
        """
        sa_id = str(uuid.uuid4())
        async with self._driver.session() as session:
            await session.run(
                """
                MATCH (o:Org {id: $org})
                MATCH (kb:SystemAgent {agent_id: $kb})
                CREATE (sa:Principal:ServiceAccount {
                    id: $id, name: $name, org_id: $org, default_kb: $kb,
                    clearance_level: $clearance, kb_default: $kb_default,
                    created_by: $by, created_at: $now
                })-[:IN_ORG {role: $org_role}]->(o)
                CREATE (sa)-[:HAS_ROLE {role: $role, granted_by: $by, granted_at: $now}]->(kb)
                """,
                org=org_id, kb=agent_id, id=sa_id, name=name, clearance=clearance_level,
                kb_default=kb_default, by=created_by, org_role=OrgRole.SERVICE.value,
                role=role, now=_now(),
            )
            raw_key = await self._issue_key(session, sa_id, SERVICE_KEY_PREFIX, name)
        return {"id": sa_id, "name": name, "role": role, "api_key": raw_key}

    async def default_service_account(self, agent_id: str) -> Optional[str]:
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (sa:ServiceAccount {default_kb: $kb, kb_default: true})
                WHERE NOT coalesce(sa.disabled, false)
                RETURN sa.id AS id
                """,
                kb=agent_id,
            )
            record = await result.single()
            return record["id"] if record else None


_service: Optional[IdentityService] = None
_service_driver = None


def get_identity_service() -> IdentityService:
    """The shared service, rebuilt if the graph driver has been replaced."""
    global _service, _service_driver
    from app.services.graph_service import get_graph_service

    driver = get_graph_service()._driver
    if _service is None or _service_driver is not driver:
        _service, _service_driver = IdentityService(driver), driver
    return _service
