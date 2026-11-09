"""
Short-lived, single-use tickets for WebSocket and EventSource connections.

Browsers cannot set headers on those handshakes, and an API key in a query
string ends up in access logs and browser history. The client exchanges its key
for a ticket over a normal authenticated request and puts only the ticket in
the URL. Access is checked again when the ticket is redeemed.
"""
from __future__ import annotations

import hashlib
import secrets
from typing import Optional

from app.config import settings
from app.core.authz import INSECURE_PRINCIPAL, Principal
from app.core.kv import get_kv

TICKET_TTL_SECONDS = 30


def _key(ticket: str) -> str:
    return "spaider:ticket:" + hashlib.sha256(ticket.encode()).hexdigest()


async def issue_ticket(principal: Principal) -> str:
    ticket = "tk-" + secrets.token_urlsafe(32)
    await get_kv().set(_key(ticket), principal.id, ex=TICKET_TTL_SECONDS)
    return ticket


async def redeem_ticket(ticket: Optional[str]) -> Optional[Principal]:
    """The ticket's principal, once only. None if unknown, expired, used or disabled."""
    if settings.spaider_insecure_dev:
        return INSECURE_PRINCIPAL
    if not ticket:
        return None
    principal_id = await get_kv().getdel(_key(ticket))
    if principal_id is None:
        return None
    from app.services.identity_service import get_identity_service

    return await get_identity_service().principal_by_id(principal_id)
