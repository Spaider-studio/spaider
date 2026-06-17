"""
LangChain memory integration for Spaider.

Requires: pip install spaider-client[langchain]
"""

from __future__ import annotations

from typing import Any, Optional

try:
    from langchain_core.memory import BaseMemory
    from pydantic import Field as PydanticField
except ImportError as exc:
    raise ImportError(
        "LangChain integration requires the langchain-core package. "
        "Install it with: pip install spaider-client[langchain]"
    ) from exc

from ..client import Spaider


class SpaiderMemory(BaseMemory):
    """
    LangChain memory backed by a Spaider knowledge graph.

    Each conversation turn is ingested into the graph as structured knowledge.
    When the chain requests memory, Spaider performs a semantic query to return
    the most relevant context.

    Usage::

        from langchain.llms import OpenAI
        from langchain.chains import LLMChain
        from langchain.prompts import PromptTemplate
        from spaider.integrations.langchain import SpaiderMemory

        memory = SpaiderMemory(api_key="sk-...", agent_id="my-agent")

        prompt = PromptTemplate(
            input_variables=["history", "input"],
            template="Context:\\n{history}\\n\\nHuman: {input}\\nAI:",
        )
        chain = LLMChain(llm=OpenAI(), prompt=prompt, memory=memory)

        chain.predict(input="Who is Max?")
    """

    # Pydantic fields (LangChain BaseMemory uses pydantic v1 internally)
    api_key: str = PydanticField(..., description="Spaider API key")
    agent_id: str = PydanticField(default="default", description="Spaider agent ID")
    base_url: str = PydanticField(
        default="https://api.spaider.studio", description="Spaider API base URL"
    )
    memory_key: str = PydanticField(
        default="history", description="Key under which memory is stored in chain inputs"
    )
    input_key: str = PydanticField(
        default="input", description="Key for human input in chain inputs"
    )
    output_key: str = PydanticField(
        default="output", description="Key for AI output in chain outputs"
    )
    top_k: int = PydanticField(default=5, description="Number of nodes to retrieve for context")
    max_context_length: int = PydanticField(
        default=2000, description="Maximum characters to include in the context string"
    )

    # Private (not included in pydantic schema)
    _client: Optional[Spaider] = None

    class Config:
        arbitrary_types_allowed = True
        underscore_attrs_are_private = True

    @property
    def client(self) -> Spaider:
        if self._client is None:
            self._client = Spaider(
                api_key=self.api_key,
                agent_id=self.agent_id,
                base_url=self.base_url,
            )
        return self._client

    @property
    def memory_variables(self) -> list[str]:
        """Return the list of memory variable names exposed to the chain."""
        return [self.memory_key]

    def load_memory_variables(self, inputs: dict[str, Any]) -> dict[str, str]:
        """
        Query Spaider for context relevant to the current input.

        Args:
            inputs: Chain inputs, expected to contain ``self.input_key``.

        Returns:
            Dict with ``self.memory_key`` mapped to a context string.
        """
        question = inputs.get(self.input_key, "")
        if not question:
            return {self.memory_key: ""}

        try:
            result = self.client.query(question, top_k=self.top_k)
            context = result.answer[: self.max_context_length]
        except Exception:
            context = ""

        return {self.memory_key: context}

    def save_context(self, inputs: dict[str, Any], outputs: dict[str, str]) -> None:
        """
        Ingest the conversation turn into the Spaider knowledge graph.

        Args:
            inputs: Chain inputs (human message).
            outputs: Chain outputs (AI message).
        """
        human = inputs.get(self.input_key, "")
        ai = outputs.get(self.output_key, "")

        if human:
            try:
                self.client.ingest(human, source="langchain:human")
            except Exception:
                pass  # Non-fatal — never break the chain

        if ai:
            try:
                self.client.ingest(ai, source="langchain:ai")
            except Exception:
                pass

    def clear(self) -> None:
        """
        Clear the in-memory state.

        Note: This does NOT delete the knowledge graph.
        To delete graph data use ``client.delete_node(node_id)`` directly.
        """
        # No in-memory state to clear; graph lives in Spaider.
        pass
