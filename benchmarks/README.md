# SpAIder Benchmarks

Reproducible **three-way** comparisons of any LLM (OpenAI, Anthropic,
Ollama, Azure, Groq, …) on SpAIder-relevant tasks: empty context vs.
context-stuffed vs. SpAIder MCP. One dashboard.

```
benchmarks/
├── pyproject.toml             # this package; not part of backend
├── runner.py                  # python -m benchmarks.runner ...
├── dashboard.py               # streamlit run benchmarks/dashboard.py
├── seed.py                    # ingest a corpus into SpAIder via MCP
├── generate_corpus.py         # produce the synthetic AcmeAI corpus
├── corpus/
│   ├── acmeai_30d.yaml        # 80+ structured facts, for `seed --corpus`
│   └── acmeai_30d.txt         # flat dump, for `--mode vanilla-context`
├── tasks/
│   ├── *.yaml                 # v1 substring tasks (5)
│   └── compounding_brain/*    # v2 strategic question suite (10)
└── runs/                      # *.jsonl output, one row per (task, mode)
```

## Why

To answer one recurring question with data instead of vibes:

> Does giving an LLM the SpAIder MCP server (durable knowledge graph)
> actually make it better, and **by how much vs. just pasting the same
> data into the prompt**?

The third mode is what makes the comparison honest. "SpAIder beats
no-context" is trivial. "SpAIder matches context-stuffing on accuracy
at a fraction of the cost, and scales to corpus sizes that don't fit in
any context window" is the actual moat.

| Mode | What it does | Use it to claim |
|---|---|---|
| `vanilla`         | Plain prompt, no tools. | "the model alone." |
| `vanilla-context` | Same prompt + the entire corpus dumped in the system message. | "the model with all the data, the dumb way." |
| `with-spaider`    | Same prompt + SpAIder retrieval (MCP tools). | "the model with structured retrieval." |

The lift on `with-spaider` over `vanilla-context` shows up in **cost and
latency**, not always in accuracy, which is the actually defensible
unit-economics claim.

## Vocabulary

A few terms used throughout this README and the dashboard:

- **Sweep**: one execution of `python -m benchmarks.runner --tasks <dir>
  --mode <m>` over a task directory. Each sweep produces one JSONL row
  per task. Running the runner twice on the same directory = 2 sweeps;
  the dashboard's time-series tab x-axis counts sweeps per (provider,
  model, mode, category) bucket. For customer demos: keep sweep counts
  equal across cells so accuracy comparisons are apples-to-apples.

- **Stack**: a (provider, model, mode) triple, e.g.
  `ollama/granite4.1:8b · with-spaider`. The dashboard's filters and
  time-series legends are stack-coloured.

- **Category**: a task subdirectory under `tasks/`, e.g.
  `compounding_brain` (synthesis), `recall_specific` (narrow lookup),
  and `hotpotqa` (industry-standard multi-hop QA). Each is a different
  SpAIder strength to demonstrate.

- **Oracle**: how a task's answer is graded. Five kinds:
  - `substring`: string match against `expected_substring` /
    `expected_all`. v1 default; cheap, brittle on paraphrase.
  - `llm_judge`: separate LLM call grades against `oracle.rubric`.
    Used by `compounding_brain` and `recall_specific`. Forgiving on
    paraphrase; costs one extra small completion per task.
  - `f1` / `exact_match`: token-overlap F1 / normalised string
    equality against `expected_output`. HotpotQA reference impl;
    pure Python, no LLM call.
  - `geval`: LLM-graded continuous correctness in [0.0, 1.0] vs
    `expected_output`. Mirrors DeepEval's GEval correctness metric.
  - `composite`: runs F1 + EM + GEval together. Best for HotpotQA-
    style tasks; one task → all four DeepEval-comparable numbers.

## DeepEval-style metrics

The `f1`, `exact_match`, `geval`, and `composite` oracles produce
metrics that follow the standard QA-benchmarking conventions:

| Metric | What it measures | Range |
|---|---|---|
| **F1** | Token-overlap between prediction and ground truth | [0, 1] |
| **EM** | Normalised exact match (lowercase, strip articles+punct) | {0, 1} |
| **GEval** | LLM-graded correctness against ground truth | [0, 1] |

The dashboard's "DeepEval scorecard" tab aggregates these per
(model, mode, category). Pure-Python F1/EM ship in the runner; no
extra dependency. The `geval` oracle reuses the existing LLM-judge
path; same model, same provider config.

