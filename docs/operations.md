# Operations

Running, scaling, and maintaining a self-hosted SpAIder deployment.

## Docker services

| Service | Port(s) | Image | Description |
|---|---|---|---|
| `neo4j` | 7474, 7687 | neo4j:5.23-community | Graph database + APOC + Full-Text Search |
| `zookeeper` | 2181 | confluentinc/cp-zookeeper:7.6.0 | Kafka coordinator |
| `kafka` | 9092, 29092 | confluentinc/cp-kafka:7.6.0 | Message queue for ingestion pipeline |
| `redis` | 6379 | redis:7.4-alpine | Cache, session store, API key hashes |
| `postgres` | 5432 | postgres:16-alpine | Connector run state & encrypted credentials |
| `clickhouse` | 8123, 9000 | clickhouse/clickhouse-server:24 | Analytics and audit logs |
| `backend-api` | 8000 | ./backend | FastAPI application |
| `backend-worker` | n/a | ./backend (Dockerfile.worker) | Kafka consumer / compressor / ConnectorScheduler |
| `frontend` | 3000 | ./frontend | Next.js Studio UI |
| `kong` | 8080 (proxy), 8001 (admin) | kong:3.7 | API gateway (rate-limiting, CORS, routing) |

## Scaling `backend-worker`

The Kafka consumer / compressor pipeline is horizontally scalable. Bring up N replicas:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --scale backend-worker=4
```

Compose auto-names replicas `spaider-backend-worker-1..N`. All replicas join the same consumer group (`spaider-compressor-workers`) and split the topic's partitions between them.

Caveats:

- Only the **async** Kafka ingest path (`POST /api/v1/ingest`) benefits. The **sync** MCP path (`spaider.ingest_fact` → `ingest_text_sync`) runs inline in `backend-api` and bypasses Kafka entirely.
- Kafka topics are created with **3 partitions** by default (`KAFKA_NUM_PARTITIONS=3`). Scaling beyond 3 workers gives diminishing returns until the partition count is also raised.

## Embedding provider: OpenAI vs local Ollama

The default `EMBEDDING_PROVIDER=openai` (`text-embedding-3-small`, 1536-dim) is recommended for any production-scale ingest. Per-call latency is ~10 ms vs ~500–1000 ms for local Ollama on a CPU host; on a 4,000-fact benchmark seed that's a 1.5×–10× total ingest speedup. Cost: ~$0.02 per 1M tokens, sub-$1 for typical corpora.

To switch to local Ollama (free, but slow), edit `.env`:

```
EMBEDDING_PROVIDER=ollama
EMBEDDING_MODEL=embeddinggemma:300m
EMBEDDING_BASE_URL=http://host.docker.internal:11434
EMBEDDING_DIMENSIONS=768          # ← MUST match the model
```

### Dimension-mismatch caveat

`EMBEDDING_DIMENSIONS` controls the dimensionality of the vectors the backend writes to Neo4j. **Mixing dimensions in a single agent's graph corrupts the vector index**, so switching providers requires either a fresh agent or a re-seed.

The backend logs a loud `ERROR` at startup if `EMBEDDING_DIMENSIONS` differs from any existing embedding in the graph. The boot does not crash (so you can revert), but search-result quality silently degrades until you fix it. The right pattern when switching providers:

1. Provision a fresh agent (`scripts/dev/setup_bench_agent.sh`, `setup_mcp_dev_agent.sh`, or `POST /api/v1/agents`).
2. Update `EMBEDDING_*` env vars.
3. Re-seed.

## Graph maintenance

Memory consolidation runs three passes:

1. **Orphan prune**: delete `SpaiderNode`s with zero relationships older than `ORPHAN_MIN_AGE_DAYS` (default 7).
2. **Duplicate fusion**: merge node pairs whose embeddings are within `MERGE_SIMILARITY_THRESHOLD` cosine similarity (default 0.95). The higher-degree node wins; edges from the dropped one are redirected.
3. **Stats snapshot**: log per-agent node counts.

The logic lives in [`backend/app/lib/consolidation.py`](../backend/app/lib/consolidation.py) and is invoked from one of two places; pick whichever fits your deployment.

### Path A: Airflow (recommended for production)

```bash
make airflow-up
# UI at http://localhost:8090   login: admin / spaider-airflow
```

The `spaider_graph_maintenance` DAG runs on `MAINTENANCE_DAG_SCHEDULE` (default `"0 3 * * 0"`, Sunday 03:00 UTC). The Airflow container bind-mounts `backend/app/lib/` at `/opt/airflow/spaider_lib` so the DAG imports the same code as the CLI, with no duplication.

Trigger off-schedule (e.g. after a heavy ingest):

```bash
# Make target: runs `airflow dags trigger` inside the scheduler container
make airflow-trigger

# REST endpoint: useful for UIs / programmatic clients
curl -X POST http://localhost:8080/api/v1/system/consolidate \
     -H "Content-Type: application/json" -d '{"note": "after big ingest"}'
```

The REST endpoint requires `AIRFLOW_BASE_URL` (and credentials) in `.env`; it returns 503 with a CLI fallback hint when Airflow isn't reachable. Tear down with `make airflow-down`.

### Path B: CLI (for deployments without Airflow)

```bash
docker exec spaider-backend-api python -m app.scripts.run_consolidation
```

Wire it into cron / k8s `CronJob` / systemd timer: same passes, same env-var tunables. The CLI exits 0 on success, 1 on consolidation error.

### Tunables (read by both paths)

| Env var | Default | What it controls |
|---|---|---|
| `MAINTENANCE_DAG_SCHEDULE` | `0 3 * * 0` | Airflow cron. `0 3 * * *` = daily; `0 * * * *` = hourly. (CLI ignores this.) |
| `ORPHAN_MIN_AGE_DAYS` | `7` | Minimum age (days) before an orphan is eligible for pruning. |
| `MERGE_SIMILARITY_THRESHOLD` | `0.95` | Cosine threshold for merging near-duplicate nodes. |
