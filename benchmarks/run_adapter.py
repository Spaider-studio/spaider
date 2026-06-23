"""Subprocess entrypoint that runs ONE memory system's arm(s).

Each competitor lives in its own virtualenv (``benchmarks/.venv-mem0``,
``benchmarks/.venv-cognee``) to keep their dependency trees from colliding with
each other or with the backend. The orchestrator shells out to:

    benchmarks/.venv-<sys>/bin/python -m benchmarks.run_adapter \\
        --system mem0 --answer-mode both \\
        --tasks benchmarks/tasks/acmeai --runs benchmarks/runs

Results are appended to the shared ``runs/*.jsonl`` in the SAME RunRecord shape
as vanilla / with-spaider, so the scorer reads every arm uniformly regardless of
which venv produced it.

Requires (in the venv): the system's package (mem0ai / cognee), ``litellm``,
``pyyaml``, ``mcp`` (spaider only), and the ``benchmarks`` package importable
(run from the repo root or ``pip install -e .``). ``OPENAI_API_KEY`` must be set
(and ``SPAIDER_API_KEY`` / ``SPAIDER_MCP_URL`` for ``--system spaider``).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

from benchmarks.adapters import ADAPTER_NAMES, build_adapter
from benchmarks.runner import (
    _append_record,
    _resolve_llm_config,
    load_tasks,
    run_with_adapter,
)

logger = logging.getLogger("benchmarks.run_adapter")


async def _run(args: argparse.Namespace) -> int:
    cfg = _resolve_llm_config(SimpleNamespace(provider=args.provider, model=args.model))
    tasks = load_tasks(Path(args.tasks))
    if args.limit:
        tasks = tasks[: args.limit]
    runs_dir = Path(args.runs)
    modes = ["fixed", "native"] if args.answer_mode == "both" else [args.answer_mode]

    adapter = build_adapter(args.system)
    print(
        f"running {len(tasks)} task(s) × {len(modes)} mode(s) × {args.sweeps} sweep(s) "
        f"for system={args.system} model={cfg.model}",
        file=sys.stderr,
    )
    failed = 0
    try:
        for sweep in range(args.sweeps):
            for task in tasks:
                for mode in modes:
                    record = await run_with_adapter(adapter, task, cfg, mode)
                    _append_record(record, runs_dir)
                    status = "ERR " if record.error else ("OK " if record.success else "FAIL")
                    print(
                        f"  [{status}] s{sweep} {record.mode:16s} {task.id:28s} "
                        f"{record.wall_time_ms:>7.0f}ms tok_in={record.tokens_in:>5} "
                        f"out={record.tokens_out:>4}",
                        file=sys.stderr,
                    )
                    if record.error:
                        failed += 1
    finally:
        await adapter.aclose()

    print(f"done: {failed} errored row(s)", file=sys.stderr)
    return 1 if failed else 0


def main() -> int:
    p = argparse.ArgumentParser(description="Run one memory system's benchmark arm(s)")
    p.add_argument("--system", required=True, choices=list(ADAPTER_NAMES))
    p.add_argument(
        "--answer-mode", default="both", choices=["fixed", "native", "both"],
        help="fixed = retrieve + shared reader; native = system's own QA; both = both.",
    )
    p.add_argument("--tasks", required=True, help="task YAML file or directory")
    p.add_argument("--runs", default="benchmarks/runs", help="JSONL output dir")
    p.add_argument("--provider", default=None, help="LLM provider (default $LLM_PROVIDER)")
    p.add_argument("--model", default=None, help="model id (default $LLM_MODEL)")
    p.add_argument("--sweeps", type=int, default=1, help="repeat the suite N times for CIs")
    p.add_argument("--limit", type=int, default=0, help="cap number of tasks (smoke test)")
    p.add_argument("--log-level", default="WARNING", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = p.parse_args()

    logging.basicConfig(level=args.log_level, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
