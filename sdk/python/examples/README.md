# spaider-client examples

Runnable scripts demonstrating the SDK. Each script is self-contained: set two environment variables and run.

## Setup

```bash
pip install spaider-client                  # or `pip install -e ..` from the repo
export SPAIDER_API_KEY=sk-your-key-here     # see the main README for how to issue one
export SPAIDER_BASE_URL=http://localhost:8080   # or your hosted instance URL
```

If you don't have an API key yet, the main repo README explains the Phase-1 self-hosted setup; `spaider init` creates your first agent and prints the key.

## Scripts

| File | What it shows |
|---|---|
| [`01_ingest_and_query.py`](01_ingest_and_query.py) | The basic loop: ingest unstructured text, ask a natural-language question, read the answer + supporting subgraph |
| [`02_traverse_graph.py`](02_traverse_graph.py) | Pick a node, walk N hops from it; useful for "show me everything connected to X" |
| [`03_swarm_query.py`](03_swarm_query.py) | Multi-agent: connect a second agent's graph and query across both |
| [`04_async_client.py`](04_async_client.py) | Same workflow using `AsyncSpaider` for concurrent ingest/query; useful when batching against the API |

Each script is short (under 50 lines) and prints what it does at each step so you can paste it into a console to follow along.
