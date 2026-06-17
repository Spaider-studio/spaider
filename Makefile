.PHONY: dev prod test eval lint clean logs neo4j-shell install setup help \
        dev-backend dev-frontend dev-neo4j test-coverage eval-quick format \
        kafka-topics sdk-install sdk-test refresh-openapi \
        airflow-up airflow-down airflow-trigger

# ── Dev ───────────────────────────────────────────────────────────────────────

dev:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build

prod:
	docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build

dev-backend:
	cd backend && uvicorn app.main:app --reload --port 8000

dev-frontend:
	cd frontend && npm run dev

dev-neo4j:
	docker compose up neo4j zookeeper kafka redis -d

# Run the MCP server as a host-side process on port 8001, pointing at the
# data layer compose is already running. Survives `docker compose build
# backend-api` so a connected Claude Code session keeps working through
# rebuilds. See backend/app/mcp_standalone.py for the rationale.
mcp-server-host:
	cd backend && uvicorn app.mcp_standalone:app --port 8001 --reload

# ── Airflow (graph_maintenance DAG) ───────────────────────────────────────────
# Optional overlay — bring up the Airflow stack alongside the main compose so
# the spaider_graph_maintenance DAG can run and consolidate the graph. The
# main stack must already be up (so the spaider_default network exists).
# UI: http://localhost:8090   default login: admin / spaider-airflow

airflow-up:
	docker compose -f docker-compose.yml -f docker-compose.airflow.yml up -d --build airflow-init
	docker compose -f docker-compose.yml -f docker-compose.airflow.yml up -d airflow-scheduler airflow-webserver

airflow-down:
	# Only stop and remove the Airflow services. Naming each one explicitly
	# (instead of plain ``compose down``) avoids tearing down the main stack
	# defined in docker-compose.yml — that was the trap on the first version
	# of this target. The dedicated airflow Postgres volume sticks around so
	# the next ``airflow-up`` reuses the existing DB schema and admin user.
	docker compose -f docker-compose.yml -f docker-compose.airflow.yml \
		rm -sfv airflow-init airflow-scheduler airflow-webserver postgres-airflow

# Trigger the maintenance DAG immediately (off-schedule).
# Useful right after a heavy ingest, or to sanity-check the wiring.
airflow-trigger:
	@docker exec spaider-airflow-scheduler airflow dags trigger spaider_graph_maintenance

# ── Test ──────────────────────────────────────────────────────────────────────

test:
	cd backend && python -m pytest tests/ -v --tb=short --ignore=tests/eval

test-coverage:
	cd backend && python -m pytest tests/ --cov=app --cov-report=html --ignore=tests/eval

# ── Eval ──────────────────────────────────────────────────────────────────────

eval:
	cd backend && python -m tests.eval.run_eval

eval-quick:
	cd backend && python -m tests.eval.run_eval --limit 10

# ── Lint / Format ─────────────────────────────────────────────────────────────

lint:
	cd backend && python -m ruff check app/ tests/ && python -m ruff format --check app/ tests/
	cd frontend && npm run lint

format:
	cd backend && python -m ruff format app/ tests/

# ── CLI (spaider-cli) ─────────────────────────────────────────────────────────

# Editable install of the spaider-cli package so `spaider` runs from this
# checkout. Use this when iterating on cli/src/.
cli-dev:
	cd cli && pip install -e ".[dev]"

# Run the cli/tests/ pytest suite.
cli-test:
	cd cli && python -m pytest -v

# Run every hook in .pre-commit-config.yaml against every tracked file.
# Bootstrap (one-time on a fresh checkout):
#   pip install -e backend[dev]
#   pre-commit install
precommit:
	pre-commit run --all-files

# ── Clean ─────────────────────────────────────────────────────────────────────

clean:
	docker compose down -v --remove-orphans
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true

# ── Logs ──────────────────────────────────────────────────────────────────────

logs:
	docker compose logs -f backend-api backend-worker

# ── Shells ────────────────────────────────────────────────────────────────────

neo4j-shell:
	docker compose exec neo4j cypher-shell -u neo4j -p $(NEO4J_PASSWORD)

kafka-topics:
	docker compose exec kafka kafka-topics --list --bootstrap-server localhost:9092

# ── Install / Setup ───────────────────────────────────────────────────────────

install:
	cd backend && pip install -r requirements.txt
	cd frontend && npm install

setup: install
	cp -n .env.example .env 2>/dev/null || true
	cp -n backend/.env.example backend/.env 2>/dev/null || true
	@echo "Edit .env and backend/.env with your API keys"

sdk-install:
	cd sdk/python && pip install -e .

# Run the SDK test suite (includes the OpenAPI contract guard).
sdk-test:
	cd sdk/python && python -m pytest -v

# Refresh the committed OpenAPI snapshot from a running backend. The contract
# guard (sdk/python/tests/test_contract.py) checks the SDK models against this
# snapshot, so re-run this whenever the backend response schemas change.
# Requires the stack up (make dev) and a reachable backend on :8000.
refresh-openapi:
	curl -sS http://localhost:8000/openapi.json | \
		python3 -c "import sys,json; json.dump(json.load(sys.stdin), open('sdk/python/contract/openapi.json','w'), indent=2, sort_keys=True)"
	@echo "Updated sdk/python/contract/openapi.json — run 'make sdk-test' to re-check the SDK models."

# ── Community scorecard (vanilla vs with-spaider) ───────────────────────────
# Aggregate runner.py JSONL into a bootstrapped-CI scorecard + chart.
# RUNS defaults to benchmarks/runs; headline metric is GEval correctness.
bench-scorecard:
	benchmarks/.venv/bin/python -m benchmarks.community_scorecard --runs $(or $(RUNS),benchmarks/runs)
	@echo "See benchmarks/COMMUNITY_SCORECARD.md + benchmarks/scorecard.png"

# Clear the local run history so the dashboard / scorecard reflect only the
# next clean test. (benchmarks/runs/ is gitignored — local data only.)
bench-clean:
	rm -f benchmarks/runs/*.jsonl
	@echo "Cleared benchmarks/runs/ — rerun the suite to repopulate the dashboard."

# ── Help ──────────────────────────────────────────────────────────────────────

help:
	@echo ""
	@echo "  SpAIder -- Memory Infrastructure for AI Agents"
	@echo ""
	@echo "  make dev          Start all services (hot reload)"
	@echo "  make prod         Start all services (production)"
	@echo "  make dev-backend  Start backend with hot-reload (no Docker)"
	@echo "  make dev-frontend Start frontend dev server (no Docker)"
	@echo "  make dev-neo4j    Start only infra services (neo4j, kafka, redis)"
	@echo "  make test         Run unit tests"
	@echo "  make test-coverage Run tests with HTML coverage report"
	@echo "  make eval         Run extraction eval (50 cases)"
	@echo "  make eval-quick   Run extraction eval (first 10 cases)"
	@echo "  make lint         Lint backend + frontend"
	@echo "  make format       Auto-format backend with ruff"
	@echo "  make clean        Remove containers, volumes, caches"
	@echo "  make setup        First-time setup"
	@echo "  make logs         Follow backend logs"
	@echo "  make neo4j-shell  Open cypher-shell"
	@echo "  make kafka-topics List Kafka topics"
	@echo "  make sdk-install  Install Python SDK in editable mode"
	@echo ""
	@echo "  make cli-dev      Editable install of spaider-cli (the install wizard)"
	@echo "  make cli-test     Run cli/tests/"
	@echo ""
