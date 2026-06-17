"""
LlamaIndex integration for Spaider.

Requires: pip install spaider-client[llamaindex]
"""

from __future__ import annotations

from typing import Any, Optional

from ..client import Spaider
from ..models import GraphPayload, QueryResult


class SpaiderIndex:
    """
    LlamaIndex-style knowledge graph index backed by Spaider.

    Provides a familiar ``add_text`` / ``query`` interface that maps to Spaider's
    ingest and query endpoints. Can be used as a drop-in knowledge source inside
    a LlamaIndex pipeline.

    Usage::

        from spaider.integrations.llamaindex import SpaiderIndex

        index = SpaiderIndex(api_key="sk-...", agent_id="my-agent")

        # Add knowledge
        index.add_text("Max arbeitet bei Google als Software Engineer seit 2023.")
        index.add_texts([
            "Google wurde 1998 von Larry Page und Sergey Brin gegründet.",
            "Der Hauptsitz befindet sich in Mountain View, Kalifornien.",
        ])

        # Query
        response = index.query("Wo arbeitet Max?")
        print(response.text)
        print(response.subgraph.nodes)

        # Traverse from a node
        subgraph = index.traverse("node-uuid", depth=3)

        # Get full graph
        graph = index.get_graph()

    LlamaIndex QueryEngine wrapper::

        from llama_index.core.query_engine import CustomQueryEngine
        from spaider.integrations.llamaindex import SpaiderQueryEngine

        engine = SpaiderQueryEngine(api_key="sk-...", agent_id="my-agent")
        response = engine.query("What do we know about Max?")
    """

    def __init__(
        self,
        api_key: str,
        agent_id: str = "default",
        base_url: str = "https://api.spaider.studio",
        timeout: float = 60.0,
    ) -> None:
        """
        Initialise the SpaiderIndex.

        Args:
            api_key: Your Spaider API key.
            agent_id: The agent / namespace to operate on.
            base_url: Override the API base URL.
            timeout: Request timeout in seconds.
        """
        self._client = Spaider(
            api_key=api_key,
            agent_id=agent_id,
            base_url=base_url,
            timeout=timeout,
        )
        self.agent_id = agent_id

    # ── Indexing ──────────────────────────────────────────────────────────────

    def add_text(self, text: str, source: Optional[str] = None) -> None:
        """
        Add a single text document to the knowledge graph.

        Args:
            text: Raw text to extract knowledge from.
            source: Optional source label (e.g. ``"pdf"``, ``"web"``).
        """
        self._client.ingest(text, source=source)

    def add_texts(self, texts: list[str], source: Optional[str] = None) -> None:
        """
        Add multiple text documents to the knowledge graph.

        Args:
            texts: List of raw text strings.
            source: Optional source label applied to all texts.
        """
        for text in texts:
            self._client.ingest(text, source=source)

    # ── Querying ──────────────────────────────────────────────────────────────

    def query(self, question: str, top_k: int = 10) -> QueryResult:
        """
        Query the knowledge graph with a natural-language question.

        Args:
            question: The question to answer.
            top_k: Number of semantically similar nodes to retrieve.

        Returns:
            :class:`~spaider.models.QueryResult` with the answer text and
            the supporting subgraph.
        """
        return self._client.query(question, top_k=top_k)

    def traverse(self, node_id: str, depth: int = 2) -> GraphPayload:
        """
        Traverse the graph from a starting node.

        Args:
            node_id: UUID of the starting node.
            depth: Maximum traversal depth.

        Returns:
            :class:`~spaider.models.GraphPayload` with the subgraph.
        """
        return self._client.traverse(node_id=node_id, depth=depth)

    def get_graph(self) -> GraphPayload:
        """
        Return the full knowledge graph for the current agent.

        Returns:
            :class:`~spaider.models.GraphPayload` with all nodes and edges.
        """
        return self._client.get_graph()

    # ── Context manager ───────────────────────────────────────────────────────

    def __enter__(self) -> "SpaiderIndex":
        return self

    def __exit__(self, *_: Any) -> None:
        self._client.close()


try:
    from llama_index.core.base.response.schema import RESPONSE_TYPE, Response
    from llama_index.core.query_engine import BaseQueryEngine
    from llama_index.core.schema import QueryBundle

    class SpaiderQueryEngine(BaseQueryEngine):
        """
        LlamaIndex ``BaseQueryEngine`` backed by Spaider.

        Usage::

            engine = SpaiderQueryEngine(api_key="sk-...", agent_id="my-agent")
            response = engine.query("What do we know about Max?")
            print(str(response))
        """

        def __init__(
            self,
            api_key: str,
            agent_id: str = "default",
            base_url: str = "https://api.spaider.studio",
            top_k: int = 10,
        ) -> None:
            self._index = SpaiderIndex(
                api_key=api_key, agent_id=agent_id, base_url=base_url
            )
            self._top_k = top_k
            super().__init__()

        def _query(self, query_bundle: QueryBundle) -> RESPONSE_TYPE:
            result = self._index.query(query_bundle.query_str, top_k=self._top_k)
            return Response(response=result.answer)

        async def _aquery(self, query_bundle: QueryBundle) -> RESPONSE_TYPE:
            # Fall back to sync — override with async client if needed
            return self._query(query_bundle)

        def retrieve(self, query_bundle: QueryBundle) -> list:
            return []

except ImportError:
    # llama-index not installed — SpaiderQueryEngine is simply not available
    pass
