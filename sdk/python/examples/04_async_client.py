"""Async client for concurrency-heavy workloads.

If you're batch-ingesting documents, syncing from a feed, or running
many queries at once, `AsyncSpaider` lets you issue requests
concurrently from a single event loop without thread overhead.

    export SPAIDER_API_KEY=sk-...
    export SPAIDER_BASE_URL=http://localhost:8080
    python 04_async_client.py
"""

import asyncio
import os

from spaider import AsyncSpaider


async def main() -> None:
    api_key = os.environ["SPAIDER_API_KEY"]
    base_url = os.environ.get("SPAIDER_BASE_URL", "http://localhost:8080")
    agent_id = os.environ.get("SPAIDER_AGENT_ID", "examples-04")

    async with AsyncSpaider(api_key=api_key, agent_id=agent_id, base_url=base_url) as sp:
        # Fan out 5 ingests concurrently — finishes in roughly the time of
        # the slowest one rather than the sum of all five.
        docs = [
            "Marie Curie discovered radium in 1898.",
            "She was the first person to win two Nobel Prizes.",
            "Pierre Curie shared the 1903 Physics Nobel with her.",
            "Curie founded the Radium Institute in Paris in 1909.",
            "Her daughter Irène also won a Nobel Prize in 1935.",
        ]
        results = await asyncio.gather(*(sp.ingest(d, source="examples-04") for d in docs))
        total_nodes = sum(r.nodes_created for r in results)
        total_edges = sum(r.edges_created for r in results)
        print(f"Ingested {len(docs)} documents concurrently → {total_nodes} nodes, {total_edges} edges")

        # Same fan-out for queries.
        questions = [
            "Who discovered radium?",
            "Who won two Nobel Prizes?",
            "What did Marie Curie found?",
        ]
        answers = await asyncio.gather(*(sp.query(q, top_k=3) for q in questions))

        print()
        for q, a in zip(questions, answers):
            print(f"Q: {q}")
            print(f"A: {a.text}\n")


if __name__ == "__main__":
    asyncio.run(main())
