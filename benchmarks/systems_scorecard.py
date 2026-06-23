"""Head-to-head scorecard across N memory systems (SpAIder vs Mem0 vs Cognee …).

Where ``community_scorecard.py`` compares exactly two arms (vanilla vs
with-spaider) for the README, this reads the SAME runner JSONL but treats every
distinct ``mode`` as an arm and renders a leaderboard per corpus (``category``):
rows = systems, columns = metrics, each as ``mean [95% CI]``. It reuses the
identical bootstrap machinery (``_ci`` / ``_paired_lift_ci`` / ``_discover_metrics``)
so the numbers are computed exactly as the published scorecard's are.

For the headline metric it also reports each arm's lift vs a baseline (default
``vanilla``) and vs ``spaider-fixed`` (the fair fixed-reader SpAIder arm), each
with a paired-bootstrap CI and a significance flag.

Usage:
    python -m benchmarks.systems_scorecard --runs benchmarks/runs \\
        --out benchmarks/COMPARISON_SYSTEMS.md [--baseline vanilla] [--vs spaider-fixed]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import statistics
from collections import defaultdict

from benchmarks.community_scorecard import (
    B,
    _ci,
    _discover_metrics,
    _paired_lift_ci,
    _per_task_means,
)


def _load_by_category(runs_dir: str, metrics):
    """{category: {arm: {metric: {task_id: [vals]}}}} plus tokens + hits."""
    data: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(list))))
    tokens: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    hits: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    arms: dict = defaultdict(set)
    qids: dict = defaultdict(set)
    rows = 0
    for path in glob.glob(os.path.join(runs_dir, "*.jsonl")):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                cat = r.get("category") or "default"
                arm = r.get("mode")
                if not arm:
                    continue
                tid = r.get("task_id") or r.get("id")
                rows += 1
                arms[cat].add(arm)
                if tid:
                    qids[cat].add(tid)
                for key, _, _ in metrics:
                    v = r.get(key)
                    if isinstance(v, (int, float)):
                        data[cat][arm][key][tid].append(float(v))
                ti, to = r.get("tokens_in"), r.get("tokens_out")
                bi, bo = r.get("backend_tokens_in", 0), r.get("backend_tokens_out", 0)
                if isinstance(ti, (int, float)) and isinstance(to, (int, float)):
                    tokens[cat][arm][tid].append(
                        float(ti) + float(to) + float(bi or 0) + float(bo or 0)
                    )
                h = r.get("retrieval_hit")
                if isinstance(h, (int, float)):
                    hits[cat][arm][tid].append(float(h))
    return {
        "data": data, "tokens": tokens, "hits": hits, "arms": arms, "rows": rows,
        "qids": {c: len(s) for c, s in qids.items()},
    }


def build(runs_dir: str, baseline: str = "vanilla", vs: str = "spaider-fixed") -> dict:
    metrics = _discover_metrics(runs_dir)
    loaded = _load_by_category(runs_dir, metrics)
    headline_key = next((k for k, _, tag in metrics if tag == "headline"), None)
    categories = {}
    for cat, arm_set in sorted(loaded["arms"].items()):
        arms = sorted(arm_set)
        per_arm = {}
        for arm in arms:
            cells = {}
            for key, label, tag in metrics:
                pt = _per_task_means(loaded["data"][cat][arm].get(key, {}))
                m, lo, hi = _ci(list(pt.values()))
                cells[key] = {"mean": m, "ci": [lo, hi], "n": len(pt), "label": label}
            tok = loaded["tokens"][cat].get(arm, {})
            tok_means = _per_task_means(tok)
            hit = loaded["hits"][cat].get(arm, {})
            hit_means = _per_task_means(hit)
            per_arm[arm] = {
                "metrics": cells,
                "avg_tokens": statistics.fmean(list(tok_means.values())) if tok_means else None,
                "retrieval_hit": statistics.fmean(list(hit_means.values())) if hit_means else None,
            }
        # Pairwise lifts on the headline metric, vs baseline and vs `vs`.
        lifts = []
        if headline_key:
            for ref in (baseline, vs):
                if ref not in arms:
                    continue
                ref_pt = _per_task_means(loaded["data"][cat][ref].get(headline_key, {}))
                for arm in arms:
                    if arm == ref:
                        continue
                    arm_pt = _per_task_means(loaded["data"][cat][arm].get(headline_key, {}))
                    lm, llo, lhi = _paired_lift_ci(ref_pt, arm_pt)
                    lifts.append({
                        "arm": arm, "ref": ref,
                        "mean": lm, "ci": [llo, lhi],
                        "significant": llo > 0 or lhi < 0,
                    })
        categories[cat] = {"arms": arms, "per_arm": per_arm, "lifts": lifts}
    qids = loaded.get("qids", {})
    return {
        "runs_dir": runs_dir, "rows": loaded["rows"], "bootstrap": B,
        "questions": sum(qids.values()), "questions_by_cat": qids,
        "metrics": [(k, lbl, t) for k, lbl, t in metrics],
        "headline": headline_key, "baseline": baseline, "vs": vs,
        "categories": categories,
    }


def to_markdown(sc: dict) -> str:
    out = ["# Head-to-head: SpAIder vs Mem0 vs Cognee", ""]
    out.append(
        f"**{sc.get('questions', 0)} distinct questions** · {sc['rows']} graded rows · "
        f"95% CI cluster-bootstrapped over questions ({sc['bootstrap']:,} resamples), "
        f"not over rows · same corpus, questions and gpt-4o judge for every system."
    )
    out.append(
        "**fixed** = system retrieves, then a shared gpt-4o-mini reader answers "
        "(isolates retrieval quality). **native** = the system answers its own way. "
        "Tokens = agent + backend (where exposed). ⭐ headline = GEval (gpt-4o judge)."
    )
    metrics = sc["metrics"]
    qbc = sc.get("questions_by_cat", {})
    for cat, cd in sc["categories"].items():
        nq = qbc.get(cat)
        out += ["", f"## {cat}" + (f" ({nq} questions)" if nq else ""), ""]
        header = "| System | " + " | ".join(
            f"{label}{' ⭐' if tag == 'headline' else ''}" for _, label, tag in metrics
        ) + " | Retr-hit | Avg tok |"
        sep = "|" + "---|" * (len(metrics) + 3)
        out += [header, sep]
        # Sort arms by the headline metric mean (desc) so it reads like a board.
        hk = sc["headline"]
        arms = sorted(
            cd["arms"],
            key=lambda a: cd["per_arm"][a]["metrics"].get(hk, {}).get("mean", -1) if hk else 0,
            reverse=True,
        )
        for arm in arms:
            pa = cd["per_arm"][arm]
            cells = []
            for key, _, _ in metrics:
                c = pa["metrics"].get(key)
                if c and c["n"]:
                    cells.append(f"{c['mean']:.2f} [{c['ci'][0]:.2f}, {c['ci'][1]:.2f}]")
                else:
                    cells.append("–")
            rh = f"{pa['retrieval_hit']:.0%}" if pa["retrieval_hit"] is not None else "–"
            tk = f"{pa['avg_tokens']:,.0f}" if pa["avg_tokens"] is not None else "–"
            out.append(f"| **{arm}** | " + " | ".join(cells) + f" | {rh} | {tk} |")
        # Headline lifts.
        if cd["lifts"]:
            hl_label = next((lbl for _, lbl, t in metrics if t == "headline"), "GEval")
            out += ["", f"_Headline ({hl_label}) lifts, paired bootstrap:_"]
            for lf in sorted(cd["lifts"], key=lambda x: (x["ref"], -x["mean"])):
                sig = "✅" if lf["significant"] else "n/s"
                out.append(
                    f"- `{lf['arm']}` − `{lf['ref']}`: {lf['mean']:+.3f} "
                    f"[{lf['ci'][0]:+.2f}, {lf['ci'][1]:+.2f}] {sig}"
                )
    out += ["", "✅ = lift's 95% CI excludes 0 · n/s = not statistically separable."]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default="benchmarks/runs")
    ap.add_argument("--out", default="benchmarks/COMPARISON_SYSTEMS.md")
    ap.add_argument("--json", default="benchmarks/comparison_systems.json")
    ap.add_argument("--baseline", default="vanilla", help="arm to lift against")
    ap.add_argument("--vs", default="spaider-fixed", help="second reference arm for lifts")
    args = ap.parse_args()
    sc = build(args.runs, baseline=args.baseline, vs=args.vs)
    with open(args.json, "w") as fh:
        json.dump(sc, fh, indent=2, default=str)
    md = to_markdown(sc)
    with open(args.out, "w") as fh:
        fh.write(md + "\n")
    print(md)
    print(f"\n→ {args.out} · {args.json}")


if __name__ == "__main__":
    main()
