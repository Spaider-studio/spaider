# API Reference

All endpoints are served through Kong at `http://localhost:8080/api/v1`.

## Auth & API Keys

SpAIder uses JWT bearer tokens for user sessions and opaque `sk-...` API keys for programmatic agent access.

### Issue an API key

```bash
curl -X POST http://localhost:8080/api/v1/agents \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{"name": "my-agent"}'
# → { "agent_id": "...", "api_key": "sk-..." }
```

The raw key is returned **once** at creation time. It is immediately hashed (SHA-256) and only the hash is stored in Redis. Keep the raw key safe.

### Rotate an API key

```bash
curl -X POST http://localhost:8080/api/v1/agents/{agent_id}/rotate-key \
  -H "Authorization: Bearer $JWT"
# → { "api_key": "sk-..." }   ← new key; old key is immediately invalid
```

### Use an API key

Pass the raw key as a Bearer token:

```bash
curl -H "Authorization: Bearer sk-..." http://localhost:8080/api/v1/query ...
```

## Agents

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/agents` | Create an agent, returns `sk-...` API key |
| `GET` | `/api/v1/agents` | List all agents |
| `GET` | `/api/v1/agents/{id}` | Get agent metadata |
| `DELETE` | `/api/v1/agents/{id}` | Delete agent and all its graph data |
| `POST` | `/api/v1/agents/{id}/rotate-key` | Rotate API key |

## Ingest

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/ingest` | Extract and store knowledge from text |
| `POST` | `/api/v1/ingest/file` | Upload a `.txt` file (multipart) |
| `GET` | `/api/v1/ingest/status/{run_id}` | Poll ingestion run status |

```bash
curl -X POST http://localhost:8080/api/v1/ingest \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"text": "Max Mustermann hat 2023 bei Google als Engineer angefangen.", "agent_id": "my-agent"}'
```

## Query

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/query` | Natural-language query over the graph |

```bash
curl -X POST http://localhost:8080/api/v1/query \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"question": "Wo arbeitet Max?", "agent_id": "my-agent", "top_k": 10}'
```

## Graph

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/v1/graph` | Fetch nodes and edges (paginated, max 2 000) |
| `GET` | `/api/v1/graph/clusters` | Fetch cluster-level overview |
| `GET` | `/api/v1/graph/stats` | Node/edge counts and type distribution |
| `GET` | `/api/v1/graph/traverse/{id}` | Subgraph traversal from a node |
| `GET` | `/api/v1/graph/multiverse` | Cross-agent graph (Multiverse mode) |
| `GET` | `/api/v1/node/{id}` | Fetch a single node |
| `DELETE` | `/api/v1/node/{id}` | Delete a node (GDPR) |
| `POST` | `/api/v1/graph/search` | Full-text node search |

### Graph pagination

The graph endpoint supports cursor-based pagination for large graphs. The server enforces a hard cap of **2 000 nodes per request**.

```bash
# First page
curl "http://localhost:8080/api/v1/graph?agent_id=my-agent&limit=500&offset=0"
# Next page
curl "http://localhost:8080/api/v1/graph?agent_id=my-agent&limit=500&offset=500"
```

```json
{ "nodes": [...], "edges": [...], "node_count": 500, "edge_count": 1243,
  "agent_id": "my-agent", "limit": 500, "offset": 0 }
```

The frontend Studio applies an additional client-side render cap of 2 000 nodes, sorted by degree centrality (highest-connected first), and shows a warning banner when the graph is trimmed.

## Connectors

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/connectors` | Register a new connector |
| `GET` | `/api/v1/connectors` | List connectors for an agent |
| `GET` | `/api/v1/connectors/{id}` | Get connector config + last run state |
| `PATCH` | `/api/v1/connectors/{id}` | Update schedule or credentials |
| `DELETE` | `/api/v1/connectors/{id}` | Remove connector |
| `POST` | `/api/v1/connectors/{id}/trigger` | Manually trigger a connector run |

Connector credentials are envelope-encrypted at rest using AES-256-GCM with the `CONNECTOR_SECRET_KEY` as the Key Encryption Key (KEK). See `.env.example` for the key-generation command.

```bash
# Register a web crawler connector
curl -X POST http://localhost:8080/api/v1/connectors \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "my-agent",
    "type": "web_crawler",
    "name": "Company Blog",
    "config": {"url": "https://example.com/blog", "max_pages": 50},
    "schedule_cron": "0 6 * * *"
  }'
```

The `ConnectorScheduler` runs inside `backend-worker` and polls PostgreSQL for due connectors. Run state (last run time, cursor position, error count) is persisted in Postgres so runs survive process restarts. Credentials are stored encrypted and never logged.

## MCP Server

SpAIder exposes itself over the [Model Context Protocol](https://modelcontextprotocol.io/), so any MCP-capable client (Claude Code, Cursor, custom LLM agents) can use SpAIder as agent-scoped, durable memory without writing HTTP glue.

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/v1/mcp/sse` | Open the long-lived SSE stream (per-API-key auth via `Authorization: Bearer`) |
| `POST` | `/api/v1/mcp/messages/` | Client→server JSON-RPC messages (managed by the MCP SDK) |

Three tools are exposed: `spaider.query`, `spaider.list_recent`, `spaider.ingest_fact`. Tool calls execute in the API key's agent namespace; different keys see disjoint graphs.

- **Disabling the surface.** Set `SPAIDER_MCP_ENABLED=false` to skip mounting the routes entirely. Defaults to enabled.
- **Long-lived deployments.** Restarting `backend-api` drops connected MCP sessions. `make mcp-server-host` runs the MCP sub-app as a standalone host-side process on port 8001 against the same Redis/Neo4j, surviving rebuilds. See `scripts/dev/setup_mcp_dev_agent.sh`.

## Swarm (Multi-Agent)

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/swarm/connect` | Connect two agent graphs with a bridge edge |
| `POST` | `/api/v1/swarm/query` | Query across multiple agents |
| `GET` | `/api/v1/swarm/graph` | Fetch the combined multiverse graph |

## Synthesize

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/synthesize` | Export graph as JSONL fine-tuning dataset |

## Health

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Liveness check (all dependencies) |
