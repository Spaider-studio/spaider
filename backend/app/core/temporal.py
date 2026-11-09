"""
Bi-temporal visibility for edges and nodes.

Each edge carries two timelines, stored as UTC ISO-8601 strings, which compare
correctly as text:

  valid_from / valid_to      when the fact was true in the world
  recorded_at / expired_at   when this graph believed it

``as_of`` asks "what did we know to be true at T?", so an edge is visible only
if both timelines include T. Edges written before 0.5 have no recorded_at or
valid_from and fall back to created_at.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


def to_utc_iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def edge_visible(var: str) -> str:
    """Cypher predicate: edge ``var`` is current, or was true at ``$as_of``."""
    return f"""(
        CASE WHEN $as_of IS NULL THEN NOT coalesce({var}.superseded, false)
        ELSE coalesce({var}.valid_from, {var}.created_at) <= $as_of
             AND ({var}.valid_to IS NULL OR {var}.valid_to > $as_of)
             AND coalesce({var}.recorded_at, {var}.created_at) <= $as_of
             AND ({var}.expired_at IS NULL OR {var}.expired_at > $as_of)
        END
    )"""


def node_visible(var: str) -> str:
    """Cypher predicate: node ``var`` is current, or existed at ``$as_of``."""
    return f"""(
        CASE WHEN $as_of IS NULL THEN NOT coalesce({var}.superseded, false)
        ELSE {var}.created_at <= $as_of
             AND ({var}.superseded_at IS NULL OR {var}.superseded_at > $as_of)
        END
    )"""
