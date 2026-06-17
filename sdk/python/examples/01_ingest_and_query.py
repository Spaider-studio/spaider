"""Basic ingest + query loop.

Pushes a few facts into the agent's knowledge graph, then asks a
natural-language question. Prints the answer and the supporting nodes
the graph retrieved.

    export SPAIDER_API_KEY=sk-...
    export SPAIDER_BASE_URL=http://localhost:8080
    python 01_ingest_and_query.py
"""

import os

from spaider import Spaider


def main() -> None:
    api_key = os.environ["SPAIDER_API_KEY"]
    base_url = os.environ.get("SPAIDER_BASE_URL", "http://localhost:8080")
    agent_id = os.environ.get("SPAIDER_AGENT_ID", "examples-01")

    with Spaider(api_key=api_key, agent_id=agent_id, base_url=base_url) as sp:
        # Ingest unstructured text — the backend's SemanticCompressor pulls
        # out entities and relationships, deduplicates against what's already
        # in the agent's graph, and stores the result.
        facts = [
            "Max Mustermann works at Google as a Software Engineer since 2023.",
            "Google was founded in 1998 by Larry Page and Sergey Brin.",
            "Google's headquarters is in Mountain View, California.",
        ]
        for text in facts:
            result = sp.ingest(text, source="examples-01")
            print(f"ingested → +{result.nodes_created} nodes, +{result.edges_created} edges")

        # Ask a natural-language question. The query path retrieves the
        # most-relevant subgraph and grounds an LLM-generated answer in it.
        answer = sp.query("Where does Max work and when did he start?", top_k=5)

        print()
        print("Question: Where does Max work and when did he start?")
        print(f"Answer:   {answer.text}")
        print(f"Confidence: {answer.confidence:.2f}")
        print("Supporting nodes:")
        for node in answer.subgraph.nodes:
            print(f"  - {node.type}: {node.label}")


if __name__ == "__main__":
    main()
