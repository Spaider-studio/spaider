# Head-to-head memory-system adapters

Run SpAIder, Mem0 and Cognee on the **same** corpus, questions and judge, in two
answer modes:

- **fixed** — the system *retrieves*; a shared `gpt-4o-mini` reader answers.
  Isolates retrieval quality (every arm shares the reader).
- **native** — the system answers end-to-end its own way.

Each arm writes one `RunRecord` per (task, mode, sweep) to `benchmarks/runs/*.jsonl`
with `mode = "<system>-<fixed|native>"`, scored by the same oracle as the
`vanilla` / `with-spaider` arms. The scorer (`benchmarks/systems_scorecard.py`)
reads every arm uniformly.

## Isolation (clean teardown)

Mem0 and Cognee pull large, conflicting dependency trees, so each runs in its
**own gitignored virtualenv** and stores data in its **own gitignored local dir**.
Nothing touches the backend image, SpAIder's Neo4j, or any env you already use.

| | venv | local store |
|---|---|---|
| Mem0 | `benchmarks/.venv-mem0` | `benchmarks/.bench_data/mem0/` (Chroma) |
| Cognee | `benchmarks/.venv-cognee` | `benchmarks/.bench_data/cognee/` (LanceDB + file graph) |

Teardown — restores the original state exactly:

```bash
rm -rf benchmarks/.venv-mem0 benchmarks/.venv-cognee benchmarks/.bench_data
```

## Setup

```bash
# uv provisions Python 3.11 and installs each tree in isolation
uv venv benchmarks/.venv-mem0   --python 3.11
uv pip install --python benchmarks/.venv-mem0/bin/python   mem0ai litellm pyyaml chromadb
uv venv benchmarks/.venv-cognee --python 3.11
uv pip install --python benchmarks/.venv-cognee/bin/python cognee litellm pyyaml

export OPENAI_API_KEY=sk-...     # also export LLM_API_KEY=$OPENAI_API_KEY
```

## Run an arm

```bash
# 1) seed the corpus into the system's own store
benchmarks/.venv-mem0/bin/python -m benchmarks.seed_competitors \
    --system mem0 --corpus benchmarks/corpus/acmeai_30d.yaml

# 2) run both answer modes, N sweeps, append to runs/
benchmarks/.venv-mem0/bin/python -m benchmarks.run_adapter \
    --system mem0 --answer-mode both --sweeps 3 \
    --tasks benchmarks/tasks/acmeai --provider openai --model gpt-4o-mini
```

Cognee is identical with `--system cognee` and its venv. The **SpAIder** arm seeds
via `python -m benchmarks.seed` (its MCP path) and runs with `--system spaider`
(needs `SPAIDER_API_KEY` + a reachable `SPAIDER_MCP_URL`).

## Score all arms

```bash
python -m benchmarks.systems_scorecard --runs benchmarks/runs \
    --out benchmarks/COMPARISON_SYSTEMS.md
```

## Notes / fairness

- Every system's internal LLM is pinned to `gpt-4o-mini`; the judge is an
  independent `gpt-4o` (`benchmarks/rejudge.py`).
- Mem0 stores facts verbatim (`MEM0_INFER=false` default) so no corpus fact is
  dropped by its extraction step — the fairest retrieval substrate. Set
  `MEM0_INFER=true` to run its extraction pipeline instead.
- Cognee does not expose internal token counts, so its **native** token columns
  read 0 (documented asymmetry — costs are estimable from call counts since all
  internal LLMs are gpt-4o-mini). Fixed-reader token counts are exact for all
  systems (the shared reader's usage).
