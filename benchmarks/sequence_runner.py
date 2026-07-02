"""
Task-sequence continual-learning benchmark for SpAIder.

Measures what no competitor reports for a memory system: does it FORGET earlier
knowledge as new knowledge arrives, and does earlier knowledge TRANSFER to help
later tasks. Runs an ordered sequence of tasks, each = a corpus to ingest plus
probe (question, expected_output) pairs, and fills the standard continual-learning
accuracy matrix, then computes the metrics in ``cl_metrics.py``.

Protocol (tasks ordered 0..T-1), all against ONE fresh agent:
  1. Cold baseline: probe every task on the empty graph.
  2. For each task i in order:
       a. probe task i BEFORE ingesting it (forward-transfer signal, pre[i]);
       b. ingest task i's corpus;
       c. probe tasks 0..i (fill matrix row i).
  3. Compute final accuracy, average forgetting, backward + forward transfer.

Talks to SpAIder over the REST API (POST /agents, /ingest/sync, /query) rather
than MCP, so it is independent of MCP transport and needs no pre-provisioned key.
A fresh agent per run gives clean isolation. Scoring reuses the canonical
HotpotQA-style scorers in ``runner.py`` (lazy-imported so this module loads with
no heavy deps for offline validation).

Usage:
    python -m benchmarks.sequence_runner --sequence benchmarks/sequences/example_org_products.yaml
    python -m benchmarks.sequence_runner --sequence <path> --dry-run   # validate only, no stack
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional

import yaml

from benchmarks.cl_metrics import compute_cl_metrics

# ---------------------------------------------------------------------------
# Sequence schema (2a)
# ---------------------------------------------------------------------------


@dataclass
class Probe:
    question: str
    expected_output: str


@dataclass
class SeqTask:
    id: str
    title: str
    corpus: list[str]
    probes: list[Probe]


@dataclass
class Sequence:
    sequence_id: str
    description: str
    tasks: list[SeqTask] = field(default_factory=list)


def load_sequence(path: Path) -> Sequence:
    """Load and validate a sequence YAML. Raises ValueError on a malformed file."""
    data = yaml.safe_load(Path(path).read_text())
    if not isinstance(data, dict) or "tasks" not in data:
        raise ValueError(f"{path}: expected a mapping with a 'tasks' list")
    tasks: list[SeqTask] = []
    for i, t in enumerate(data["tasks"]):
        for key in ("id", "title", "corpus", "probes"):
            if key not in t:
                raise ValueError(f"{path}: task #{i} missing '{key}'")
        probes = [Probe(question=p["question"], expected_output=p["expected_output"]) for p in t["probes"]]
        if not t["corpus"]:
            raise ValueError(f"{path}: task '{t['id']}' has an empty corpus")
        if not probes:
            raise ValueError(f"{path}: task '{t['id']}' has no probes")
        tasks.append(SeqTask(id=t["id"], title=t["title"], corpus=list(t["corpus"]), probes=probes))
    if len(tasks) < 2:
        raise ValueError(f"{path}: a sequence needs at least 2 tasks to measure forgetting")
    ids = [t.id for t in tasks]
    if len(set(ids)) != len(ids):
        raise ValueError(f"{path}: duplicate task ids {ids}")
    return Sequence(
        sequence_id=data.get("sequence_id", Path(path).stem),
        description=data.get("description", ""),
        tasks=tasks,
    )


# ---------------------------------------------------------------------------
# Scoring (lazy import so offline validation needs no heavy deps)
# ---------------------------------------------------------------------------


def _get_scorer(metric: str) -> Callable[[str, str], float]:
    from benchmarks.runner import _compute_exact_match, _compute_f1, _compute_rouge_l

    table = {"f1": _compute_f1, "em": _compute_exact_match, "rouge": _compute_rouge_l}
    if metric not in table:
        raise ValueError(f"unknown metric '{metric}' (choose from {sorted(table)})")
    return table[metric]


# ---------------------------------------------------------------------------
# SpAIder REST client (minimal)
# ---------------------------------------------------------------------------


async def _create_agent(client, base_url: str, name: str, tenant: str) -> tuple[str, str]:
    resp = await client.post(
        f"{base_url}/agents",
        json={"name": name, "tenant_id": tenant, "permissions": ["read", "write", "query"]},
    )
    resp.raise_for_status()
    d = resp.json()
    agent = d.get("agent", d)
    return agent["id"], agent.get("api_key", "")


async def _delete_agent(client, base_url: str, agent_id: str) -> None:
    try:
        await client.delete(f"{base_url}/agents/{agent_id}")
    except Exception:
        pass  # cleanup is best-effort


async def _ingest(client, base_url: str, key: str, agent_id: str, text: str) -> None:
    resp = await client.post(
        f"{base_url}/ingest/sync",
        headers={"Authorization": f"Bearer {key}"},
        json={"agent_id": agent_id, "text": text},
    )
    resp.raise_for_status()


def _query_cache_key(agent_id: str, question: str) -> str:
    """Mirror backend QueryService._cache_key so the harness can bust it.

    A continual-learning run probes the same question at different knowledge
    states on one agent; without busting, the empty-graph baseline answer would
    be served from cache after ingestion. Kept in lockstep with query_service.py.
    """
    h = hashlib.sha256(f"{agent_id}:{question.strip().lower()}".encode()).hexdigest()[:16]
    return f"spaider:query:cache:{h}"


async def _query(client, base_url: str, key: str, agent_id: str, question: str, rds=None) -> str:
    # Force a fresh read: the same question is probed at different graph states.
    if rds is not None:
        try:
            await rds.delete(_query_cache_key(agent_id, question))
        except Exception:
            pass  # a stale cache would only understate learning; never fatal
    resp = await client.post(
        f"{base_url}/query",
        headers={"Authorization": f"Bearer {key}"},
        json={"agent_id": agent_id, "question": question},
    )
    resp.raise_for_status()
    d = resp.json()
    # Prefer the concise factoid span; fall back to the full grounded answer.
    return (d.get("direct_answer") or d.get("answer") or "").strip()


async def _task_accuracy(
    client, base_url: str, key: str, agent_id: str, task: SeqTask,
    scorer: Callable[[str, str], float], rds=None,
) -> float:
    scores: list[float] = []
    for probe in task.probes:
        answer = await _query(client, base_url, key, agent_id, probe.question, rds=rds)
        scores.append(scorer(answer, probe.expected_output))
    return sum(scores) / len(scores) if scores else 0.0


# ---------------------------------------------------------------------------
# Orchestration (2b forgetting + 2c transfer)
# ---------------------------------------------------------------------------


async def run_sequence(
    seq: Sequence, base_url: str, metric: str, tenant: str, keep_agent: bool,
    redis_url: Optional[str] = None,
) -> dict:
    import httpx  # lazy: only needed for a live run

    scorer = _get_scorer(metric)
    t = len(seq.tasks)

    # Optional cache-busting client: the query cache is keyed by (agent, question)
    # and would otherwise serve the empty-graph baseline answer after ingestion.
    rds = None
    if redis_url:
        try:
            import redis.asyncio as aioredis

            rds = aioredis.from_url(redis_url, decode_responses=True)
            await rds.ping()
        except Exception as exc:
            print(f"WARNING: could not reach Redis at {redis_url} ({exc}); "
                  "results may be understated by stale query cache.")
            rds = None

    async with httpx.AsyncClient(timeout=180.0) as client:
        agent_id, key = await _create_agent(client, base_url, f"cl-{seq.sequence_id}", tenant)
        try:
            # 1. Cold baseline on the empty graph.
            baseline = [
                await _task_accuracy(client, base_url, key, agent_id, task, scorer, rds=rds)
                for task in seq.tasks
            ]

            matrix: list[list[Optional[float]]] = [[None] * t for _ in range(t)]
            pre = [0.0] * t
            pre[0] = baseline[0]

            for i, task in enumerate(seq.tasks):
                # a. forward-transfer probe: task i before it is ingested.
                if i >= 1:
                    pre[i] = await _task_accuracy(client, base_url, key, agent_id, task, scorer, rds=rds)
                # b. ingest this task's corpus.
                for fact in task.corpus:
                    await _ingest(client, base_url, key, agent_id, fact)
                # c. probe every task seen so far.
                for j in range(i + 1):
                    matrix[i][j] = await _task_accuracy(
                        client, base_url, key, agent_id, seq.tasks[j], scorer, rds=rds
                    )

            report = compute_cl_metrics([x.id for x in seq.tasks], matrix, baseline, pre=pre)
        finally:
            if not keep_agent:
                await _delete_agent(client, base_url, agent_id)
            if rds is not None:
                await rds.aclose()

    return {
        "sequence_id": seq.sequence_id,
        "metric": metric,
        "agent_id": agent_id,
        "kept_agent": keep_agent,
        "report": asdict(report),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_summary(result: dict) -> None:
    r = result["report"]
    print(f"\nSequence: {result['sequence_id']}  (metric={result['metric']})")
    print("-" * 60)
    print(f"  Final accuracy (ACC):   {r['final_accuracy']:.3f}")
    print(f"  Average forgetting:     {r['average_forgetting']:.3f}  (lower is better; <=0 = none)")
    print(f"  Backward transfer (BWT):{r['backward_transfer']:+.3f}  (>0 later learning helped earlier)")
    fwt = r["forward_transfer"]
    if fwt is not None:
        print(f"  Forward transfer (FWT): {fwt:+.3f}  (>0 earlier learning helped later)")
    else:
        print("  Forward transfer (FWT): n/a")
    print("  Per-task final / forgetting:")
    for tid in r["task_ids"]:
        f = r["per_task_forgetting"].get(tid)
        fs = f"{f:+.3f}" if f is not None else "  -  "
        print(f"    {tid:<20} final={r['per_task_final'][tid]:.3f}  forgetting={fs}")


def main() -> int:
    p = argparse.ArgumentParser(description="SpAIder continual-learning (forgetting + transfer) benchmark")
    p.add_argument("--sequence", required=True, help="path to a sequence YAML")
    p.add_argument("--base-url", default="http://localhost:8000/api/v1", help="SpAIder REST base URL")
    p.add_argument("--metric", default="f1", choices=["f1", "em", "rouge"], help="per-probe accuracy metric")
    p.add_argument("--tenant", default="default", help="tenant_id for the throwaway agent")
    p.add_argument("--keep-agent", action="store_true", help="do not delete the agent after the run")
    p.add_argument(
        "--redis-url",
        default="redis://localhost:6379",
        help="Redis URL for busting the query cache so re-probes read fresh state; "
             "set to empty to disable (risks stale, understated results)",
    )
    p.add_argument("--runs", default="benchmarks/runs", help="output dir for the summary JSON")
    p.add_argument("--dry-run", action="store_true", help="validate the sequence and print the plan; no stack calls")
    args = p.parse_args()

    seq = load_sequence(Path(args.sequence))

    if args.dry_run:
        print(f"Sequence '{seq.sequence_id}' OK: {len(seq.tasks)} tasks")
        for task in seq.tasks:
            print(f"  - {task.id}: {len(task.corpus)} facts, {len(task.probes)} probes  ({task.title})")
        print("\nPlan: cold baseline -> for each task [pre-probe, ingest, probe all seen] -> CL metrics.")
        return 0

    result = asyncio.run(
        run_sequence(
            seq, args.base_url, args.metric, args.tenant, args.keep_agent,
            redis_url=args.redis_url or None,
        )
    )
    _print_summary(result)

    runs_dir = Path(args.runs)
    runs_dir.mkdir(parents=True, exist_ok=True)
    out = runs_dir / f"cl_{seq.sequence_id}_{args.metric}.json"
    out.write_text(json.dumps(result, indent=2))
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
