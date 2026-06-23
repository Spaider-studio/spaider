# Memory systems, head-to-head: SpAIder vs Mem0 vs Cognee

A reproducible comparison of three open knowledge/memory systems on the same
corpora, questions and judge. Full tables (per-corpus, every metric, 95% CIs)
are auto-generated in [`COMPARISON_SYSTEMS.md`](./COMPARISON_SYSTEMS.md); this is
the written summary.

## Summary

On identical inputs and an independent judge, **SpAIder, Mem0 and Cognee are
statistically indistinguishable** — across all three corpora, every pairwise
difference between systems has a 95% confidence interval that includes zero.
All three lift a bare model from near-zero to ~0.85–0.98 on private data, where
the bare model cannot answer at all. The takeaway is not a winner; it is that
SpAIder performs **on par with the leading open memory systems**, and that the
entire value of any of them is the memory.

## Method

- **Corpora.** AcmeAI (16 Q, private narrative), HotpotQA gold (24 Q, public
  multi-hop), nexora_mid (60 Q, private exact-value lookup). The two private
  corpora are synthetic and contamination-free — a bare LLM scores 0.00, so the
  score is entirely attributable to retrieval.
- **Two answer modes per system.** *fixed* — the system retrieves, then a single
  shared `gpt-4o-mini` reader answers (isolates retrieval quality; every system
  shares the reader). *native* — the system answers end-to-end its own way.
- **Same everything else.** All internal LLMs pinned to `gpt-4o-mini`; correctness
  scored by an independent `gpt-4o` GEval judge plus deterministic EM/F1/ROUGE-L;
  95% confidence intervals via 10,000-resample bootstrap over questions.
- **Isolation.** Each system runs in its own environment and store; identical
  facts ingested into each.

## Results (independent gpt-4o GEval; higher is better)

| Corpus | SpAIder (fixed) | Mem0 (fixed) | Cognee (fixed) | Bare LLM | Systems separable? |
|---|---|---|---|---|---|
| AcmeAI (private) | 0.97 | 0.89 | 0.90 | 0.00 | no — all `n/s` |
| HotpotQA (public) | 0.83 | 0.88 | 0.86 | 0.46 | no — all `n/s` |
| nexora_mid (private) | 0.96 | 0.98 | 0.96 | 0.00 | no — all `n/s` |

Every system-vs-system lift CI includes zero (e.g. on nexora_mid
`mem0-fixed − spaider-fixed = +0.025 [−0.02, +0.08]`,
`cognee-fixed − spaider-fixed = +0.000 [−0.03, +0.04]`). Every memory-vs-bare-LLM
lift excludes zero by a wide margin. Across all arms the shared-reader (*fixed*)
mode meets or beats each system's *native* mode.

## What the benchmark found and fixed

The harness is contribution enough on its own: building it surfaced and drove
three concrete fixes, two of them real product/infra bugs.

1. **A fact-deduplication bug in SpAIder.** Ingesting 150
   distinct facts yielded only 137 graph nodes — the entity resolver was
   semantically merging *facts* (not just entities) that shared a template.
   Pre-fix, SpAIder trailed verbatim-storage systems on exact-value recall by a
   statistically significant margin; the fix (facts are atomic, never merged)
   raised `spaider-fixed` on nexora_mid from EM 0.92 → **1.00**, erasing the gap.
2. **A Redis connection-pool stall** under load (separate, fixed and merged) that
   was degrading queries on the backend.
3. **An ingest concurrency limit** in the harness — parallel MCP ingests dropped
   facts; serial ingest is reliable.

Each was a categorical issue that initially read as a "SpAIder is worse" signal.
Controlling all three turned a spurious gap into a clean tie — a reminder to audit
the pipeline before trusting a head-to-head.

## Honest limitations

- **Modest n** (16 / 24 / 60). The systems are tied here; larger question sets
  could still separate them in either direction. More questions — not more sweeps
  (temperature 0 makes sweeps near-identical) — is the way to tighten this.
- **`fixed` is the fair retrieval comparison;** `native` confounds retrieval with
  each system's own answer model and is reported for completeness.
- **Token columns are directional, not a cost ranking** (systems expose internal
  token usage inconsistently).
- **CIs resample questions, not rows.** Each question's sweeps and both judges are
  collapsed to one per-question mean *before* the bootstrap (a cluster bootstrap
  over the 16 / 24 / 60 distinct questions), so the intervals reflect
  question-level uncertainty rather than the inflated n of 1,020 graded rows.

## Not yet covered

This is strong *internal* validity on small, standard micro-benchmarks. It is
deliberately silent on two things, and we'd rather name them than imply they're
settled:

- **Other systems.** Only Mem0 and Cognee are wired up. **Graphiti** is the
  obvious next arm — the harness is N-arm, so it's one adapter plus a sweep away.
- **Harder regimes.** These corpora are single-session factoid recall. The
  benchmarks that actually stress a *temporal, multi-session* memory —
  **LongMemEval** and **LOCOMO** — are not run yet. That is precisely the axis on
  which a graph-memory system should eventually differentiate from a flat vector
  store, so until those land, "tied on micro-benchmarks" is the honest ceiling of
  this evidence.

## Reproduce

See [`adapters/README.md`](./adapters/README.md). In short: install each system in
its own venv, seed the shared corpus, run both modes, then
`python -m benchmarks.systems_scorecard`.

_100 distinct questions — AcmeAI(16) + HotpotQA(24) + nexora_mid(60) — across
1,020 graded rows; gpt-4o-mini systems, independent gpt-4o judge, healthy backend,
the fact-dedup fix, error-free serial seed, 10k-resample CIs cluster-bootstrapped
over questions. Last run 2026-06._
