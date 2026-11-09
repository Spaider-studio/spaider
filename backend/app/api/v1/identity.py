"""
Identity API: bootstrap, callers, keys, members, service accounts and groups.

``/auth/bootstrap`` is the only unauthenticated write in the API. It works once,
and only with the operator's ``SPAIDER_BOOTSTRAP_TOKEN``.
"""
from __future__ import annotations

import hmac
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field

from app.config import settings
from app.core.authz import (
    Action,
    KBRole,
    OrgAction,
    OrgRole,
    Principal,
    authorize,
    get_principal,
    require_org,
)
from app.core.tickets import TICKET_TTL_SECONDS, issue_ticket
from app.services.identity_service import (
    AlreadyBootstrapped,
    UnknownPrincipal,
    get_identity_service,
)

public_router = APIRouter()
router = APIRouter()


class BootstrapRequest(BaseModel):
    org_name: str = Field(min_length=1, max_length=200)
    owner_name: str = Field(min_length=1, max_length=200)
    email: Optional[str] = None


class UserCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: Optional[str] = None
    org_role: OrgRole = OrgRole.MEMBER
    clearance_level: int = Field(default=1, ge=1, le=5)


class ServiceAccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    role: KBRole = KBRole.EDITOR
    clearance_level: int = Field(default=1, ge=1, le=5)


class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class RoleAssignment(BaseModel):
    role: KBRole


def _check_clearance(auth: Principal, requested: int) -> None:
    if requested > auth.clearance_level:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Cannot exceed your own clearance level.")


def _not_found(exc: UnknownPrincipal) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, detail="Principal not found in this organization.")


@public_router.post("/auth/bootstrap", status_code=201)
async def bootstrap(
    body: BootstrapRequest,
    x_bootstrap_token: Optional[str] = Header(default=None, alias="X-Bootstrap-Token"),
):
    """Create the first organization and its owner. Works exactly once."""
    expected = settings.spaider_bootstrap_token
    if not expected or not x_bootstrap_token or not hmac.compare_digest(expected, x_bootstrap_token):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Bootstrap is not available.")
    try:
        return await get_identity_service().bootstrap(body.org_name, body.owner_name, body.email)
    except AlreadyBootstrapped as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/auth/whoami")
async def whoami(auth: Principal = Depends(get_principal)):
    return {
        "id": auth.id,
        "kind": auth.kind,
        "name": auth.name,
        "org_id": auth.org_id,
        "org_role": auth.org_role,
        "clearance_level": auth.clearance_level,
        "default_kb": auth.default_kb,
    }


@router.post("/auth/ticket", status_code=201)
async def create_ticket(auth: Principal = Depends(get_principal)):
    """A single-use ticket for one WebSocket or event-stream connection."""
    return {"ticket": await issue_ticket(auth), "expires_in": TICKET_TTL_SECONDS}


@router.post("/auth/rotate")
async def rotate_own_key(auth: Principal = Depends(get_principal)):
    """Replace every key the caller holds with a new one, shown once."""
    if auth.insecure:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="No key exists in insecure dev mode.")
    return {"api_key": await get_identity_service().rotate_key(auth.id)}


@router.get("/principals")
async def list_principals(auth: Principal = Depends(get_principal)):
    await require_org(auth, OrgAction.MANAGE_PRINCIPALS)
    return {"principals": await get_identity_service().list_principals(auth.org_id)}


@router.post("/users", status_code=201)
async def create_user(body: UserCreate, auth: Principal = Depends(get_principal)):
    await require_org(auth, OrgAction.MANAGE_PRINCIPALS)
    _check_clearance(auth, body.clearance_level)
    if body.org_role == OrgRole.SERVICE:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Users cannot hold the service role.")
    if body.org_role == OrgRole.OWNER and auth.org_role != OrgRole.OWNER and not auth.insecure:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Only an owner can create an owner.")
    return await get_identity_service().create_user(
        auth.org_id,
        body.name,
        email=body.email,
        org_role=body.org_role.value,
        clearance_level=body.clearance_level,
        created_by=auth.id,
    )


@router.delete("/principals/{principal_id}")
async def disable_principal(principal_id: str, auth: Principal = Depends(get_principal)):
    """Disable a user or service account and revoke all of its keys."""
    await require_org(auth, OrgAction.MANAGE_PRINCIPALS)
    identity = get_identity_service()
    target_role = await identity.org_role_of(auth.org_id, principal_id)
    if target_role is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Principal not found in this organization.")
    if target_role == OrgRole.OWNER and auth.org_role != OrgRole.OWNER and not auth.insecure:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Only an owner can disable an owner.")
    await identity.disable_principal(auth.org_id, principal_id)
    return {"success": True}


@router.post("/groups", status_code=201)
async def create_group(body: GroupCreate, auth: Principal = Depends(get_principal)):
    await require_org(auth, OrgAction.MANAGE_PRINCIPALS)
    return await get_identity_service().create_group(auth.org_id, body.name, created_by=auth.id)


@router.put("/groups/{group_id}/members/{principal_id}")
async def add_group_member(group_id: str, principal_id: str, auth: Principal = Depends(get_principal)):
    await require_org(auth, OrgAction.MANAGE_PRINCIPALS)
    try:
        await get_identity_service().add_to_group(auth.org_id, group_id, principal_id)
    except UnknownPrincipal as exc:
        raise _not_found(exc) from exc
    return {"success": True}


@router.delete("/groups/{group_id}/members/{principal_id}")
async def remove_group_member(group_id: str, principal_id: str, auth: Principal = Depends(get_principal)):
    await require_org(auth, OrgAction.MANAGE_PRINCIPALS)
    await get_identity_service().remove_from_group(auth.org_id, group_id, principal_id)
    return {"success": True}


@router.get("/agents/{agent_id}/members")
async def list_members(agent_id: str, auth: Principal = Depends(get_principal)):
    await authorize(auth, agent_id, Action.MANAGE)
    return {"members": await get_identity_service().list_members(agent_id)}


@router.put("/agents/{agent_id}/members/{principal_id}")
async def set_member_role(
    agent_id: str,
    principal_id: str,
    body: RoleAssignment,
    auth: Principal = Depends(get_principal),
):
    await authorize(auth, agent_id, Action.MANAGE)
    try:
        await get_identity_service().grant_role(
            auth.org_id, agent_id, principal_id, body.role.value, granted_by=auth.id
        )
    except UnknownPrincipal as exc:
        raise _not_found(exc) from exc
    return {"success": True}


@router.delete("/agents/{agent_id}/members/{principal_id}")
async def remove_member(agent_id: str, principal_id: str, auth: Principal = Depends(get_principal)):
    await authorize(auth, agent_id, Action.MANAGE)
    await get_identity_service().revoke_role(agent_id, principal_id)
    return {"success": True}


@router.post("/agents/{agent_id}/service-accounts", status_code=201)
async def create_service_account(
    agent_id: str,
    body: ServiceAccountCreate,
    auth: Principal = Depends(get_principal),
):
    """Create a service account bound to this KB. Its key is shown once."""
    await authorize(auth, agent_id, Action.MANAGE)
    _check_clearance(auth, body.clearance_level)
    return await get_identity_service().create_service_account(
        auth.org_id,
        agent_id,
        body.name,
        role=body.role.value,
        clearance_level=body.clearance_level,
        created_by=auth.id,
    )
