# Test rig

A throwaway copy of the whole stack inside one Docker container (`spaider-rig`), for the checks that
unit tests cannot make: the browser tests in `frontend/e2e`, restarts of single services, a restart of
the Docker daemon, and a count of what the services log. It never touches the real stack. It uses its
own ports and every script refuses any address except the rig's.

| What | Where |
|---|---|
| Studio | http://localhost:13000 |
| API | http://localhost:18000 |
| Gateway | http://localhost:18080 |

The language model is a deterministic stub (`backend/tests/stubs`) that answers "ok", so the runs are
repeatable and free. Answer quality is not tested here.

## Start

From the repository root, with Docker running:

```bash
python backend/tests/rig/rig.py create     # start the container (once)
python backend/tests/rig/rig.py sync       # copy the working tree in
python backend/tests/rig/rig.py up         # build and start the stack, wait until it is healthy
python backend/tests/rig/rig.py env RATE_LIMIT_REQUESTS_PER_MINUTE=3000   # the checks send many requests
backend/.venv/bin/python backend/tests/rig/seed.py   # owner, members, knowledge bases, some facts
```

Keys are written to `.rig-keys.json` and `.rig-state.json` (both git-ignored, mode 600) and are never
printed. Other commands: `reset` empties the databases, `rebuild frontend` copies the tree in and
rebuilds one service, `exec -- docker ps` runs docker inside the rig, `down` removes everything.

## Checks

```bash
cd frontend && npx playwright test                 # browser tests: pages, states, roles, journeys, layout, axe
backend/.venv/bin/python backend/tests/rig/load.py [--n 40]            # parallel ingests and queries, then counts audit rows
backend/.venv/bin/python backend/tests/rig/chaos.py stop               # stop and start every service, time the recovery
backend/.venv/bin/python backend/tests/rig/chaos.py daemon --rounds 5  # restart the Docker daemon, all containers at once
backend/.venv/bin/python backend/tests/rig/logs.py --since 30m         # errors and warnings in the logs, against an allow list
```

Run `logs.py` on a quiet stack, not after `chaos.py`, which fills the logs with expected errors.

## With a real model

To see answers from a model and not the stub, point the rig at a local Ollama (the same setup the
README describes) and empty the databases, because the embedding size changes and the vector index
is built for one size:

```bash
python backend/tests/rig/rig.py env LLM_PROVIDER=ollama LLM_MODEL=ollama/granite4.1:8b \
  LLM_BASE_URL=http://host.docker.internal:11434 LLM_API_KEY= EMBEDDING_PROVIDER=ollama \
  EMBEDDING_MODEL=embeddinggemma:300m EMBEDDING_BASE_URL=http://host.docker.internal:11434 \
  EMBEDDING_API_KEY= EMBEDDING_DIMENSIONS=768
python backend/tests/rig/rig.py reset
backend/.venv/bin/python backend/tests/rig/seed.py
cd frontend && RIG_REAL_LLM=1 npx playwright test e2e/onboarding.spec.ts
```

Use any model you have; a hosted model works the same way with its key in `LLM_API_KEY`. Answers take
seconds instead of milliseconds, so seeding is slow.
