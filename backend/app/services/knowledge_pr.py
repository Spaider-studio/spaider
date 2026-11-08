"""
Knowledge Pull Requests: proposing that one KB's knowledge be merged into another.

A KPR records a diff, a steward of the target approves it, and applying it
writes the diff. Every applied change is recorded per field, in order, so a KPR
can be undone later even after others have touched the same node:

* A field a later KPR also changed keeps the later value. That KPR inherits the
  older ``before``, so reverting it afterwards still restores the original.
* A node or edge a later KPR relied on stays, and the earliest such KPR becomes
  its creator.
* A field nothing else changed goes back to its ``before``, unless someone edited
  it outside a KPR, in which case the edit is left alone and reported.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import HTTPException, status

from app.core.authz import Action, Principal, authorize
from app.core.kv import bump_cache_generation
from app.services.audit_store import AuditStore

logger = logging.getLogger(__name__)

NODE_FIELDS = ("label", "type", "description", "source_text", "properties", "embedding", "clearance_level")
_DISPLAY_FIELDS = tuple(field for field in NODE_FIELDS if field != "embedding")
_MAX_NODES = 500
_EXISTS = "__exists__"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _edge_id(target_kb: str, source: str, relation: str, target: str) -> str:
    return "kpr-" + hashlib.sha256("\0".join((target_kb, source, relation, target)).encode()).hexdigest()[:24]


async def _single(tx, cypher: str, **params) -> Optional[Any]:
    return await (await tx.run(cypher, **params)).single()


async def _node(tx, kb: str, node_id: str) -> Optional[dict]:
    record = await _single(
        tx, "MATCH (n:SpaiderNode {id: $id, agent_id: $kb}) RETURN properties(n) AS p", id=node_id, kb=kb
    )
    return dict(record["p"]) if record else None


class KnowledgePRService:
    def __init__(self, driver) -> None:
        self._driver = driver

    async def _audit(self, kpr: dict, event: str, actor: Principal) -> None:
        await AuditStore(self._driver).record_event(
            workflow_id=f"kpr:{kpr['id']}", agent_id=kpr["target_kb"], event_type=event, payload={"actor_id": actor.id}
        )

    # ── Proposing ─────────────────────────────────────────────────────────

    async def create(
        self,
        principal: Principal,
        source_kb: str,
        target_kb: str,
        node_ids: list[str],
        title: str,
    ) -> dict:
        if source_kb == target_kb:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Source and target must differ.")
        if not 0 < len(node_ids) <= _MAX_NODES:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Propose 1 to {_MAX_NODES} nodes.")
        await authorize(principal, source_kb, Action.READ)
        await authorize(principal, target_kb, Action.WRITE)

        async def _read(tx) -> dict:
            return await self._diff(tx, source_kb, target_kb, node_ids, principal.clearance_level)

        async with self._driver.session() as session:
            diff = await session.execute_read(_read)
        if not diff["nodes"]:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="None of those nodes can be proposed.")
        kpr = {
            "id": str(uuid.uuid4()),
            "source_kb": source_kb,
            "target_kb": target_kb,
            "title": title,
            "status": "open",
            "created_by": principal.id,
            "created_at": _now(),
            "max_clearance": max(item["clearance"] for item in diff["nodes"]),
            "diff": json.dumps(diff),
        }
        async with self._driver.session() as session:
            await session.run("CREATE (k:KnowledgePR) SET k = $kpr", kpr=kpr)
        await self._audit(kpr, "kpr_opened", principal)
        return self._view(kpr)

    async def _diff(self, tx, source_kb: str, target_kb: str, node_ids: list[str], clearance: int) -> dict:
        result = await tx.run(
            """
            MATCH (n:SpaiderNode {agent_id: $source})
            WHERE n.id IN $ids AND coalesce(n.clearance_level, 1) <= $clearance AND NOT n:SystemAgent
            OPTIONAL MATCH (byId:SpaiderNode {id: n.id, agent_id: $target})
            OPTIONAL MATCH (byName:SpaiderNode {agent_id: $target, label: n.label, type: n.type})
            WITH n, byId, head(collect(byName)) AS byName
            WITH n, coalesce(byId, byName) AS existing
            RETURN properties(n) AS source, properties(existing) AS existing
            """,
            source=source_kb,
            target=target_kb,
            ids=node_ids,
            clearance=clearance,
        )
        rows = [dict(record) async for record in result]
        mapping: dict[str, str] = {}
        nodes = []
        for row in rows:
            source, existing = row["source"], row["existing"]
            mapping[source["id"]] = existing["id"] if existing else source["id"]
            source_clearance = int(source.get("clearance_level") or 1)
            changes = {}
            if existing is None:
                action = "add"
                for field in NODE_FIELDS:
                    if source.get(field) is not None:
                        changes[field] = {"before": None, "after": source[field]}
            else:
                for field in NODE_FIELDS:
                    after = source.get(field)
                    if field == "clearance_level":
                        # A merge can make a node more sensitive, never less.
                        after = max(int(existing.get(field) or 1), source_clearance)
                    if after is not None and after != existing.get(field):
                        changes[field] = {"before": existing.get(field), "after": after}
                action = "update" if changes else "keep"
            nodes.append(
                {
                    "source_id": source["id"],
                    "target_id": mapping[source["id"]],
                    "action": action,
                    "clearance": source_clearance,
                    "label": source.get("label"),
                    "changes": changes,
                }
            )
        result = await tx.run(
            """
            MATCH (a:SpaiderNode {agent_id: $source})-[r:RELATION {agent_id: $source}]->
                  (b:SpaiderNode {agent_id: $source})
            WHERE a.id IN $ids AND b.id IN $ids AND NOT coalesce(r.superseded, false)
            RETURN a.id AS a, b.id AS b, r.relation AS relation, r.properties AS properties
            """,
            source=source_kb,
            ids=list(mapping),
        )
        edges = []
        async for record in result:
            source_id, target_id = mapping[record["a"]], mapping[record["b"]]
            edges.append(
                {
                    "id": _edge_id(target_kb, source_id, record["relation"] or "", target_id),
                    "source": source_id,
                    "target": target_id,
                    "relation": record["relation"],
                    "properties": record["properties"],
                }
            )
        return {"nodes": nodes, "edges": edges}

    @staticmethod
    def _view(kpr: dict) -> dict:
        view = {key: value for key, value in kpr.items() if key != "diff"}
        diff = json.loads(kpr["diff"])
        for node in diff["nodes"]:
            node["changes"] = {key: value for key, value in node["changes"].items() if key in _DISPLAY_FIELDS}
        view["diff"] = diff
        return view

    async def _load(self, kpr_id: str) -> dict:
        async with self._driver.session() as session:
            record = await (
                await session.run("MATCH (k:KnowledgePR {id: $id}) RETURN properties(k) AS k", id=kpr_id)
            ).single()
        if record is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Knowledge PR not found.")
        return dict(record["k"])

    async def get(self, principal: Principal, kpr_id: str) -> dict:
        kpr = await self._load(kpr_id)
        await authorize(principal, kpr["target_kb"], Action.READ)
        if kpr["max_clearance"] > principal.clearance_level:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Not permitted on this knowledge base.")
        return self._view(kpr)

    async def list_for(self, principal: Principal, target_kb: str) -> list[dict]:
        await authorize(principal, target_kb, Action.READ)
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (k:KnowledgePR {target_kb: $kb})
                WHERE k.max_clearance <= $clearance
                RETURN properties(k) AS k ORDER BY k.created_at DESC LIMIT 200
                """,
                kb=target_kb,
                clearance=principal.clearance_level,
            )
            return [{key: value for key, value in dict(r["k"]).items() if key != "diff"} async for r in result]

    # ── Decisions ─────────────────────────────────────────────────────────

    async def _authorize_decision(self, principal: Principal, kpr: dict) -> None:
        await authorize(principal, kpr["target_kb"], Action.MANAGE)
        if kpr["max_clearance"] > principal.clearance_level:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="This diff is above your clearance.")

    async def reject(self, principal: Principal, kpr_id: str) -> dict:
        kpr = await self._load(kpr_id)
        await self._authorize_decision(principal, kpr)
        async with self._driver.session() as session:
            record = await (
                await session.run(
                    """
                    MATCH (k:KnowledgePR {id: $id, status: 'open'})
                    SET k.status = 'rejected', k.rejected_by = $by, k.rejected_at = $now
                    RETURN properties(k) AS k
                    """,
                    id=kpr_id,
                    by=principal.id,
                    now=_now(),
                )
            ).single()
        if record is None:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="This Knowledge PR is no longer open.")
        await self._audit(kpr, "kpr_rejected", principal)
        return self._view(dict(record["k"]))

    @staticmethod
    async def _lock_target(tx, target_kb: str) -> int:
        """Serialise changes to one KB and hand out the next sequence number."""
        record = await _single(
            tx,
            """
            MERGE (l:KPRLock {kb: $kb})
            ON CREATE SET l.seq = 0
            SET l.seq = l.seq + 1
            RETURN l.seq AS seq
            """,
            kb=target_kb,
        )
        return record["seq"]

    async def approve(self, principal: Principal, kpr_id: str) -> dict:
        """Apply the reviewed diff, atomically. A changed target means re-review."""
        kpr = await self._load(kpr_id)
        await self._authorize_decision(principal, kpr)
        if kpr["created_by"] == principal.id and not (principal.is_org_admin or principal.insecure):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="A proposal needs a second person to approve it.")

        async def _apply(tx) -> dict:
            seq = await self._lock_target(tx, kpr["target_kb"])
            current = await _single(tx, "MATCH (k:KnowledgePR {id: $id}) RETURN k.status AS s", id=kpr_id)
            if current["s"] != "open":
                raise HTTPException(status.HTTP_409_CONFLICT, detail="This Knowledge PR is no longer open.")
            await self._write_diff(tx, kpr, json.loads(kpr["diff"]), seq)
            record = await _single(
                tx,
                """
                MATCH (k:KnowledgePR {id: $id})
                SET k.status = 'applied', k.approved_by = $by, k.applied_at = $now, k.seq = $seq
                RETURN properties(k) AS k
                """,
                id=kpr_id,
                by=principal.id,
                now=_now(),
                seq=seq,
            )
            return dict(record["k"])

        async with self._driver.session() as session:
            updated = await session.execute_write(_apply)
        await bump_cache_generation(kpr["target_kb"])
        await self._audit(kpr, "kpr_applied", principal)
        return self._view(updated)

    async def _write_diff(self, tx, kpr: dict, diff: dict, seq: int) -> None:
        target_kb = kpr["target_kb"]
        now = _now()
        for item in diff["nodes"]:
            existing = await _node(tx, target_kb, item["target_id"])
            if (item["action"] == "add") != (existing is None):
                raise HTTPException(status.HTTP_409_CONFLICT, detail="The target changed since this diff was made.")
            fields = dict(item["changes"])
            for field, change in fields.items():
                if existing is not None and existing.get(field) != change["before"]:
                    raise HTTPException(status.HTTP_409_CONFLICT, detail="The target changed since this diff was made.")
            values = {field: change["after"] for field, change in fields.items()}
            if item["action"] == "add":
                await tx.run(
                    """
                    CREATE (n:SpaiderNode {id: $id, agent_id: $kb})
                    SET n += $values, n.created_at = $now, n.energy_level = 1.0, n.retrieval_count = 0
                    """,
                    id=item["target_id"], kb=target_kb, values=values, now=now,
                )
            elif values:
                await tx.run(
                    "MATCH (n:SpaiderNode {id: $id, agent_id: $kb}) SET n += $values",
                    id=item["target_id"], kb=target_kb, values=values,
                )
            await self._record(tx, kpr["id"], target_kb, f"node:{item['target_id']}", item["action"], fields, seq)

        for edge in diff["edges"]:
            exists = await _single(
                tx, "MATCH ()-[r:RELATION {id: $id, agent_id: $kb}]->() RETURN r.id AS id", id=edge["id"], kb=target_kb
            )
            if exists is None:
                await tx.run(
                    """
                    MATCH (a:SpaiderNode {id: $source, agent_id: $kb}), (b:SpaiderNode {id: $target, agent_id: $kb})
                    CREATE (a)-[r:RELATION {id: $id, agent_id: $kb}]->(b)
                    SET r.relation = $relation, r.properties = $properties, r.created_at = $now, r.recorded_at = $now,
                        r.valid_from = $now, r.contradiction_checked = true
                    """,
                    id=edge["id"], kb=target_kb, source=edge["source"], target=edge["target"],
                    relation=edge["relation"], properties=edge["properties"], now=now,
                )
            await self._record(tx, kpr["id"], target_kb, f"edge:{edge['id']}", "keep" if exists else "add", {}, seq)

    @staticmethod
    async def _record(tx, kpr_id: str, target_kb: str, key: str, action: str, fields: dict, seq: int) -> None:
        await tx.run(
            """
            CREATE (:KPRChange {kpr_id: $kpr, target_kb: $kb, key: $key, action: $action,
                                fields: $fields, seq: $seq, active: true})
            """,
            kpr=kpr_id, kb=target_kb, key=key, action=action, fields=json.dumps(fields), seq=seq,
        )

    # ── Reverting ─────────────────────────────────────────────────────────

    async def revert(self, principal: Principal, kpr_id: str) -> dict:
        kpr = await self._load(kpr_id)
        await self._authorize_decision(principal, kpr)

        async def _undo(tx) -> tuple[dict, list[str]]:
            await self._lock_target(tx, kpr["target_kb"])
            current = await _single(tx, "MATCH (k:KnowledgePR {id: $id}) RETURN k.status AS s", id=kpr_id)
            if current["s"] != "applied":
                raise HTTPException(status.HTTP_409_CONFLICT, detail="Only an applied Knowledge PR can be reverted.")
            result = await tx.run(
                """
                MATCH (c:KPRChange {kpr_id: $id, active: true})
                RETURN elementId(c) AS element, c.key AS key, c.action AS action, c.fields AS fields, c.seq AS seq
                ORDER BY CASE WHEN c.key STARTS WITH 'edge:' THEN 0 ELSE 1 END
                """,
                id=kpr_id,
            )
            skipped: list[str] = []
            for change in [dict(record) async for record in result]:
                await self._undo_change(tx, kpr["target_kb"], change, skipped)
            record = await _single(
                tx,
                """
                MATCH (k:KnowledgePR {id: $id})
                SET k.status = 'reverted', k.reverted_by = $by, k.reverted_at = $now
                RETURN properties(k) AS k
                """,
                id=kpr_id,
                by=principal.id,
                now=_now(),
            )
            return dict(record["k"]), skipped

        async with self._driver.session() as session:
            updated, skipped = await session.execute_write(_undo)
        await bump_cache_generation(kpr["target_kb"])
        await self._audit(kpr, "kpr_reverted", principal)
        view = self._view(updated)
        view["skipped_fields"] = skipped
        return view

    @staticmethod
    async def _later(tx, target_kb: str, key: str, seq: int) -> list[dict]:
        result = await tx.run(
            """
            MATCH (c:KPRChange {target_kb: $kb, key: $key, active: true})
            WHERE c.seq > $seq
            RETURN elementId(c) AS element, c.action AS action, c.fields AS fields, c.seq AS seq
            ORDER BY c.seq
            """,
            kb=target_kb,
            key=key,
            seq=seq,
        )
        return [dict(record) async for record in result]

    @staticmethod
    async def _save_change(tx, element: str, action: str, fields: dict) -> None:
        await tx.run(
            "MATCH (c:KPRChange) WHERE elementId(c) = $element SET c.action = $action, c.fields = $fields",
            element=element, action=action, fields=json.dumps(fields),
        )

    async def _undo_change(self, tx, target_kb: str, change: dict, skipped: list[str]) -> None:
        kind, identifier = change["key"].split(":", 1)
        fields = json.loads(change["fields"])
        later = await self._later(tx, target_kb, change["key"], change["seq"])

        if change["action"] == "add":
            if later:
                # Something later relies on this existing, so it stays and that
                # later change becomes its creator.
                heir = later[0]
                heir_fields = json.loads(heir["fields"])
                for field, value in fields.items():
                    if field in heir_fields:
                        heir_fields[field]["before"] = value["before"]
                    else:
                        heir_fields[field] = value
                await self._save_change(tx, heir["element"], "add", heir_fields)
            elif kind == "node":
                await tx.run(
                    "MATCH (n:SpaiderNode {id: $id, agent_id: $kb}) DETACH DELETE n", id=identifier, kb=target_kb
                )
            else:
                await tx.run("MATCH ()-[r:RELATION {id: $id, agent_id: $kb}]->() DELETE r", id=identifier, kb=target_kb)
        elif change["action"] == "update":
            restore: dict[str, Any] = {}
            existing = await _node(tx, target_kb, identifier)
            for field, value in fields.items():
                heir = next((item for item in later if field in json.loads(item["fields"])), None)
                if heir is not None:
                    heir_fields = json.loads(heir["fields"])
                    heir_fields[field]["before"] = value["before"]
                    await self._save_change(tx, heir["element"], heir["action"], heir_fields)
                    for item in later:
                        if item["element"] == heir["element"]:
                            item["fields"] = json.dumps(heir_fields)
                elif existing is not None and existing.get(field) != value["after"]:
                    # Edited outside any KPR since; that edit is not ours to undo.
                    skipped.append(f"{identifier}.{field}")
                else:
                    restore[field] = value["before"]
            if restore:
                await tx.run(
                    "MATCH (n:SpaiderNode {id: $id, agent_id: $kb}) SET n += $values",
                    id=identifier, kb=target_kb, values=restore,
                )
        await tx.run(
            "MATCH (c:KPRChange) WHERE elementId(c) = $element SET c.active = false", element=change["element"]
        )
