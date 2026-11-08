"""
Federation: who may see into which knowledge base, and how far.

A grant lets a target KB read a source KB within a scope: node types, relation
types, a clearance ceiling and an expiry. A steward of the source approves it.
``sources_for`` lists what a KB can see, and ``policy_predicate`` turns that into
Cypher, so out-of-scope nodes are filtered by the database and never reach the
LLM. Prompts cannot leak what retrieval never returned.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException, status

from app.core.authz import Action, Principal, authorize, can
from app.core.kv import bump_cache_generation
from app.services.audit_store import AuditStore

logger = logging.getLogger(__name__)

_NOT_FOUND = "Grant not found."


@dataclass(frozen=True)
class FederationSource:
    kb: str
    name: str
    max_clearance: int
    node_types: Optional[list[str]] = None
    relation_types: Optional[list[str]] = None


def sources_param(sources: list[FederationSource]) -> list[dict]:
    return [asdict(source) for source in sources]


def policy_predicate(var: str, param: str = "fed_sources") -> str:
    """Cypher: node ``var`` lies inside one of the sources' scopes."""
    return (
        f"any(s IN ${param} WHERE s.kb = {var}.agent_id "
        f"AND coalesce({var}.clearance_level, 1) <= s.max_clearance "
        f"AND (s.node_types IS NULL OR {var}.type IN s.node_types))"
    )


