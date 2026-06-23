"""Seed a competitor memory system with a corpus, using the SAME ingest dicts
the SpAIder seeder uses (``benchmarks.seed._load_corpus``) so every system sees
identical facts.

Runs inside the system's own venv:

    benchmarks/.venv-mem0/bin/python -m benchmarks.seed_competitors \\
        --system mem0 --corpus benchmarks/corpus/acmeai_30d.yaml

    benchmarks/.venv-cognee/bin/python -m benchmarks.seed_competitors \\
        --system cognee --corpus benchmarks/corpus/hotpotqa_24.yaml

Each system writes to its own local store under ``benchmarks/.bench_data/<sys>/``
(gitignored). ``--system spaider`` is intentionally rejected — seed SpAIder with
``python -m benchmarks.seed`` instead (its dedicated MCP path).
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from benchmarks.adapters import build_adapter
from benchmarks.seed import _load_corpus


async def _seed(system: str, corpus: Path, limit: int) -> int:
    facts = _load_corpus(corpus)
    if limit:
        facts = facts[:limit]
    adapter = build_adapter(system)
    print(
        f"seeding {len(facts)} fact(s) from {corpus.name} into {system} "
        f"(store: benchmarks/.bench_data/{system}/) …",
        file=sys.stderr,
    )
    try:
        await adapter.ingest(facts)
    finally:
        await adapter.aclose()
    print(f"done: {len(facts)} fact(s) ingested into {system}", file=sys.stderr)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Seed a competitor memory system")
    p.add_argument("--system", required=True, choices=["mem0", "cognee"])
    p.add_argument("--corpus", required=True, help="corpus YAML (e.g. benchmarks/corpus/acmeai_30d.yaml)")
    p.add_argument("--limit", type=int, default=0, help="cap number of facts (smoke test)")
    args = p.parse_args()
    return asyncio.run(_seed(args.system, Path(args.corpus), args.limit))


if __name__ == "__main__":
    sys.exit(main())
