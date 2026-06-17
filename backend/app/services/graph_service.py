"""
Graph Service: Full Neo4j implementation with connection pooling,
vector index, parameterized Cypher, and GDPR-compliant deletion.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from neo4j import AsyncDriver, AsyncGraphDatabase

from app.config import settings
from app.models.schemas import (
    ClusterGraphPayload,
    Edge,
    GraphCluster,
    GraphClusterEdge,
    GraphPayload,
    Node,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Vector search tuning constants
# ---------------------------------------------------------------------------

# db.index.vector.queryNodes returns the global top-k nearest neighbors across
# ALL tenants.  The WHERE node.agent_id = $agent_id post-filter then discards
# irrelevant tenants, which means the caller receives far fewer than top_k
# results on a multi-tenant database.
#
# Fix: overfetch candidate_k = top_k * OVERFETCH_FACTOR from the ANN index,
# post-filter by agent_id, then LIMIT to top_k.  The ANN traversal is still
# fully index-backed — we only widen the candidate window.
#
# Tuning: set to the expected maximum number of concurrent tenants in the DB.
# Over-estimating is safe (slightly more ANN work, same result quality);
# under-estimating causes result starvation on large deployments.
_VECTOR_OVERFETCH_FACTOR: int = 50


# ---------------------------------------------------------------------------
# Temporal / append-only node versioning (Tier-3 Memory Architecture,
# Goal B.1 — see ARCHITECTURE_PROPOSAL.md).
#
# When MEMORY_VERSIONING_ENABLED is true, every UPDATE to an existing
# SpaiderNode first clones the node's pre-update state into a new
# :NodeVersion node and links it via (mainNode)-[:PREVIOUS_VERSION]->(:NodeVersion).
# When the flag is false (default), behavior is byte-identical to current
# main — the legacy Cypher path is selected and no :NodeVersion nodes are
# ever produced. This lets the foundation ship safely without forcing a
# global schema migration.
#
# Read-path safety: the FTS index spaider_label_fulltext and the vector
# index spaider_embedding are both `FOR (n:SpaiderNode)` (see initialize()
# at ~line 214 / 230). Neither index will return :NodeVersion nodes, so
# retrieve_swarm_context_v2 cannot accidentally surface archived snapshots.
# The :PREVIOUS_VERSION relationship is a distinct type from :RELATION, so
# graph traversals that walk RELATION edges also stay clean.
# ---------------------------------------------------------------------------

_MEMORY_VERSIONING_ENABLED: bool = (
    os.environ.get("MEMORY_VERSIONING_ENABLED", "false").lower() == "true"
)

# Versioning prelude — prepended to the node-write Cypher when the flag
# is on. Archives the pre-update snapshot of any existing node before
# the MERGE's ON MATCH branch overwrites it.
#
# Cypher mechanics:
#   1. OPTIONAL MATCH finds the existing node (or returns NULL).
#   2. FOREACH conditionally runs the archive only when existing IS NOT NULL —
#      this is the canonical Cypher idiom for "execute when bound".
#   3. CREATE (not MERGE) ensures every update produces a NEW :NodeVersion
#      with its own version_id, so re-running the same write does not
#      collapse versions.
#   4. WITH row drops the `existing` binding before the main MERGE so the
#      subsequent MERGE picks a fresh node binding cleanly.
_VERSIONING_PRELUDE: str = """
    OPTIONAL MATCH (existing:SpaiderNode {id: row.id})
    FOREACH (_ IN CASE WHEN existing IS NOT NULL THEN [1] ELSE [] END |
        CREATE (v:NodeVersion {
            version_id:   randomUUID(),
            original_id:  existing.id,
            agent_id:     existing.agent_id,
            label:        existing.label,
            type:         existing.type,
            description:  existing.description,
            properties:   existing.properties,
            embedding:    existing.embedding,
            created_at:   existing.created_at,
            updated_at:   existing.updated_at,
            archived_at:  $now
        })
        CREATE (existing)-[:PREVIOUS_VERSION {archived_at: $now}]->(v)
    )
    WITH row
"""

# Common tail of the node-write Cypher — identical between legacy and
# versioned paths. Extracted as a constant so the two write functions
# in this module share a single source of truth.
_NODE_WRITE_TAIL: str = """
    MERGE (n:SpaiderNode {id: row.id})
    ON CREATE SET
        n.agent_id    = row.agent_id,
        n.label       = row.label,
        n.type        = row.type,
        n.created_at  = $now,
        n.updated_at  = $now,
        n.properties  = row.properties
    ON MATCH SET
        n.updated_at  = $now,
        n.properties  = row.properties
    WITH n, row,
        CASE WHEN n.created_at = $now THEN 1 ELSE 0 END AS was_created
    SET n.embedding   = CASE WHEN row.embedding IS NOT NULL THEN row.embedding ELSE n.embedding END
    SET n.description = COALESCE(row.description, n.description)
    SET n.source_text = COALESCE(row.source_text, n.source_text)
    RETURN sum(was_created) AS created, sum(1 - was_created) AS merged
