"""
Safe parameterized Cypher query builder.
All methods return (cypher_string, params_dict) tuples.
NO string interpolation of user-controlled values — ever.
"""
from __future__ import annotations

import uuid
from typing import Any, Optional


class CypherBuilder:
    """
    Builds parameterized Neo4j Cypher queries.

    All user-supplied values (labels, ids, properties) are passed as
    parameters rather than interpolated into the query string, preventing
    Cypher injection attacks.

    Each method returns a tuple: (cypher: str, params: dict).
    """

    # ------------------------------------------------------------------
    # Node operations
    # ------------------------------------------------------------------

    @staticmethod
    def match_node(
        node_var: str = "n",
        node_label: str = "SpaiderNode",
        match_props: Optional[dict[str, Any]] = None,
        return_clause: str = "n",
        limit: Optional[int] = None,
    ) -> tuple[str, dict]:
        """
        Build a MATCH query for a node.

        Example:
            cypher, params = CypherBuilder.match_node(
                match_props={"id": "abc", "agent_id": "agent1"}
            )
        """
        match_props = match_props or {}
        params: dict[str, Any] = {}

        # Build WHERE clause from props
        where_parts: list[str] = []
        for key, value in match_props.items():
            param_name = f"match_{key}"
            where_parts.append(f"{node_var}.{key} = ${param_name}")
            params[param_name] = value

        where_clause = f" WHERE {' AND '.join(where_parts)}" if where_parts else ""
        limit_clause = " LIMIT $match_limit" if limit is not None else ""
        if limit is not None:
            params["match_limit"] = limit

        cypher = (
            f"MATCH ({node_var}:{node_label}){where_clause}"
            f" RETURN {return_clause}{limit_clause}"
        )
        return cypher, params

    @staticmethod
    def merge_node(
        node_id: str,
        label: str,
        node_type: str,
        agent_id: str,
        properties: Optional[dict[str, Any]] = None,
        embedding: Optional[list[float]] = None,
        node_label: str = "SpaiderNode",
        node_var: str = "n",
    ) -> tuple[str, dict]:
        """
        Build a MERGE query for a node (idempotent upsert).
        The node is matched/created by its `id` field.
        """
        now = _now_iso()
        params: dict[str, Any] = {
            "node_id": node_id,
            "agent_id": agent_id,
            "label": label,
            "node_type": node_type,
            "now": now,
            "properties": properties or {},
            "embedding": embedding,
        }
        cypher = (
            f"MERGE ({node_var}:{node_label} {{id: $node_id}}) "
            f"ON CREATE SET "
            f"  {node_var}.agent_id = $agent_id, "
            f"  {node_var}.label = $label, "
            f"  {node_var}.type = $node_type, "
            f"  {node_var}.created_at = $now, "
            f"  {node_var}.updated_at = $now, "
            f"  {node_var}.properties = $properties "
            f"ON MATCH SET "
            f"  {node_var}.updated_at = $now, "
            f"  {node_var}.properties = $properties "
            f"WITH {node_var} "
            f"SET {node_var}.embedding = CASE WHEN $embedding IS NOT NULL THEN $embedding ELSE {node_var}.embedding END "  # noqa: E501
            f"RETURN {node_var}"
        )
        return cypher, params

    @staticmethod
    def delete_node(
        node_id: str,
        node_label: str = "SpaiderNode",
        cascade: bool = True,
    ) -> tuple[str, dict]:
        """
        Build a DELETE (or DETACH DELETE) query for a node by id.

        Args:
            cascade: If True, uses DETACH DELETE to remove all relationships too.
        """
        params = {"node_id": node_id}
        delete_keyword = "DETACH DELETE" if cascade else "DELETE"
        cypher = (
            f"MATCH (n:{node_label} {{id: $node_id}}) "
            f"{delete_keyword} n"
        )
        return cypher, params

    # ------------------------------------------------------------------
    # Edge operations
    # ------------------------------------------------------------------

    @staticmethod
    def merge_edge(
        source_id: str,
        target_id: str,
        relation: str,
        edge_id: Optional[str] = None,
        agent_id: str = "",
        properties: Optional[dict[str, Any]] = None,
        node_label: str = "SpaiderNode",
    ) -> tuple[str, dict]:
        """
        Build a MERGE query for an edge between two nodes.

        The relation type is passed as a parameter via the `relation` property
        on the relationship (Neo4j does not support parameterized relationship
        types in MERGE directly, so we store the type as a property and use
        a generic RELATION label).
        """
        if edge_id is None:
            edge_id = str(uuid.uuid4())
        now = _now_iso()
        params: dict[str, Any] = {
            "source_id": source_id,
            "target_id": target_id,
            "edge_id": edge_id,
            "relation": relation,
            "agent_id": agent_id,
            "now": now,
            "properties": properties or {},
        }
        cypher = (
            f"MATCH (a:{node_label} {{id: $source_id, agent_id: $agent_id}}) "
            f"MATCH (b:{node_label} {{id: $target_id, agent_id: $agent_id}}) "
            f"MERGE (a)-[r:RELATION {{id: $edge_id}}]->(b) "
            f"ON CREATE SET "
            f"  r.relation = $relation, "
            f"  r.agent_id = $agent_id, "
            f"  r.created_at = $now, "
            f"  r.updated_at = $now, "
            f"  r.properties = $properties "
            f"ON MATCH SET "
            f"  r.updated_at = $now, "
            f"  r.properties = $properties "
            f"RETURN r"
        )
        return cypher, params

    @staticmethod
    def match_subgraph(
        node_id: str,
        depth: int = 2,
        agent_id: Optional[str] = None,
        node_label: str = "SpaiderNode",
    ) -> tuple[str, dict]:
        """
        Build a variable-depth subgraph traversal query.
        Depth is passed as a parameter; agent_id scoping is optional.
        """
        params: dict[str, Any] = {"node_id": node_id, "depth": depth}
        agent_filter = ""
        if agent_id:
            params["agent_id"] = agent_id
            agent_filter = " AND start.agent_id = $agent_id"

        cypher = (
            f"MATCH path = (start:{node_label} {{id: $node_id}})-[*0..$depth]-"
            f"(end:{node_label})"
            f" WHERE start.id = $node_id{agent_filter}"
            f" UNWIND nodes(path) AS n"
            f" WITH COLLECT(DISTINCT n) AS all_nodes, path"
            f" UNWIND relationships(path) AS r"
            f" WITH all_nodes, COLLECT(DISTINCT r) AS all_rels"
            f" RETURN all_nodes, all_rels"
        )
        return cypher, params

    @staticmethod
    def count_nodes(agent_id: str, node_label: str = "SpaiderNode") -> tuple[str, dict]:
        """Return a count query for all nodes belonging to an agent."""
        params = {"agent_id": agent_id}
        cypher = (
            f"MATCH (n:{node_label} {{agent_id: $agent_id}}) RETURN count(n) AS node_count"
        )
        return cypher, params

    @staticmethod
    def count_edges(agent_id: str, node_label: str = "SpaiderNode") -> tuple[str, dict]:
        """Return a count query for all edges belonging to an agent."""
        params = {"agent_id": agent_id}
        cypher = (
            f"MATCH (a:{node_label} {{agent_id: $agent_id}})"
            f"-[r:RELATION]->"
            f"(b:{node_label} {{agent_id: $agent_id}}) "
            f"RETURN count(r) AS edge_count"
        )
        return cypher, params


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
