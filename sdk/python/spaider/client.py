"""
Spaider synchronous Python client.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx

from .exceptions import AuthError, NotFoundError, RateLimitError, ServerError, SpaiderError
from .models import (
    GraphPayload,
    IngestResult,
    Node,
    QueryResult,
    SwarmConnection,
    SwarmQueryResult,
    SynthesisDataset,
)


class Spaider:
    """
    Spaider Memory Infrastructure Client (synchronous).

    Usage::

        sp = Spaider(api_key="sk-...", agent_id="my-agent")

        # Ingest text into the knowledge graph
        result = sp.ingest("Max arbeitet bei Google als Engineer seit 2023.")
        print(result.nodes_created)

        # Natural-language query
        answer = sp.query("Wo arbeitet Max?")
        print(answer.text)
        print(answer.subgraph.nodes)

        # Traverse the graph from a node
        subgraph = sp.traverse(node_id="uuid", depth=3)

        # Fetch the full graph for the agent
        graph = sp.get_graph()

        # Delete a node (GDPR right-to-erasure)
        sp.delete_node("uuid-here")

        # Synthesize a fine-tuning dataset
        dataset = sp.synthesize(strategy="reasoning", max_samples=1000)
        dataset.save("training.jsonl")

        # Swarm: connect to another agent's graph
        conn = sp.create_swarm_connection(target_agent="agent_sales")

        # Query across multiple agents
        result = sp.swarm_query("What are our top clients?", target_agents=["agent_sales"])
    """

    DEFAULT_BASE_URL = "https://api.spaider.studio"
    DEFAULT_TIMEOUT = 60.0

    def __init__(
        self,
        api_key: str,
        agent_id: str = "default",
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        """
        Initialise the Spaider client.

        Args:
            api_key: Your Spaider API key (``sk-...``).
            agent_id: The agent / namespace to operate on.
            base_url: Override the API base URL (useful for self-hosted deployments).
            timeout: Default request timeout in seconds.
            http_client: Inject a custom ``httpx.Client`` (e.g. for testing).
        """
        if not api_key:
            raise ValueError("api_key must not be empty")

        self.agent_id = agent_id
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-Spaider-Agent": agent_id,
        }
        self._client = http_client or httpx.Client(
            base_url=self._base_url,
            headers=self._headers,
            timeout=timeout,
        )

    # ── Internal ──────────────────────────────────────────────────────────────

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Optional[dict] = None,
        params: Optional[dict] = None,
    ) -> Any:
        """Execute an HTTP request and raise appropriate exceptions on failure."""
        response = self._client.request(method, path, json=json, params=params)
        return self._handle_response(response)

    @staticmethod
    def _handle_response(response: httpx.Response) -> Any:
        """Raise a typed exception for non-2xx responses."""
        if response.is_success:
            if response.status_code == 204:
                return None
            return response.json()

        body = response.text
        status = response.status_code

        if status == 401:
            raise AuthError(response_body=body)
        if status == 404:
            raise NotFoundError(response_body=body)
        if status == 429:
            retry_after = None
            raw = response.headers.get("Retry-After")
            if raw and raw.isdigit():
                retry_after = int(raw)
            raise RateLimitError(response_body=body, retry_after=retry_after)
        if status >= 500:
            raise ServerError(status_code=status, response_body=body)
        raise SpaiderError(
            f"Unexpected API response {status}",
            status_code=status,
            response_body=body,
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def ingest(self, text: str, source: Optional[str] = None) -> IngestResult:
        """
        Extract entities and relationships from *text* and store them in the graph.

        Args:
            text: Raw text to process.
            source: Optional source label (e.g. ``"wikipedia"``, ``"email"``).

        Returns:
            :class:`IngestResult` with counts of created/merged nodes and edges.
        """
        payload: dict[str, Any] = {"text": text, "agent_id": self.agent_id}
        if source:
            payload["source"] = source
        data = self._request("POST", "/api/v1/ingest", json=payload)
        return IngestResult(**data)

    def query(self, question: str, top_k: int = 10) -> QueryResult:
        """
        Answer a natural-language *question* grounded in the agent's knowledge graph.

        Args:
            question: The question to answer.
            top_k: Number of semantically similar nodes to retrieve.

        Returns:
            :class:`QueryResult` with the answer text and supporting subgraph.
        """
        data = self._request(
            "POST",
            "/api/v1/query",
            json={"question": question, "agent_id": self.agent_id, "top_k": top_k},
        )
        return QueryResult(**data)

    def traverse(self, node_id: str, depth: int = 2) -> GraphPayload:
        """
        Traverse the graph starting from *node_id* up to *depth* hops.

        Args:
            node_id: UUID of the starting node.
            depth: Maximum traversal depth.

        Returns:
            :class:`GraphPayload` containing nodes and edges in the subgraph.
        """
        data = self._request(
            "GET",
            f"/api/v1/graph/traverse/{node_id}",
            params={"depth": depth, "agent_id": self.agent_id},
        )
        return GraphPayload(**data)

    def get_graph(self) -> GraphPayload:
        """
        Fetch the complete knowledge graph for the current agent.

        Returns:
            :class:`GraphPayload` with all nodes and edges.
        """
        data = self._request("GET", "/api/v1/graph", params={"agent_id": self.agent_id})
        return GraphPayload(**data)

    def get_node(self, node_id: str) -> Node:
        """
        Fetch a single node by ID.

        Args:
            node_id: UUID of the node.

        Returns:
            :class:`Node` instance.

        Raises:
            :class:`NotFoundError`: If the node does not exist.
        """
        data = self._request(
            "GET",
            f"/api/v1/node/{node_id}",
            params={"agent_id": self.agent_id},
        )
        return Node(**data)

    def delete_node(self, node_id: str) -> None:
        """
        Delete a node and all its relationships (GDPR right-to-erasure).

        Args:
            node_id: UUID of the node to delete.

        Raises:
            :class:`NotFoundError`: If the node does not exist.
        """
        self._request(
            "DELETE",
            f"/api/v1/node/{node_id}",
            params={"agent_id": self.agent_id},
        )

    def synthesize(
        self,
        strategy: str = "reasoning",
        max_samples: int = 1000,
    ) -> SynthesisDataset:
        """
        Generate a fine-tuning dataset from the agent's knowledge graph.

        Args:
            strategy: Synthesis strategy. One of ``"reasoning"``, ``"qa"``, ``"chat"``.
            max_samples: Maximum number of training samples to generate.

        Returns:
            :class:`SynthesisDataset` — call ``.save("file.jsonl")`` to persist.
        """
        data = self._request(
            "POST",
            "/api/v1/synthesize",
            json={
                "agent_id": self.agent_id,
                "strategy": strategy,
                "max_samples": max_samples,
            },
        )
        return SynthesisDataset(**data)

    def create_swarm_connection(
        self,
        target_agent: str,
        *,
        permission: str = "read_only",
        scope: str = "full",
    ) -> SwarmConnection:
        """
        Create a swarm connection from the current agent to *target_agent*.

        This allows :meth:`swarm_query` to reach into *target_agent*'s graph.

        Args:
            target_agent: ID of the target agent to connect to.
            permission: ``read_only`` (default) or ``read_write``.
            scope: ``full`` (default) or ``filtered``.

        Returns:
            :class:`SwarmConnection` describing the new connection.
        """
        data = self._request(
            "POST",
            "/api/v1/swarm/connections",
            json={
                "source_agent_id": self.agent_id,
                "target_agent_id": target_agent,
                "permission": permission,
                "scope": scope,
            },
        )
        # Response envelope is {success, connection: {...}}.
        return SwarmConnection(**data["connection"])

    def swarm_query(
        self,
        question: str,
        target_agents: Optional[list[str]] = None,
    ) -> SwarmQueryResult:
        """
        Query across multiple agents' knowledge graphs simultaneously.

        Args:
            question: The question to answer.
            target_agents: Agent IDs to include. ``None`` queries all agents.

        Returns:
            :class:`SwarmQueryResult` with the merged answer and source node ids.
        """
        data = self._request(
            "POST",
            "/api/v1/swarm/query",
            json={"query": question, "agent_ids": target_agents},
        )
        return SwarmQueryResult(**data)

    # ── Context manager ───────────────────────────────────────────────────────

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()

    def __enter__(self) -> "Spaider":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
