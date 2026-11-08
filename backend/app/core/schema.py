"""Neo4j constraints and indexes for the identity, audit and episode models."""
from __future__ import annotations

from app.core.hashing import HASH_EMBEDDING_DIM

FOUNDATION_SCHEMA: tuple[str, ...] = (
    "CREATE INDEX audit_workflow IF NOT EXISTS FOR (e:AuditEvent) ON (e.workflow_id)",
    "CREATE INDEX audit_agent_time IF NOT EXISTS FOR (e:AuditEvent) ON (e.agent_id, e.timestamp)",
    "CREATE CONSTRAINT org_id IF NOT EXISTS FOR (o:Org) REQUIRE o.id IS UNIQUE",
    "CREATE CONSTRAINT workspace_id IF NOT EXISTS FOR (w:Workspace) REQUIRE w.id IS UNIQUE",
    "CREATE CONSTRAINT principal_id IF NOT EXISTS FOR (p:Principal) REQUIRE p.id IS UNIQUE",
    "CREATE CONSTRAINT api_key_hash IF NOT EXISTS FOR (k:ApiKey) REQUIRE k.hash IS UNIQUE",
    "CREATE CONSTRAINT bootstrap_lock IF NOT EXISTS FOR (b:BootstrapLock) REQUIRE b.id IS UNIQUE",
    "CREATE INDEX service_account_kb IF NOT EXISTS FOR (s:ServiceAccount) ON (s.default_kb)",
    "CREATE CONSTRAINT episode_id IF NOT EXISTS FOR (e:Episode) REQUIRE e.id IS UNIQUE",
    "CREATE CONSTRAINT episode_idempotency IF NOT EXISTS "
    "FOR (e:Episode) REQUIRE (e.agent_id, e.idempotency_key) IS UNIQUE",
    "CREATE CONSTRAINT feedback_query IF NOT EXISTS FOR (f:Feedback) REQUIRE (f.agent_id, f.query_id) IS UNIQUE",
    "CREATE CONSTRAINT sleep_lease_kb IF NOT EXISTS FOR (l:SleepLease) REQUIRE l.agent_id IS UNIQUE",
    "CREATE INDEX episode_status IF NOT EXISTS FOR (e:Episode) ON (e.agent_id, e.status)",
    "CREATE CONSTRAINT federation_grant_id IF NOT EXISTS FOR (g:FederationGrant) REQUIRE g.id IS UNIQUE",
    "CREATE INDEX federation_grant_target IF NOT EXISTS FOR (g:FederationGrant) ON (g.target_kb, g.status)",
    "CREATE CONSTRAINT kpr_id IF NOT EXISTS FOR (k:KnowledgePR) REQUIRE k.id IS UNIQUE",
    "CREATE CONSTRAINT kpr_lock_kb IF NOT EXISTS FOR (l:KPRLock) REQUIRE l.kb IS UNIQUE",
    "CREATE INDEX kpr_change_kpr IF NOT EXISTS FOR (c:KPRChange) ON (c.kpr_id)",
    "CREATE INDEX kpr_change_key IF NOT EXISTS FOR (c:KPRChange) ON (c.target_kb, c.key)",
    "CREATE INDEX episode_agent_time IF NOT EXISTS FOR (e:Episode) ON (e.agent_id, e.occurred_at)",
    "CREATE POINT INDEX episode_location IF NOT EXISTS FOR (e:Episode) ON (e.location)",
    "CREATE VECTOR INDEX episode_hash_embedding IF NOT EXISTS FOR (e:Episode) ON (e.hash_embedding) "
    f"OPTIONS {{indexConfig: {{`vector.dimensions`: {HASH_EMBEDDING_DIM}, "
    "`vector.similarity_function`: 'cosine'}}",
)
