"""Neo4j constraints and indexes for the identity, audit and episode models."""
from __future__ import annotations

FOUNDATION_SCHEMA: tuple[str, ...] = (
    "CREATE INDEX audit_workflow IF NOT EXISTS FOR (e:AuditEvent) ON (e.workflow_id)",
    "CREATE INDEX audit_agent_time IF NOT EXISTS FOR (e:AuditEvent) ON (e.agent_id, e.timestamp)",
    "CREATE CONSTRAINT org_id IF NOT EXISTS FOR (o:Org) REQUIRE o.id IS UNIQUE",
    "CREATE CONSTRAINT workspace_id IF NOT EXISTS FOR (w:Workspace) REQUIRE w.id IS UNIQUE",
    "CREATE CONSTRAINT principal_id IF NOT EXISTS FOR (p:Principal) REQUIRE p.id IS UNIQUE",
    "CREATE CONSTRAINT api_key_hash IF NOT EXISTS FOR (k:ApiKey) REQUIRE k.hash IS UNIQUE",
    "CREATE CONSTRAINT bootstrap_lock IF NOT EXISTS FOR (b:BootstrapLock) REQUIRE b.id IS UNIQUE",
    "CREATE INDEX service_account_kb IF NOT EXISTS FOR (s:ServiceAccount) ON (s.default_kb)",
)
