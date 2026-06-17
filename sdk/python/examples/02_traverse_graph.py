"""Traverse the graph from a starting node.

Useful for "show me everything connected to X" use cases — visualisations,
audit reports, GDPR data-subject-access requests. Pick a node by querying
first (to get its ID), then walk N hops from there.

    export SPAIDER_API_KEY=sk-...
    export SPAIDER_BASE_URL=http://localhost:8080
    python 02_traverse_graph.py
"""

import os

from spaider import Spaider


def main() -> None:
    api_key = os.environ["SPAIDER_API_KEY"]
    base_url = os.environ.get("SPAIDER_BASE_URL", "http://localhost:8080")
    agent_id = os.environ.get("SPAIDER_AGENT_ID", "examples-02")

    with Spaider(api_key=api_key, agent_id=agent_id, base_url=base_url) as sp:
        # Seed some interconnected facts so the traversal has something to walk.
        for text in (
            "Alice founded Acme Corp in 2018.",
            "Acme Corp acquired Beta LLC in 2022.",
            "Beta LLC is based in Berlin and was founded by Bob in 2015.",
            "Bob and Alice studied together at TU Munich.",
        ):
            sp.ingest(text, source="examples-02")

        # Find Alice's node by querying for her name.
        result = sp.query("Who is Alice?", top_k=3)
        if not result.subgraph.nodes:
            print("No matching node found — ingest may still be processing.")
            return

        seed = next(
            (n for n in result.subgraph.nodes if "alice" in n.label.lower()),
            result.subgraph.nodes[0],
        )
        print(f"Starting traversal from node: {seed.label} (id={seed.id})")

        # Walk 3 hops out from Alice. Should reach Acme, Beta, Bob, TU Munich.
        subgraph = sp.traverse(node_id=seed.id, depth=3)

        print(f"\nReached {subgraph.total_nodes} nodes via {subgraph.total_edges} edges:")
        for node in subgraph.nodes:
            print(f"  - {node.type}: {node.label}")

        print("\nRelationships:")
        for edge in subgraph.edges:
            print(f"  - {edge.source_id[:8]}… --[{edge.relation}]--> {edge.target_id[:8]}…")


if __name__ == "__main__":
    main()
