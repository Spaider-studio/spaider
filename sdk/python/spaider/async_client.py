"""
Spaider asynchronous Python client.
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


class AsyncSpaider:
    """
    Spaider Memory Infrastructure Client (asynchronous).

    Usage::

        async with AsyncSpaider(api_key="sk-...", agent_id="my-agent") as sp:
            result = await sp.ingest("Max arbeitet bei Google als Engineer seit 2023.")
            answer = await sp.query("Wo arbeitet Max?")
            print(answer.text)

    All methods mirror the synchronous :class:`spaider.Spaider` client.
    """

    DEFAULT_BASE_URL = "https://api.spaider.studio"
    DEFAULT_TIMEOUT = 60.0

    def __init__(
        self,
        api_key: str,
        agent_id: str = "default",
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        """
        Initialise the async Spaider client.

        Args:
            api_key: Your Spaider API key (``sk-...``).
            agent_id: The agent / namespace to operate on.
            base_url: Override the API base URL.
            timeout: Default request timeout in seconds.
            http_client: Inject a custom ``httpx.AsyncClient`` (e.g. for testing).
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
        self._owned_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            base_url=self._base_url,
            headers=self._headers,
            timeout=timeout,
        )

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Optional[dict] = None,
        params: Optional[dict] = None,
    ) -> Any:
        """Execute an async HTTP request and raise appropriate exceptions."""
        response = await self._client.request(method, path, json=json, params=params)
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

    async def ingest(self, text: str, source: Optional[str] = None) -> IngestResult:
        """
        Extract entities and relationships from *text* and store them in the graph.

        Args:
            text: Raw text to process.
            source: Optional source label.

        Returns:
            :class:`IngestResult` with counts of created/merged nodes and edges.
        """
        payload: dict[str, Any] = {"text": text, "agent_id": self.agent_id}
        if source:
            payload["source"] = source
        data = await self._request("POST", "/api/v1/ingest", json=payload)
        return IngestResult(**data)

    async def query(self, question: str, top_k: int = 10) -> QueryResult:
        """
        Answer a natural-language *question* grounded in the agent's knowledge graph.

        Args:
            question: The question to answer.
            top_k: Number of semantically similar nodes to retrieve.

        Returns:
            :class:`QueryResult` with the answer text and supporting subgraph.
        """
        data = await self._request(
            "POST",
            "/api/v1/query",
            json={"question": question, "agent_id": self.agent_id, "top_k": top_k},
        )
        return QueryResult(**data)

    async def traverse(self, node_id: str, depth: int = 2) -> GraphPayload:
        """
        Traverse the graph starting from *node_id* up to *depth* hops.

        Args:
            node_id: UUID of the starting node.
            depth: Maximum traversal depth.

        Returns:
            :class:`GraphPayload` containing nodes and edges in the subgraph.
        """
        data = await self._request(
            "GET",
            f"/api/v1/graph/traverse/{node_id}",
            params={"depth": depth, "agent_id": self.agent_id},
        )
        return GraphPayload(**data)

    async def get_graph(self) -> GraphPayload:
        """
        Fetch the complete knowledge graph for the current agent.

        Returns:
            :class:`GraphPayload` with all nodes and edges.
        """
        data = await self._request("GET", "/api/v1/graph", params={"agent_id": self.agent_id})
        return GraphPayload(**data)

    async def get_node(self, node_id: str) -> Node:
        """
        Fetch a single node by ID.

        Args:
            node_id: UUID of the node.

        Returns:
            :class:`Node` instance.

        Raises:
            :class:`NotFoundError`: If the node does not exist.
        """
        data = await self._request(
            "GET",
            f"/api/v1/node/{node_id}",
            params={"agent_id": self.agent_id},
        )
        return Node(**data)

    async def delete_node(self, node_id: str) -> None:
        """
        Delete a node and all its relationships (GDPR right-to-erasure).

        Args:
            node_id: UUID of the node to delete.

        Raises:
            :class:`NotFoundError`: If the node does not exist.
        """
        await self._request(
            "DELETE",
            f"/api/v1/node/{node_id}",
            params={"agent_id": self.agent_id},
        )

    async def synthesize(
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
        data = await self._request(
            "POST",
            "/api/v1/synthesize",
            json={
                "agent_id": self.agent_id,
                "strategy": strategy,
                "max_samples": max_samples,
            },
        )
        return SynthesisDataset(**data)

    async def create_swarm_connection(
        self,
        target_agent: str,
        *,
        permission: str = "read_only",
        scope: str = "full",
    ) -> SwarmConnection:
        """
        Create a swarm connection from the current agent to *target_agent*.

        Args:
            target_agent: ID of the target agent to connect to.
            permission: ``read_only`` (default) or ``read_write``.
            scope: ``full`` (default) or ``filtered``.

        Returns:
            :class:`SwarmConnection` describing the new connection.
        """
        data = await self._request(
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

    async def swarm_query(
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
        data = await self._request(
            "POST",
            "/api/v1/swarm/query",
            json={"query": question, "agent_ids": target_agents},
        )
        return SwarmQueryResult(**data)

    # ── Context manager ───────────────────────────────────────────────────────

    async def aclose(self) -> None:
        """Close the underlying async HTTP client."""
        if self._owned_client:
            await self._client.aclose()

    async def __aenter__(self) -> "AsyncSpaider":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()
