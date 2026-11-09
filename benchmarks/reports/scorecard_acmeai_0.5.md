# SpAIder community scorecard

16 distinct questions · 128 graded rows · 95% CI cluster-bootstrapped over questions (10,000 resamples), not over rows.
Arms: **vanilla** (gpt-4o-mini alone) vs **with-spaider** (gpt-4o-mini + SpAIder memory). GEval = LLM-judge correctness (headline; EM/F1 understate correct-but-verbose answers).

| Metric | Vanilla | With SpAIder | Lift (95% CI) | Sig. |
|--------|--------:|-------------:|---------------|:----:|
| GEval (judge: gpt-4o) ⭐ | 0.000 [0.00, 0.00] | 0.961 [0.89, 1.00] | +0.961 [+0.89, +1.00] | ✅ |
| GEval (self-judge) | 0.000 [0.00, 0.00] | 0.922 [0.78, 1.00] | +0.922 [+0.80, +1.00] | ✅ |
| F1 | 0.000 [0.00, 0.00] | 0.778 [0.59, 0.94] | +0.778 [+0.58, +0.94] | ✅ |
| Exact Match | 0.000 [0.00, 0.00] | 0.688 [0.44, 0.88] | +0.688 [+0.44, +0.88] | ✅ |
| ROUGE-L | 0.000 [0.00, 0.00] | 0.778 [0.59, 0.95] | +0.778 [+0.59, +0.94] | ✅ |

_Retrieval hit-rate (with-spaider): 93.8% of tasks surfaced a supporting node._

⭐ headline metric · ✅ = lift's 95% CI excludes 0 (statistically clear).
