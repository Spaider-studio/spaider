"""
Re-embed an agent's nodes under their full semantic surface.

Older deployments embedded most nodes under the bare label (see
entity_resolver.build_embed_text for the why), so vector recall against
question-shaped queries was poor. After the description/source_text
column migration, run this once per agent to recompute embeddings from
label + description + source_text.

Deliberately a manual maintenance script, not a boot migration: it makes
one embedding-API call per node and a big production graph could cost
real money — the operator decides when and for which agents.

Run inside the backend container:

    python -m app.scripts.reembed_agent --agent-id <uuid> [--batch-size 64] [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from app.models.schemas import Node
from app.services.embedding_service import EmbeddingService
from app.services.entity_resolver import build_embed_text
from app.services.graph_service import GraphService

logger = logging.getLogger(__name__)


async def reembed_agent(agent_id: str, batch_size: int = 64, dry_run: bool = False) -> int:
    """Recompute embeddings for every node of *agent_id*. Returns node count."""
    graph = GraphService()
    await graph.initialize()
    embedder = EmbeddingService()

    try:
        async with graph._driver.session() as session:
            result = await session.run(
                """
                MATCH (n:SpaiderNode {agent_id: $agent_id})
                RETURN n.id AS id, n.label AS label, n.type AS type,
                       n.description AS description, n.properties AS properties
                """,
                agent_id=agent_id,
            )
            records = await result.data()

        if not records:
            print(f"no nodes found for agent {agent_id}")
            return 0

        nodes: list[Node] = []
        for rec in records:
            props = rec.get("properties")
            if isinstance(props, str):
                try:
                    props = json.loads(props)
                except (json.JSONDecodeError, ValueError):
                    props = {}
            nodes.append(Node(
                id=rec["id"],
                label=rec.get("label") or "",
                type=rec.get("type") or "OTHER",
                description=rec.get("description"),
                properties=props or {},
            ))

        print(f"re-embedding {len(nodes)} node(s) for agent {agent_id}"
              f"{' (dry run)' if dry_run else ''}")

        done = 0
        for start in range(0, len(nodes), batch_size):
            batch = nodes[start:start + batch_size]
            texts = [build_embed_text(n) for n in batch]
            if dry_run:
                for n, t in zip(batch[:3], texts[:3]):
                    print(f"  {n.label!r} -> embeds {t[:90]!r}")
                done += len(batch)
                continue
            embeddings = await embedder.embed_batch(texts)
            rows = [
                {"id": n.id, "embedding": e}
                for n, e in zip(batch, embeddings)
                if e
            ]
            async with graph._driver.session() as session:
                await session.run(
                    """
                    UNWIND $rows AS row
                    MATCH (n:SpaiderNode {id: row.id})
                    SET n.embedding = row.embedding
                    """,
                    rows=rows,
                )
            done += len(rows)
            print(f"  [{done}/{len(nodes)}]")

        return done
    finally:
        await embedder.close()
        await graph.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--dry-run", action="store_true",
                        help="print the embed text for a sample, write nothing")
    args = parser.parse_args()

    count = asyncio.run(reembed_agent(args.agent_id, args.batch_size, args.dry_run))
    print(f"done: {count} node(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
