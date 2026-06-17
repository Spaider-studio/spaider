"""
Pydantic models for SpAIder SDK — mirrors backend response schemas (simplified).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class Node(BaseModel):
    """A knowledge-graph node — mirrors the backend ``SlimNode`` / ``Node`` response."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str = Field(..., description="UUID of the node")
    label: str = Field(..., description="Human-readable label, e.g. 'Max Mustermann'")
    type: str = Field(default="OTHER", description="Entity type: Person, Organization, ...")
    description: Optional[str] = Field(default=None, description="Long-form text (FACT nodes)")
    properties: dict[str, Any] = Field(default_factory=dict)
    agent_id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class Edge(BaseModel):
    """A directed relationship between two nodes.

    The API serialises edges differently per endpoint: ``GET /graph`` returns
    the node *labels* as ``source``/``target``; the ``/query`` subgraph returns
    the full edge with ``source_id``/``target_id``. All four are accepted and
    unused ones stay ``None`` — so one model parses every endpoint's shape.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str = Field(..., description="UUID of the edge")
    relation: str = Field(..., description="Relationship type, e.g. 'WORKS_AT'")
    source: Optional[str] = Field(default=None, description="Source node label (graph endpoint)")
    target: Optional[str] = Field(default=None, description="Target node label (graph endpoint)")
    source_id: Optional[str] = Field(default=None, description="Source node id (query subgraph)")
    target_id: Optional[str] = Field(default=None, description="Target node id (query subgraph)")
    type: Optional[str] = None
    properties: dict[str, Any] = Field(default_factory=dict)
    agent_id: Optional[str] = None
    created_at: Optional[datetime] = None


class GraphPayload(BaseModel):
    """Subgraph returned by ``get_graph()`` / ``traverse()`` — mirrors ``GraphResponse``."""

    model_config = ConfigDict(extra="ignore")

    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    agent_id: Optional[str] = None
    node_count: int = 0
    edge_count: int = 0
    limit: Optional[int] = None
    offset: Optional[int] = None


class QueryResult(BaseModel):
    """Result of a natural-language query — mirrors the backend ``QueryResult``."""

    model_config = ConfigDict(extra="ignore")

    answer: str = Field(..., description="The LLM-generated, graph-grounded answer")
    question: Optional[str] = None
    subgraph: GraphPayload = Field(default_factory=GraphPayload)
    cypher_used: Optional[str] = None
    iterations_used: int = 1
    re_query_happened: bool = False
    confidence_score: float = Field(default=1.0, ge=0.0, le=1.0)
    verifier_feedback: Optional[list[str]] = None


class IngestResult(BaseModel):
    """Result of an ingest call.

    Async ``ingest()`` (``POST /ingest``) returns ``status`` + ``message_id``;
    the sync ingest endpoints add the created/merged counts. Both shapes parse.
    """

    model_config = ConfigDict(extra="ignore")

    status: str = Field(default="queued", description="'queued' (async) or 'ok'")
    message_id: Optional[str] = Field(default=None, description="Kafka message id for async tracking")
    agent_id: Optional[str] = None
    success: Optional[bool] = None
    nodes_created: int = 0
    nodes_merged: int = 0
    edges_created: int = 0
    edges_merged: int = 0
    latency_ms: Optional[float] = None


class SynthesisDataset(BaseModel):
    """Dataset returned by synthesize()."""

    samples: list[dict[str, Any]] = Field(default_factory=list)
    agent_id: str
    strategy: str
    total: int = 0

    def save(self, path: str) -> None:
        """Write dataset to a .jsonl file."""
        import json
        from pathlib import Path

        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as fh:
            for sample in self.samples:
                fh.write(json.dumps(sample, ensure_ascii=False) + "\n")


class SwarmConnection(BaseModel):
    """A cross-agent swarm connection — mirrors the backend ``SwarmConnection``."""

    model_config = ConfigDict(extra="ignore")

    id: str
    source_agent_id: str
    target_agent_id: str
    permission: str = "read_only"
    scope: str = "full"
    allowed_node_types: Optional[list[str]] = None
    allowed_relation_types: Optional[list[str]] = None
    expires_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


class SwarmQueryResult(BaseModel):
    """Result of a swarm query — mirrors the backend ``SwarmQueryResponse``."""

    model_config = ConfigDict(extra="ignore")

    answer: str
    source_node_ids: list[str] = Field(default_factory=list)
    agents_involved: list[str] = Field(default_factory=list)
