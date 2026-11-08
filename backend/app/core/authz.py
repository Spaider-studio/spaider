"""
Who may do what.

Tenancy is Org > Workspace > KB, where a KB is a ``SystemAgent`` node.
``authorize`` is the single decision point for anything touching a KB, and the
API router requires ``get_principal`` on every route, so a handler that forgets
its check still cannot be reached anonymously.

Denials read the same whether the KB is forbidden or does not exist, so a
caller cannot probe for other tenants' KB ids. Org admins get a 404 for a
missing KB because they may already see every KB in their org.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from fastapi import Header, HTTPException, Request, status

from app.config import settings
from app.logging_context import agent_id_var

logger = logging.getLogger(__name__)


class Action(str, Enum):
    READ = "kb.read"
    WRITE = "kb.write"
    EDIT = "kb.edit"
    MANAGE = "kb.manage"
    AUDIT = "kb.audit"


class OrgAction(str, Enum):
    CREATE_KB = "org.create_kb"
    MANAGE_PRINCIPALS = "org.manage_principals"
    ADMIN = "org.admin"
    AUDIT = "org.audit"


class KBRole(str, Enum):
    READER = "reader"
    CONTRIBUTOR = "contributor"
    EDITOR = "editor"
    STEWARD = "steward"
    AUDITOR = "auditor"


class OrgRole(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    AUDITOR = "auditor"
    # Service accounts belong to an org but hold no org-level powers.
    SERVICE = "service"


# The auditor reads the audit trail but not the knowledge itself.
KB_ROLE_ACTIONS: dict[str, frozenset[Action]] = {
    KBRole.READER: frozenset({Action.READ}),
    KBRole.CONTRIBUTOR: frozenset({Action.READ, Action.WRITE}),
    KBRole.EDITOR: frozenset({Action.READ, Action.WRITE, Action.EDIT}),
    KBRole.STEWARD: frozenset({Action.READ, Action.WRITE, Action.EDIT, Action.MANAGE, Action.AUDIT}),
    KBRole.AUDITOR: frozenset({Action.AUDIT}),
}

ORG_ROLE_ACTIONS: dict[str, frozenset[OrgAction]] = {
    OrgRole.OWNER: frozenset(OrgAction),
    OrgRole.ADMIN: frozenset(OrgAction),
    OrgRole.MEMBER: frozenset({OrgAction.CREATE_KB}),
    OrgRole.AUDITOR: frozenset({OrgAction.AUDIT}),
    OrgRole.SERVICE: frozenset(),
}

_ORG_WIDE_KB_ROLES = (OrgRole.OWNER, OrgRole.ADMIN)
_DENIED = "Not permitted on this knowledge base."


@dataclass(frozen=True)
class Principal:
    id: str
    kind: str  # "user" | "service_account"
    org_id: str
    org_role: str
    clearance_level: int = 1
    default_kb: Optional[str] = None
    name: str = ""
    insecure: bool = False

    @property
    def is_org_admin(self) -> bool:
        return self.org_role in _ORG_WIDE_KB_ROLES


INSECURE_PRINCIPAL = Principal(
    id="insecure-dev",
    kind="user",
    org_id="dev",
    org_role=OrgRole.OWNER,
    clearance_level=5,
    name="insecure-dev",
    insecure=True,
)


def _driver():
    from app.services.graph_service import get_graph_service

    return get_graph_service()._driver


def _api_key_from_headers(x_api_key: Optional[str], authorization: Optional[str]) -> Optional[str]:
    if x_api_key:
        return x_api_key
    if authorization and authorization[:7].lower() == "bearer ":
        return authorization[7:].strip() or None
    return None


async def get_principal(
    request: Request,
    x_api_key: Optional[str] = Header(default=None, alias="X-Api-Key"),
    authorization: Optional[str] = Header(default=None),
) -> Principal:
    """FastAPI dependency: the authenticated caller, or 401."""
    if settings.spaider_insecure_dev:
        request.state.principal = INSECURE_PRINCIPAL
        return INSECURE_PRINCIPAL

    principal = await resolve_token(_api_key_from_headers(x_api_key, authorization))
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid API key.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    request.state.principal = principal
    agent_id_var.set(principal.default_kb or principal.id)
    return principal


async def resolve_token(token: Optional[str]) -> Optional[Principal]:
    """Principal for a raw API key, None if absent, unknown or unusable."""
    if settings.spaider_insecure_dev:
        return INSECURE_PRINCIPAL
    if not token:
        return None
    from app.services.identity_service import get_identity_service

    try:
        return await get_identity_service().resolve_key(token)
    except Exception as exc:  # noqa: BLE001 — fail closed on any lookup error
        logger.warning("API key lookup failed: %s", type(exc).__name__)
        return None


async def _kb_roles(principal: Principal, agent_id: str) -> tuple[bool, set[str]]:
    """Whether the KB exists in the caller's org, and the caller's roles on it."""
    async with _driver().session() as session:
        result = await session.run(
            """
            OPTIONAL MATCH (kb:SystemAgent {agent_id: $kb})-[:IN_WORKSPACE]->(:Workspace)
                           -[:IN_ORG]->(:Org {id: $org})
            WITH kb
            OPTIONAL MATCH (:Principal {id: $pid})-[:IN_GROUP*0..1]->(:Principal)-[r:HAS_ROLE]->(kb)
            RETURN kb IS NOT NULL AS exists, collect(DISTINCT r.role) AS roles
            """,
            kb=agent_id,
            org=principal.org_id,
            pid=principal.id,
        )
        record = await result.single()
    return bool(record["exists"]), set(record["roles"])


def _org_grants(principal: Principal, action: Action) -> bool:
    if principal.is_org_admin:
        return True
    return principal.org_role == OrgRole.AUDITOR and action == Action.AUDIT


async def can(principal: Principal, agent_id: str, action: Action) -> bool:
    if principal.insecure:
        return True
    exists, roles = await _kb_roles(principal, agent_id)
    if not exists:
        return False
    if _org_grants(principal, action):
        return True
    return any(action in KB_ROLE_ACTIONS.get(role, frozenset()) for role in roles)


async def authorize(principal: Principal, agent_id: str, action: Action) -> None:
    """Raise unless ``principal`` may perform ``action`` on the KB."""
    if principal.insecure:
        return
    exists, roles = await _kb_roles(principal, agent_id)
    if exists and (
        _org_grants(principal, action)
        or any(action in KB_ROLE_ACTIONS.get(role, frozenset()) for role in roles)
    ):
        return
    if not exists and principal.is_org_admin:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Knowledge base not found.")
    raise HTTPException(status.HTTP_403_FORBIDDEN, detail=_DENIED)


async def require_org(principal: Principal, action: OrgAction) -> None:
    if principal.insecure:
        return
    if action not in ORG_ROLE_ACTIONS.get(principal.org_role, frozenset()):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Not permitted in this organization.")


async def accessible_kbs(principal: Principal, action: Action = Action.READ) -> Optional[list[str]]:
    """KB ids the caller may act on. ``None`` means no restriction (insecure dev)."""
    if principal.insecure:
        return None
    async with _driver().session() as session:
        if _org_grants(principal, action):
            result = await session.run(
                """
                MATCH (kb:SystemAgent)-[:IN_WORKSPACE]->(:Workspace)-[:IN_ORG]->(:Org {id: $org})
                RETURN kb.agent_id AS id
                """,
                org=principal.org_id,
            )
        else:
            roles = [role.value for role, actions in KB_ROLE_ACTIONS.items() if action in actions]
            result = await session.run(
                """
                MATCH (:Principal {id: $pid})-[:IN_GROUP*0..1]->(:Principal)
                      -[r:HAS_ROLE]->(kb:SystemAgent)-[:IN_WORKSPACE]->(:Workspace)
                      -[:IN_ORG]->(:Org {id: $org})
                WHERE r.role IN $roles
                RETURN DISTINCT kb.agent_id AS id
                """,
                pid=principal.id,
                org=principal.org_id,
                roles=roles,
            )
        return [record["id"] async for record in result]


def resolve_kb(principal: Principal, agent_id: Optional[str]) -> Optional[str]:
    """The KB a request targets: explicit, or the caller's default.

    Returns None only in insecure dev; otherwise a request with neither is 422.
    """
    target = agent_id or principal.default_kb
    if target is None and not principal.insecure:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="agent_id is required: this key has no default knowledge base.",
        )
    return target
