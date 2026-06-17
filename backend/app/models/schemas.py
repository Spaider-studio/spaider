"""
Pydantic schemas for SpAIder knowledge graph entities, responses, and configuration.
"""
import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class NodeType(str, Enum):
    PERSON = "PERSON"
    ORGANIZATION = "ORGANIZATION"
    LOCATION = "LOCATION"
    EVENT = "EVENT"
    CONCEPT = "CONCEPT"
    PRODUCT = "PRODUCT"
    TECHNOLOGY = "TECHNOLOGY"
    DATE = "DATE"
    METRIC = "METRIC"
    DOCUMENT = "DOCUMENT"
    TEAM = "TEAM"
    ROLE = "ROLE"
    PROJECT = "PROJECT"
    OTHER = "OTHER"


class RelationType(str, Enum):
    WORKS_AT = "WORKS_AT"
    IS_CEO_OF = "IS_CEO_OF"
    LEADS = "LEADS"
    PART_OF = "PART_OF"
    LOCATED_IN = "LOCATED_IN"
    CAUSED_BY = "CAUSED_BY"
    DEPENDS_ON = "DEPENDS_ON"
    CREATED = "CREATED"
    APPROVED = "APPROVED"
    BLOCKED_BY = "BLOCKED_BY"
    REPORTS_TO = "REPORTS_TO"
    COLLABORATES_WITH = "COLLABORATES_WITH"
    USES = "USES"
    FUNDED_BY = "FUNDED_BY"
    ACQUIRED = "ACQUIRED"
    COMPETING_WITH = "COMPETING_WITH"
    PRECEDED_BY = "PRECEDED_BY"
    FOLLOWED_BY = "FOLLOWED_BY"
    CONTAINS = "CONTAINS"
    RELATED_TO = "RELATED_TO"
    FOUNDED_BY = "FOUNDED_BY"
    FOUNDED = "FOUNDED"
    CEO_OF = "CEO_OF"


class NodeProperties(BaseModel):
    description: Optional[str] = None
    aliases: list[str] = Field(default_factory=list)
    source_text: Optional[str] = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    temporal: Optional[str] = None
    source: Optional[str] = None
    model_config = {"extra": "allow"}


class EdgeProperties(BaseModel):
    description: Optional[str] = None
    source_text: Optional[str] = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    temporal: Optional[str] = None
    model_config = {"extra": "allow"}