(Optional: `pip install -e benchmarks[deepeval]` adds the actual
`deepeval` library if a caller wants native DeepEval test reports
on top of our metric computation. Default install does not pull it
in, which keeps the dependency surface light.)

## Provider config (same contract as SpAIder's `.env`)

The runner reuses SpAIder's existing LiteLLM env vars. If your `.env` is
already filled in for the SpAIder backend, the benchmark Just Works.

| Variable        | Required | Example                                |
| --------------- | -------- | -------------------------------------- |
| `LLM_PROVIDER`  | yes      | `openai`, `anthropic`, `ollama`        |
| `LLM_MODEL`     | yes      | `gpt-4o-mini`, `llama3.2:3b`           |
| `LLM_API_KEY`   | usually  | provider key (Ollama: any string is fine) |
| `LLM_BASE_URL`  | optional | `http://localhost:11434` for Ollama    |

CLI flags `--provider` and `--model` override the env, so you can sweep
a different model on a given day without touching `.env`.

## Quick start

```bash
# 1. Install the harness (uv recommended)
uv venv benchmarks/.venv --python 3.11
uv pip install --python benchmarks/.venv/bin/python -e 'benchmarks[dev,dashboard]'

# 2a. Free local sweep: Ollama with a tool-capable model
ollama pull llama3.2:3b              # one-time
export LLM_PROVIDER=ollama
export LLM_MODEL=llama3.2:3b
export LLM_BASE_URL=http://localhost:11434
benchmarks/.venv/bin/python -m benchmarks.runner \
    --tasks benchmarks/tasks --mode vanilla

# 2b. Cloud sweep: OpenAI
export LLM_PROVIDER=openai
export LLM_MODEL=gpt-4o-mini
export LLM_API_KEY=sk-...
benchmarks/.venv/bin/python -m benchmarks.runner \
    --tasks benchmarks/tasks --mode vanilla

# 2c. Cloud sweep: Anthropic
export LLM_PROVIDER=anthropic
export LLM_MODEL=claude-haiku-4-5
export LLM_API_KEY=sk-ant-...
benchmarks/.venv/bin/python -m benchmarks.runner \
    --tasks benchmarks/tasks --mode vanilla

# 3. With-MCP mode: start the host MCP server first
make mcp-server-host                       # SpAIder MCP at :8001
export SPAIDER_API_KEY=sk-...              # bench-{suite}-{state} agent's key
benchmarks/.venv/bin/python -m benchmarks.runner \
    --tasks benchmarks/tasks --mode with-spaider

# 4. Look at results
streamlit run benchmarks/dashboard.py
```

### MCP env vars (only for `--mode with-spaider`)

| Variable          | Default                                    |
| ----------------- | ------------------------------------------ |
| `SPAIDER_API_KEY` | required; benchmark agent's API key (e.g. `bench-acmeai-clean`); see CLAUDE.md §9 for naming |
| `SPAIDER_MCP_URL` | `http://localhost:8001/api/v1/mcp/sse`     |

If you'd rather hit the compose stack, set
`SPAIDER_MCP_URL=http://localhost:8000/api/v1/mcp/sse`. The host-side
standalone (`make mcp-server-host`) is recommended because it survives
`docker compose down/up`.

## Which models work in `with-spaider` mode

`with-spaider` requires the model to emit OpenAI-style `tool_calls`. Most
modern providers support this; a few notable ones for local Ollama:

| Ollama model      | Tool calls? | Notes                                |
| ----------------- | ----------- | ------------------------------------ |
| `llama3.2:3b`     | ✅          | Smallest sane local choice           |
| `llama3.1:8b`     | ✅          | Stronger; fits 8GB unified memory    |
| `qwen2.5:7b`      | ✅          | Good multilingual coverage           |
| `mistral-nemo`    | ✅          |                                      |
| `gemma2:2b` / `gemma3:4b` | ❌  | No tool calling on Ollama            |

For vanilla mode, anything works.

## Authoring a task

A task is a single YAML file. Minimal shape:

```yaml
id: 06_my_task                # filename-stem-style
title: "Short human-readable description"
prompt: |
  Multi-line prompt sent to the model verbatim.
expected_substring: "answer-fragment"   # substring oracle, lowercased
expected_all:                  # optional list; ALL must appear
  - "another"
  - "fragment"
max_tokens: 512                # optional, default 1024
requires_mcp: false            # true ⇒ task is unsolvable without SpAIder
```

