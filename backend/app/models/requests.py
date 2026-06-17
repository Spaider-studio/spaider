"""
Request body models for SpAIder API endpoints.
"""
from typing import List, Optional

from pydantic import BaseModel, Field


class IngestRequest(BaseModel):
    text: str = Field(..., min_length=1)
    agent_id: str = Field(default="default")
    source: Optional[str] = None
    metadata: Optional[dict] = None


class QueryRequest(BaseModel):
    question: str
    agent_id: str = "default"
    session_id: str = Field(
        default="",
        description=(
            "Client-supplied session identifier for grouping related interaction turns. "
            "Used by episodic memory (interaction_memory=True) to link related queries. "
            "A UUID is generated server-side when omitted."
        ),
    )
    top_k: Optional[int] = Field(
        default=None,
        ge=1,
        le=100,
        description=(
            "Number of seed nodes retrieved by vector search. "
            "Downstream context limits scale proportionally (context_limit = ceil(top_k * 2.5)). "
            "Omit to use the server default (top_k=8). "
            "Recommended: 8 for small graphs (<1k nodes), 20 for medium, 50 for large (50k+ nodes)."
        ),
    )


class CypherQueryRequest(BaseModel):
    cypher: str
    agent_id: str = "default"


class TraverseRequest(BaseModel):
    start_node_id: str
    depth: int = Field(default=2, ge=1, le=5)
    relation_filter: Optional[list[str]] = None


class SynthesizeRequest(BaseModel):
    agent_id: str = "default"
    strategy: str = "factual"
    max_samples: int = Field(default=1000, ge=1, le=50000)
    min_path_length: int = Field(default=2, ge=1, le=10)
    min_confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    output_format: str = "both"


class AgentCreateRequest(BaseModel):
    name: str
    description: Optional[str] = None
    tenant_id: str = "default"
    permissions: list[str] = ["read", "write", "query"]
    clearance_level: int = Field(
        default=1,
        ge=1,
        le=5,
        description="Diplomat Protocol clearance level (1=Public/Guest … 5=Admin).",
    )
    interaction_memory: bool = Field(
        default=False,
        description="Enable episodic memory recording for this agent (opt-in).",
    )


class AgentConnectRequest(BaseModel):
    """Request body for POST /agents/connect — creates a SHARES_KNOWLEDGE_WITH bridge."""
    source_agent_id: str = Field(..., description="ID of the source SystemAgent")
    target_agent_id: str = Field(..., description="ID of the target SystemAgent")


class SwarmConnectionRequest(BaseModel):
    source_agent_id: str
    target_agent_id: str
    permission: str = "read_only"
    scope: str = "full"
    allowed_node_types: Optional[list[str]] = None
    allowed_relation_types: Optional[list[str]] = None


class SwarmFederatedQueryRequest(BaseModel):
    """Legacy connection-based swarm query (kept for backward compatibility)."""
    question: str
    source_agent_id: str
    target_agent_ids: list[str]
    merge_results: bool = True


class SwarmQueryRequest(BaseModel):
    """
    Swarm Intelligence query — searches across all (or selected) agent brains,
    synthesises an answer via LLM, and returns the source node IDs for highlighting.
    """
    query: str = Field(..., min_length=1, description="Natural language question for the swarm")
    agent_ids: Optional[List[str]] = Field(
        default=None,
        description="Restrict search to these agent IDs. None = query all agents.",
    )
