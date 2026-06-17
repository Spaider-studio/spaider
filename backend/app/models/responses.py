"""
Response models for SpAIder API endpoints.
"""
from typing import Any, Optional

from pydantic import BaseModel

from .schemas import (
    Agent,
    SwarmConnection,
)


class APIResponse(BaseModel):
    success: bool
    message: str
    data: Optional[Any] = None


class IngestQueuedResponse(BaseModel):
    status: str = "queued"
    message_id: str
    agent_id: str


class SlimNode(BaseModel):
    """Node without embedding — used in API responses to avoid sending 1536 floats."""
    id: str
    label: str
    type: str
    properties: dict = {}
    agent_id: Optional[str] = None


class SlimEdge(BaseModel):
    """Edge without redundant fields."""
    id: str
    source: str
    target: str
    relation: str
    agent_id: Optional[str] = None


class IngestSyncResponse(BaseModel):
    success: bool
    agent_id: str
    nodes_created: int
    nodes_merged: int
    edges_created: int
    edges_merged: int
    nodes: list[SlimNode]
    edges: list[SlimEdge]
    latency_ms: float


class GraphResponse(BaseModel):
    nodes: list[dict]
    edges: list[dict]
    node_count: int
    edge_count: int
    agent_id: str
    # Pagination envelope — present on paginated endpoints, None on full dumps
    limit: int | None = None
    offset: int | None = None


class DeleteNodeResponse(BaseModel):
    success: bool
    deleted_node_id: str
    deleted_edges: int
    audit_entry: dict


class AgentResponse(BaseModel):
    success: bool
    agent: Agent


class RotateKeyResponse(BaseModel):
    """Response for POST /agents/{agent_id}/rotate-key.

    ``api_key`` is the new raw credential — shown exactly once; the caller
    must persist it immediately, as only its SHA-256 hash is kept in Redis
    after this call returns.
    """
    success: bool = True
    agent_id: str
    api_key: str
    message: str = "Key rotated successfully."


class AgentListResponse(BaseModel):
    agents: list[Agent]
    total: int


class AgentBridgeResponse(BaseModel):
    """Response for POST /agents/connect — confirms the synaptic bridge was created."""
    success: bool
    source_agent_id: str
    target_agent_id: str
    link_type: str  # Always 'SHARES_KNOWLEDGE_WITH'


class SwarmConnectionResponse(BaseModel):
    success: bool
    connection: SwarmConnection


class SwarmQueryResponse(BaseModel):
    answer: str
    source_node_ids: list[str]
    agents_involved: list[str]


class SwarmLinkResponse(BaseModel):
    """A single active SHARES_KNOWLEDGE_WITH edge between two SystemAgent nodes."""
    source_id: str
    source_name: str
    target_id: str
    target_name: str


class SwarmLinkDeleteResponse(BaseModel):
    """Confirmation that a synaptic bridge was deleted."""
    success: bool
    deleted_count: int
    source_agent_id: str
    target_agent_id: str


class DeleteInteractionsResponse(BaseModel):
    """Confirmation that an agent's episodic memory was wiped."""
    success: bool
    agent_id: str
    deleted_count: int


class AgentImportResponse(BaseModel):
    """
    Summary returned after a successful NDJSON graph import.

    ``new_api_key`` is a freshly generated credential for the restored agent —
    the original key is never stored in the export file for security reasons.
    Show this value to the operator exactly once.
    """
    success: bool
    agent_id: str
    new_api_key: str            # show once — generated fresh on every import
    nodes_restored: int
    edges_restored: int
    skipped: int                # malformed lines + unhandled types (interaction, informed_by)


class AgentExportResponse(BaseModel):
    """
    Full export payload for a single agent.

    Contains all SpaiderNodes and RELATION edges, plus all InteractionNodes
    and their INFORMED_BY edges (episodic memory), enabling a complete
    round-trip import into another SpAIder instance.
    """
    agent_id: str
    exported_at: str                    # ISO-8601 UTC timestamp
    spaider_node_count: int
    spaider_edge_count: int
    interaction_node_count: int
    informed_by_edge_count: int
    spaider_nodes: list[dict]
    spaider_edges: list[dict]
    interaction_nodes: list[dict]       # includes reconstructed source_node_ids list
    informed_by_edges: list[dict]       # {interaction_node_id, spaider_node_id}
