# Repository structure & developer commands

## Make targets

| Command | What it does |
|---|---|
| `make setup` | Copy `.env.example` → `.env` and install dependencies |
| `make dev` | Bring up the stack with hot reload |
| `make prod` | Bring up the stack with production overrides |
| `make test` | Run all unit tests |
| `make test-coverage` | Run tests with coverage → `htmlcov/index.html` |
| `make eval` | Extraction eval (50 curated cases) |
| `make eval-quick` | Extraction eval (first 10 cases) |
| `make lint` / `make format` | Ruff lint / autofix |
| `make logs` | Follow stack logs |
| `make neo4j-shell` | Open a `cypher-shell` against Neo4j |
| `make kafka-topics` | List Kafka topics |
| `make bench-scorecard` | Aggregate benchmark runs → scorecard + chart |

Run `make help` for the full list.

## Project layout

```
spaider/
├── docker-compose.yml          Core service definitions
├── docker-compose.dev.yml      Dev overrides (hot reload, volume mounts)
├── docker-compose.prod.yml     Prod overrides (restart policies, replicas)
├── .env.example                Environment variable template (copy to .env)
├── Makefile                    Developer convenience targets
├── docs/                       API, operations, developer guide
├── gateway/
│   └── kong.yml                Kong declarative config (rate-limit, CORS)
├── backend/
│   ├── Dockerfile              API server image
│   ├── Dockerfile.worker       Worker image
│   ├── app/
│   │   ├── main.py             FastAPI entry point
│   │   ├── config.py           Settings (pydantic-settings)
│   │   ├── worker.py           Kafka consumer entry point
│   │   ├── db/postgres.py      SQLAlchemy async engine + init_db()
│   │   ├── models/             Pydantic request/response schemas
│   │   ├── scheduler/          ConnectorScheduler + connector_runner
│   │   ├── services/
│   │   │   ├── compressor.py       LLM entity extraction
│   │   │   ├── entity_resolver.py  Embedding deduplication
│   │   │   ├── graph_service.py    Neo4j CRUD + FTS + vector search
│   │   │   └── synthesizer.py      JSONL export
│   │   ├── connectors/         BaseConnector + registry (see __init__.py)
│   │   ├── workers/            Kafka consumers, REM-sleep worker
│   │   └── api/v1/             REST route handlers
│   └── tests/
│       ├── unit/               Fast unit tests (no external deps)
│       ├── eval/               Extraction evaluation framework
│       └── rig/                Throwaway full stack in Docker for browser, restart and load checks
├── cli/                        `spaider` CLI (Apache-2.0)
├── sdk/python/                 Python SDK (Apache-2.0)
├── benchmarks/                 Benchmark runner, scorecards, dashboard
└── frontend/
    ├── e2e/                    Playwright browser tests (run against the rig)
    └── src/
        ├── app/                Next.js App Router pages
        ├── components/         React components (GraphCanvas3D, etc.)
        ├── hooks/              Zustand stores
        └── lib/api.ts          Typed API client
```

See [developer-guide.md](developer-guide.md) for the stigmergic-swarm internals.
