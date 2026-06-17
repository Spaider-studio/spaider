# SpAIder Developer Guide: Stigmergic Swarm Routing

## Why Redis Streams instead of Pub/Sub?

Classic Redis Pub/Sub is fire-and-forget: if no subscriber is connected when
a message is published, **it is permanently lost**.  In a distributed system
where workers restart or deploy independently, this is unacceptable.

Redis Streams with Consumer Groups give us **at-least-once delivery**:

| Property | Pub/Sub | Redis Streams |
|----------|---------|---------------|
| Message durability | None (lost if no subscriber) | Persisted until `XACK` |
| Crash recovery | Message gone | Stays in Pending Entry List (PEL) |
| Multiple workers | All receive every message | Each message processed by exactly one worker |
| Audit trail | None | Full history queryable with `XRANGE` |

---

## Stigmergic Routing: How It Works

Inspired by ant colony pheromone trails.  A node in the graph marks itself
as needing specialist work.  A worker detects the mark, does the work, and
removes the mark.  No central coordinator required.

```
┌─────────────────────────────────────────────────────────────────┐
│                        HAPPY PATH                               │
│                                                                 │
│  1. Any service calls:                                          │
│     pheromone.mark_node_and_notify(node_id, "summariser")       │
│                                                                 │
│  2. PheromoneService:                                           │
│     A) Neo4j: SET n.needs_agent = "summariser"   (graph stamp)  │
│     B) Redis: XADD pheromone_stream {node_id, agent_type}       │
│                                                                 │
│  3. SwarmListener (XREADGROUP ">"):                             │
│     - Reads entry → moves to Pending Entry List (PEL)           │
│     - Logs "[summariser] woke up to process node [<id>]"        │
│     - Runs specialist logic                                     │
│     - Neo4j: REMOVE n.needs_agent       (pheromone cleared)     │
│     - Redis: XACK pheromone_stream swarm_workers <msg_id>       │
│               → message leaves PEL permanently                  │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                      CRASH RECOVERY                             │
│                                                                 │
│  Worker crashes between step 3 and XACK                         │
│    → message stays in PEL                                       │
│                                                                 │
│  After _CLAIM_INTERVAL_S seconds (default 30s):                 │
│    → XAUTOCLAIM reclaims message from PEL                       │
│    → _process_message() runs again (idempotent by design)       │
│    → XACK sent on success                                       │
└─────────────────────────────────────────────────────────────────┘
```

---

## Running Locally

### Prerequisites

- Docker Desktop running
- `spaider/` repo cloned

### Start all services

```bash
cd spaider
docker compose up -d
```

This starts Neo4j, Redis, Kafka, ClickHouse, the backend API, and the
frontend.  The swarm listener starts automatically inside the backend
container as an `asyncio` background task.

### Trigger a pheromone event manually

```bash
# Mark a node and watch the worker log
curl -s -X POST http://localhost:8080/api/v1/ingest/sync \
  -H "Content-Type: application/json" \
  -d '{"text": "Test node for pheromone demo", "agent_id": "default"}'

# Inspect the stream
docker exec spaider-redis redis-cli XRANGE pheromone_stream - + COUNT 5

# Inspect pending entries (should be empty if worker is healthy)
docker exec spaider-redis redis-cli XPENDING pheromone_stream swarm_workers - + 10
```

### Watch the worker logs

```bash
docker logs spaider-backend-api --follow | grep SwarmListener
```

---

## Adding a New Specialist Worker

1. Open `backend/app/workers/swarm_listener.py`
2. Add a new `case "my_specialist":` branch in `_dispatch()`
3. Implement the async stub function `_specialist_my_specialist(node_id)`
4. Trigger it from any service:
   ```python
   await pheromone.mark_node_and_notify(node_id, "my_specialist")
   ```

---

## Key Files

| File | Purpose |
|------|---------|
| `backend/app/services/redis_service.py` | `PheromoneService`: XADD publisher + Neo4j pheromone writer |
| `backend/app/workers/swarm_listener.py` | XREADGROUP consumer + XAUTOCLAIM recovery loop |
| `backend/app/main.py` | Lifespan wiring: stream init (step 2b) + task launch (step 6) |

---

## Stream Configuration

| Constant | Default | Description |
|----------|---------|-------------|
| `PHEROMONE_STREAM` | `pheromone_stream` | Redis Stream key |
| `CONSUMER_GROUP` | `swarm_workers` | Consumer Group name |
| `STREAM_MAXLEN` | `10 000` | Approximate max stream length (auto-trimmed) |
| `_BLOCK_MS` | `5 000` | XREADGROUP block timeout (ms) |
| `_CLAIM_MIN_IDLE_MS` | `60 000` | Min PEL idle time before XAUTOCLAIM reclaims (ms) |
| `_CLAIM_INTERVAL_S` | `30.0` | How often the recovery scan runs (seconds) |
