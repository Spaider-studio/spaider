"""
Swarm Service: Cross-agent knowledge graph federation.
Manages SwarmConnection nodes in Neo4j and enables scoped cross-agent queries.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field

from app.models.schemas import GraphPayload
from app.services.graph_service import GraphService
from app.services.query_service import QueryService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class SwarmConnectionConfig(BaseModel):
    source_agent_id: str
    target_agent_id: str
    allowed_node_types: Optional[list[str]] = None   # None = all
    allowed_relation_types: Optional[list[str]] = None  # None = all
    description: Optional[str] = None
    expires_at: Optional[str] = None  # ISO 8601 or None for no expiry


class SwarmConnection(BaseModel):
    id: str
    source_agent_id: str
    target_agent_id: str
    allowed_node_types: Optional[list[str]]
    allowed_relation_types: Optional[list[str]]
    description: Optional[str]
    created_at: str
    expires_at: Optional[str]
    active: bool = True


class SwarmQueryResult(BaseModel):
    question: str
    source_agent_id: str
    results: dict[str, dict] = Field(
        default_factory=dict,
        description="target_agent_id -> QueryResult dict",
    )
    merged_subgraph: dict = Field(default_factory=dict)
    errors: dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# SwarmService
# ---------------------------------------------------------------------------

class SwarmService:
    """
    Manages SwarmConnection records in Neo4j and enables federated querying
    across multiple agent graphs with permission and scope enforcement.
    """

    def __init__(self, graph_service: GraphService) -> None:
        self._graph = graph_service

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    async def create_connection(self, config: SwarmConnectionConfig) -> SwarmConnection:
        """
        Create a new SwarmConnection granting source_agent read access to target_agent's graph.
        Stored as a special SwarmConnection node in Neo4j.
        """
        conn_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        async with self._graph._driver.session() as session:
            await session.run(
                """
                CREATE (sc:SwarmConnection {
                    id:                     $id,
                    source_agent_id:        $source_agent_id,
                    target_agent_id:        $target_agent_id,
                    allowed_node_types:     $allowed_node_types,
                    allowed_relation_types: $allowed_relation_types,
                    description:            $description,
                    created_at:             $created_at,
                    expires_at:             $expires_at,
                    active:                 true
                })
                """,
                id=conn_id,
                source_agent_id=config.source_agent_id,
                target_agent_id=config.target_agent_id,
                allowed_node_types=config.allowed_node_types,
                allowed_relation_types=config.allowed_relation_types,
                description=config.description,
                created_at=now,
                expires_at=config.expires_at,
            )

        connection = SwarmConnection(
            id=conn_id,
            source_agent_id=config.source_agent_id,
            target_agent_id=config.target_agent_id,
            allowed_node_types=config.allowed_node_types,
            allowed_relation_types=config.allowed_relation_types,
            description=config.description,
            created_at=now,
            expires_at=config.expires_at,
        )
        logger.info(
            "SwarmConnection created | id=%s src=%s tgt=%s",
            conn_id, config.source_agent_id, config.target_agent_id,
        )
        return connection

    async def revoke_connection(self, connection_id: str) -> None:
        """Deactivate a SwarmConnection (soft delete)."""
        async with self._graph._driver.session() as session:
            await session.run(
                """
                MATCH (sc:SwarmConnection {id: $id})
                SET sc.active = false, sc.revoked_at = $now
                """,
                id=connection_id,
                now=datetime.now(timezone.utc).isoformat(),
            )
        logger.info("SwarmConnection revoked | id=%s", connection_id)

    async def list_connections(self, agent_id: str) -> list[SwarmConnection]:
        """List all active SwarmConnections where agent_id is source or target."""
        async with self._graph._driver.session() as session:
            result = await session.run(
                """
                MATCH (sc:SwarmConnection)
                WHERE (sc.source_agent_id = $agent_id OR sc.target_agent_id = $agent_id)
                  AND sc.active = true
                RETURN sc
                ORDER BY sc.created_at DESC
                """,
                agent_id=agent_id,
            )
            records = await result.data()

        connections: list[SwarmConnection] = []
        for rec in records:
            sc = rec["sc"]
            connections.append(
                SwarmConnection(
                    id=sc["id"],
                    source_agent_id=sc["source_agent_id"],
                    target_agent_id=sc["target_agent_id"],
                    allowed_node_types=sc.get("allowed_node_types"),
                    allowed_relation_types=sc.get("allowed_relation_types"),
                    description=sc.get("description"),
                    created_at=sc["created_at"],
                    expires_at=sc.get("expires_at"),
                    active=sc.get("active", True),
                )
            )
        return connections

    async def validate_access(
        self,
        source_agent_id: str,
        target_agent_id: str,
    ) -> tuple[bool, Optional[SwarmConnection]]:
        """
        Check if source_agent has an active SwarmConnection to target_agent.

        Returns:
            (allowed: bool, connection: Optional[SwarmConnection])
        """
        async with self._graph._driver.session() as session:
            result = await session.run(
                """
                MATCH (sc:SwarmConnection {
                    source_agent_id: $source_agent_id,
                    target_agent_id: $target_agent_id,
                    active: true
                })
                WHERE sc.expires_at IS NULL OR sc.expires_at > $now
                RETURN sc
                LIMIT 1
                """,
                source_agent_id=source_agent_id,
                target_agent_id=target_agent_id,
                now=datetime.now(timezone.utc).isoformat(),
            )
            record = await result.single()

        if not record:
            return False, None

        sc = record["sc"]
        connection = SwarmConnection(
            id=sc["id"],
            source_agent_id=sc["source_agent_id"],
            target_agent_id=sc["target_agent_id"],
            allowed_node_types=sc.get("allowed_node_types"),
            allowed_relation_types=sc.get("allowed_relation_types"),
            description=sc.get("description"),
            created_at=sc["created_at"],
            expires_at=sc.get("expires_at"),
            active=sc.get("active", True),
        )
        return True, connection

    # ------------------------------------------------------------------
    # Federated query
    # ------------------------------------------------------------------

    async def swarm_query(
        self,
        question: str,
        source_agent_id: str,
        target_agent_ids: list[str],
    ) -> SwarmQueryResult:
        """
        Execute a natural-language query against multiple target agent graphs.

        For each target:
          1. Validate permission
          2. Apply scope restrictions (allowed_node_types, allowed_relation_types)
          3. Run the query
          4. Merge results into a combined subgraph

        Args:
            question: Natural-language question.
            source_agent_id: The requesting agent.
            target_agent_ids: List of agent graphs to query.

        Returns:
            SwarmQueryResult with per-target results and merged subgraph.
        """
        result = SwarmQueryResult(
            question=question,
            source_agent_id=source_agent_id,
        )

        merged_nodes: dict[str, dict] = {}
        merged_edges: dict[str, dict] = {}

        for target_id in target_agent_ids:
            allowed, connection = await self.validate_access(source_agent_id, target_id)
            if not allowed:
                logger.warning(
                    "swarm_query: access denied | src=%s tgt=%s", source_agent_id, target_id
                )
                result.errors[target_id] = "Access denied: no active SwarmConnection."
                continue

            try:
                query_service = QueryService(graph_service=self._graph)
                query_result = await query_service.query_nl(
                    question=question,
                    agent_id=target_id,
                )

                # Apply scope restrictions
                scoped_subgraph = self._apply_scope(query_result.subgraph, connection)

                result.results[target_id] = {
                    "answer": query_result.answer,
                    "subgraph": {
                        "nodes": [n.model_dump(exclude={"embedding"}) for n in scoped_subgraph.nodes],
                        "edges": [e.model_dump() for e in scoped_subgraph.edges],
                    },
                    "cypher": query_result.cypher,
                }

                # Merge into combined subgraph
                for node in scoped_subgraph.nodes:
                    merged_nodes[node.id] = node.model_dump(exclude={"embedding"})
                for edge in scoped_subgraph.edges:
                    merged_edges[edge.id] = edge.model_dump()

            except Exception as exc:
                logger.error("swarm_query error for target %s: %s", target_id, exc)
                result.errors[target_id] = str(exc)

        result.merged_subgraph = {
            "nodes": list(merged_nodes.values()),
            "edges": list(merged_edges.values()),
        }

        logger.info(
            "swarm_query | src=%s targets=%s results=%d errors=%d",
            source_agent_id,
            target_agent_ids,
            len(result.results),
            len(result.errors),
        )
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _apply_scope(
        subgraph: GraphPayload,
        connection: SwarmConnection,
    ) -> GraphPayload:
        """
        Filter a subgraph to only include node types and relation types
        permitted by the SwarmConnection scope.
        """
        allowed_nodes = connection.allowed_node_types
        allowed_relations = connection.allowed_relation_types

        filtered_nodes = subgraph.nodes
        if allowed_nodes:
            allowed_lower = {t.lower() for t in allowed_nodes}
            filtered_nodes = [n for n in filtered_nodes if n.type.lower() in allowed_lower]

        allowed_node_ids = {n.id for n in filtered_nodes}

        filtered_edges = subgraph.edges
        if allowed_relations:
            allowed_rel_lower = {r.lower() for r in allowed_relations}
            filtered_edges = [
                e for e in filtered_edges if e.relation.lower() in allowed_rel_lower
            ]

        # Only keep edges where both endpoints are in the filtered node set
        filtered_edges = [
            e for e in filtered_edges
            if e.source_id in allowed_node_ids and e.target_id in allowed_node_ids
        ]

        return GraphPayload(nodes=filtered_nodes, edges=filtered_edges)
