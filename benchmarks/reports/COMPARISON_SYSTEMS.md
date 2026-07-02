# Head-to-head: SpAIder vs Mem0 vs Cognee

**100 distinct questions** · 1,020 graded rows · 95% CI cluster-bootstrapped over questions (10,000 resamples), not over rows · same corpus, questions and gpt-4o judge for every system.
**fixed** = system retrieves, then a shared gpt-4o-mini reader answers (isolates retrieval quality). **native** = the system answers its own way. Tokens = agent + backend (where exposed). ⭐ headline = GEval (gpt-4o judge).

## acmeai (16 questions)

| System | GEval (judge: gpt-4o) ⭐ | GEval (self-judge) | F1 | Exact Match | ROUGE-L | Retr-hit | Avg tok |
|---|---|---|---|---|---|---|---|
| **spaider-fixed** | 0.97 [0.91, 1.00] | 0.91 [0.75, 1.00] | 0.71 [0.51, 0.89] | 0.56 [0.31, 0.81] | 0.71 [0.50, 0.89] | 94% | 568 |
| **cognee-native** | 0.91 [0.77, 0.99] | 0.90 [0.75, 1.00] | 0.69 [0.50, 0.87] | 0.65 [0.42, 0.85] | 0.69 [0.50, 0.88] | 67% | 0 |
| **mem0-native** | 0.91 [0.75, 1.00] | 0.84 [0.66, 1.00] | 0.70 [0.49, 0.88] | 0.58 [0.33, 0.81] | 0.70 [0.49, 0.88] | 94% | 563 |
| **cognee-fixed** | 0.90 [0.75, 1.00] | 0.85 [0.69, 1.00] | 0.71 [0.50, 0.89] | 0.56 [0.31, 0.81] | 0.71 [0.51, 0.88] | 94% | 604 |
| **mem0-fixed** | 0.89 [0.74, 1.00] | 0.84 [0.66, 1.00] | 0.68 [0.46, 0.87] | 0.56 [0.31, 0.81] | 0.68 [0.46, 0.88] | 94% | 608 |
| **spaider-native** | 0.88 [0.72, 1.00] | 0.84 [0.66, 1.00] | 0.72 [0.50, 0.91] | 0.62 [0.38, 0.88] | 0.72 [0.50, 0.91] | 94% | 7,908 |
| **vanilla** | 0.00 [0.00, 0.00] | 0.00 [0.00, 0.00] | 0.00 [0.00, 0.00] | 0.00 [0.00, 0.00] | 0.00 [0.00, 0.00] | – | 109 |

_Headline (GEval (judge: gpt-4o)) lifts, paired bootstrap:_
- `cognee-native` − `spaider-fixed`: -0.062 [-0.21, +0.04] n/s
- `mem0-native` − `spaider-fixed`: -0.062 [-0.19, +0.00] n/s
- `cognee-fixed` − `spaider-fixed`: -0.073 [-0.21, +0.00] n/s
- `mem0-fixed` − `spaider-fixed`: -0.083 [-0.23, +0.00] n/s
- `spaider-native` − `spaider-fixed`: -0.094 [-0.25, +0.00] n/s
- `vanilla` − `spaider-fixed`: -0.969 [-1.00, -0.91] ✅
- `spaider-fixed` − `vanilla`: +0.969 [+0.91, +1.00] ✅
- `cognee-native` − `vanilla`: +0.906 [+0.77, +0.99] ✅
- `mem0-native` − `vanilla`: +0.906 [+0.75, +1.00] ✅
- `cognee-fixed` − `vanilla`: +0.896 [+0.75, +1.00] ✅
- `mem0-fixed` − `vanilla`: +0.885 [+0.74, +1.00] ✅
- `spaider-native` − `vanilla`: +0.875 [+0.72, +1.00] ✅

## hotpotqa (24 questions)

| System | GEval (judge: gpt-4o) ⭐ | GEval (self-judge) | F1 | Exact Match | ROUGE-L | Retr-hit | Avg tok |
|---|---|---|---|---|---|---|---|
| **mem0-fixed** | 0.88 [0.71, 1.00] | 0.88 [0.74, 1.00] | 0.84 [0.70, 0.96] | 0.71 [0.50, 0.88] | 0.83 [0.69, 0.95] | 100% | 1,163 |
| **cognee-fixed** | 0.86 [0.71, 0.99] | 0.86 [0.71, 0.99] | 0.83 [0.68, 0.95] | 0.69 [0.50, 0.88] | 0.82 [0.68, 0.94] | 100% | 1,146 |
| **mem0-native** | 0.83 [0.67, 0.96] | 0.83 [0.67, 0.96] | 0.79 [0.63, 0.92] | 0.62 [0.42, 0.83] | 0.78 [0.62, 0.91] | 100% | 1,171 |
| **spaider-fixed** | 0.83 [0.67, 0.96] | 0.85 [0.71, 0.98] | 0.81 [0.65, 0.94] | 0.71 [0.50, 0.88] | 0.81 [0.65, 0.94] | 100% | 944 |
| **cognee-native** | 0.78 [0.62, 0.92] | 0.78 [0.62, 0.92] | 0.74 [0.59, 0.88] | 0.56 [0.36, 0.74] | 0.72 [0.57, 0.86] | 42% | 0 |
| **spaider-native** | 0.77 [0.60, 0.92] | 0.79 [0.62, 0.96] | 0.73 [0.57, 0.88] | 0.58 [0.38, 0.79] | 0.73 [0.57, 0.88] | 100% | 9,205 |
| **vanilla** | 0.46 [0.27, 0.65] | 0.48 [0.29, 0.67] | 0.09 [0.05, 0.14] | 0.00 [0.00, 0.00] | 0.07 [0.04, 0.11] | – | 104 |

