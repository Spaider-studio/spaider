"""Neo4j constraints and indexes for the identity, audit and episode models."""
from __future__ import annotations

FOUNDATION_SCHEMA: tuple[str, ...] = (
    "CREATE INDEX audit_workflow IF NOT EXISTS FOR (e:AuditEvent) ON (e.workflow_id)",
    "CREATE INDEX audit_agent_time IF NOT EXISTS FOR (e:AuditEvent) ON (e.agent_id, e.timestamp)",
)
