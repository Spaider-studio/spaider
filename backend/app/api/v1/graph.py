"""
Graph API endpoints: retrieve graph data and statistics.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from app.models.responses import GraphResponse
from app.models.schemas import ClusterGraphPayload, GraphStats

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Lazy singleton
# ---------------------------------------------------------------------------

_graph_service = None


def _get_graph_service():
    global _graph_service
    if _graph_service is None:
        from app.services.graph_service import GraphService
        _graph_service = GraphService()
    return _graph_service


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("", response_model=GraphResponse)
async def get_graph(
    agent_id: str = Query(default="default", description="Agent namespace to query"),
    limit: int = Query(
        default=500,
        ge=1,
        le=2000,
        description=(
            "Maximum nodes per page. Hard cap of 2000 prevents OOM on the "
            "backend and browser physics engine. Use offset to page through "
            "larger graphs."
        ),
    ),
    offset: int = Query(default=0, ge=0, description="Node offset for pagination"),
):
    """
    Return a coherent page of nodes and edges for an agent namespace.

    Pagination contract
    -------------------
    * Nodes are ordered deterministically by ``n.id`` so pages are stable
      across concurrent writes.
    * Edges are **only** included when both endpoints fall within the
      requested page — no dangling edge IDs are ever returned.
    * ``limit`` is capped at 2000 (hard server-side guardrail).  Clients
      that need the full graph should iterate via ``offset`` or switch to
      the ``/clusters`` endpoint for an aggregated LOD overview.

    Response envelope
    -----------------
    ``limit`` and ``offset`` are echoed back so clients can construct the
    next-page URL without tracking state:
    ``GET /graph?agent_id=X&limit=500&offset=500``
    """
    graph = _get_graph_service()
    try:
        payload = await graph.get_full_graph(agent_id=agent_id, limit=limit, offset=offset)
        nodes = payload.nodes
        edges = payload.edges

        serialized_nodes = [
            {
                "id": n.id,
                "label": n.label,
                "type": n.type,
                "properties": n.properties,
                "agent_id": n.agent_id,
                "created_at": n.created_at.isoformat() if hasattr(n, "created_at") and n.created_at else None,
            }
            for n in nodes
        ]

        serialized_edges = [
            {
                "id": e.id,
                # ``source``/``target`` carry node ids (force-graph convention,
                # consumed by the frontend canvas); ``source_id``/``target_id``
                # are emitted too so the shape matches the /query subgraph.
                "source": e.source_id,
                "target": e.target_id,
                "source_id": e.source_id,
                "target_id": e.target_id,
                "relation": e.relation,
                "type": e.relation,   # WebGL filter contract: link.type must equal the relation label
                "properties": e.properties,
                "agent_id": e.agent_id,
            }
            for e in edges
        ]

        return GraphResponse(
            nodes=serialized_nodes,
            edges=serialized_edges,
            node_count=len(nodes),
            edge_count=len(edges),
            agent_id=agent_id,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        logger.exception("Error retrieving graph for agent_id=%s: %s", agent_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/multiverse", response_model=GraphResponse)
async def get_multiverse_graph(
    limit: int = Query(default=2000, ge=1, le=10000, description="Max nodes to return"),
):
    """
    Return the full multiverse: every SystemAgent gravity-centre node,
    every SpaiderNode across all agents, plus RELATION and BELONGS_TO_AGENT edges.
    Used by the 3D galaxy / Neural Multiverse frontend view.
    """
    graph = _get_graph_service()
    try:
        payload = await graph.get_all_agents_graph(limit=limit)

        serialized_nodes = [
            {
                "id": n.id,
                "label": n.label,
                "type": n.type,
                "properties": n.properties,
                "agent_id": n.agent_id,
            }
            for n in payload.nodes
        ]
        serialized_edges = [
            {
                "id": e.id,
                "source": e.source_id,
                "target": e.target_id,
                "relation": e.relation,
                "type": e.relation,          # WebGL filter contract: link.type must equal the relation label
                "properties": e.properties,
                "agent_id": e.agent_id,
            }
            for e in payload.edges
        ]

        return GraphResponse(
            nodes=serialized_nodes,
            edges=serialized_edges,
            node_count=len(payload.nodes),
            edge_count=len(payload.edges),
            agent_id="multiverse",
        )
    except Exception as exc:
        logger.exception("Error building multiverse graph: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/clusters", response_model=ClusterGraphPayload)
async def get_graph_clusters(
    agent_id: str = Query(default="default", description="Agent namespace to cluster"),
    zoom_level: int = Query(
        default=0,
        ge=0,
        le=3,
        description=(
            "Aggregation granularity.  0 = one cluster per node type "
            "(coarse overview, fastest). Higher values reserved for future "
            "sub-community refinement."
        ),
    ),
):
    """
    Return a level-of-detail overview of the agent's graph.

    Groups nodes by type into a small number of clusters; each cluster carries
    its member count (→ sphere size) and up to 10 sample ids (→ drill-down).
    Designed for million-node scale — the payload stays bounded by the number
    of distinct node types, regardless of total graph size.
    """
    graph = _get_graph_service()
    try:
        return await graph.get_graph_clusters(agent_id=agent_id, zoom_level=zoom_level)
    except Exception as exc:
        logger.exception("Error clustering graph for agent_id=%s: %s", agent_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/stats", response_model=GraphStats)
async def get_graph_stats(
    agent_id: str = Query(default="default", description="Agent namespace"),
):
    """
    Return aggregate statistics for the agent's graph:
    node count, edge count, type distributions, and graph density.

    Uses server-side Cypher aggregation (``get_graph_stats``) instead of
    materialising the full graph into Python memory — safe on graphs with
    millions of nodes.
    """
    graph = _get_graph_service()
    try:
        stats = await graph.get_graph_stats(agent_id=agent_id)

        node_count = stats["node_count"]
        edge_count = stats["edge_count"]

        # get_graph_stats returns node_types as a list of distinct type strings.
        # Build a type→count distribution via a second focused Cypher call if
        # needed; for now surface the flat counts the service already provides.
        type_distribution: dict[str, int] = {
            t: 0 for t in (stats.get("node_types") or [])
        }

        # Relation distribution is not returned by get_graph_stats (it only
        # returns relation *types*, not counts) — use get_schema to stay
        # memory-safe rather than loading all edges.
        schema = await graph.get_schema(agent_id=agent_id)
        relation_distribution: dict[str, int] = {
            r: 0 for r in (schema.get("relation_types") or [])
        }

        # Graph density: edges / (nodes * (nodes - 1)) for directed graph
        max_edges = node_count * (node_count - 1) if node_count > 1 else 1
        density = round(edge_count / max_edges, 6) if max_edges > 0 else 0.0

        return GraphStats(
            node_count=node_count,
            edge_count=edge_count,
            type_distribution=type_distribution,
            relation_distribution=relation_distribution,
            density=density,
            agent_id=agent_id,
        )
    except Exception as exc:
        logger.exception("Error computing stats for agent_id=%s: %s", agent_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))