class Node(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    label: str
    type: str = "OTHER"
    # Long-form text for the node, persisted as a top-level Neo4j column
    # so the `spaider_label_fulltext` index (which already references
    # `[n.label, n.description]`) actually covers it. Used by FACT-type
    # nodes to carry the original ingested text verbatim —.
    # Other node types may leave this NULL.
    description: Optional[str] = None
    properties: dict = Field(default_factory=dict)
    embedding: Optional[list[float]] = None
    agent_id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class Edge(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_id: str  # node id
    target_id: str  # node id
    source: Optional[str] = None  # label (for display)
    target: Optional[str] = None  # label (for display)
    relation: str
    properties: dict = Field(default_factory=dict)
    agent_id: Optional[str] = None
    created_at: Optional[datetime] = None
    utility_weight: float = Field(
        default=1.0,
        description="Synapse strength (V2 Cognitive Graph). Controls edge width in 3D view.",
    )

    @model_validator(mode="after")
    def _fill_endpoints_from_ids(self) -> "Edge":
        # API contract: consumers join edges on node ids. When display labels
        # aren't populated, expose the ids under ``source``/``target`` so the
        # field is consistent across endpoints (GET /graph already uses ids).
        if self.source is None:
            self.source = self.source_id
        if self.target is None:
            self.target = self.target_id
        return self


class GraphPayload(BaseModel):
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)


class GraphCluster(BaseModel):
    """
    Aggregated cluster used for level-of-detail rendering.
    A cluster groups many SpaiderNodes — its sphere size in the frontend
    scales with `node_count`.
    """
    id: str = Field(..., description="Stable cluster identifier, e.g. 'cluster:PERSON'")
    label: str = Field(..., description="Human-readable label shown on the cluster sphere")
    type: str = Field(..., description="Node type that defines the cluster (used for colour)")
    node_count: int = Field(..., ge=0, description="Number of member nodes")
    sample_node_ids: list[str] = Field(
        default_factory=list,
        description="Up to 10 representative member ids — useful for click-to-drill-down",
    )


class GraphClusterEdge(BaseModel):
    """Aggregated relation between two clusters."""
    id: str = Field(..., description="Stable id, e.g. 'cluster-edge:PERSON->ORGANIZATION'")
    source_cluster_id: str
    target_cluster_id: str
    count: int = Field(..., ge=1, description="Number of underlying RELATION edges")


class ClusterGraphPayload(BaseModel):
    """Response body for GET /graph/clusters — LOD overview of an agent's graph."""
    clusters: list[GraphCluster] = Field(default_factory=list)
    cluster_edges: list[GraphClusterEdge] = Field(default_factory=list)
    total_nodes: int = 0
    total_edges: int = 0
    zoom_level: int = 0
    agent_id: str


class WriteResult(BaseModel):
    nodes_created: int = 0
    nodes_merged: int = 0
    edges_created: int = 0
    edges_merged: int = 0


class DeleteResult(BaseModel):
    nodes_deleted: int = 0
    edges_deleted: int = 0
    labels_deleted: list[str] = Field(default_factory=list)


class GraphStats(BaseModel):
    node_count: int
    edge_count: int
    type_distribution: dict[str, int]
    relation_distribution: dict[str, int]
    density: float
    agent_id: str


class SwarmConnectionConfig(BaseModel):
    source_agent_id: str
    target_agent_id: str
    permission: str = "read_only"
    scope: str = "full"
    allowed_node_types: Optional[list[str]] = None
    allowed_relation_types: Optional[list[str]] = None
    expires_at: Optional[datetime] = None


class SwarmConnection(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_agent_id: str
    target_agent_id: str
    permission: str
    scope: str
    allowed_node_types: Optional[list[str]] = None
    allowed_relation_types: Optional[list[str]] = None
    expires_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Agent(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: Optional[str] = None
    tenant_id: str = "default"
    permissions: list[str] = Field(default_factory=lambda: ["read", "write", "query"])
    clearance_level: int = Field(
        default=1,
        ge=1,
        le=5,
        description=(
            "Diplomat Protocol clearance level (1=Public/Guest … 5=Admin). "
            "The agent can only retrieve SpaiderNodes whose clearance_level "
            "is ≤ this value.  Nodes without an explicit clearance_level are "
            "treated as level 1 (public)."
        ),
    )
    api_key: Optional[str] = Field(
        default=None,
        description=(
            "Transient response field — populated exactly once in POST /agents "
            "and POST /agents/{id}/rotate-key so the caller can copy the raw "
            "key. Never persisted: only its SHA-256 hash lives in Redis (at "
            "spaider:apikey:<hash>) via AuthService.generate_and_store_api_key()."
        ),
    )
    api_key_hash: Optional[str] = Field(
        default=None,
        description=(
            "Internal: SHA-256 hash of the agent's current API key. Persisted "
            "on the agent record so rotation can revoke the previous key with "
            "a single Redis DEL instead of scanning spaider:apikey:*. Always "
            "nulled on list/get/update responses — never exposed to callers."
        ),
    )
    interaction_memory: bool = Field(
        default=False,
        description=(
            "Episodic Memory opt-in flag. When True, every query/response pair "
            "is recorded as an InteractionNode in Neo4j, linked to the SystemAgent "
            "and to the SpaiderNodes that informed the answer."
        ),
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)


class InteractionNode(BaseModel):
    """
    Episodic memory record stored as a Neo4j node (label: InteractionNode).

    Created by the memory engine after each query when the owning agent has
    ``interaction_memory=True``.  Linked via:
      - BELONGS_TO_AGENT  → SystemAgent
      - INFORMED_BY       → SpaiderNode  (one edge per source node)
    """
    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Stable UUID4 identifier for this interaction record.",
    )
    session_id: str = Field(
        ...,
        description="Client-supplied session identifier grouping related turns.",
    )
    question: str = Field(
        ...,
        max_length=200,
        description="User question (truncated to 200 chars before Neo4j write).",
    )
    answer_summary: str = Field(
        ...,
        max_length=500,
        description="Agent answer summary (truncated to 500 chars before Neo4j write).",
    )
    timestamp: datetime = Field(
        default_factory=datetime.utcnow,
        description="UTC timestamp of the interaction.",
    )
    agent_id: str = Field(..., description="ID of the owning agent.")
    source_node_ids: list[str] = Field(
        default_factory=list,
        description="IDs of the SpaiderNodes that informed the answer.",
    )


class SynthesizeConfig(BaseModel):
    strategy: str = "factual"  # factual | reasoning | relations
    max_samples: int = 1000
    min_path_length: int = 2
    min_confidence: float = 0.7
    output_format: str = "both"  # openai | alpaca | both


class SynthesizeResult(BaseModel):
    status: str = "completed"
    dataset_id: str
    dataset_path: str
    stats: dict


class VerifierResult(BaseModel):
    """Structured output from the evidence-sufficiency verifier LLM call.

    The model is used as ``response_format`` in the LiteLLM call so the
    provider returns valid JSON that Pydantic validates — raw ``json.loads``
    is never used, eliminating silent parse failures.
    """
    is_sufficient: bool = Field(
        description="True when the accumulated context is enough to answer the question."
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Verifier's confidence that the retrieved context is sufficient (0–1).",
    )
    missing_information_categories: list[str] = Field(
        default_factory=list,
        description=(
            "Semantic categories of information the verifier judges to be absent "
            "from the current context (e.g. 'founding date', 'revenue figures'). "
            "Used to construct the next search query."
        ),
    )
    next_search_query: Optional[str] = Field(
        default=None,
        description=(
            "A targeted search query that should surface the missing information. "
            "Must be set when is_sufficient is False; ignored otherwise."
        ),
    )


class TokenUsage(BaseModel):
    """Backend LLM token spend for a single query (counts only, never text)."""
    prompt_tokens: int = Field(
        default=0, description="Total input tokens across all backend LLM calls."
    )
    completion_tokens: int = Field(
        default=0, description="Total output tokens across all backend LLM calls."
    )


class QueryResult(BaseModel):
    question: str
    answer: str
    subgraph: GraphPayload
    cypher_used: Optional[str] = None
    token_usage: Optional[TokenUsage] = Field(
        default=None,
        description=(
            "Backend (server-side) LLM token spend for grounding this query — "
            "decomposition, synthesis, verification, etc. None on a cache hit. "
            "This is the cost the agent does not see; counts only, no prompt text."
        ),
    )
    # Agentic QA loop observability metadata
    iterations_used: int = Field(
        default=1,
        description="Number of retrieve-verify iterations executed before synthesis.",
    )
    re_query_happened: bool = Field(
        default=False,
        description="True when at least one re-query was issued due to insufficient context.",
    )
    confidence_score: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Verifier confidence score from the final iteration.",
    )
    verifier_feedback: Optional[list[str]] = Field(
        default=None,
        description=(
            "Accumulated missing-information categories reported by the verifier "
            "across all iterations. None when the first pass was sufficient."
        ),
    )


class SwarmQueryResult(BaseModel):
    question: str
    results: list[dict]
    merged_subgraph: GraphPayload
