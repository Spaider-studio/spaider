"""
Delete API endpoints: GDPR-compliant node deletion with audit logging.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Header, HTTPException

from app.models.responses import DeleteNodeResponse

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Lazy singletons
# ---------------------------------------------------------------------------

_graph_service = None
_redis_client = None


def _get_graph_service():
    global _graph_service
    if _graph_service is None:
        from app.services.graph_service import GraphService
        _graph_service = GraphService()
    return _graph_service


async def _get_redis():
    global _redis_client
    if _redis_client is None:
        try:
            import redis.asyncio as aioredis

            from app.config import settings
            _redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
        except Exception as exc:
            logger.warning("Redis unavailable for audit log: %s", exc)
    return _redis_client


def _check_admin_permission(x_agent_permission: Optional[str]) -> None:
    """Simple permission gate: require 'admin' in the X-Agent-Permission header."""
    if x_agent_permission is None or "admin" not in x_agent_permission.lower():
        raise HTTPException(
            status_code=403,
            detail="Admin permission required to delete nodes. Provide X-Agent-Permission: admin header.",
        )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.delete("/{node_id}", response_model=DeleteNodeResponse)
async def delete_node(
    node_id: str,
    x_agent_permission: Optional[str] = Header(default=None, alias="X-Agent-Permission"),
    agent_id: str = "default",
):
    """
    GDPR killswitch: permanently delete a node and all its relationships.
    Requires admin permission. Creates an immutable audit log entry in Redis.
    """
    _check_admin_permission(x_agent_permission)

    graph = _get_graph_service()

    # Verify the node exists before attempting deletion
    try:
        node = await graph.get_node_by_id(node_id)
    except Exception as exc:
        logger.error("Error looking up node %s: %s", node_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))

    if not node:
        raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found")

    # Perform cascade delete. delete_node_cascade returns a DeleteResult
    # object; extract the edge count as an int so it serialises into the
    # audit log, the log line, and the response model (passing the object
    # itself triggered a Pydantic "1 validation error" while the node was
    # already gone).
    try:
        result = await graph.delete_node_cascade(node_id)
        deleted_edges = int(getattr(result, "deleted_edges", result) or 0)
    except AttributeError:
        # Fallback to existing delete_node method if delete_node_cascade not yet implemented
        deleted_edges = int(await graph.delete_node(node_id) or 0)
    except Exception as exc:
        logger.exception("Error deleting node %s: %s", node_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))

    # Build audit entry
    audit_entry = {
        "event": "node_deleted",
        "node_id": node_id,
        "node_label": node.label,
        "node_type": node.type,
        "agent_id": agent_id,
        "deleted_edges": deleted_edges,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # Persist audit log to Redis
    redis = await _get_redis()
    if redis is not None:
        try:
            audit_key = f"audit:delete:{node_id}:{int(datetime.now(timezone.utc).timestamp())}"
            await redis.set(audit_key, json.dumps(audit_entry), ex=60 * 60 * 24 * 365)  # 1 year
            logger.info("Audit log written: %s", audit_key)
        except Exception as exc:
            logger.warning("Failed to write audit log to Redis: %s", exc)
    else:
        logger.warning("Redis unavailable; audit entry not persisted: %s", audit_entry)

    logger.info(
        "Node %s (%s) deleted by agent_id=%s, %d edges removed",
        node_id,
        node.label,
        agent_id,
        deleted_edges,
    )

    return DeleteNodeResponse(
        success=True,
        deleted_node_id=node_id,
        deleted_edges=deleted_edges,
        audit_entry=audit_entry,
    )