_Headline (GEval (judge: gpt-4o)) lifts, paired bootstrap:_
- `mem0-fixed` − `spaider-fixed`: +0.042 [+0.00, +0.12] n/s
- `cognee-fixed` − `spaider-fixed`: +0.028 [+0.00, +0.08] n/s
- `mem0-native` − `spaider-fixed`: +0.000 [-0.12, +0.12] n/s
- `cognee-native` − `spaider-fixed`: -0.049 [-0.20, +0.09] n/s
- `spaider-native` − `spaider-fixed`: -0.062 [-0.21, +0.08] n/s
- `vanilla` − `spaider-fixed`: -0.375 [-0.60, -0.12] ✅
- `mem0-fixed` − `vanilla`: +0.417 [+0.19, +0.65] ✅
- `cognee-fixed` − `vanilla`: +0.403 [+0.17, +0.62] ✅
- `mem0-native` − `vanilla`: +0.375 [+0.15, +0.58] ✅
- `spaider-fixed` − `vanilla`: +0.375 [+0.12, +0.62] ✅
- `cognee-native` − `vanilla`: +0.326 [+0.07, +0.58] ✅
- `spaider-native` − `vanilla`: +0.312 [+0.10, +0.52] ✅

## nexora_mid (60 questions)

| System | GEval (judge: gpt-4o) ⭐ | GEval (self-judge) | F1 | Exact Match | ROUGE-L | Retr-hit | Avg tok |
|---|---|---|---|---|---|---|---|
| **mem0-fixed** | 0.98 [0.95, 1.00] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 100% | 778 |
| **spaider-native** | 0.97 [0.93, 1.00] | 0.98 [0.95, 1.00] | 0.98 [0.95, 1.00] | 0.98 [0.95, 1.00] | 0.98 [0.95, 1.00] | 100% | 8,513 |
| **mem0-native** | 0.97 [0.92, 1.00] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 100% | 759 |
| **cognee-fixed** | 0.96 [0.90, 1.00] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 100% | 768 |
| **spaider-fixed** | 0.96 [0.90, 1.00] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 1.00 [1.00, 1.00] | 100% | 696 |
| **cognee-native** | 0.88 [0.80, 0.95] | 0.92 [0.83, 0.98] | 0.92 [0.83, 0.98] | 0.92 [0.83, 0.98] | 0.92 [0.83, 0.98] | 92% | 0 |
| **vanilla** | 0.00 [0.00, 0.00] | 0.00 [0.00, 0.00] | 0.00 [0.00, 0.00] | 0.00 [0.00, 0.00] | 0.00 [0.00, 0.00] | – | 78 |

_Headline (GEval (judge: gpt-4o)) lifts, paired bootstrap:_
- `mem0-fixed` − `spaider-fixed`: +0.025 [-0.02, +0.08] n/s
- `spaider-native` − `spaider-fixed`: +0.017 [-0.03, +0.08] n/s
- `mem0-native` − `spaider-fixed`: +0.008 [-0.03, +0.05] n/s
- `cognee-fixed` − `spaider-fixed`: +0.000 [-0.03, +0.04] n/s
- `cognee-native` − `spaider-fixed`: -0.075 [-0.16, +0.00] n/s
- `vanilla` − `spaider-fixed`: -0.958 [-1.00, -0.91] ✅
- `mem0-fixed` − `vanilla`: +0.983 [+0.95, +1.00] ✅
- `spaider-native` − `vanilla`: +0.975 [+0.93, +1.00] ✅
- `mem0-native` − `vanilla`: +0.967 [+0.92, +1.00] ✅
- `cognee-fixed` − `vanilla`: +0.958 [+0.91, +1.00] ✅
- `spaider-fixed` − `vanilla`: +0.958 [+0.91, +1.00] ✅
- `cognee-native` − `vanilla`: +0.883 [+0.80, +0.95] ✅

✅ = lift's 95% CI excludes 0 · n/s = not statistically separable.
