"""
Scaled A/B for ingest-time supersession.

Runs a supersession sequence N times against the current backend and reports,
per update-probe, the staleness rate (current value present AND stale absent)
with a bootstrapped 95% CI. Supersession is a backend flag (SUPERSESSION_ENABLED),
so run this twice — once with it on, once off (restart the backend between) — and
compare the two JSON outputs to get the lift with confidence intervals.

    python -m benchmarks.continual.ab_supersession \
        --sequence benchmarks/continual/sequences/example_supersession_multi.yaml \
        --iterations 12 --out /tmp/ab_on.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
from pathlib import Path

from benchmarks.continual.sequence_runner import load_sequence, run_sequence


def _bootstrap_ci(values: list[float], iters: int = 3000, alpha: float = 0.05):
    """Percentile bootstrap CI for the mean of Bernoulli-ish staleness values."""
    n = len(values)
    if n == 0:
        return 0.0, 0.0, 0.0
    mean = sum(values) / n
    means = []
    for _ in range(iters):
        resample = [values[random.randrange(n)] for _ in range(n)]
        means.append(sum(resample) / n)
    means.sort()
    lo = means[int((alpha / 2) * iters)]
    hi = means[int((1 - alpha / 2) * iters)]
    return mean, lo, hi


async def _run(args) -> dict:
    seq = load_sequence(Path(args.sequence))
    # Update probes live on tasks that declare a `stale` value.
    update_task_ids = [t.id for t in seq.tasks if any(p.stale for p in t.probes)]
    per_task: dict[str, list[float]] = {tid: [] for tid in update_task_ids}

    for i in range(args.iterations):
        result = await run_sequence(
            seq, args.base_url, "staleness", args.tenant, keep_agent=False,
            redis_url=args.redis_url or None, memory_mode=args.memory_mode, top_k=args.top_k,
        )
        report = result["report"]
        ids = report["task_ids"]
        matrix = report["accuracy_matrix"]
        for tid in update_task_ids:
            idx = ids.index(tid)
            per_task[tid].append(float(matrix[idx][idx]))  # staleness when the task is current
        print(f"  iter {i + 1}/{args.iterations}: " +
              ", ".join(f"{tid}={per_task[tid][-1]:.0f}" for tid in update_task_ids), flush=True)

    out = {
        "sequence": seq.sequence_id,
        "label": args.label,
        "iterations": args.iterations,
        "tasks": {},
    }
    for tid, vals in per_task.items():
        mean, lo, hi = _bootstrap_ci(vals)
        out["tasks"][tid] = {"mean": mean, "ci95": [lo, hi], "n": len(vals), "values": vals}
    return out


def main() -> int:
    p = argparse.ArgumentParser(description="Scaled A/B for supersession (staleness + CIs)")
    p.add_argument("--sequence", required=True)
    p.add_argument("--iterations", type=int, default=12)
    p.add_argument("--label", default="run", help="label for this batch (e.g. 'supersession-on')")
    p.add_argument("--base-url", default="http://localhost:8000/api/v1")
    p.add_argument("--redis-url", default="redis://localhost:6379")
    p.add_argument("--memory-mode", default="on", choices=["on", "off"])
    p.add_argument("--top-k", type=int, default=None)
    p.add_argument("--tenant", default="default")
    p.add_argument("--out", default=None, help="write the summary JSON here")
    args = p.parse_args()

    out = asyncio.run(_run(args))
    print(json.dumps(out, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(out, indent=2))
        print(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
