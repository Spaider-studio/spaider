"""
Erasing a knowledge base (GDPR right to erasure).

Runs in two passes with a pause between them: a write that was already in
flight when the caller's access was revoked can land after the first pass, and
would otherwise survive as orphaned data. One tombstone audit event remains,
recording that the erasure happened and holding no content.

Cached query answers cannot be enumerated because their keys are hashed. The
generation bump makes them unreachable at once, and they expire within minutes.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Optional

from sqlalchemy import delete, select

from app.config import settings
from app.core.kv import bump_cache_generation, get_kv
from app.services.audit_store import AuditStore

logger = logging.getLogger(__name__)

SETTLE_SECONDS = 1.0

_DELETE_BATCHES = (
    "MATCH (n:NodeVersion {agent_id: $kb}) DETACH DELETE n RETURN count(n) AS c",
    "MATCH (n:Episode {agent_id: $kb}) DETACH DELETE n RETURN count(n) AS c",
    "MATCH (n:InteractionNode {agent_id: $kb}) DETACH DELETE n RETURN count(n) AS c",
    "MATCH (n:AuditEvent {agent_id: $kb}) DETACH DELETE n RETURN count(n) AS c",
    "MATCH (n:Feedback {agent_id: $kb}) DETACH DELETE n RETURN count(n) AS c",
    "MATCH (n:ModelVersion {agent_id: $kb}) DETACH DELETE n RETURN count(n) AS c",
    "MATCH (n:FederationGrant) WHERE n.source_kb = $kb OR n.target_kb = $kb DETACH DELETE n RETURN count(n) AS c",
    "MATCH (n:KnowledgePR) WHERE n.source_kb = $kb OR n.target_kb = $kb DETACH DELETE n RETURN count(n) AS c",
    "MATCH (n:KPRChange {target_kb: $kb}) DETACH DELETE n RETURN count(n) AS c",
    "MATCH (n:KPRLock {kb: $kb}) DETACH DELETE n RETURN count(n) AS c",
    "MATCH (n:SleepReport {agent_id: $kb}) DETACH DELETE n RETURN count(n) AS c",
    "MATCH (n:SleepLease {agent_id: $kb}) DETACH DELETE n RETURN count(n) AS c",
)


def dataset_root() -> Path:
    return Path(settings.data_dir) / "datasets"


async def _purge_graph(graph, agent_id: str) -> dict:
    counts = {"nodes": 0, "edges": 0, "other": 0}
    result = await graph.delete_agent_graph(agent_id)
    counts["nodes"], counts["edges"] = result.deleted_nodes, result.deleted_edges
    async with graph._driver.session() as session:
        for statement in _DELETE_BATCHES:
            record = await (await session.run(statement, kb=agent_id)).single()
            counts["other"] += record["c"]
    return counts


async def _purge_kv(agent_id: str) -> None:
    kv = get_kv()
    await bump_cache_generation(agent_id)
    async for key in kv.scan_iter(match=f"spaider:connector:run_state:*:{agent_id}"):
        await kv.delete(key)


async def _purge_connector_state(agent_id: str) -> None:
    from app.db.postgres import get_engine
    from app.models.connector import ConnectorConfig, ConnectorRunState, ConnectorSecret

    owned = select(ConnectorConfig.id).where(ConnectorConfig.agent_id == agent_id)
    async with get_engine().begin() as connection:
        # SQLite does not enforce the foreign-key cascades.
        await connection.execute(delete(ConnectorSecret).where(ConnectorSecret.connector_id.in_(owned)))
        await connection.execute(delete(ConnectorRunState).where(ConnectorRunState.connector_id.in_(owned)))
        await connection.execute(delete(ConnectorConfig).where(ConnectorConfig.agent_id == agent_id))


def _purge_files(agent_id: str) -> None:
    if Path(agent_id).name != agent_id or agent_id in {"", ".", ".."}:
        raise ValueError("Invalid KB id.")
    shutil.rmtree(dataset_root() / agent_id, ignore_errors=True)
    shutil.rmtree(Path(settings.data_dir) / "finetune" / agent_id, ignore_errors=True)


async def purge_kb(graph, agent_id: str, *, actor_id: Optional[str] = None) -> dict:
    """Delete everything stored for a KB. Roles must already be revoked."""
    first = await _purge_graph(graph, agent_id)
    await asyncio.sleep(SETTLE_SECONDS)
    second = await _purge_graph(graph, agent_id)

    await _purge_kv(agent_id)
    try:
        await _purge_connector_state(agent_id)
    except Exception as exc:  # noqa: BLE001 — connector tables are optional
        logger.warning("Connector state not purged for %s: %s", agent_id, type(exc).__name__)
    await asyncio.to_thread(_purge_files, agent_id)

    counts = {key: first[key] + second[key] for key in first}
    await AuditStore(graph._driver).record_event(
        workflow_id=f"purge:{agent_id}",
        agent_id=agent_id,
        event_type="kb_purged",
        payload={"actor_id": actor_id, "counts": counts},
    )
    logger.info("Purged KB %s: %s", agent_id, counts)
    return counts