def edge_policy_predicate(var: str, param: str = "fed_sources") -> str:
    """Cypher: edge ``var`` lies inside one of the sources' scopes."""
    return (
        f"any(s IN ${param} WHERE s.kb = {var}.agent_id "
        f"AND (s.relation_types IS NULL OR {var}.relation IN s.relation_types))"
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _public(record: dict) -> dict:
    return {key: value for key, value in record.items() if key != "org_id"}


class FederationService:
    def __init__(self, driver) -> None:
        self._driver = driver

    async def sources_for(self, kb: str, clearance: int) -> list[FederationSource]:
        """What ``kb`` can see: itself, plus every live grant into it.

        Expiry is checked here, on every read, so a lapsed grant stops working
        without any job having to run.
        """
        async with self._driver.session() as session:
            record = await (
                await session.run(
                    """
                    MATCH (primary:SystemAgent {agent_id: $kb})
                    OPTIONAL MATCH (g:FederationGrant {target_kb: $kb, status: 'approved'})
                    WHERE g.expires_at IS NULL OR g.expires_at > $now
                    OPTIONAL MATCH (source:SystemAgent {agent_id: g.source_kb})
                    WITH primary, g, source WHERE g IS NULL OR source IS NOT NULL
                    RETURN coalesce(primary.name, primary.agent_id) AS name,
                           collect(CASE WHEN g IS NULL THEN null ELSE {
                               kb: g.source_kb, name: coalesce(source.name, g.source_kb),
                               max_clearance: g.max_clearance, node_types: g.node_types,
                               relation_types: g.relation_types
                           } END) AS grants
                    """,
                    kb=kb,
                    now=_now(),
                )
            ).single()
        if record is None:
            return []
        sources = [FederationSource(kb=kb, name=record["name"], max_clearance=clearance)]
        for grant in record["grants"]:
            sources.append(
                FederationSource(
                    kb=grant["kb"],
                    name=grant["name"],
                    max_clearance=min(clearance, grant["max_clearance"]),
                    node_types=grant["node_types"],
                    relation_types=grant["relation_types"],
                )
            )
        return sources

    async def _same_org(self, org_id: str, *kbs: str) -> bool:
        async with self._driver.session() as session:
            record = await (
                await session.run(
                    """
                    MATCH (kb:SystemAgent)-[:IN_WORKSPACE]->(:Workspace)-[:IN_ORG]->(:Org {id: $org})
                    WHERE kb.agent_id IN $kbs
                    RETURN count(DISTINCT kb) AS found
                    """,
                    org=org_id,
                    kbs=list(kbs),
                )
            ).single()
        return record["found"] == len(set(kbs))

    async def _audit(self, grant: dict, event: str, actor: Principal) -> None:
        for kb in (grant["source_kb"], grant["target_kb"]):
            await AuditStore(self._driver).record_event(
                workflow_id=f"grant:{grant['id']}", agent_id=kb, event_type=event, payload={"actor_id": actor.id}
            )

    async def request(
        self,
        principal: Principal,
        source_kb: str,
        target_kb: str,
        *,
        node_types: Optional[list[str]] = None,
        relation_types: Optional[list[str]] = None,
        max_clearance: int = 1,
        expires_at: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> dict:
        if source_kb == target_kb:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="A KB cannot grant to itself.")
        await authorize(principal, target_kb, Action.MANAGE)
        if not principal.insecure and not await self._same_org(principal.org_id, source_kb):
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Knowledge base not found.")
        grant = {
            "id": str(uuid.uuid4()),
            "org_id": principal.org_id,
            "source_kb": source_kb,
            "target_kb": target_kb,
            "node_types": node_types,
            "relation_types": relation_types,
            "max_clearance": max_clearance,
            "expires_at": expires_at,
            "reason": reason,
            "status": "requested",
            "requested_by": principal.id,
            "created_at": _now(),
        }
        async with self._driver.session() as session:
            await session.run("CREATE (g:FederationGrant) SET g = $grant", grant=grant)
        await self._audit(grant, "grant_requested", principal)
        return _public(grant)

    async def _load(self, grant_id: str) -> dict:
        async with self._driver.session() as session:
            record = await (
                await session.run("MATCH (g:FederationGrant {id: $id}) RETURN properties(g) AS g", id=grant_id)
            ).single()
        if record is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
        return dict(record["g"])

    async def _transition(self, grant_id: str, expected: list[str], new_status: str, fields: dict) -> dict:
        """Change status only from an expected state, so two decisions cannot both win."""
        async with self._driver.session() as session:
            record = await (
                await session.run(
                    """
                    MATCH (g:FederationGrant {id: $id}) WHERE g.status IN $expected
                    SET g += $fields, g.status = $status
                    RETURN properties(g) AS g
                    """,
                    id=grant_id,
                    expected=expected,
                    fields=fields,
                    status=new_status,
                )
            ).single()
        if record is None:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="The grant is no longer in a state that allows this.")
        grant = dict(record["g"])
        # Answers cached under the old access must not outlive it.
        await bump_cache_generation(grant["target_kb"])
        return grant

    async def approve(
        self,
        principal: Principal,
        grant_id: str,
        *,
        node_types: Optional[list[str]] = None,
        relation_types: Optional[list[str]] = None,
        max_clearance: Optional[int] = None,
        expires_at: Optional[str] = None,
    ) -> dict:
        """A steward of the source decides. They may narrow the request, never exceed their own clearance."""
        grant = await self._load(grant_id)
        await authorize(principal, grant["source_kb"], Action.MANAGE)
        clearance = grant["max_clearance"] if max_clearance is None else max_clearance
        if clearance > principal.clearance_level:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Cannot grant above your own clearance.")
        fields = {
            "max_clearance": clearance,
            "node_types": node_types if node_types is not None else grant.get("node_types"),
            "relation_types": relation_types if relation_types is not None else grant.get("relation_types"),
            "expires_at": expires_at if expires_at is not None else grant.get("expires_at"),
            "approved_by": principal.id,
            "approved_at": _now(),
        }
        updated = await self._transition(grant_id, ["requested"], "approved", fields)
        await self._audit(updated, "grant_approved", principal)
        return _public(updated)

    async def deny(self, principal: Principal, grant_id: str) -> dict:
        grant = await self._load(grant_id)
        await authorize(principal, grant["source_kb"], Action.MANAGE)
        updated = await self._transition(
            grant_id, ["requested"], "denied", {"denied_by": principal.id, "denied_at": _now()}
        )
        await self._audit(updated, "grant_denied", principal)
        return _public(updated)

    async def revoke(self, principal: Principal, grant_id: str) -> dict:
        """Either side can end a grant: the owner of the data, or the KB relying on it."""
        grant = await self._load(grant_id)
        if not (
            await can(principal, grant["source_kb"], Action.MANAGE)
            or await can(principal, grant["target_kb"], Action.MANAGE)
        ):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Not permitted on this grant.")
        updated = await self._transition(
            grant_id, ["requested", "approved"], "revoked", {"revoked_by": principal.id, "revoked_at": _now()}
        )
        await self._audit(updated, "grant_revoked", principal)
        return _public(updated)

    async def list_for(self, kbs: Optional[list[str]]) -> list[dict]:
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (g:FederationGrant)
                WHERE ($kbs IS NULL OR g.source_kb IN $kbs OR g.target_kb IN $kbs)
                RETURN properties(g) AS g ORDER BY g.created_at DESC LIMIT 500
                """,
                kbs=kbs,
            )
            return [_public(dict(record["g"])) async for record in result]
