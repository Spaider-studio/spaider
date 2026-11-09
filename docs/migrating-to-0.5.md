# Migrating from 0.4 to 0.5

0.5 changes how identity, sharing and time are stored. `backend/scripts/migrate_0_5.py`
moves existing data. It is a dry run unless you pass `--apply`, and it can be run
again safely: each step only fills in what is missing, so anything edited after a
first run is left alone.

## Before you start

1. Back up the Neo4j volume and Redis. Rehearse on a copy if the data matters.
2. Stop the old backend and workers. Leave Neo4j and Redis running.
3. Upgrade the code and set the new environment values (see [Environment](#environment)).

## Run it

From `backend/`, with `NEO4J_*` and `REDIS_URL` pointing at the data:

```bash
python scripts/migrate_0_5.py
python scripts/migrate_0_5.py --apply --owner-key-file ./owner-key.txt
```

The first command prints what would change. If no owner exists yet, `--apply`
creates one and writes its new `sk-` key only to that file, readable only by you.
Move the key somewhere safe and delete the file.

## What it does

| Step | Change |
| --- | --- |
| Tenancy | Creates an organization and workspace if there is none, and attaches every KB. |
| Knowledge bases | Adds agents that existed only in Redis, without overwriting existing fields. |
| API keys | Moves key hashes from Redis to Neo4j under one service account per KB. Existing keys keep working. |
| Graph | Normalises timestamps to ISO text and backfills energy and bi-temporal fields. |
| Superseded facts | Gives legacy superseded edges `valid_to` and `expired_at`, and nodes `superseded_at`. |
| `:RELATED_TO` | Rewrites these edges as `:RELATION`, which retrieval reads. |
| Interactions | Copies `InteractionNode` records to query and answer episodes. |
| Sharing | Turns `SHARES_KNOWLEDGE_WITH` links and swarm connections into approved grants. |

Grants created this way have a clearance ceiling of 5, matching the old behaviour.
Each caller's own clearance still applies. Review them under **Sharing** or with
`spaider grant list`, and narrow them if you can.

Redis data and `InteractionNode` records are left in place as a fallback. Once you
are satisfied, you can remove them yourself.

Legacy edges are marked as already checked for contradictions, so the first sleep
cycle does not send your whole history to the LLM.

## Breaking changes to act on

- **Auth is always on.** JWT is gone. Clients send an API key as `X-Api-Key` or
  `Authorization: Bearer`. For local experiments only, `SPAIDER_INSECURE_DEV=true`
  turns auth off, and it is refused in production.
- **WebSocket and event streams** take a single-use `?ticket=` from
  `POST /api/v1/auth/ticket`. `?token=` is removed.
- **Sharing routes** under `swarm/connections`, and the `agents/connect`,
  `agents/links` and `agents/link` routes, are replaced by
  `/api/v1/federation/grants`.
- **`docker compose up` needs a profile.** Set `COMPOSE_PROFILES=lite` or `full`.
- **Redis now needs a password** in the compose stack, and published ports bind to
  loopback. Set `SPAIDER_BIND` to expose them deliberately.
- **Images run as uid 10001.** Volumes mounted from the host must be writable by it.
- **Node ids are unique per KB**, so `DELETE /api/v1/node/{id}` takes `agent_id` when
  the key has no default KB.

## Environment

| Variable | Purpose |
| --- | --- |
| `COMPOSE_PROFILES` | `lite` or `full`. |
| `REDIS_PASSWORD` | Required by the compose stack. Include it in `REDIS_URL`. |
| `SPAIDER_BOOTSTRAP_TOKEN` | Enables first-owner creation on a new install. Clear it afterwards. |
| `SPAIDER_INSECURE_DEV` | Turns auth off for local use. |
| `LLM_FAST_MODEL` | Optional cheaper model for routing and background work. |
| `FINETUNE_OPENAI_API_KEY`, `FINETUNE_TOGETHER_API_KEY`, `FINETUNE_TINKER_API_KEY` | Needed only for fine-tuning. |

`JWT_SECRET` is no longer read and can be removed.

The app refuses to start outside `ENVIRONMENT=development` with the default Neo4j
password.

## Rolling back

Restore the backups from step 1. The script does not delete Redis data, but it
does rewrite `:RELATED_TO` edges and timestamps, which 0.4 reads differently.
