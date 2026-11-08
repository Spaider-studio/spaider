"""
Delete API endpoints: GDPR-compliant node deletion with audit logging.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from app.core.authz import Action, Principal, authorize, get_principal
from app.models.responses import DeleteNodeResponse
from app.services.audit_store import AuditStore

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Lazy singletons
# ---------------------------------------------------------------------------

def _get_graph_service():
    from app.services.graph_service import get_graph_service

    return get_graph_service()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.delete("/{node_id}", response_model=DeleteNodeResponse)
async def delete_node(node_id: str, auth: Principal = Depends(get_principal)):
    """
    GDPR killswitch: permanently delete a node and all its relationships.

    Requires the editor role on the KB that owns the node. Every deletion is
    written to the audit log.
    """
    graph = _get_graph_service()

    # Verify the node exists before attempting deletion
    try:
        node = await graph.get_node_by_id(node_id)
    except Exception as exc:
        logger.error("Error looking up node %s: %s", node_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))

    if not node:
        # Same response as a node the caller may not touch.
        if auth.insecure or auth.is_org_admin:
            raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found")
        raise HTTPException(status_code=403, detail="Not permitted on this knowledge base.")
    await authorize(auth, node.agent_id, Action.EDIT)

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
        "agent_id": node.agent_id,
        "actor_id": auth.id,
        "deleted_edges": deleted_edges,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    try:
        await AuditStore(graph._driver).record_event(
            workflow_id=f"delete:{node_id}",
            agent_id=node.agent_id,
            event_type="node_deleted",
            payload=audit_entry,
        )
    except Exception as exc:  # noqa: BLE001 — the delete already happened
        logger.error("Failed to persist delete audit for node %s: %s", node_id, exc)

    logger.info(
        "Node %s (%s) deleted by agent_id=%s, %d edges removed",
        node_id,
        node.label,
        auth.id,
        deleted_edges,
    )

    return DeleteNodeResponse(
        success=True,
        deleted_node_id=node_id,
        deleted_edges=deleted_edges,
        audit_entry=audit_entry,
    )