"""


def _node_write_cypher() -> str:
    """Return the node-write Cypher gated by the versioning flag.

    When MEMORY_VERSIONING_ENABLED=true, prepend the archive prelude so
    every update produces a :NodeVersion clone of the prior state. When
    false, return the legacy single-MERGE Cypher unchanged — production
    behavior is byte-identical to pre-Tier-3 main.
    """
    head = "UNWIND $rows AS row\n"
    if _MEMORY_VERSIONING_ENABLED:
        return head + _VERSIONING_PRELUDE + _NODE_WRITE_TAIL
    return head + _NODE_WRITE_TAIL


class VectorIndexUnavailableError(RuntimeError):
    """
    Raised when semantic vector search is requested but the Neo4j
    `spaider_embedding` vector index is missing.

    The ingest pipeline writes node embeddings unconditionally, so the
    correct remedy is to create the index in Neo4j and let it populate —
    not to fall back to loading thousands of nodes into Python memory.
    """


def _node_text_fields(node: "Node") -> tuple[Optional[str], Optional[str]]:
    """Return ``(description, source_text)`` for a node about to be written.

    Ingest paths historically buried both inside the ``properties`` dict,
    leaving the top-level Neo4j columns NULL — which made the fulltext index
    (label/description/source_text) blind to the actual fact text and broke
    retrieval recall. Promote them out of properties at the single write
    choke point so every ingest path gets searchable columns.
    """
    props = node.properties or {}
    description = node.description or props.get("description") or None
    source_text = props.get("source_text") or None
    return description, source_text


def _flatten_properties(props: dict) -> dict:
    """Flatten a properties dict so all values are Neo4j-compatible primitives."""
    flat = {}
    for k, v in props.items():
        if v is None:
            continue
        elif isinstance(v, (str, int, float, bool)):
            flat[k] = v
        elif isinstance(v, list) and all(isinstance(i, (str, int, float, bool)) for i in v):
            flat[k] = v
        else:
            flat[k] = json.dumps(v)
    return flat


# ---------------------------------------------------------------------------
# Response data classes (inline to avoid circular imports)
# ---------------------------------------------------------------------------

class WriteResult:
    def __init__(
        self,
        nodes_created: int,
        nodes_merged: int,
        edges_created: int,
        edges_merged: int,
    ) -> None:
        self.nodes_created = nodes_created
        self.nodes_merged = nodes_merged
        self.edges_created = edges_created
        self.edges_merged = edges_merged

    def to_dict(self) -> dict:
        return {
            "nodes_created": self.nodes_created,
            "nodes_merged": self.nodes_merged,
            "edges_created": self.edges_created,
            "edges_merged": self.edges_merged,
        }


class DeleteResult:
    def __init__(self, deleted_nodes: int, deleted_edges: int) -> None:
        self.deleted_nodes = deleted_nodes
        self.deleted_edges = deleted_edges

    def to_dict(self) -> dict:
        return {
            "deleted_nodes": self.deleted_nodes,
            "deleted_edges": self.deleted_edges,
        }


# ---------------------------------------------------------------------------
# GraphService
# ---------------------------------------------------------------------------

class GraphService:
    """
    Async Neo4j graph service with:
    - Connection pooling via the official neo4j driver
    - Parameterized Cypher (NO string interpolation for user data)
    - Agent-scoped graph operations
    - Vector index for semantic search
    - GDPR-compliant cascade deletion
    """

    def __init__(self) -> None:
        self._driver: AsyncDriver = AsyncGraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
            max_connection_pool_size=50,
        )
        # Flipped True by initialize() once the vector index is confirmed
        # present (either pre-existing or freshly created on an empty DB).
        # When False, vector_search() raises VectorIndexUnavailableError
        # instead of silently degrading to a 5000-node in-memory scan.
        self.vector_index_available: bool = False

    async def ping(self) -> None:
        """Verify the Neo4j connection is alive."""
        async with self._driver.session() as session:
            await session.run("RETURN 1")

    async def close(self) -> None:
        await self._driver.close()

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """
        Create constraints + indexes on startup and verify the vector index.

        Vector index handling (hybrid, Option C):
          • Empty database       → auto-create the vector index; safe because
                                   no embeddings exist yet and index build is
                                   instantaneous.
          • Populated + present  → verify and continue.
          • Populated + missing  → refuse to auto-create (would block startup
                                   behind a long index build). Log a loud
                                   error, leave `vector_index_available=False`
                                   so /health reports `vector_index:
                                   unavailable` and queries 503 instead of
                                   silently returning wrong results.

        Indexes created (idempotent, IF NOT EXISTS):
          spaider_node_id        — UNIQUE constraint on SpaiderNode.id
                                   → O(1) MERGE lookup by primary key
          system_agent_id        — UNIQUE constraint on SystemAgent.agent_id
          spaider_agent_label    — Composite B-Tree (agent_id, label)
                                   → fast exact/alias lookup within a tenant
          spaider_node_type      — B-Tree on SpaiderNode.type
          spaider_agent_id       — Standalone B-Tree on SpaiderNode.agent_id
                                   → O(log N) tenant isolation without a label
                                   predicate (required by get_full_graph,
                                   delete_agent_graph, vector post-filter)
          spaider_label_fulltext — Full-text index on (label, description)
                                   → O(log N + hits) keyword search, replacing
                                   O(N) toLower(n.label) CONTAINS scans
          spaider_embedding      — Vector ANN index (managed separately below)
        """
        async with self._driver.session() as session:
            # ── Uniqueness constraints ────────────────────────────────────
            await session.run(
                "CREATE CONSTRAINT spaider_node_id IF NOT EXISTS "
                "FOR (n:SpaiderNode) REQUIRE n.id IS UNIQUE"
            )
            await session.run(
                "CREATE CONSTRAINT system_agent_id IF NOT EXISTS "
                "FOR (a:SystemAgent) REQUIRE a.agent_id IS UNIQUE"
            )

            # ── B-Tree indexes ────────────────────────────────────────────
            # Composite (agent_id, label): exact/alias lookup within a tenant
            await session.run(
                "CREATE INDEX spaider_agent_label IF NOT EXISTS "
                "FOR (n:SpaiderNode) ON (n.agent_id, n.label)"
            )
            # Type index: filter by entity type within any agent namespace
            await session.run(
                "CREATE INDEX spaider_node_type IF NOT EXISTS "
                "FOR (n:SpaiderNode) ON (n.type)"
            )
            # Standalone agent_id index: prevents full-graph scans when
            # querying by tenant without a label predicate (e.g. get_full_graph,
            # delete_agent_graph, vector post-filter WHERE node.agent_id = …)
            await session.run(
                "CREATE INDEX spaider_agent_id IF NOT EXISTS "
                "FOR (n:SpaiderNode) ON (n.agent_id)"
            )

            # ── Full-text index ───────────────────────────────────────────
            # Replaces O(N) toLower(n.label) CONTAINS … scans with an inverted
            # posting-list lookup.  Covers label, description AND source_text:
            # most ingested facts carry their content in source_text, and an
            # index that only sees labels can't match questions phrased around
            # the fact ("when does the domain expire") rather than the entity
            # name. Recreate the index when an older two-field definition is
            # found so existing deployments pick up source_text on reboot.
            try:
                result = await session.run(
                    "SHOW FULLTEXT INDEXES YIELD name, properties "
                    "WHERE name = 'spaider_label_fulltext' RETURN properties"
                )
                record = await result.single()
                existing_fields = record["properties"] if record else None
                if (
                    isinstance(existing_fields, (list, tuple))
                    and "source_text" not in existing_fields
                ):
                    await session.run("DROP INDEX spaider_label_fulltext IF EXISTS")
                    logger.info(
                        "Dropped stale spaider_label_fulltext (missing source_text); recreating."
                    )
            except Exception as exc:
                logger.warning("Fulltext index definition check skipped: %s", exc)
            await session.run(
                "CREATE FULLTEXT INDEX spaider_label_fulltext IF NOT EXISTS "
                "FOR (n:SpaiderNode) ON EACH [n.label, n.description, n.source_text]"
            )

            # ── Vector index (requires Neo4j >= 5.11) ────────────────────
            index_present = await self._vector_index_exists(session)
            if index_present:
                self.vector_index_available = True
                logger.info("Vector index 'spaider_embedding' present.")
            else:
                db_empty = await self._database_is_empty(session)
                if db_empty:
                    try:
                        await session.run(
                            """
                            CREATE VECTOR INDEX spaider_embedding IF NOT EXISTS
                            FOR (n:SpaiderNode) ON (n.embedding)
                            OPTIONS {indexConfig: {
                                `vector.dimensions`: $dims,
                                `vector.similarity_function`: 'cosine'
                            }}
                            """,
                            dims=settings.embedding_dimensions,
                        )
                        self.vector_index_available = True
                        logger.warning(
                            "Vector index 'spaider_embedding' was missing on an "
                            "empty database; auto-created with dims=%d.",
                            settings.embedding_dimensions,
                        )
                    except Exception as exc:
                        self.vector_index_available = False
                        logger.error(
                            "Failed to create vector index on empty DB "
                            "(Neo4j < 5.11 or plugin missing?): %s. "
                            "Semantic search will return 503 until the index "
                            "is created manually.",
                            exc,
                        )
                else:
                    self.vector_index_available = False
                    logger.error(
                        "Vector index 'spaider_embedding' is MISSING on a "
                        "non-empty database. Refusing to auto-create (index "
                        "build could block startup for minutes). Semantic "
                        "search will return 503 until an operator runs: "
                        "CREATE VECTOR INDEX spaider_embedding FOR "
                        "(n:SpaiderNode) ON (n.embedding) OPTIONS "
                        "{indexConfig: {`vector.dimensions`: %d, "
                        "`vector.similarity_function`: 'cosine'}}",
                        settings.embedding_dimensions,
                    )

        logger.info(
            "GraphService initialized: constraints and indexes created "
            "(vector_index_available=%s).",
            self.vector_index_available,
        )

    @staticmethod
    async def _vector_index_exists(session) -> bool:
        """True iff an index named `spaider_embedding` is reported by SHOW INDEXES."""
        result = await session.run(
            "SHOW INDEXES YIELD name WHERE name = 'spaider_embedding' RETURN name"
        )
        record = await result.single()
        return record is not None

    @staticmethod
    async def _database_is_empty(session) -> bool:
        """True iff there are zero SpaiderNodes (safe proxy for 'fresh DB')."""
        result = await session.run(
            "MATCH (n:SpaiderNode) RETURN count(n) AS c LIMIT 1"
        )
        record = await result.single()
        return bool(record is None or (record["c"] or 0) == 0)

    async def create_agent_node(self, agent_id: str, name: str) -> None:
        """Idempotent MERGE: ensures a SystemAgent gravity-centre node exists in Neo4j."""
        async with self._driver.session() as session:
            await session.run(
                """
                MERGE (a:SystemAgent {agent_id: $agent_id})
                ON CREATE SET a.name = $name, a.created_at = $now
                ON MATCH  SET a.name = $name
                """,
                agent_id=agent_id,
                name=name,
                now=datetime.now(timezone.utc).isoformat(),
            )
        logger.debug("SystemAgent node ensured for agent_id=%s", agent_id)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def write_graph_batch(
        self, items: list[tuple[GraphPayload, str]]
    ) -> WriteResult:
        """
        Write multiple (GraphPayload, agent_id) pairs in a SINGLE Neo4j transaction.
        Used by the Kafka micro-batcher to flush many messages at once.
        Each row carries its own agent_id so mixed-agent batches are safe.
        """
        now = datetime.now(timezone.utc).isoformat()
        node_rows: list[dict] = []
        edge_rows: list[dict] = []

        for payload, agent_id in items:
            for node in payload.nodes:
                description, source_text = _node_text_fields(node)
                node_rows.append({
                    "id": node.id,
                    "agent_id": agent_id,
                    "label": node.label,
                    "type": node.type,
                    "description": description,
                    "source_text": source_text,
                    "properties": json.dumps(_flatten_properties(node.properties or {})),
                    "embedding": node.embedding,
                })
            for edge in payload.edges:
                edge_rows.append({
                    "source_id": edge.source_id,
                    "target_id": edge.target_id,
                    "edge_id": edge.id,
                    "relation": edge.relation,
                    "agent_id": agent_id,
                    "properties": json.dumps(_flatten_properties(edge.properties or {})),
                })

        nodes_created = nodes_merged = edges_created = edges_merged = 0

        async with self._driver.session() as session:
            if node_rows:
                # Cypher selection is gated by MEMORY_VERSIONING_ENABLED.
                # When off (default), this is identical to the pre-Tier-3
                # path. When on, the prelude archives every pre-update
                # snapshot into a :NodeVersion before the MERGE overwrites
                # it. See module-level docs for the read-path safety
                # analysis (FTS / vector indexes are :SpaiderNode-scoped).
                result = await session.run(
                    _node_write_cypher(),
                    rows=node_rows,
                    now=now,
                )
                rec = await result.single()
                if rec:
                    nodes_created = rec["created"] or 0
                    nodes_merged  = rec["merged"]  or 0

            if edge_rows:
                result = await session.run(
                    """
                    UNWIND $rows AS row
                    MATCH (a:SpaiderNode {id: row.source_id, agent_id: row.agent_id})
                    MATCH (b:SpaiderNode {id: row.target_id, agent_id: row.agent_id})
                    MERGE (a)-[r:RELATION {id: row.edge_id}]->(b)
                    ON CREATE SET
                        r.relation   = row.relation,
                        r.agent_id   = row.agent_id,
                        r.created_at = $now,
                        r.updated_at = $now,
                        r.properties = row.properties
                    ON MATCH SET
                        r.updated_at = $now,
                        r.properties = row.properties
                    WITH r,
                        CASE WHEN r.created_at = $now THEN 1 ELSE 0 END AS was_created
                    RETURN sum(was_created) AS created, sum(1 - was_created) AS merged
                    """,
                    rows=edge_rows,
                    now=now,
                )
                rec = await result.single()
                if rec:
                    edges_created = rec["created"] or 0
                    edges_merged  = rec["merged"]  or 0

            # Bind every SpaiderNode to its SystemAgent gravity centre (idempotent).
            # MATCH (not MERGE) on the SystemAgent — agents must exist before
            # any write reaches this path; auto-creating one was the source of
            # the phantom "default" agent that appeared in the multiverse when
            # an ingest endpoint was called with a missing agent_id and
            # silently fell back to its `Form(default="default")`.
            for aid in {agent_id for _, agent_id in items}:
                await session.run(
                    """
                    MATCH (agent:SystemAgent {agent_id: $agent_id})
                    WITH agent
                    MATCH (n:SpaiderNode {agent_id: $agent_id})
                    MERGE (n)-[:BELONGS_TO_AGENT]->(agent)
                    """,
                    agent_id=aid,
                )

        logger.info(
            "write_graph_batch | items=%d nodes_created=%d nodes_merged=%d "
            "edges_created=%d edges_merged=%d",
            len(items), nodes_created, nodes_merged, edges_created, edges_merged,
        )
        return WriteResult(nodes_created, nodes_merged, edges_created, edges_merged)

    async def write_graph(self, payload: GraphPayload, agent_id: str) -> WriteResult:
        """
        Persist nodes and edges to Neo4j with agent_id scoping.
        Uses UNWIND batch queries — one round-trip for all nodes, one for all edges.
        """
        now = datetime.now(timezone.utc).isoformat()

        node_rows = []
        for node in payload.nodes:
            description, source_text = _node_text_fields(node)
            node_rows.append({
                "id": node.id,
                "agent_id": agent_id,
                "label": node.label,
                "type": node.type,
                "description": description,
                "source_text": source_text,
                "properties": json.dumps(_flatten_properties(node.properties or {})),
                "embedding": node.embedding,
            })

        edge_rows = [
            {
                "source_id": edge.source_id,
                "target_id": edge.target_id,
                "edge_id": edge.id,
                "relation": edge.relation,
                "agent_id": agent_id,
                "properties": json.dumps(_flatten_properties(edge.properties or {})),
            }
            for edge in payload.edges
        ]

        nodes_created = 0
        nodes_merged = 0
        edges_created = 0
        edges_merged = 0

        async with self._driver.session() as session:
            if node_rows:
                # Shares _node_write_cypher() with write_graph_batch — both
                # paths produce identical schema and both pick up versioning
                # behavior in lock-step when MEMORY_VERSIONING_ENABLED is on.
                result = await session.run(
                    _node_write_cypher(),
                    rows=node_rows,
                    now=now,
                )
                rec = await result.single()
                if rec:
                    nodes_created = rec["created"] or 0
                    nodes_merged = rec["merged"] or 0

            if edge_rows:
                result = await session.run(
                    """
                    UNWIND $rows AS row
                    MATCH (a:SpaiderNode {id: row.source_id, agent_id: row.agent_id})
                    MATCH (b:SpaiderNode {id: row.target_id, agent_id: row.agent_id})
                    MERGE (a)-[r:RELATION {id: row.edge_id}]->(b)
                    ON CREATE SET
                        r.relation   = row.relation,
                        r.agent_id   = row.agent_id,
                        r.created_at = $now,
                        r.updated_at = $now,
                        r.properties = row.properties
                    ON MATCH SET
                        r.updated_at = $now,
                        r.properties = row.properties
                    WITH r,
                        CASE WHEN r.created_at = $now THEN 1 ELSE 0 END AS was_created
                    RETURN sum(was_created) AS created, sum(1 - was_created) AS merged
                    """,
                    rows=edge_rows,
                    now=now,
                )
                rec = await result.single()
                if rec:
                    edges_created = rec["created"] or 0
                    edges_merged = rec["merged"] or 0

            # Bind every SpaiderNode to its SystemAgent gravity centre (idempotent).
            # MATCH (not MERGE) — see write_graph_batch for the same fix.
            await session.run(
                """
                MATCH (agent:SystemAgent {agent_id: $agent_id})
                WITH agent
                MATCH (n:SpaiderNode {agent_id: $agent_id})
                MERGE (n)-[:BELONGS_TO_AGENT]->(agent)
                """,
                agent_id=agent_id,
            )

        logger.info(
            "write_graph | agent=%s nodes_created=%d nodes_merged=%d "
            "edges_created=%d edges_merged=%d",
            agent_id, nodes_created, nodes_merged, edges_created, edges_merged,
        )
        return WriteResult(nodes_created, nodes_merged, edges_created, edges_merged)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get_full_graph(
        self,
        agent_id: str,
        limit: int = 500,
        offset: int = 0,
    ) -> GraphPayload:
        """
        Return a coherent page of the agent's graph.

        Graph-safe pagination strategy
        --------------------------------
        Naively applying SKIP/LIMIT to a path pattern ``(n)-[r]->(m)`` is a
        fatal anti-pattern: Neo4j limits the *path stream*, producing edges
        whose endpoints may lie outside the current page — a structurally
        incoherent subgraph.

        The correct approach is:

          1. Page the *nodes* first with a deterministic ``ORDER BY n.id``
             so repeated calls produce stable, non-overlapping pages.
          2. Collect the page into a list bound to a single variable.
          3. UNWIND and OPTIONAL MATCH edges only between nodes that are
             *both* members of the page (``WHERE m IN paged_nodes``).

        This guarantees every returned edge has both its source and target
        present in the same payload — no dangling edges, no phantom node IDs.

        Result shape
        ------------
        A single Cypher query returns one row per (node, optional edge) pair.
        Nodes with k in-page out-edges appear k times; nodes with no in-page
        out-edges appear once with all r_* columns NULL.  Python deduplicates
        nodes via a seen-id set while collecting edges from non-NULL rows.
        """
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (n:SpaiderNode {agent_id: $agent_id})
                WITH n ORDER BY n.id SKIP $offset LIMIT $limit
                WITH collect(n) AS paged_nodes
                UNWIND paged_nodes AS n
                OPTIONAL MATCH (n)-[r:RELATION]->(m) WHERE m IN paged_nodes
                RETURN
                    n.id           AS id,
                    n.label        AS label,
                    n.type         AS type,
                    n.description  AS description,
                    n.properties   AS properties,
                    n.embedding    AS embedding,
                    n.agent_id     AS agent_id,
                    r.id           AS r_id,
                    r.relation     AS r_relation,
                    r.properties   AS r_properties,
                    r.agent_id     AS r_agent_id,
                    m.id           AS tgt,
                    coalesce(r.utility_weight, 1.0) AS utility_weight
                """,
                agent_id=agent_id,
                offset=offset,
                limit=limit,
            )
            records = await result.data()

        seen_node_ids: set[str] = set()
        nodes: list[Node] = []
        edges: list[Edge] = []

        for rec in records:
            # Deduplicate nodes — a node with k out-edges produces k rows
            node_id = rec["id"]
            if node_id not in seen_node_ids:
                seen_node_ids.add(node_id)
                nodes.append(self._record_to_node(rec))

            # Only materialise an edge when the OPTIONAL MATCH found one
            if rec["r_id"] is not None:
                edge_rec = {
                    "id":           rec["r_id"],
                    "relation":     rec["r_relation"],
                    "properties":   rec["r_properties"],
                    "agent_id":     rec["r_agent_id"],
                    "utility_weight": rec["utility_weight"],
                }
                edges.append(self._record_to_edge(edge_rec, node_id, rec["tgt"]))

        logger.debug(
            "get_full_graph | agent=%s offset=%d limit=%d → nodes=%d edges=%d",
            agent_id, offset, limit, len(nodes), len(edges),
        )
        return GraphPayload(nodes=nodes, edges=edges)

    async def get_graph_clusters(
        self,
        agent_id: str,
        zoom_level: int = 0,
    ) -> ClusterGraphPayload:
        """
        Return a pre-aggregated LOD overview of the agent's graph.

        Nodes are grouped by their `type` (PERSON, ORGANIZATION, …); each cluster
        carries its member count plus up to 10 representative ids that the
        frontend can use for drill-down.  Edges are collapsed to source-type →
        target-type buckets with occurrence counts.

        Single round-trip, two Cypher queries, no additional indexes required.
        Suitable for graphs with 100k+ nodes where shipping the full payload
        would exceed browser memory or crash the force-graph simulation.

        `zoom_level` is reserved for future refinement (sub-community splits).
        At zoom_level=0 a single cluster per type is returned.
        """
        async with self._driver.session() as session:
            cluster_result = await session.run(
                """
                MATCH (n:SpaiderNode {agent_id: $agent_id})
                WHERE NOT n:SystemAgent
                WITH coalesce(n.type, 'OTHER') AS cluster_type,
                     count(n)                  AS node_count,
                     collect(n.id)[..10]       AS sample_ids
                RETURN cluster_type, node_count, sample_ids
                ORDER BY node_count DESC
                """,
                agent_id=agent_id,
            )
            cluster_rows = await cluster_result.data()

            edge_result = await session.run(
                """
                MATCH (a:SpaiderNode {agent_id: $agent_id})-[r:RELATION]->(b:SpaiderNode {agent_id: $agent_id})
                WHERE NOT a:SystemAgent AND NOT b:SystemAgent
                WITH coalesce(a.type, 'OTHER') AS src_type,
                     coalesce(b.type, 'OTHER') AS tgt_type,
                     count(r)                  AS rel_count
                RETURN src_type, tgt_type, rel_count
                ORDER BY rel_count DESC
                """,
                agent_id=agent_id,
            )
            edge_rows = await edge_result.data()

        clusters = [
            GraphCluster(
                id=f"cluster:{row['cluster_type']}",
                label=row["cluster_type"],
                type=row["cluster_type"],
                node_count=row["node_count"],
                sample_node_ids=row["sample_ids"] or [],
            )
            for row in cluster_rows
        ]

        cluster_edges = [
            GraphClusterEdge(
                id=f"cluster-edge:{row['src_type']}->{row['tgt_type']}",
                source_cluster_id=f"cluster:{row['src_type']}",
                target_cluster_id=f"cluster:{row['tgt_type']}",
                count=row["rel_count"],
            )
            for row in edge_rows
        ]

        total_nodes = sum(c.node_count for c in clusters)
        total_edges = sum(e.count for e in cluster_edges)

        return ClusterGraphPayload(
            clusters=clusters,
            cluster_edges=cluster_edges,
            total_nodes=total_nodes,
            total_edges=total_edges,
            zoom_level=zoom_level,
            agent_id=agent_id,
        )

    async def get_node_by_id(self, node_id: str) -> Optional[Node]:
        """Fetch a specific node by its id."""
        async with self._driver.session() as session:
            result = await session.run(
                "MATCH (n:SpaiderNode {id: $node_id}) RETURN properties(n) AS p",
                node_id=node_id,
            )
            record = await result.single()
            if not record:
                return None
            return self._record_to_node(record)

    async def set_node_description(self, node_id: str, description: str) -> bool:
        """Overwrite a node's description. Used by the summariser specialist.

        Returns True if a node matched, False otherwise.
        """
        now = datetime.now(timezone.utc).isoformat()
        async with self._driver.session() as session:
            result = await session.run(
                "MATCH (n:SpaiderNode {id: $node_id}) "
                "SET n.description = $description, n.updated_at = $now "
                "RETURN count(n) AS updated",
                node_id=node_id, description=description, now=now,
            )
            rec = await result.single()
            return bool(rec and rec["updated"])

    async def set_node_type(self, node_id: str, node_type: str) -> bool:
        """Overwrite a node's type. Used by the classifier specialist.

        Returns True if a node matched, False otherwise.
        """
        now = datetime.now(timezone.utc).isoformat()
        async with self._driver.session() as session:
            result = await session.run(
                "MATCH (n:SpaiderNode {id: $node_id}) "
                "SET n.type = $node_type, n.updated_at = $now "
                "RETURN count(n) AS updated",
                node_id=node_id, node_type=node_type, now=now,
            )
            rec = await result.single()
            return bool(rec and rec["updated"])

    async def get_subgraph(self, node_id: str, depth: int = 2) -> GraphPayload:
        """Retrieve the subgraph surrounding a node up to given traversal depth."""
        # Neo4j 5.x does not allow parameters in variable-length quantifiers,
        # so inline the depth value directly into the query string.
        d = max(0, int(depth))
        async with self._driver.session() as session:
            node_result = await session.run(
                f"""
                MATCH path = (start:SpaiderNode {{id: $node_id}})-[*0..{d}]-(end:SpaiderNode)
                UNWIND nodes(path) AS n
                RETURN DISTINCT n.id AS id, n.label AS label, n.type AS type,
                       n.description AS description,
                       n.properties AS properties, n.embedding AS embedding,
                       n.agent_id AS agent_id, n.clearance_level AS clearance_level
                """,
                node_id=node_id,
            )
            node_records = await node_result.data()

            edge_result = await session.run(
                f"""
                MATCH path = (start:SpaiderNode {{id: $node_id}})-[*0..{d}]-(end:SpaiderNode)
                UNWIND relationships(path) AS r
                RETURN DISTINCT r.id AS id, r.relation AS relation,
                       r.properties AS properties, r.agent_id AS agent_id,
                       startNode(r).id AS src, endNode(r).id AS tgt,
                       coalesce(r.utility_weight, 1.0) AS utility_weight
                """,
                node_id=node_id,
            )
            edge_records = await edge_result.data()

        if not node_records:
            return GraphPayload()

        nodes = [self._record_to_node(rec) for rec in node_records]
        edges = [self._record_to_edge(rec, rec["src"], rec["tgt"]) for rec in edge_records]
        return GraphPayload(nodes=nodes, edges=edges)

    async def search_nodes(
        self,
        query: str,
        agent_id: str,
        limit: int = 10,
    ) -> list[Node]:
        """Full-text label/type search for nodes belonging to an agent."""
        if not query:
            # Return all nodes (up to limit) when query is empty
            async with self._driver.session() as session:
                result = await session.run(
                    """
                    MATCH (n:SpaiderNode {agent_id: $agent_id})
                    RETURN n.id AS id, n.label AS label, n.type AS type,
                           properties(n) AS node_props
                    LIMIT $limit
                    """,
                    agent_id=agent_id,
                    limit=limit,
                )
                records = await result.data()
            return [self._record_to_node(rec) for rec in records]

        # Build a Lucene prefix query so partial terms like "alic" match "Alice".
        # Special Lucene characters are not escaped here because user input goes
        # through a controlled API (not a public search bar), and Neo4j's FTS
        # parser is lenient.  Trailing * enables prefix matching.
        ft_query = f"{query}*"

        async with self._driver.session() as session:
            result = await session.run(
                """
                CALL db.index.fulltext.queryNodes("spaider_label_fulltext", $ft_query)
                YIELD node AS n, score
                WHERE n.agent_id = $agent_id
                RETURN n.id AS id, n.label AS label, n.type AS type,
                       n.description AS description,
                       n.properties AS properties, n.embedding AS embedding,
                       n.agent_id AS agent_id
                ORDER BY score DESC
                LIMIT $limit
                """,
                ft_query=ft_query,
                agent_id=agent_id,
                limit=limit,
            )
            records = await result.data()
        return [self._record_to_node(rec) for rec in records]

    async def list_nodes_for_resolver(
        self,
        agent_id: str,
    ) -> list[Node]:
        """List all of an agent's SpaiderNodes WITHOUT embeddings.

        the EntityResolver previously called ``search_nodes
        (query="", limit=2000)`` which capped at 2K, hauled embeddings
        across the wire, and was the dominant per-fact cost as graphs
        grew. The resolver only needs labels + aliases for strategies
        1-3 (exact / alias / fuzzy Levenshtein); semantic-match
        (strategy 4) is now handled per-new-node via the existing
        ``vector_search`` Neo4j vector-index probe.

        This call returns the full label-set with NO 2K cap and NO
        embedding payload — bytes per node drop from ~12 KB (1536-dim
        float embedding) to ~200 B (label + aliases). 10 KB → 50 KB
        even on a 250 KB / 35K-node agent, safe to fetch in full.
        """
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (n:SpaiderNode {agent_id: $agent_id})
                RETURN n.id AS id,
                       n.label AS label,
                       n.type AS type,
                       n.description AS description,
                       n.properties AS properties,
                       n.agent_id AS agent_id,
                       n.clearance_level AS clearance_level
                """,
                agent_id=agent_id,
            )
            records = await result.data()
        # Skip the embedding column; resolver doesn't need it for
        # strategies 1-3, and strategy 4 will fetch candidates via
        # vector_search.
        nodes: list[Node] = []
        for rec in records:
            node = self._record_to_node(rec)
            node.embedding = None  # explicit — saves resolver thinking
            nodes.append(node)
        return nodes

    async def vector_search(
        self,
        embedding: list[float],
        agent_id: str,
        top_k: int = 10,
    ) -> list[Node]:
        """
        Find top-k semantically similar nodes using the Neo4j vector index.

        Pre-filter strategy (overfetch)
        ---------------------------------
        Neo4j 5.x ``db.index.vector.queryNodes`` has no native metadata
        pre-filter parameter — it always returns the *global* top-k ANN
        candidates across all tenants before any WHERE clause is applied.
        On a multi-tenant database the post-filter ``WHERE node.agent_id =
        $agent_id`` discards most candidates, leaving the caller with far
        fewer than ``top_k`` results.

        The fix: request ``top_k × _VECTOR_OVERFETCH_FACTOR`` candidates from
        the index (widening the ANN window), then filter by ``agent_id`` and
        ``LIMIT $top_k``.  The traversal is still fully index-backed — the
        extra candidates are evaluated in the ANN graph, not as a table scan.

        Clearance filtering is intentionally absent here: this method is called
        concurrently with ``_get_agent_clearance()`` inside ``asyncio.gather``,
        so the clearance value is not yet known.  Callers (``retrieve_swarm_context``
        / ``retrieve_swarm_context_v2``) apply the Diplomat Protocol filter on
        the seed nodes before building the LLM context.

        Raises
        ------
        VectorIndexUnavailableError
            When the ``spaider_embedding`` index is missing.  Callers should
            surface this as HTTP 503 so the operator can recreate the index.
        """
        if not self.vector_index_available:
            raise VectorIndexUnavailableError(
                "Neo4j vector index 'spaider_embedding' is not available. "
                "Check /health for details."
            )

        candidate_k = top_k * _VECTOR_OVERFETCH_FACTOR

        async with self._driver.session() as session:
            result = await session.run(
                """
                CALL db.index.vector.queryNodes('spaider_embedding', $candidate_k, $embedding)
                YIELD node, score
                WHERE node.agent_id = $agent_id
                RETURN node.id AS id, node.label AS label, node.type AS type,
                       node.description AS description,
                       node.properties AS properties, node.embedding AS embedding,
                       node.agent_id AS agent_id,
                       node.clearance_level AS clearance_level, score
                ORDER BY score DESC
                LIMIT $top_k
                """,
                candidate_k=candidate_k,
                embedding=embedding,
                agent_id=agent_id,
                top_k=top_k,
            )
            records = await result.data()
        return [self._record_to_node(rec) for rec in records]

    async def connect_agents(self, source_agent_id: str, target_agent_id: str) -> str:
        """
        Create (or confirm) a SHARES_KNOWLEDGE_WITH relationship between two SystemAgent nodes.
        Returns the relationship type string on success.
        Raises ValueError if either SystemAgent node does not exist in Neo4j.
        """
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (a:SystemAgent {agent_id: $source}), (b:SystemAgent {agent_id: $target})
                MERGE (a)-[r:SHARES_KNOWLEDGE_WITH]->(b)
                RETURN type(r) AS link_type
                """,
                source=source_agent_id,
                target=target_agent_id,
            )
            record = await result.single()
            if record is None:
                raise ValueError(
                    f"One or both SystemAgent nodes not found: "
                    f"source='{source_agent_id}' target='{target_agent_id}'"
                )
            link_type: str = record["link_type"]
            logger.info(
                "Synaptic bridge established: %s -[%s]-> %s",
                source_agent_id, link_type, target_agent_id,
            )
            return link_type

    async def get_all_agents_graph(self, limit: int = 2000) -> GraphPayload:
        """
        Multiverse view: all SystemAgent cores + all SpaiderNodes + RELATION + BELONGS_TO_AGENT edges.
        Used by the /graph/multiverse endpoint and the 3D galaxy frontend.
        """
        async with self._driver.session() as session:
            # All SpaiderNodes (entity nodes)
            node_result = await session.run(
                """
                MATCH (n:SpaiderNode)
                RETURN n.id AS id, n.label AS label, n.type AS type,
                       n.description AS description,
                       n.properties AS properties, n.agent_id AS agent_id
                LIMIT $limit
                """,
                limit=limit,
            )
            node_records = await node_result.data()

            # SystemAgent gravity-centre nodes
            agent_result = await session.run(
                """
                MATCH (a:SystemAgent)
                RETURN a.agent_id AS id,
                       coalesce(a.name, a.agent_id) AS label,
                       a.agent_id AS agent_id
                """
            )
            agent_records = await agent_result.data()

            # RELATION edges between SpaiderNodes
            edge_result = await session.run(
                """
                MATCH (a:SpaiderNode)-[r:RELATION]->(b:SpaiderNode)
                RETURN r.id AS id, r.relation AS relation,
                       r.properties AS properties, r.agent_id AS agent_id,
                       a.id AS src, b.id AS tgt,
                       coalesce(r.utility_weight, 1.0) AS utility_weight
                LIMIT $limit
                """,
                limit=limit,
            )
            edge_records = await edge_result.data()

            # BELONGS_TO_AGENT edges (SpaiderNode → SystemAgent)
            bta_result = await session.run(
                """
                MATCH (n:SpaiderNode)-[:BELONGS_TO_AGENT]->(a:SystemAgent)
                RETURN n.id + '__bta__' + a.agent_id AS id,
                       n.id AS src, a.agent_id AS tgt, n.agent_id AS agent_id
                LIMIT $limit
                """,
                limit=limit,
            )
            bta_records = await bta_result.data()

            # SHARES_KNOWLEDGE_WITH edges (SystemAgent → SystemAgent synaptic bridges)
            skw_result = await session.run(
                """
                MATCH (a:SystemAgent)-[r:SHARES_KNOWLEDGE_WITH]->(b:SystemAgent)
                RETURN a.agent_id + '__skw__' + b.agent_id AS id,
                       a.agent_id AS src, b.agent_id AS tgt
                """
            )
            skw_records = await skw_result.data()

        # Build node list — SpaiderNodes first, then SystemAgent cores
        nodes: list[Node] = [self._record_to_node(rec) for rec in node_records]
        for rec in agent_records:
            nodes.append(Node(
                id=rec["id"],
                label=rec["label"],
                type="agent_core",
                properties={},
                agent_id=rec["agent_id"],
            ))

        # Build edge list — RELATION edges, then BELONGS_TO_AGENT, then SHARES_KNOWLEDGE_WITH
        edges: list[Edge] = [
            self._record_to_edge(rec, rec["src"], rec["tgt"])
            for rec in edge_records
        ]
        for rec in bta_records:
            edges.append(Edge(
                id=rec["id"],
                source_id=rec["src"],
                target_id=rec["tgt"],
                relation="BELONGS_TO_AGENT",
                properties={},
                agent_id=None,
            ))
        for rec in skw_records:
            edges.append(Edge(
                id=rec["id"],
                source_id=rec["src"],
                target_id=rec["tgt"],
                relation="SHARES_KNOWLEDGE_WITH",
                properties={},
                agent_id=None,
            ))

        logger.info(
            "get_all_agents_graph | nodes=%d (cores=%d) edges=%d (bta=%d skw=%d)",
            len(nodes), len(agent_records), len(edges),
            len(bta_records), len(skw_records),
        )
        return GraphPayload(nodes=nodes, edges=edges)

    # ------------------------------------------------------------------
    # Delete (GDPR killswitch)
    # ------------------------------------------------------------------

    async def delete_node_cascade(self, node_id: str) -> DeleteResult:
        """DETACH DELETE a single node and all its relationships."""
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (n:SpaiderNode {id: $node_id})
                OPTIONAL MATCH (n)-[r]-()
                WITH n, count(r) AS edge_count
                DETACH DELETE n
                RETURN edge_count
                """,
                node_id=node_id,
            )
            record = await result.single()
        deleted_edges = record["edge_count"] if record else 0
        logger.info("delete_node_cascade | node_id=%s edges_deleted=%d", node_id, deleted_edges)
        return DeleteResult(deleted_nodes=1, deleted_edges=deleted_edges)

    async def delete_agent_graph(self, agent_id: str) -> DeleteResult:
        """GDPR killswitch: delete all agent data, including SystemAgent core."""
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (n:SpaiderNode {agent_id: $agent_id})
                OPTIONAL MATCH (n)-[r]-()
                WITH collect(DISTINCT n) AS nodes, count(DISTINCT r) AS edge_count
                FOREACH (n IN nodes | DETACH DELETE n)
                WITH size(nodes) AS node_count, edge_count
                OPTIONAL MATCH (a:SystemAgent {agent_id: $agent_id})
                WITH node_count, edge_count, collect(a) AS cores
                FOREACH (a IN cores | DETACH DELETE a)
                RETURN node_count, edge_count
                """,
                agent_id=agent_id,
            )
            record = await result.single()
        deleted_nodes = record["node_count"] if record else 0
        deleted_edges = record["edge_count"] if record else 0
        logger.info(
            "delete_agent_graph | agent_id=%s nodes=%d edges=%d",
            agent_id, deleted_nodes, deleted_edges,
        )
        return DeleteResult(deleted_nodes=deleted_nodes, deleted_edges=deleted_edges)

    # ------------------------------------------------------------------
    # Analytics
    # ------------------------------------------------------------------

    async def get_graph_stats(self, agent_id: str) -> dict:
        """Return node count, edge count, and type distribution for an agent."""
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (n:SpaiderNode {agent_id: $agent_id})
                WITH count(n) AS node_count,
                     collect(DISTINCT n.type) AS node_types
                OPTIONAL MATCH (a:SpaiderNode {agent_id: $agent_id})-[r:RELATION]->(b:SpaiderNode {agent_id: $agent_id})
                RETURN node_count, node_types, count(r) AS edge_count
                """,
                agent_id=agent_id,
            )
            record = await result.single()
        if not record:
            return {"node_count": 0, "edge_count": 0, "node_types": []}
        return {
            "node_count": record["node_count"],
            "edge_count": record["edge_count"],
            "node_types": record["node_types"],
        }

    async def get_schema(self, agent_id: str) -> dict:
        """Return all node types and edge relation types for an agent."""
        async with self._driver.session() as session:
            node_result = await session.run(
                """
                MATCH (n:SpaiderNode {agent_id: $agent_id})
                RETURN DISTINCT n.type AS node_type
                """,
                agent_id=agent_id,
            )
            node_records = await node_result.data()

            edge_result = await session.run(
                """
                MATCH (:SpaiderNode {agent_id: $agent_id})-[r:RELATION]->(:SpaiderNode {agent_id: $agent_id})
                RETURN DISTINCT r.relation AS relation_type
                """,
                agent_id=agent_id,
            )
            edge_records = await edge_result.data()

        return {
            "node_types": [rec["node_type"] for rec in node_records],
            "relation_types": [rec["relation_type"] for rec in edge_records],
        }

    # ------------------------------------------------------------------
    # Atomic Leasing — Stigmergic Swarm Concurrency Control
    # ------------------------------------------------------------------

    async def claim_node_for_agent(
        self,
        node_id: str,
        agent_id: str,
        lease_duration_sec: int = 30,
    ) -> bool:
        """
        Attempt to acquire an exclusive lease on a SpaiderNode.

        The Atomic Lock
        ---------------
        A single Cypher statement acts as a compare-and-set (CAS) operation:

          MATCH the node
          WHERE it is unclaimed  (claimed_by IS NULL)
             OR its lease has expired (lease_expires < datetime())
          SET claimed_by = agent_id
              lease_expires = now + duration({seconds: lease_duration_sec})
          RETURN COUNT(n) > 0 AS claimed_successfully

        Because Neo4j evaluates the WHERE filter and the SET in the same
        logical step for each matched row, no two concurrent transactions
        can both pass the WHERE guard for the same node.  The second writer
        sees the first writer's SET on retry and finds ``claimed_by IS NOT NULL``
        with a future ``lease_expires`` — WHERE returns no rows — claim fails.

        This prevents "Ghost Work": two swarm workers processing the same
        node simultaneously.

        Lease expiry safety
        -------------------
        If a worker claims a node and crashes before calling
        ``release_node_claim()``, the lease automatically expires after
        ``lease_duration_sec`` seconds.  The next worker that runs the
        same query will find ``lease_expires < datetime()`` and acquire
        the lease cleanly — no manual intervention required.

        Parameters
        ----------
        node_id:
            UUID of the SpaiderNode (``n.id``).
        agent_id:
            Identifier of the agent requesting the lease.
        lease_duration_sec:
            How long the lease is valid.  Callers should set this to a
            realistic upper bound for their work + a safety margin.

        Returns
        -------
        bool
            ``True``  — lease acquired; caller may proceed.
            ``False`` — node is held by another agent; caller must back off.
        """
        # The WHERE clause is the lock gate:
        #   • n.claimed_by IS NULL  → node is free
        #   • n.lease_expires < datetime() → previous lease expired (crashed worker)
        # COUNT(n) aggregation always returns exactly one row (0 or 1),
        # so result.single() is always non-None.
        _CLAIM_CYPHER = """
        MATCH (n:SpaiderNode {id: $node_id})
        WHERE n.claimed_by IS NULL OR n.lease_expires < datetime()
        SET n.claimed_by    = $agent_id,
            n.lease_expires = datetime() + duration({seconds: $lease_duration_sec})
        RETURN COUNT(n) > 0 AS claimed_successfully
        """
        try:
            async with self._driver.session() as session:
                result = await session.run(
                    _CLAIM_CYPHER,
                    node_id=node_id,
                    agent_id=agent_id,
                    lease_duration_sec=lease_duration_sec,
                )
                record = await result.single()
                # record is always returned (COUNT aggregation), never None.
                claimed: bool = bool(record["claimed_successfully"]) if record else False

            if claimed:
                logger.info(
                    "AtomicLease | ACQUIRED node=%s agent=%s lease=%ds",
                    node_id, agent_id, lease_duration_sec,
                )
            else:
                logger.debug(
                    "AtomicLease | CONTENDED node=%s agent=%s — held by another worker",
                    node_id, agent_id,
                )
            return claimed

        except Exception as exc:
            logger.error(
                "AtomicLease | claim FAILED for node=%s agent=%s: %s",
                node_id, agent_id, exc,
            )
            # Treat a Neo4j error as a failed claim — the caller must not
            # proceed without a confirmed lock.
            return False

    async def release_node_claim(
        self,
        node_id: str,
        agent_id: str,
    ) -> None:
        """
        Release a previously acquired lease.

        Only removes the claim if the requesting ``agent_id`` currently holds
        it.  This prevents Agent B from accidentally releasing Agent A's lock
        (e.g. when a slow duplicate event arrives after the first worker
        already finished and a new worker claimed the node).

        Called by the swarm worker after successful processing AND XACK.
        Also called on worker shutdown for any nodes still held.

        Parameters
        ----------
        node_id:
            UUID of the SpaiderNode.
        agent_id:
            Must match the ``claimed_by`` value set during ``claim_node_for_agent``.
            No-op (and no error) if the node is not currently held by this agent.
        """
        _RELEASE_CYPHER = """
        MATCH (n:SpaiderNode {id: $node_id, claimed_by: $agent_id})
        REMOVE n.claimed_by, n.lease_expires
        """
        try:
            async with self._driver.session() as session:
                result = await session.run(
                    _RELEASE_CYPHER,
                    node_id=node_id,
                    agent_id=agent_id,
                )
                await result.consume()
                # Any matched row means we removed the properties — log accordingly.
                logger.debug(
                    "AtomicLease | RELEASED node=%s agent=%s",
                    node_id, agent_id,
                )
        except Exception as exc:
            logger.error(
                "AtomicLease | release FAILED for node=%s agent=%s: %s",
                node_id, agent_id, exc,
            )
            # Non-fatal: the lease will expire on its own via lease_expires.

    # -------------------------------------------------------------------------
    # Episodic Memory
    # -------------------------------------------------------------------------

    async def delete_agent_interactions(self, agent_id: str) -> int:
        """
        Hard-delete every InteractionNode that belongs to *agent_id*.

        Uses ``DETACH DELETE`` so all outgoing and incoming relationships
        (``BELONGS_TO_AGENT``, ``INFORMED_BY``) are removed automatically.
        SpaiderNodes are never touched.

        Returns the number of InteractionNodes deleted.
        """
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (i:InteractionNode {agent_id: $agent_id})
                WITH count(i) AS total, collect(i) AS to_delete
                FOREACH (i IN to_delete | DETACH DELETE i)
                RETURN total AS deleted_count
                """,
                agent_id=agent_id,
            )
            record = await result.single()

        deleted = record["deleted_count"] if record else 0
        logger.info(
            "delete_agent_interactions | agent=%s deleted=%d",
            agent_id, deleted,
        )
        return deleted

    async def export_agent_graph(self, agent_id: str) -> dict:
        """
        Return a complete export payload for *agent_id*:

          - All SpaiderNodes and RELATION edges (knowledge graph).
          - All InteractionNodes with ``source_node_ids`` reconstructed from
            INFORMED_BY edges (episodic memory).
          - All INFORMED_BY edge pairs for relational fidelity.

        Three Cypher queries run concurrently (asyncio.gather) in separate
        sessions to minimise round-trip latency.
        """
        import asyncio as _asyncio
        from datetime import datetime, timezone

        async def _fetch_spaider_nodes() -> list[dict]:
            async with self._driver.session() as session:
                result = await session.run(
                    """
                    MATCH (n:SpaiderNode {agent_id: $agent_id})
                    RETURN n.id          AS id,
                           n.label       AS label,
                           n.type        AS type,
                           n.description AS description,
                           n.properties  AS properties,
                           n.agent_id    AS agent_id,
                           n.created_at  AS created_at
                    ORDER BY n.created_at
                    """,
                    agent_id=agent_id,
                )
                return await result.data()

        async def _fetch_spaider_edges() -> list[dict]:
            async with self._driver.session() as session:
                result = await session.run(
                    """
                    MATCH (a:SpaiderNode {agent_id: $agent_id})
                          -[r:RELATION]->
                          (b:SpaiderNode {agent_id: $agent_id})
                    RETURN r.id                              AS id,
                           a.id                             AS source_id,
                           b.id                             AS target_id,
                           r.relation                       AS relation,
                           r.properties                     AS properties,
                           r.agent_id                       AS agent_id,
                           coalesce(r.utility_weight, 1.0)  AS utility_weight
                    """,
                    agent_id=agent_id,
                )
                return await result.data()

        async def _fetch_interactions() -> tuple[list[dict], list[dict]]:
            """
            Returns (interaction_node_rows, informed_by_edge_rows).
            A single session, two queries — INFORMED_BY edges are fetched
            separately so InteractionNodes without sources are not filtered out.
            """
            async with self._driver.session() as session:
                i_result = await session.run(
                    """
                    MATCH (i:InteractionNode {agent_id: $agent_id})
                    OPTIONAL MATCH (i)-[:INFORMED_BY]->(n:SpaiderNode)
                    WITH i, collect(n.id) AS source_node_ids
                    RETURN i.id             AS id,
                           i.session_id     AS session_id,
                           i.question       AS question,
                           i.answer_summary AS answer_summary,
                           toString(i.timestamp) AS timestamp,
                           i.agent_id       AS agent_id,
                           source_node_ids
                    ORDER BY i.timestamp
                    """,
                    agent_id=agent_id,
                )
                i_rows = await i_result.data()

                e_result = await session.run(
                    """
                    MATCH (i:InteractionNode {agent_id: $agent_id})
                          -[:INFORMED_BY]->(n:SpaiderNode)
                    RETURN i.id AS interaction_node_id,
                           n.id AS spaider_node_id
                    """,
                    agent_id=agent_id,
                )
                e_rows = await e_result.data()

            return i_rows, e_rows

        node_rows, edge_rows, (i_rows, ie_rows) = await _asyncio.gather(
            _fetch_spaider_nodes(),
            _fetch_spaider_edges(),
            _fetch_interactions(),
        )

        return {
            "agent_id":               agent_id,
            "exported_at":            datetime.now(timezone.utc).isoformat(),
            "spaider_node_count":     len(node_rows),
            "spaider_edge_count":     len(edge_rows),
            "interaction_node_count": len(i_rows),
            "informed_by_edge_count": len(ie_rows),
            "spaider_nodes":          node_rows,
            "spaider_edges":          edge_rows,
            "interaction_nodes":      i_rows,
            "informed_by_edges":      ie_rows,
        }

    async def record_interaction(
        self,
        agent_id: str,
        session_id: str,
        question: str,
        answer_summary: str,
        source_node_ids: list[str],
    ) -> None:
        """
        Persist an InteractionNode and its relationships to Neo4j.

        Graph topology written in a single atomic transaction:

          (InteractionNode)-[:BELONGS_TO_AGENT]->(SystemAgent)
          (InteractionNode)-[:INFORMED_BY]------>(SpaiderNode)  × len(source_node_ids)

        Privacy / size constraints (enforced here as the final safety net):
          question       truncated to 200 characters
          answer_summary truncated to 500 characters

        The method is designed to run as a Fire-and-Forget background task
        and therefore swallows all exceptions — a failed memory write must
        never surface to the caller.

        Parameters
        ----------
        agent_id:
            ID of the owning agent (must have a SystemAgent node in Neo4j).
        session_id:
            Client-supplied session identifier for grouping related turns.
        question:
            Raw user question (truncated before persistence).
        answer_summary:
            Full LLM answer (truncated before persistence).
        source_node_ids:
            IDs of the SpaiderNodes whose content informed the answer.
        """
        node_id   = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()

        # Privacy-safe truncation — enforced regardless of caller
        q = question[:200]
        a = answer_summary[:500]
        ids: list[str] = source_node_ids or []

        _CREATE_INTERACTION = """
        MATCH (agent:SystemAgent {agent_id: $agent_id})
        CREATE (i:InteractionNode {
            id:             $id,
            session_id:     $session_id,
            question:       $question,
            answer_summary: $answer_summary,
            timestamp:      datetime($timestamp),
            agent_id:       $agent_id
        })-[:BELONGS_TO_AGENT]->(agent)
        """

        _CREATE_INFORMED_BY = """
        MATCH (i:InteractionNode {id: $id})
        UNWIND $source_node_ids AS source_id
        MATCH (n:SpaiderNode {id: source_id})
        CREATE (i)-[:INFORMED_BY]->(n)
        """

        try:
            async def _write(tx) -> None:
                await tx.run(
                    _CREATE_INTERACTION,
                    id=node_id,
                    session_id=session_id,
                    question=q,
                    answer_summary=a,
                    timestamp=timestamp,
                    agent_id=agent_id,
                )
                if ids:
                    await tx.run(
                        _CREATE_INFORMED_BY,
                        id=node_id,
                        source_node_ids=ids,
                    )

            async with self._driver.session() as session:
                await session.execute_write(_write)

            logger.debug(
                "record_interaction | agent=%s session=%s sources=%d",
                agent_id, session_id, len(ids),
            )

        except Exception as exc:
            # Background task — log and swallow; never propagate to the caller.
            logger.warning(
                "record_interaction failed (non-fatal) | agent=%s: %s",
                agent_id, exc,
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_properties(raw) -> dict:
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                return {}
        if isinstance(raw, dict):
            return raw
        return {}

    @staticmethod
    def _record_to_node(n: dict) -> Node:
        node_props = n.get("node_props") if isinstance(n.get("node_props"), dict) else {}
        raw_properties = n.get("properties", node_props.get("properties", {}))
        props = GraphService._parse_properties(raw_properties)

        # Surface clearance_level into properties so callers can filter without
        # a separate database round-trip.
        cl = n.get("clearance_level", node_props.get("clearance_level"))
        if cl is not None:
            props["clearance_level"] = int(cl)

        return Node(
            id=n.get("id", node_props.get("id", str(uuid.uuid4()))),
            label=n.get("label", node_props.get("label", "")),
            type=n.get("type", node_props.get("type", "Other")),
            description=n.get("description", node_props.get("description")),
            properties=props,
            embedding=n.get("embedding", node_props.get("embedding")),
            agent_id=n.get("agent_id", node_props.get("agent_id")),
        )

    @staticmethod
    def _record_to_edge(r: dict, src_id: str, tgt_id: str) -> Edge:
        # Use `or` rather than dict-get defaults: legacy edges can carry an
        # explicit NULL id/relation/properties (the key is present with value
        # None), which a `.get(key, default)` would pass straight through and
        # fail Edge validation — this surfaced as a 500 on the multiverse view.
        return Edge(
            id=r.get("id") or str(uuid.uuid4()),
            source_id=src_id,
            target_id=tgt_id,
            relation=r.get("relation") or "RELATED_TO",
            properties=GraphService._parse_properties(r.get("properties") or {}),
            agent_id=r.get("agent_id"),
            utility_weight=float(r.get("utility_weight") or 1.0),
        )
