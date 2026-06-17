"""
Synaptic Plasticity Feedback Loop — Hebbian Learning for the V2 Cognitive Graph.

Endpoint
--------
POST /api/v1/system/feedback

Clients submit feedback after a query round-trip: which nodes were actually used
in the answer, and whether the answer was judged successful.  The endpoint accepts
immediately (202) and dispatches the weight update as a FastAPI BackgroundTask so
the response latency is never affected by the Neo4j write.

Hebbian Update Rules
--------------------
  success == true  → utility_weight += 0.1  (cap:   2.0)
  success == false → utility_weight -= 0.1  (floor: 0.1)

All RELATION edges between any two nodes in `used_node_ids` are updated in a
single UNWIND batch transaction.  Neo4j's row-level write locks on each
relationship serialise concurrent updates — no application-level locking needed.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class FeedbackPayload(BaseModel):
    query_id: str = Field(
        description="Correlation ID returned by the query endpoint (or any client-generated UUID).",
    )
    used_node_ids: List[str] = Field(
        min_length=1,
        description="IDs of SpaiderNodes that were part of the RAG context for this query.",
    )
    success: bool = Field(
        description="True if the answer was judged correct/helpful; False otherwise.",
    )

    @field_validator("used_node_ids")
    @classmethod
    def deduplicate_node_ids(cls, v: List[str]) -> List[str]:
        """Remove duplicates while preserving insertion order."""
        seen: set[str] = set()
        return [x for x in v if not (x in seen or seen.add(x))]  # type: ignore[func-returns-value]


class FeedbackResponse(BaseModel):
    accepted: bool
    query_id: str
    message: str
    node_count: int


# ---------------------------------------------------------------------------
# Lazy driver accessor
# ---------------------------------------------------------------------------


def _get_driver():
    from app.services.graph_service import GraphService
    return GraphService()._driver


# ---------------------------------------------------------------------------
# Cypher — batch Hebbian weight update
#
# Strategy: UNWIND × UNWIND to generate all ordered pairs (a, b) where a ≠ b,
# then MATCH the RELATION edge between them (uses Neo4j's relationship index).
# A single transaction covers all edges from one feedback call, minimising the
# lock-acquisition window and eliminating partial-update scenarios.
#
# CASE expression keeps weights within [0.1, 2.0] atomically — no read-then-
# write race possible because Neo4j evaluates and writes within one lock scope.
# ---------------------------------------------------------------------------

_HEBBIAN_UPDATE_CYPHER = """
UNWIND $node_ids AS src_id
UNWIND $node_ids AS tgt_id
WITH src_id, tgt_id
WHERE src_id <> tgt_id
MATCH (a:SpaiderNode {id: src_id})-[r:RELATION]->(b:SpaiderNode {id: tgt_id})
WITH r, coalesce(r.utility_weight, 1.0) AS w
SET r.utility_weight = CASE
    WHEN $success AND w + 0.1 >= 2.0 THEN 2.0
    WHEN $success                     THEN w + 0.1
    WHEN NOT $success AND w - 0.1 <= 0.1 THEN 0.1
    ELSE                                   w - 0.1
END
RETURN count(r) AS updated_count
"""


# ---------------------------------------------------------------------------
# Background task
# ---------------------------------------------------------------------------


async def _apply_hebbian_update(
    query_id: str,
    node_ids: List[str],
    success: bool,
    received_at: str,
) -> None:
    """
    Runs in a FastAPI BackgroundTask — fully decoupled from the HTTP response.

    Finds all RELATION edges between the supplied nodes and nudges their
    utility_weight in the direction indicated by `success`.  Errors are
    logged but never re-raised (background tasks must not crash the worker).
    """
    driver = _get_driver()
    try:
        async with driver.session() as session:
            result = await session.run(
                _HEBBIAN_UPDATE_CYPHER,
                node_ids=node_ids,
                success=success,
            )
            record = await result.single()
            updated = int(record["updated_count"]) if record else 0

        direction = "↑ +0.1" if success else "↓ -0.1"
        logger.info(
            "Hebbian update | query_id=%s success=%s nodes=%d edges_updated=%d weight%s",
            query_id,
            success,
            len(node_ids),
            updated,
            direction,
        )
    except Exception as exc:
        logger.error(
            "Hebbian update FAILED | query_id=%s error=%s",
            query_id,
            exc,
        )


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.post(
    "/feedback",
    response_model=FeedbackResponse,
    status_code=202,
    summary="Submit query feedback for synaptic plasticity update",
)
async def submit_feedback(
    body: FeedbackPayload,
    background_tasks: BackgroundTasks,
):
    """
    Submit feedback for a completed query to trigger Hebbian weight updates.

    The endpoint returns **202 Accepted** immediately.  The actual Neo4j write
    runs as a background task so the client is never blocked on DB latency.

    **Hebbian rules applied to all RELATION edges between `used_node_ids`:**
    - `success = true`  → `utility_weight = min(2.0, weight + 0.1)`
    - `success = false` → `utility_weight = max(0.1, weight - 0.1)`

    In V2 engine mode, edges with `utility_weight < 0.3` are filtered out of
    RAG retrieval (Managed Forgetting), while high-weight edges are prioritised
    at the top of the LLM context window.
    """
    if not body.used_node_ids:
        raise HTTPException(
            status_code=422,
            detail="used_node_ids must contain at least one node ID.",
        )

    received_at = datetime.now(timezone.utc).isoformat()

    background_tasks.add_task(
        _apply_hebbian_update,
        query_id=body.query_id,
        node_ids=body.used_node_ids,
        success=body.success,
        received_at=received_at,
    )

    direction = "reinforced" if body.success else "weakened"
    return FeedbackResponse(
        accepted=True,
        query_id=body.query_id,
        message=(
            f"Feedback accepted. {len(body.used_node_ids)} nodes queued for "
            f"synaptic {direction} update."
        ),
        node_count=len(body.used_node_ids),
    )
