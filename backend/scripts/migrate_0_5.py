#!/usr/bin/env python3
"""
Upgrade a 0.4 installation's data to 0.5.

Dry run by default: it reports what would change and writes nothing. Pass --apply
to migrate. Safe to run repeatedly. Every step fills in only what is missing, so
a field edited after a first run is never overwritten by a later one. Redis is
only read, and InteractionNodes are kept, so the old data stays as a fallback.

    python scripts/migrate_0_5.py                    # report
    python scripts/migrate_0_5.py --apply            # migrate

Existing API keys keep working; their hashes move to Neo4j. If no owner exists one
is created, and its new key is written only to --owner-key-file.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_BATCH = 2000
_ORG_NAME = "Default"
_WORKSPACE = "default"
_OWNER = "migrated-owner"

_NODE_DATES = """
MATCH (n:SpaiderNode)
WHERE n.created_at IS NOT NULL AND NOT n.created_at IS :: STRING
"""
_EDGE_DATES = """
MATCH ()-[r:RELATION]->()
WHERE r.created_at IS NOT NULL AND NOT r.created_at IS :: STRING
"""

# Each entry: name, cypher that counts the work, cypher that does it for one batch
# and returns how many rows it changed. Each repeats until nothing is left.
_GRAPH_STEPS: tuple[tuple[str, str, str], ...] = (
    (
        "node_dates",
        _NODE_DATES + "RETURN count(n) AS c",
        _NODE_DATES + """
        WITH n LIMIT $batch
        SET n.created_at = CASE WHEN n.created_at IS :: INTEGER
            THEN toString(datetime({epochMillis: n.created_at})) ELSE toString(n.created_at) END
        RETURN count(n) AS c
        """,
    ),
    (
        "edge_dates",
        _EDGE_DATES + "RETURN count(r) AS c",
        _EDGE_DATES + """
        WITH r LIMIT $batch
        SET r.created_at = CASE WHEN r.created_at IS :: INTEGER
            THEN toString(datetime({epochMillis: r.created_at})) ELSE toString(r.created_at) END
        RETURN count(r) AS c
        """,
    ),
    (
        "node_energy",
        """
        MATCH (n:SpaiderNode)
        WHERE n.energy_level IS NULL OR n.retrieval_count IS NULL OR n.last_activation IS NULL
        RETURN count(n) AS c
        """,
        """
        MATCH (n:SpaiderNode)
        WHERE n.energy_level IS NULL OR n.retrieval_count IS NULL OR n.last_activation IS NULL
        WITH n LIMIT $batch
        SET n.energy_level = coalesce(n.energy_level, 1.0),
            n.retrieval_count = coalesce(n.retrieval_count, 0),
            n.last_activation = coalesce(n.last_activation, datetime())
        RETURN count(n) AS c
        """,
    ),
    (
        "superseded_nodes",
        """
        MATCH (n:SpaiderNode) WHERE coalesce(n.superseded, false) AND n.superseded_at IS NULL
        RETURN count(n) AS c
        """,
        """
        MATCH (n:SpaiderNode) WHERE coalesce(n.superseded, false) AND n.superseded_at IS NULL
        WITH n LIMIT $batch
        SET n.superseded_at = coalesce(n.updated_at, n.created_at, $now)
        RETURN count(n) AS c
        """,
    ),
    (
        "edge_time",
        """
        MATCH ()-[r:RELATION]->()
        WHERE r.recorded_at IS NULL OR r.valid_from IS NULL OR r.utility_weight IS NULL
        RETURN count(r) AS c
        """,
        """
        MATCH ()-[r:RELATION]->()
        WHERE r.recorded_at IS NULL OR r.valid_from IS NULL OR r.utility_weight IS NULL
        WITH r LIMIT $batch
        SET r.recorded_at = coalesce(r.recorded_at, r.created_at, $now),
            r.valid_from = coalesce(r.valid_from, r.created_at, $now),
            r.utility_weight = coalesce(r.utility_weight, 1.0),
            r.contradiction_checked = coalesce(r.contradiction_checked, true)
        RETURN count(r) AS c
        """,
    ),
    (
        "superseded_edges",
        """
        MATCH ()-[r:RELATION]->()
        WHERE coalesce(r.superseded, false) AND (r.valid_to IS NULL OR r.expired_at IS NULL)
        RETURN count(r) AS c
        """,
        """
        MATCH ()-[r:RELATION]->()
        WHERE coalesce(r.superseded, false) AND (r.valid_to IS NULL OR r.expired_at IS NULL)
        WITH r LIMIT $batch
        WITH r, coalesce(r.superseded_at, r.updated_at, r.created_at, $now) AS ended
        SET r.valid_to = coalesce(r.valid_to, ended), r.expired_at = coalesce(r.expired_at, ended)
        RETURN count(r) AS c
        """,
    ),
    (
        "related_to_edges",
        """
        MATCH (a:SpaiderNode)-[r:RELATED_TO]->(b:SpaiderNode)
        WHERE a.agent_id = b.agent_id
        RETURN count(r) AS c
        """,
        """
        MATCH (a:SpaiderNode)-[r:RELATED_TO]->(b:SpaiderNode)
        WHERE a.agent_id = b.agent_id
        WITH a, r, b LIMIT $batch
        WITH a, r, b, properties(r) AS old, coalesce(r.id, 'repaired-' + elementId(r)) AS edge_id
        MERGE (a)-[e:RELATION {id: edge_id, agent_id: a.agent_id}]->(b)
        ON CREATE SET e += old, e.id = edge_id, e.agent_id = a.agent_id,
                      e.relation = coalesce(old.relation, 'RELATED_TO'),
                      e.recorded_at = coalesce(old.recorded_at, old.created_at, $now),
                      e.valid_from = coalesce(old.valid_from, old.created_at, $now),
                      e.utility_weight = coalesce(old.utility_weight, 1.0),
                      e.contradiction_checked = true
        DELETE r
        RETURN count(e) AS c
        """,
    ),
    (
        "shared_links_to_grants",
        """
        MATCH (a:SystemAgent)-[:SHARES_KNOWLEDGE_WITH]->(b:SystemAgent)
        WHERE NOT EXISTS { MATCH (g:FederationGrant {id: 'migrated-' + a.agent_id + '-' + b.agent_id}) }
        RETURN count(*) AS c
        """,
        """
        MATCH (a:SystemAgent)-[:SHARES_KNOWLEDGE_WITH]->(b:SystemAgent)
        WHERE NOT EXISTS { MATCH (g:FederationGrant {id: 'migrated-' + a.agent_id + '-' + b.agent_id}) }
        WITH a, b LIMIT $batch
        CREATE (g:FederationGrant {
            id: 'migrated-' + a.agent_id + '-' + b.agent_id, source_kb: b.agent_id, target_kb: a.agent_id,
            status: 'approved', max_clearance: 5, approved_by: 'migration', created_at: $now, approved_at: $now
        })
        RETURN count(g) AS c
        """,
    ),
    (
        "interactions_to_episodes",
        """
        MATCH (i:InteractionNode)
        WHERE i.agent_id IS NOT NULL AND i.migrated_at IS NULL
        RETURN count(i) AS c
        """,
        """
        MATCH (i:InteractionNode)
        WHERE i.agent_id IS NOT NULL AND i.migrated_at IS NULL
        WITH i LIMIT $batch
        OPTIONAL MATCH (i)-[:INFORMED_BY]->(n:SpaiderNode)
        WITH i, [x IN collect(n.id) WHERE x IS NOT NULL] AS sources
        WITH i, sources, coalesce(toString(i.timestamp), $now) AS occurred
        MERGE (q:Episode {agent_id: i.agent_id, idempotency_key: 'query:' + i.id + ':query'})
        ON CREATE SET q.id = i.id + ':q', q.kind = 'query', q.status = 'new', q.content = coalesce(i.question, ''),
                      q.session_id = i.session_id, q.occurred_at = occurred, q.recorded_at = occurred,
                      q.metadata = '{"query_id": "' + i.id + '"}', q.clearance_level = 1
        MERGE (a:Episode {agent_id: i.agent_id, idempotency_key: 'query:' + i.id + ':answer'})
        ON CREATE SET a.id = i.id + ':a', a.kind = 'answer', a.status = 'new',
                      a.content = coalesce(i.answer_summary, ''), a.session_id = i.session_id,
                      a.occurred_at = occurred, a.recorded_at = occurred, a.clearance_level = 1,
                      a.metadata = '{"query_id": "' + i.id + '", "migrated_sources": ' + toString(size(sources)) + '}'
        SET i.migrated_at = $now
        RETURN count(i) AS c
        """,
    ),
)

_ALIASES = """
MATCH (n:SpaiderNode)
WHERE n.aliases_text IS NULL AND n.properties CONTAINS '"aliases"'
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Migration:
    def __init__(self, driver, redis, *, apply: bool, owner_key_file: Optional[Path] = None) -> None:
        self._driver = driver
        self._redis = redis
        self._apply = apply
        self._owner_key_file = owner_key_file
        self.report: dict[str, Any] = {"mode": "apply" if apply else "dry-run", "steps": {}}

    async def _single(self, cypher: str, **params) -> Any:
        async with self._driver.session() as session:
            return await (await session.run(cypher, **params)).single()

    async def _run(self, cypher: str, **params) -> None:
        async with self._driver.session() as session:
            await session.run(cypher, **params)

    async def run(self) -> dict:
        org_id, workspace_id = await self._organization()
        await self._agents(org_id, workspace_id)
        await self._keys(org_id)
        for name, count, change in _GRAPH_STEPS:
            await self._graph_step(name, count, change)
        await self._aliases()
        await self._redis_connections()
        await self._owner(org_id)
        return self.report

    def _note(self, step: str, **counts: Any) -> None:
        self.report["steps"][step] = counts

    # ── Tenancy ───────────────────────────────────────────────────────────

    async def _organization(self) -> tuple[str, str]:
        record = await self._single("MATCH (w:Workspace)-[:IN_ORG]->(o:Org) RETURN o.id AS org, w.id AS ws LIMIT 1")
        if record is not None:
            self._note("organization", created=0)
            return record["org"], record["ws"]
        org_id, workspace_id = "migrated-org", "migrated-workspace"
        self._note("organization", created=1)
        if self._apply:
            await self._run(
                """
                MERGE (o:Org {id: $org}) ON CREATE SET o.name = $name, o.created_at = $now
                MERGE (w:Workspace {id: $ws}) ON CREATE SET w.name = $ws_name, w.org_id = $org, w.created_at = $now
                MERGE (w)-[:IN_ORG]->(o)
                """,
                org=org_id, ws=workspace_id, name=_ORG_NAME, ws_name=_WORKSPACE, now=_now(),
            )
        return org_id, workspace_id

    async def _redis_json(self, pattern: str) -> list[tuple[str, dict]]:
        if self._redis is None:
            return []
        rows = []
        async for key in self._redis.scan_iter(match=pattern):
            try:
                value = json.loads(await self._redis.get(key) or "null")
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(value, dict):
                rows.append((key, value))
        return rows

    async def _agents(self, org_id: str, workspace_id: str) -> None:
        agents = [
            row for _, row in await self._redis_json("spaider:agent:*") if row.get("id")
        ]
        rows = [
            {
                "id": agent["id"],
                "name": agent.get("name"),
                "description": agent.get("description"),
                "clearance": agent.get("clearance_level", 1),
                "created_at": agent.get("created_at"),
            }
            for agent in agents
        ]
        record = await self._single(
            """
            UNWIND $ids AS id
            OPTIONAL MATCH (a:SystemAgent {agent_id: id})
            WITH count(id) AS total, count(a) AS existing
            RETURN total - existing AS missing
            """,
            ids=[row["id"] for row in rows],
        )
        unattached = await self._single(
            "MATCH (a:SystemAgent) WHERE NOT (a)-[:IN_WORKSPACE]->() RETURN count(a) AS c"
        )
        self._note("knowledge_bases", from_redis=record["missing"], to_attach=unattached["c"] + record["missing"])
        if not self._apply:
            return
        await self._run(
            """
            UNWIND $rows AS row
            MERGE (a:SystemAgent {agent_id: row.id})
            ON CREATE SET a.created_at = coalesce(row.created_at, $now)
            SET a.name = coalesce(a.name, row.name),
                a.description = coalesce(a.description, row.description),
                a.clearance_level = coalesce(a.clearance_level, row.clearance)
            """,
            rows=rows,
            now=_now(),
        )
        await self._run(
            """
            MATCH (w:Workspace {id: $ws})
            MATCH (a:SystemAgent) WHERE NOT (a)-[:IN_WORKSPACE]->()
            MERGE (a)-[:IN_WORKSPACE]->(w)
            """,
            ws=workspace_id,
        )

    async def _known_kbs(self) -> dict[str, dict]:
        async with self._driver.session() as session:
            result = await session.run(
                "MATCH (a:SystemAgent) RETURN a.agent_id AS id, a.name AS name, a.clearance_level AS c"
            )
            return {record["id"]: dict(record) async for record in result}

    async def _keys(self, org_id: str) -> None:
        """One service account per KB, holding every key that KB had."""
        records = await self._redis_json("spaider:apikey:*")
        known = await self._known_kbs()
        accounts: dict[str, dict] = {}
        keys, orphans = [], 0
        for redis_key, record in records:
            key_hash = redis_key.rsplit(":", 1)[1]
            permissions = record.get("permissions") or []
            kb = record.get("agent_id")
            if "admin" in permissions:
                account_id = "migrated-admin"
                accounts[account_id] = {"id": account_id, "name": "Migrated admin keys", "kb": None, "admin": True}
            elif kb in known:
                account_id = f"migrated-{hashlib.sha256(kb.encode()).hexdigest()[:24]}"
                account = accounts.setdefault(
                    account_id,
                    {
                        "id": account_id,
                        "name": f"Migrated keys for {known[kb]['name'] or kb}",
                        "kb": kb,
                        "clearance": known[kb]["c"] or 1,
                        "role": "reader",
                        "admin": False,
                    },
                )
                if "write" in permissions:
                    account["role"] = "editor"
            else:
                orphans += 1
                continue
            keys.append({"hash": key_hash, "account": account_id, "created_at": record.get("created_at")})
        record = await self._single(
            "UNWIND $hashes AS h OPTIONAL MATCH (k:ApiKey {hash: h}) RETURN count(h) - count(k) AS missing",
            hashes=[key["hash"] for key in keys],
        )
        self._note("api_keys", to_create=record["missing"], accounts=len(accounts), without_a_kb=orphans)
        if not self._apply:
            return
        await self._run(
            """
            UNWIND $accounts AS account
            MATCH (o:Org {id: $org})
            MERGE (p:Principal {id: account.id})
            ON CREATE SET p:ServiceAccount, p.name = account.name, p.org_id = $org, p.default_kb = account.kb,
                          p.clearance_level = coalesce(account.clearance, 5), p.created_at = $now,
                          p.created_by = 'migration', p.kb_default = account.kb IS NOT NULL
            MERGE (p)-[m:IN_ORG]->(o)
            ON CREATE SET m.role = CASE WHEN account.admin THEN 'admin' ELSE 'service' END
            WITH p, account WHERE account.kb IS NOT NULL
            MATCH (kb:SystemAgent {agent_id: account.kb})
            MERGE (p)-[r:HAS_ROLE]->(kb)
            ON CREATE SET r.role = account.role, r.granted_by = 'migration', r.granted_at = $now
            """,
            accounts=list(accounts.values()),
            org=org_id,
            now=_now(),
        )
        await self._run(
            """
            UNWIND $keys AS key
            MATCH (p:Principal {id: key.account})
            MERGE (k:ApiKey {hash: key.hash})
            ON CREATE SET k.created_at = coalesce(key.created_at, $now), k.prefix = 'legacy', k.name = 'migrated'
            MERGE (k)-[:AUTHENTICATES]->(p)
            """,
            keys=keys,
            now=_now(),
        )

    # ── Graph ─────────────────────────────────────────────────────────────

    async def _graph_step(self, name: str, count: str, change: str) -> None:
        pending = (await self._single(count, now=_now()))["c"]
        changed = 0
        if self._apply:
            while True:
                done = (await self._single(change, batch=_BATCH, now=_now()))["c"]
                changed += done
                if done == 0:
                    break
        self._note(name, pending=pending, changed=changed)

    async def _aliases(self) -> None:
        pending = (await self._single(_ALIASES + "RETURN count(n) AS c"))["c"]
        changed = 0
        while self._apply:
            async with self._driver.session() as session:
                result = await session.run(
                    _ALIASES + "RETURN elementId(n) AS id, n.properties AS p LIMIT $batch", batch=_BATCH
                )
                rows = [dict(record) async for record in result]
            if not rows:
                break
            updates = []
            for row in rows:
                try:
                    aliases = json.loads(row["p"]).get("aliases") or []
                except (json.JSONDecodeError, TypeError, AttributeError):
                    aliases = []
                updates.append({"id": row["id"], "text": " ".join(str(a) for a in aliases)})
            await self._run(
                "UNWIND $rows AS row MATCH (n) WHERE elementId(n) = row.id SET n.aliases_text = row.text", rows=updates
            )
            changed += len(updates)
        self._note("aliases", pending=pending, changed=changed)

    async def _redis_connections(self) -> None:
        connections = [
            row for key, row in await self._redis_json("spaider:swarm:connection:*") if row.get("id")
            and key.count(":") == 3
        ]
        rows = [
            {
                "id": f"migrated-{connection['id']}",
                # In 0.4 the source of a connection reads from its target.
                "source": connection.get("target_agent_id"),
                "target": connection.get("source_agent_id"),
                "node_types": connection.get("allowed_node_types"),
                "relation_types": connection.get("allowed_relation_types"),
                "expires_at": connection.get("expires_at"),
                "created_at": connection.get("created_at"),
            }
            for connection in connections
        ]
        record = await self._single(
            "UNWIND $ids AS id OPTIONAL MATCH (g:FederationGrant {id: id}) RETURN count(id) - count(g) AS missing",
            ids=[row["id"] for row in rows],
        )
        self._note("swarm_connections", to_create=record["missing"])
        if not self._apply:
            return
        await self._run(
            """
            UNWIND $rows AS row
            MERGE (g:FederationGrant {id: row.id})
            ON CREATE SET g.source_kb = row.source, g.target_kb = row.target, g.status = 'approved',
                          g.node_types = row.node_types, g.relation_types = row.relation_types,
                          g.expires_at = row.expires_at, g.max_clearance = 5, g.approved_by = 'migration',
                          g.created_at = coalesce(row.created_at, $now), g.approved_at = $now
            """,
            rows=rows,
            now=_now(),
        )

    async def _owner(self, org_id: str) -> None:
        record = await self._single("MATCH (:Principal)-[m:IN_ORG {role: 'owner'}]->(:Org) RETURN count(m) AS c")
        needed = record["c"] == 0
        self._note("owner", to_create=int(needed))
        if not (self._apply and needed):
            return
        raw_key = f"sk-{secrets.token_hex(24)}"
        from app.core.security import hash_api_key

        await self._run(
            """
            MATCH (o:Org {id: $org})
            MERGE (p:Principal {id: $owner})
            ON CREATE SET p:User, p.name = 'Owner', p.org_id = $org, p.clearance_level = 5, p.created_at = $now
            MERGE (p)-[m:IN_ORG]->(o) ON CREATE SET m.role = 'owner'
            CREATE (k:ApiKey {hash: $hash, created_at: $now, prefix: 'sk-', name: 'migration'})-[:AUTHENTICATES]->(p)
            """,
            org=org_id, owner=_OWNER, hash=hash_api_key(raw_key), now=_now(),
        )
        if self._owner_key_file is not None:
            fd = os.open(self._owner_key_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(raw_key + "\n")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Write changes. Without it, only report.")
    parser.add_argument("--owner-key-file", type=Path, default=Path("spaider-owner-key.txt"))
    args = parser.parse_args()

    import redis.asyncio as aioredis

    from app.config import settings
    from app.services.graph_service import GraphService

    graph = GraphService()
    if args.apply:
        await graph.initialize()
    redis = aioredis.from_url(settings.redis_url, decode_responses=True) if settings.redis_url else None
    try:
        report = await Migration(graph._driver, redis, apply=args.apply, owner_key_file=args.owner_key_file).run()
    finally:
        await graph.close()
        if redis is not None:
            await redis.aclose()
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
