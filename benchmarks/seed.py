"""
Seed the calling agent's SpAIder graph with facts before running with-spaider.

Two modes:

* **Default (`--corpus` not set):** ingest the v1 fixed seed pack — the
  two facts that the v1 memory-only tasks (04, 05) ask about.
* **`--corpus PATH`:** ingest a structured corpus YAML produced by
  ``benchmarks.generate_corpus``. The Compounding-Brain demo uses this
  with ``benchmarks/corpus/acmeai_30d.yaml``.

Both paths use ``spaider.ingest_fact`` over the public MCP endpoint —
same code path Claude Code itself uses. Calls run in bounded parallel
(default 8 in flight) so 4K-fact corpora finish in hours instead of
days; cap with ``--concurrency`` if your MCP server or LLM provider
can't keep up.

Usage
-----
    # v1 seed (2 facts):
    export SPAIDER_API_KEY=sk-...
    benchmarks/.venv/bin/python -m benchmarks.seed

    # Compounding-Brain seed (~80 facts):
    benchmarks/.venv/bin/python -m benchmarks.seed \\
        --corpus benchmarks/corpus/acmeai_30d.yaml

    # Big haystack with conservative concurrency:
    benchmarks/.venv/bin/python -m benchmarks.seed \\
        --corpus benchmarks/corpus/hotpotqa_haystack_500k.yaml \\
        --concurrency 4

Exits non-zero if the MCP server is unreachable, auth fails, or any
single ingest fails.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import yaml

# v1 seed pack — kept inline for the legacy memory-recall tasks (04, 05).
_V1_SEED_FACTS: list[dict[str, str]] = [
    {
        "task": "04_recall_branch_pref",
        "text": (
            "User preference: branch naming convention for new feature work in "
            "this repository is feature/issue-NN-short-name. Bug fixes use "
            "fix/issue-NN-short-desc. Repo hygiene uses chore/short-desc. "
            "One issue per branch, one branch per PR."
        ),
        "source": "benchmark-seed:branch-convention",
    },
    {
        "task": "05_recall_hot_files",
        "text": (
            "Concurrency hot-files in this repository — files that should be "
            "checked against open PRs before editing because parallel agents "
            "tend to collide on them: "
            "backend/app/api/v1/ingest.py, "
            "backend/app/services/query_service.py, "
            "backend/app/services/parser_service.py, "
            "backend/app/connectors/__init__.py, "
            "backend/app/services/auth_service.py, "
            "frontend/src/components/agents/AgentDetail.tsx, "
            "frontend/src/components/studio/MultiverseCanvas.tsx."
        ),
        "source": "benchmark-seed:hot-files",
    },
]


def _load_corpus(path: Path) -> list[dict[str, str]]:
    """Read a corpus YAML and return ingest-ready dicts."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    facts_in: list[dict[str, Any]] = raw.get("facts", []) or []
    out: list[dict[str, str]] = []
    for f in facts_in:
        # Prefix with the date and type so the extraction pipeline keeps
        # temporal/category structure as graph entities.
        text = f"[{f['date']}] [{f['type']}] {f['text']}"
        out.append({
            "task": f.get("source", "corpus"),
            "text": text,
            "source": f.get("source", "corpus"),
        })
    return out


async def _seed(facts: list[dict[str, str]], concurrency: int) -> int:
    """Ingest each fact via spaider.ingest_fact over MCP, with bounded
    parallelism.

    Why one MCP session per worker (not one shared session): Starlette's
    SSE middleware fights with itself when many concurrent ``call_tool``
    invocations multiplex on a single response stream — observed during a
    4,177-fact run, where the long-lived shared session eventually
    AssertionError'd inside Starlette and most subsequent tool calls came
    back as exceptions with empty error messages. Spawning N independent
    workers, each with its own ``sse_client`` + ``ClientSession``, costs
    ~200 ms of handshake per worker (one-time) but eliminates the SSE
    multiplexing race.
    """
    from mcp import ClientSession
    from mcp.client.sse import sse_client

    api_key = os.environ.get("SPAIDER_API_KEY")
    if not api_key:
        print(
            "error: SPAIDER_API_KEY not set. Run scripts/dev/setup_mcp_dev_agent.sh "
            "to provision a dev-{user} agent and copy its key.",
            file=sys.stderr,
        )
        return 2
    mcp_url = os.environ.get(
        "SPAIDER_MCP_URL", "http://localhost:8001/api/v1/mcp/sse",
    )

    total = len(facts)
    # Print every 10 completions for small seeds, every ~1% for big ones —
    # avoids drowning a 4K-fact run in noise while keeping a heartbeat for
    # 50-fact ones. The counter is incremented *as ingests complete* (not in
    # input order), since concurrent tasks finish out of order.
    progress_step = max(10, total // 100)
    print(
        f"seeding {total} fact(s) via {mcp_url} (concurrency={concurrency})",
        file=sys.stderr,
    )

    queue: asyncio.Queue[dict[str, str]] = asyncio.Queue()
    for fact in facts:
        queue.put_nowait(fact)

    done = 0
    failed = 0
    headers = {"Authorization": f"Bearer {api_key}"}

    async def _worker(worker_id: int) -> None:
        """Open one MCP session and drain the queue. Each call_tool runs on
        this worker's own SSE stream — no cross-worker multiplexing."""
        nonlocal done, failed
        async with sse_client(mcp_url, headers=headers) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                while True:
                    try:
                        fact = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        return
                    try:
                        result = await session.call_tool(
                            "spaider.ingest_fact",
                            {"text": fact["text"], "source": fact["source"]},
                        )
                        if getattr(result, "isError", False):
                            failed += 1
                            print(
                                f"    FAILED: {fact['source']}",
                                file=sys.stderr,
                            )
                    except Exception as exc:  # noqa: BLE001
                        failed += 1
                        print(
                            f"    EXCEPTION on {fact['source']}: {exc!r}",
                            file=sys.stderr,
                        )
                    finally:
                        done += 1
                        if done == 1 or done == total or done % progress_step == 0:
                            print(
                                f"  [{done:>4}/{total}] ingesting...",
                                file=sys.stderr,
                            )

    workers = [asyncio.create_task(_worker(i)) for i in range(concurrency)]
    await asyncio.gather(*workers, return_exceptions=False)

    if failed:
        print(f"seed complete with {failed} failure(s).", file=sys.stderr)
        return 1
    print("seed complete.", file=sys.stderr)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Seed SpAIder with benchmark facts.")
    p.add_argument(
        "--corpus", default=None,
        help="path to a corpus YAML (e.g. benchmarks/corpus/acmeai_30d.yaml). "
             "If omitted, ingests the v1 fixed seed pack.",
    )
    p.add_argument(
        "--concurrency", type=int, default=8,
        help="number of in-flight spaider.ingest_fact calls (default: 8). "
             "Drop to 1 to reproduce the legacy serial behaviour; bump higher "
             "if your MCP server and LLM provider can keep up.",
    )
    args = p.parse_args()
    if args.concurrency < 1:
        print("error: --concurrency must be >= 1", file=sys.stderr)
        return 2
    if args.corpus:
        facts = _load_corpus(Path(args.corpus))
    else:
        facts = _V1_SEED_FACTS
    return asyncio.run(_seed(facts, concurrency=args.concurrency))


if __name__ == "__main__":
    sys.exit(main())
