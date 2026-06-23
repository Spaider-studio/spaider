# SpAIder community scorecard

24 distinct questions · 384 graded rows · 95% CI cluster-bootstrapped over questions (10,000 resamples), not over rows.
Arms: **vanilla** (gpt-4o-mini alone) vs **with-spaider** (gpt-4o-mini + SpAIder memory). GEval = LLM-judge correctness (headline; EM/F1 understate correct-but-verbose answers).

| Metric | Vanilla | With SpAIder | Lift (95% CI) | Sig. |
|--------|--------:|-------------:|---------------|:----:|
| GEval (judge: gpt-4o) ⭐ | 0.430 [0.25, 0.61] | 0.773 [0.61, 0.91] | +0.344 [+0.12, +0.57] | ✅ |
| GEval (self-judge) | 0.456 [0.28, 0.64] | 0.784 [0.62, 0.92] | +0.328 [+0.09, +0.56] | ✅ |
| F1 | 0.088 [0.05, 0.13] | 0.699 [0.54, 0.85] | +0.611 [+0.45, +0.77] | ✅ |
| Exact Match | 0.000 [0.00, 0.00] | 0.516 [0.32, 0.71] | +0.516 [+0.32, +0.70] | ✅ |
| ROUGE-L | 0.068 [0.04, 0.11] | 0.685 [0.52, 0.83] | +0.617 [+0.46, +0.77] | ✅ |

_Retrieval hit-rate (with-spaider): 100.0% of tasks surfaced a supporting node._

⭐ headline metric · ✅ = lift's 95% CI excludes 0 (statistically clear).