`requires_mcp: true` does not change runner behaviour; both modes still
attempt the task. The flag is a documentation hint that the dashboard
will use to highlight the with-spaider lift on memory-dependent tasks.

Keep tasks small and decisive. The judge is a substring check, so prompts
should constrain the model's answer shape (one sentence, named items, no
hedging).

## Evaluation: how we score a task

**v1 is a substring oracle.** `_judge(final_text, task)` lowercases the
model's final text and checks every needle from `expected_substring` /
`expected_all` is present. Empty text always fails. Cheap, deterministic,
reproducible.

What this is good at:

- Catches obvious failures (the model said "unknown" when we asked it to
  pick a merge order; the model never mentioned the duplicate class).
- Reproducible: re-running gives identical numbers.
- Costs nothing.

What it's bad at:

- False negatives on paraphrase. If the model writes "lines six and
  fourteen" instead of "6 and 14", we'd fail it wrongly. Prompt
  authoring constrains answer shape to mitigate this: task prompts
  should ask for one sentence, named items, no hedging.

What we still capture even when the substring oracle misjudges, so you
can eyeball a misjudged task in the dashboard's per-task tab:

| Field           | Tells you                                |
| --------------- | ---------------------------------------- |
| `wall_time_ms`  | speed                                    |
| `tokens_in/out` | cost / verbosity                         |
| `tool_calls`    | did MCP actually get used in `with-spaider`  |
| `final_text`    | qualitative read (truncated to 2000c)    |

**Upgrade path** (not in v1, by design: don't build the judge until we
have ~50 runs and can see which tasks are systematically misjudged):

1. **Per-task oracle function.** AST-compare for code-diff tasks;
   exact-match a small acceptable-set for recall tasks. Cuts ~80% of
   false negatives.
2. **LLM-as-judge.** A separate model graded against a rubric. Adds cost
   and judge bias; the judge model should be ≥ the model under test.
3. **Pairwise human review tab** in the dashboard with 👍/👎 widgets that
   write back into the JSONL.

## Output format

Each run appends one JSONL row to
`benchmarks/runs/{date}_{provider}_{model}.jsonl`:

```json
{
  "run_id": "uuid",
  "task_id": "01_merge_order",
  "task_title": "...",
  "mode": "vanilla",
  "provider": "ollama",
  "model": "llama3.2:3b",
  "started_at": "2026-04-28T...",
  "wall_time_ms": 812.4,
  "tokens_in": 184,
  "tokens_out": 96,
  "tool_calls": 0,
  "success": true,
  "final_text": "...",
  "error": null
}
```

Wipe a bad day with `rm benchmarks/runs/2026-04-28_*.jsonl`.

## Cost expectations

| Provider                  | Full sweep (5 tasks × 2 modes) |
| ------------------------- | ------------------------------ |
| `ollama` (local)          | $0                             |
| `openai/gpt-4o-mini`      | <$0.05                         |
| `anthropic/claude-haiku`  | <$0.10                         |
| `anthropic/claude-sonnet` | ~$0.50                         |
| `anthropic/claude-opus`   | ~$3                            |

Default to Ollama for iteration; promote to a cloud provider for the
marketing chart.

## Validation flow: seeing the with-spaider lift

A clean answer to "is SpAIder actually helping?" needs the loop end-to-end:
write a fact via SpAIder, then in a fresh session retrieve it and use it
to answer a question. The shipped task suite is structured around exactly
that: tasks 04 and 05 are *unanswerable* without prior knowledge in the
graph.

That means: **without seeding, both modes fail tasks 04 and 05.** The
graph is empty for a fresh agent; SpAIder doesn't know your facts
until you (or a previous run) tell it.

### One-time setup

Use a **benchmark agent** (`bench-acmeai-clean`), separate from your
personal `dev-{username}` agent. Mixing them pollutes both; see
CLAUDE.md §9 for the convention.

```bash
# 1. SpAIder stack up (recommended: make dev for hot-reload)
make dev

# 2. (Optional, recommended for dev-loop resilience)
make mcp-server-host

# 3. Provision a benchmark agent (defaults to bench-acmeai-clean)
scripts/dev/setup_bench_agent.sh
# → prints SPAIDER_API_KEY=sk-... for the rest of your shell session

# 4. Seed the graph with the test facts (one-time per fresh agent)
export SPAIDER_API_KEY=<key from step 3>
benchmarks/.venv/bin/python -m benchmarks.seed
```

