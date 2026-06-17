# SpAIder lift: public (known) vs private (unknown) corpora

| Metric | HotpotQA (public): vanilla→spaider (lift) | AcmeAI (private): vanilla→spaider (lift) |
|--------|---|---|
| GEval (judge: gpt-4o) ⭐ | 0.43→0.77 (**+0.34** ✅) | 0.00→0.97 (**+0.97** ✅) |
| GEval (self-judge) | 0.46→0.78 (**+0.33** ✅) | 0.00→0.96 (**+0.96** ✅) |
| F1 | 0.09→0.70 (**+0.61** ✅) | 0.00→0.78 (**+0.78** ✅) |
| Exact Match | 0.00→0.52 (**+0.52** ✅) | 0.00→0.72 (**+0.72** ✅) |
| ROUGE-L | 0.07→0.69 (**+0.62** ✅) | 0.00→0.78 (**+0.78** ✅) |

- _HotpotQA (public): 384 graded rows, retrieval hit-rate 100%._
- _AcmeAI (private): 256 graded rows, retrieval hit-rate 94%._

⭐ semantic-correctness judge · ✅ = lift's 95% CI excludes 0.

**Read it directly:** where vanilla already scores on GEval, the LLM knew the answer (public/memorized) and SpAIder's semantic lift is small; where vanilla ≈ 0, the LLM *could not* know it (private data) and SpAIder lifts every metric. That gap is the memory's value.
