"""Community scorecard: vanilla-vs-with-spaider benchmark aggregation.

Reads runner.py JSONL output (composite oracle: EM / F1 / GEval / ROUGE-L) for
two arms, ``vanilla`` (LLM alone) and ``with-spaider`` (LLM + SpAIder memory),
and reports, per metric, each arm's mean with a **95% CI bootstrapped over the
distinct questions** (not the graded rows), plus the **lift** (with-spaider −
vanilla) with a *paired* bootstrap CI.

Methodology: each distinct question's repeated sweeps are collapsed to one
per-question mean before resampling (a cluster bootstrap over questions), so the
CIs reflect question-level uncertainty. GEval is the headline metric:
EM/F1 reward surface token-overlap and understate correct-but-verbose answers;
exactly the failure mode an AI-memory system is meant to fix.

Usage:
    python -m benchmarks.community_scorecard --runs benchmarks/runs \
        --out benchmarks/COMMUNITY_SCORECARD.md [--chart benchmarks/scorecard.png]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import random
import statistics
from collections import defaultdict

random.seed(1234)  # reproducible CIs

# Base (model-independent) metrics; always shown.
_BASE_METRICS = [
    ("f1_score", "F1", ""),
    ("exact_match", "Exact Match", ""),
    ("rouge_l_score", "ROUGE-L", ""),
]
ARMS = ["vanilla", "with-spaider"]
B = 10000  # bootstrap resamples


def _discover_metrics(runs_dir: str) -> list[tuple[str, str, str]]:
    """GEval judge family (independent judges first, headlined; then the
    run-model self-judge) followed by the base EM/F1/ROUGE metrics."""
    judges: set[str] = set()
    self_judge = False
    for path in glob.glob(os.path.join(runs_dir, "*.jsonl")):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                for k in d:
                    if k.startswith("geval__") and not k.endswith("__rationale"):
                        judges.add(k)
                    elif k == "geval_score":
                        self_judge = True
    metrics: list[tuple[str, str, str]] = []
    for i, g in enumerate(sorted(judges)):
        model = g[len("geval__"):]
        metrics.append((g, f"GEval (judge: {model})", "headline" if i == 0 else ""))
    if self_judge:
        metrics.append(("geval_score", "GEval (self-judge)", "" if judges else "headline"))
    metrics += _BASE_METRICS
    return metrics


def _load(runs_dir: str, metrics) -> dict[str, dict[str, dict[str, list[float]]]]:
    """{arm: {metric: {task_id: [values across sweeps]}}} + token bookkeeping."""
    data: dict = {a: defaultdict(lambda: defaultdict(list)) for a in ARMS}
    tokens: dict = {a: defaultdict(list) for a in ARMS}
    hits: dict = {a: defaultdict(list) for a in ARMS}
    rows = 0
    for path in glob.glob(os.path.join(runs_dir, "*.jsonl")):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                arm = r.get("mode")
                if arm not in ARMS:
                    continue
                tid = r.get("task_id") or r.get("id")
                rows += 1
                for key, _, _ in metrics:
                    v = r.get(key)
                    if isinstance(v, (int, float)):
                        data[arm][key][tid].append(float(v))
                ti, to = r.get("tokens_in"), r.get("tokens_out")
                if isinstance(ti, (int, float)) and isinstance(to, (int, float)):
                    tokens[arm][tid].append(float(ti) + float(to))
                h = r.get("retrieval_hit")
                if isinstance(h, (int, float)):
                    hits[arm][tid].append(float(h))
    return {"data": data, "tokens": tokens, "hits": hits, "rows": rows}


def _per_task_means(metric_map: dict[str, list[float]]) -> dict[str, float]:
    return {t: statistics.fmean(v) for t, v in metric_map.items() if v}


def _ci(per_task: list[float]) -> tuple[float, float, float]:
    """mean + bootstrapped 95% CI over tasks."""
    if not per_task:
        return 0.0, 0.0, 0.0
    n = len(per_task)
    mean = statistics.fmean(per_task)
    boots = []
    for _ in range(B):
        sample = [per_task[random.randrange(n)] for _ in range(n)]
        boots.append(statistics.fmean(sample))
    boots.sort()
    return mean, boots[int(0.025 * B)], boots[int(0.975 * B)]


def _paired_lift_ci(van: dict[str, float], spa: dict[str, float]) -> tuple[float, float, float]:
    """Lift = mean(spaider) − mean(vanilla), paired bootstrap over shared tasks."""
    tasks = sorted(set(van) & set(spa))
    if not tasks:
        return 0.0, 0.0, 0.0
    diffs = [spa[t] - van[t] for t in tasks]
    n = len(diffs)
    mean = statistics.fmean(diffs)
    boots = []
    for _ in range(B):
        sample = [diffs[random.randrange(n)] for _ in range(n)]
        boots.append(statistics.fmean(sample))
    boots.sort()
    return mean, boots[int(0.025 * B)], boots[int(0.975 * B)]


def build(runs_dir: str) -> dict:
    metrics = _discover_metrics(runs_dir)
    loaded = _load(runs_dir, metrics)
    data = loaded["data"]
    results = []
    for key, label, tag in metrics:
        van_pt = _per_task_means(data["vanilla"].get(key, {}))
        spa_pt = _per_task_means(data["with-spaider"].get(key, {}))
        vm, vlo, vhi = _ci(list(van_pt.values()))
        sm, slo, shi = _ci(list(spa_pt.values()))
        lm, llo, lhi = _paired_lift_ci(van_pt, spa_pt)
        results.append({
            "metric": key, "label": label, "headline": tag == "headline",
            "n_tasks": len(spa_pt),
            "vanilla": {"mean": vm, "ci": [vlo, vhi]},
            "with_spaider": {"mean": sm, "ci": [slo, shi]},
            "lift": {"mean": lm, "ci": [llo, lhi], "significant": llo > 0},
        })
    # retrieval hit-rate + token cost (context only, not a vanilla/spaider lift)
    spa_hits = _per_task_means(loaded["hits"]["with-spaider"])
    hit_rate = statistics.fmean(list(spa_hits.values())) if spa_hits else None
    return {"runs_dir": runs_dir, "rows": loaded["rows"], "bootstrap": B,
            "questions": max((r["n_tasks"] for r in results), default=0),
            "results": results, "retrieval_hit_rate": hit_rate}


def to_markdown(sc: dict) -> str:
    lines = ["# SpAIder community scorecard", ""]
    lines.append(f"{sc.get('questions', 0)} distinct questions · {sc['rows']} graded rows · "
                 f"95% CI cluster-bootstrapped over questions "
                 f"({sc['bootstrap']:,} resamples), not over rows.")
    lines.append("Arms: **vanilla** (gpt-4o-mini alone) vs **with-spaider** "
                 "(gpt-4o-mini + SpAIder memory). GEval = LLM-judge correctness "
                 "(headline; EM/F1 understate correct-but-verbose answers).")
    lines.append("")
    lines.append("| Metric | Vanilla | With SpAIder | Lift (95% CI) | Sig. |")
    lines.append("|--------|--------:|-------------:|---------------|:----:|")
    for r in sc["results"]:
        star = " ⭐" if r["headline"] else ""
        v, s, lift = r["vanilla"], r["with_spaider"], r["lift"]
        sig = "✅" if lift["significant"] else "n/a"
        lines.append(
            f"| {r['label']}{star} | {v['mean']:.3f} "
            f"[{v['ci'][0]:.2f}, {v['ci'][1]:.2f}] | {s['mean']:.3f} "
            f"[{s['ci'][0]:.2f}, {s['ci'][1]:.2f}] | "
            f"{lift['mean']:+.3f} [{lift['ci'][0]:+.2f}, {lift['ci'][1]:+.2f}] | {sig} |"
        )
    if sc.get("retrieval_hit_rate") is not None:
        lines.append("")
        lines.append(f"_Retrieval hit-rate (with-spaider): "
                     f"{sc['retrieval_hit_rate']:.1%} of tasks surfaced a supporting node._")
    lines.append("")
    lines.append("⭐ headline metric · ✅ = lift's 95% CI excludes 0 (statistically clear).")
    return "\n".join(lines)


def make_chart(sc: dict, path: str) -> bool:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return False
    rows = sc["results"]
    labels = [r["label"] for r in rows]
    van = [r["vanilla"]["mean"] for r in rows]
    spa = [r["with_spaider"]["mean"] for r in rows]
    van_err = [[r["vanilla"]["mean"] - r["vanilla"]["ci"][0] for r in rows],
               [r["vanilla"]["ci"][1] - r["vanilla"]["mean"] for r in rows]]
    spa_err = [[r["with_spaider"]["mean"] - r["with_spaider"]["ci"][0] for r in rows],
               [r["with_spaider"]["ci"][1] - r["with_spaider"]["mean"] for r in rows]]
    x = range(len(labels))
    w = 0.38
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.bar([i - w / 2 for i in x], van, w, yerr=van_err, capsize=4,
           label="vanilla (LLM alone)", color="#9aa7b2")
    ax.bar([i + w / 2 for i in x], spa, w, yerr=spa_err, capsize=4,
           label="with SpAIder", color="#2f7d54")
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels)
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("score")
    ax.set_title("SpAIder vs LLM-alone: 24 HotpotQA (95% bootstrapped CI)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default="benchmarks/runs")
    ap.add_argument("--out", default="benchmarks/COMMUNITY_SCORECARD.md")
    ap.add_argument("--json", default="benchmarks/community_scorecard.json")
    ap.add_argument("--chart", default="benchmarks/scorecard.png")
    args = ap.parse_args()
    sc = build(args.runs)
    with open(args.json, "w") as fh:
        json.dump(sc, fh, indent=2)
    md = to_markdown(sc)
    with open(args.out, "w") as fh:
        fh.write(md + "\n")
    charted = make_chart(sc, args.chart)
    print(md)
    chart_note = f" · {args.chart}" if charted else " · (chart skipped: matplotlib unavailable)"
    print(f"\n→ {args.out} · {args.json}{chart_note}")


if __name__ == "__main__":
    main()
