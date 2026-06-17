"""Side-by-side corpus comparison: public (known) vs private (unknown).

Renders the SpAIder lift per metric across multiple corpora so the core
insight (*the value is the memory, not the model*) reads directly off the
numbers instead of needing interpretation:

- On a PUBLIC corpus the base LLM already memorized (HotpotQA / Wikipedia),
  vanilla scores well on semantic correctness, so the GEval lift is ~0.
- On a PRIVATE corpus the LLM has never seen (AcmeAI internal data), vanilla
  can't know it -> ~0, and SpAIder lifts every metric, GEval included.

Usage:
    python -m benchmarks.community_compare \
        --label "HotpotQA (public)=benchmarks/runs_hotpotqa" \
        --label "AcmeAI (private)=benchmarks/runs_acmeai" \
        --out benchmarks/COMPARISON.md --chart benchmarks/comparison.png
"""
from __future__ import annotations

import argparse

from benchmarks.community_scorecard import build


def _fmt(arm):
    return f"{arm['mean']:.2f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", action="append", required=True,
                    help='repeatable "Label=runs_dir"')
    ap.add_argument("--out", default="benchmarks/COMPARISON.md")
    ap.add_argument("--chart", default="benchmarks/comparison.png")
    args = ap.parse_args()

    corpora = []
    for spec in args.label:
        label, rd = spec.split("=", 1)
        corpora.append((label, build(rd)))

    # Metric order from the first corpus (same task format -> same metrics).
    metrics = [(r["metric"], r["label"]) for r in corpora[0][1]["results"]]

    lines = ["# SpAIder lift: public (known) vs private (unknown) corpora", ""]
    hdr = "| Metric | " + " | ".join(
        f"{lab}: vanilla→spaider (lift)" for lab, _ in corpora) + " |"
    sep = "|--------|" + "|".join(["---"] * len(corpora)) + "|"
    lines += [hdr, sep]

    for mkey, mlabel in metrics:
        cells = []
        for _, sc in corpora:
            r = next((x for x in sc["results"] if x["metric"] == mkey), None)
            if not r:
                cells.append("n/a")
                continue
            v, s, lift = r["vanilla"], r["with_spaider"], r["lift"]
            sig = " ✅" if lift["significant"] else ""
            cells.append(f"{_fmt(v)}→{_fmt(s)} (**{lift['mean']:+.2f}**{sig})")
        star = " ⭐" if mlabel.startswith("GEval (judge") else ""
        lines.append(f"| {mlabel}{star} | " + " | ".join(cells) + " |")

    # Retrieval hit-rate + n per corpus.
    lines.append("")
    for lab, sc in corpora:
        hr = sc.get("retrieval_hit_rate")
        hr_s = f", retrieval hit-rate {hr:.0%}" if hr is not None else ""
        lines.append(f"- _{lab}: {sc['rows']} graded rows{hr_s}._")
    lines += ["", "⭐ semantic-correctness judge · ✅ = lift's 95% CI excludes 0.",
              "", "**Read it directly:** where vanilla already scores on GEval, the "
              "LLM knew the answer (public/memorized) and SpAIder's semantic lift is "
              "small; where vanilla ≈ 0, the LLM *could not* know it (private data) "
              "and SpAIder lifts every metric. That gap is the memory's value."]
    md = "\n".join(lines)
    with open(args.out, "w") as fh:
        fh.write(md + "\n")

    charted = _chart(corpora, metrics, args.chart)
    print(md)
    print(f"\n→ {args.out}" + (f" · {args.chart}" if charted else " · (chart skipped)"))


def _chart(corpora, metrics, path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return False
    labels = [ml for _, ml in metrics]
    x = range(len(labels))
    n = len(corpora)
    w = 0.8 / n
    fig, ax = plt.subplots(figsize=(9, 4.8))
    colors = ["#9aa7b2", "#2f7d54", "#b5651d", "#3a6ea5"]
    for i, (lab, sc) in enumerate(corpora):
        lifts, errs = [], [[], []]
        for mkey, _ in metrics:
            r = next((z for z in sc["results"] if z["metric"] == mkey), None)
            lm = r["lift"]["mean"] if r else 0.0
            lo, hi = (r["lift"]["ci"] if r else (0.0, 0.0))
            lifts.append(lm)
            errs[0].append(max(0.0, lm - lo))
            errs[1].append(max(0.0, hi - lm))
        ax.bar([j + (i - (n - 1) / 2) * w for j in x], lifts, w,
               yerr=errs, capsize=3, label=lab, color=colors[i % len(colors)])
    ax.axhline(0, color="#444", linewidth=0.8)
    ax.set_xticks(list(x))
    ax.set_xticklabels([lab.replace("GEval (judge: ", "GEval\n").replace(")", "") for lab in labels],
                       fontsize=8)
    ax.set_ylabel("SpAIder lift (with-spaider − vanilla)")
    ax.set_title("SpAIder lift by metric: public vs private corpus (95% CI)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    return True


if __name__ == "__main__":
    main()
