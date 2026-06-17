"""
NetworkX-based graph analysis utilities.
Functions operate on plain node/edge data (dicts or Pydantic models).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

# NetworkX is an optional dependency — functions degrade gracefully if absent.
try:
    import networkx as nx
    _HAS_NETWORKX = True
except ImportError:
    logger.warning("networkx not installed. Graph algorithm functions will raise ImportError.")
    _HAS_NETWORKX = False

if TYPE_CHECKING:
    pass


def _require_networkx() -> None:
    if not _HAS_NETWORKX:
        raise ImportError(
            "networkx is required for graph algorithms. "
            "Install it with: pip install networkx"
        )


def _build_nx_graph(
    nodes: list[Any],
    edges: list[Any],
) -> "nx.DiGraph":
    """
    Build a NetworkX DiGraph from node/edge objects or dicts.
    Accepts either Pydantic models (with .id) or plain dicts.
    """
    _require_networkx()
    G = nx.DiGraph()

    for node in nodes:
        if hasattr(node, "id"):
            G.add_node(node.id, label=getattr(node, "label", ""), type=getattr(node, "type", ""))
        elif isinstance(node, dict):
            G.add_node(node["id"], **{k: v for k, v in node.items() if k != "id"})

    for edge in edges:
        if hasattr(edge, "source_id"):
            G.add_edge(
                edge.source_id,
                edge.target_id,
                relation=getattr(edge, "relation", ""),
                id=getattr(edge, "id", ""),
            )
        elif isinstance(edge, dict):
            G.add_edge(
                edge["source_id"],
                edge["target_id"],
                **{k: v for k, v in edge.items() if k not in ("source_id", "target_id")},
            )

    return G


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def find_communities(
    nodes: list[Any],
    edges: list[Any],
) -> list[list[str]]:
    """
    Detect communities in the graph using the Louvain algorithm (via networkx-louvain)
    or Girvan-Newman as fallback.

    Args:
        nodes: List of Node objects or dicts with 'id'.
        edges: List of Edge objects or dicts with 'source_id', 'target_id'.

    Returns:
        List of communities, each community being a list of node_ids.
    """
    _require_networkx()
    G = _build_nx_graph(nodes, edges)
    undirected = G.to_undirected()

    if len(undirected.nodes) == 0:
        return []

    try:
        import community as community_louvain  # python-louvain
        partition: dict[str, int] = community_louvain.best_partition(undirected)
        community_map: dict[int, list[str]] = {}
        for node_id, comm_id in partition.items():
            community_map.setdefault(comm_id, []).append(node_id)
        return list(community_map.values())
    except ImportError:
        pass

    # Fallback: connected components
    components = list(nx.connected_components(undirected))
    return [list(c) for c in components]


def compute_centrality(
    nodes: list[Any],
    edges: list[Any],
) -> dict[str, float]:
    """
    Compute PageRank centrality for all nodes.

    Args:
        nodes: List of Node objects or dicts with 'id'.
        edges: List of Edge objects or dicts with 'source_id', 'target_id'.

    Returns:
        Dict mapping node_id -> centrality score (0.0 - 1.0).
    """
    _require_networkx()
    G = _build_nx_graph(nodes, edges)

    if len(G.nodes) == 0:
        return {}

    try:
        return nx.pagerank(G, alpha=0.85)
    except Exception as exc:
        logger.warning("PageRank failed (%s); falling back to degree centrality.", exc)
        return nx.degree_centrality(G)


def find_paths(
    nodes: list[Any],
    edges: list[Any],
    source_id: str,
    target_id: str,
    max_length: int = 5,
) -> list[list[str]]:
    """
    Find all simple paths between two nodes up to max_length hops.

    Args:
        nodes: List of Node objects or dicts.
        edges: List of Edge objects or dicts.
        source_id: Starting node id.
        target_id: Target node id.
        max_length: Maximum path length (number of edges).

    Returns:
        List of paths, each path being a list of node_ids from source to target.
    """
    _require_networkx()
    G = _build_nx_graph(nodes, edges)

    if source_id not in G or target_id not in G:
        logger.debug(
            "find_paths: source %s or target %s not in graph.", source_id, target_id
        )
        return []

    try:
        paths = list(
            nx.all_simple_paths(G, source=source_id, target=target_id, cutoff=max_length)
        )
        return paths
    except nx.NetworkXNoPath:
        return []
    except Exception as exc:
        logger.error("find_paths error: %s", exc)
        return []


def compute_graph_density(node_count: int, edge_count: int) -> float:
    """
    Compute the density of a directed graph.

    Density = edges / (nodes * (nodes - 1))
    Returns 0.0 for graphs with fewer than 2 nodes.

    Args:
        node_count: Total number of nodes.
        edge_count: Total number of directed edges.

    Returns:
        Density value in [0.0, 1.0].
    """
    if node_count < 2:
        return 0.0
    max_edges = node_count * (node_count - 1)
    return min(edge_count / max_edges, 1.0)


def get_node_degrees(
    nodes: list[Any],
    edges: list[Any],
) -> dict[str, dict[str, int]]:
    """
    Compute in-degree, out-degree, and total degree for all nodes.

    Returns:
        Dict mapping node_id -> {"in": int, "out": int, "total": int}
    """
    _require_networkx()
    G = _build_nx_graph(nodes, edges)
    result: dict[str, dict[str, int]] = {}
    for node_id in G.nodes:
        result[node_id] = {
            "in": G.in_degree(node_id),
            "out": G.out_degree(node_id),
            "total": G.degree(node_id),
        }
    return result