The seeder uses `spaider.ingest_fact` over the same MCP path Claude Code
itself uses, eating its own dogfood. Re-running is safe; SpAIder
deduplicates on entity resolution.

### Sweep + compare

```bash
# Pick a provider/model (same env contract as SpAIder backend)
export LLM_PROVIDER=ollama LLM_MODEL=gemma4:e4b
export LLM_BASE_URL=http://localhost:11434 LLM_API_KEY=ollama

# Vanilla baseline: expect 04 + 05 to FAIL
benchmarks/.venv/bin/python -m benchmarks.runner \
    --tasks benchmarks/tasks --mode vanilla

# With-MCP: expect 04 + 05 to PASS (graph was seeded above)
benchmarks/.venv/bin/python -m benchmarks.runner \
    --tasks benchmarks/tasks --mode with-spaider

# Look at the dashboard; the lift on memory-only tasks is the headline
streamlit run benchmarks/dashboard.py
```

### Honest expected results

| Task | Vanilla | With-MCP (post-seed) | Why |
| ---- | ------- | -------------------- | --- |
| 01 merge order      | model-dependent | ≈ vanilla | no memory needed |
| 02 missing await    | model-dependent | ≈ vanilla | no memory needed |
| 03 duplicate class  | model-dependent | ≈ vanilla | no memory needed |
| **04 branch convention** | **FAIL** | **PASS** | unknowable without memory |
| **05 hot-files list**    | **FAIL** | **PASS** | unknowable without memory |

