# SpAIder community scorecard

24 HotpotQA multi-hop questions · 256 graded rows · bootstrapped 95% CI (10,000 resamples).
Arms: **vanilla** (gpt-4o-mini alone) vs **with-spaider** (gpt-4o-mini + SpAIder memory). GEval = LLM-judge correctness (headline; EM/F1 understate correct-but-verbose answers).

| Metric | Vanilla | With SpAIder | Lift (95% CI) | Sig. |
|--------|--------:|-------------:|---------------|:----:|
| GEval (judge: gpt-4o) ⭐ | 0.000 [0.00, 0.00] | 0.969 [0.91, 1.00] | +0.969 [+0.91, +1.00] | ✅ |
| GEval (self-judge) | 0.000 [0.00, 0.00] | 0.961 [0.89, 1.00] | +0.961 [+0.89, +1.00] | ✅ |
| F1 | 0.000 [0.00, 0.00] | 0.783 [0.60, 0.94] | +0.783 [+0.59, +0.94] | ✅ |
| Exact Match | 0.000 [0.00, 0.00] | 0.719 [0.49, 0.91] | +0.719 [+0.49, +0.91] | ✅ |
| ROUGE-L | 0.000 [0.00, 0.00] | 0.783 [0.58, 0.95] | +0.783 [+0.59, +0.95] | ✅ |

_Retrieval hit-rate (with-spaider): 93.8% of tasks surfaced a supporting node._

⭐ headline metric · ✅ = lift's 95% CI excludes 0 (statistically clear).