So the visible with-spaider lift is **2 tasks out of 5 (~40 percentage
points)** in the v1 suite. That's the floor, not a ceiling; every new
memory-dependent task adds to it. To grow the lift on analytical tasks
(01–03), seed lessons-learned facts that prime the model toward known
pitfalls (e.g. "this codebase has a recurring missing-await bug class
in async ingest handlers").

If with-spaider also fails 04+05 after seeding, check in order:

1. Is the MCP server reachable? `curl -i http://localhost:8001/api/v1/mcp/sse`
2. Did the seeder succeed for both facts? Re-run `python -m benchmarks.seed`.
3. Is the model tool-capable? Gemma 2/3 don't emit `tool_calls` on Ollama;
   switch to `llama3.2:3b` or `qwen2.5:7b`. See the model table above.
4. Look at `tool_calls` in the dashboard's per-task tab; if it's `0`,
   the model never queried SpAIder. That's a model/prompt issue, not a
   SpAIder issue.

## The Compounding Brain demo

This is the v2 demo, designed for an investor audience. Lives under
`benchmarks/tasks/compounding_brain/` (10 questions) and
`benchmarks/corpus/acmeai_30d.*` (80+ facts about a fake startup over 30 days).

### The story

AcmeAI is launching project Atlas in Q2. Over 30 days the team logs ~80
events: PR merges, customer escalations, blockers, decisions, retros.
Embedded in that activity are several risks and threads that the test
questions ask about: top launch risks, the unowned domain renewal, the
Stark Industries scope creep, etc.

### Three contestants on the same 10 questions

```bash
# 1. Vanilla: empty context. Expect FAIL on most strategic questions.
benchmarks/.venv/bin/python -m benchmarks.runner \
    --tasks benchmarks/tasks/compounding_brain --mode vanilla

# 2. Vanilla-context: full corpus dumped as system prompt. Expect PASS
#    on most, but slow and expensive (per-query cost grows with corpus).
benchmarks/.venv/bin/python -m benchmarks.runner \
    --tasks benchmarks/tasks/compounding_brain --mode vanilla-context \
    --context-file benchmarks/corpus/acmeai_30d.txt

# 3. With-MCP: same questions, SpAIder retrieves only what's needed.
#    Expect PASS at a fraction of the token cost.
#    First-time-only: ingest the corpus into your bench-acmeai-clean agent.
benchmarks/.venv/bin/python -m benchmarks.seed \
    --corpus benchmarks/corpus/acmeai_30d.yaml
benchmarks/.venv/bin/python -m benchmarks.runner \
    --tasks benchmarks/tasks/compounding_brain --mode with-spaider
```

### What the dashboard tells you

Open `streamlit run benchmarks/dashboard.py`. Two views matter:

- **Latest summary**: three rows, one per mode. The headline metrics
  show the with-spaider accuracy gap vs. vanilla-context (closer to 0 = SpAIder
  matches dumb-context-stuffing on accuracy) and the cost ratio (how
  many times cheaper SpAIder is per query).
- **Cost vs accuracy**: the marketing slide. One bubble per (mode,
  category). The argument: with-spaider clusters with vanilla-context on the
  Y-axis (accuracy), but is far to the left on the X-axis (cost). And
  scales to corpus sizes vanilla-context cannot fit in context.

### Regenerate or extend the corpus

```bash
benchmarks/.venv/bin/python -m benchmarks.generate_corpus
# overwrites corpus/acmeai_30d.{yaml,txt} reproducibly (random seed=42)
```

To add new strategic questions, drop a YAML in `tasks/compounding_brain/`
following the shape of `cb_01_top_risks.yaml`; note the
`oracle: {kind: llm_judge, rubric: ...}` block which controls how the
judge grades the response.

## The HotpotQA scaling benchmark: moat demo + backend regression test

The 24-question HotpotQA suite under `tasks/hotpotqa/` plus the
4 KB gold corpus (`corpus/hotpotqa_24.yaml`) is a *parity* benchmark;
small enough that `vanilla-context` mode pastes the whole thing into
the prompt and wins by structural advantage. SpAIder's intended use
case (retrieval from corpora that don't fit in context) is not
exercised at that scale.

The **scaling benchmark** uses the same 24 questions but ingests them
into a haystack of HotpotQA distractor paragraphs sampled from the
public dev set:

- `corpus/hotpotqa_haystack.yaml`: 466 facts, ~56K tokens
  (48 gold paragraphs + 418 distractors). Generated deterministically
  (`random.Random(seed=42)` on the dev set sorted by `_id`).
  The output is checked into the repo with a SHA256 stamp; consumers
  do not run the generator.

### Why this works as a backend regression test

The corpus is byte-identical across machines and across time. The 24
questions and their ground-truth answers don't change. So when someone
changes retrieval logic in `query_service.py`, `graph_service.py`, or
the V2 forget threshold, the benchmark output is **directly comparable**
to the previous run on the same code, with the only variable being
their changes plus judge noise.

Canonical regression command:

```bash
# One-time setup
scripts/dev/setup_bench_agent.sh hotpotqa-haystack
export SPAIDER_API_KEY=<key from above>
benchmarks/.venv/bin/python -m benchmarks.seed \
    --corpus benchmarks/corpus/hotpotqa_haystack.yaml
# ~10 min one-shot ingest of 466 facts; then the agent is ready
# to be queried as many times as you like.

# Regression run; repeat after any retrieval change
benchmarks/.venv/bin/python -m benchmarks.runner \
    --tasks benchmarks/tasks/hotpotqa --mode with-spaider \
    --runs benchmarks/runs
```

Compare F1/EM/GEval against the parity numbers (gold-only corpus) and
the previous run on main. A regression in retrieval shows up as a drop
on the haystack benchmark with no movement on parity, the gold
paragraphs are still ingested, but the noise now matters.

### Regenerate the haystack

```bash
# First run downloads ~47 MB of HotpotQA dev distractor JSON to
# benchmarks/corpus/.cache/ (gitignored). Subsequent runs reuse it.
benchmarks/.venv/bin/python -m benchmarks.generate_hotpotqa_haystack \
    --target-tokens 50000
```

Higher-scale variants for the moat demo (where vanilla-context
provably can no longer fit the corpus) are produced with a larger
`--target-tokens` value. We ship the 50K-token corpus by default
because it ingests in ~10 minutes and clearly demonstrates the
retrieval cost advantage; chasing 1M-token vanilla-context-failure
demos is left as a follow-up exercise.

## Why Streamlit

Three reasons we kept it instead of writing a Jinja page:

1. The dataframe table + Altair time-series chart + per-task drill-down
   are all one-liners in Streamlit, ~30 LoC total.
2. `streamlit run` works on any dev box without an HTTP server, port
   wrangling, or templating loops.
3. It's an optional extra (`pip install -e benchmarks[dashboard]`); the
   runner has no Streamlit dependency, so CI installs nothing extra.

If we ever ship the dashboard outside dev machines, swapping to a static
HTML export is straightforward.
