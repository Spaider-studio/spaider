-- Schema v6 (0.5): verified workflows, explicit dependencies, host bindings,
-- workflow runs, success criteria / outcome on tasks, call ids on actions.
-- No new graph database: dependencies are rows in the same profile SQLite file
-- and point at existing FACT nodes (nodes.id), files, bindings, apps and tools.
-- History is never deleted: invalidation only flips `valid` and records why.

CREATE TABLE IF NOT EXISTS agent_workflows (
    id                   TEXT PRIMARY KEY,
    version              INTEGER NOT NULL DEFAULT 1,
    name                 TEXT NOT NULL,
    status               TEXT NOT NULL DEFAULT 'candidate' CHECK (status IN ('candidate','verified','demoted','retired')),
    workflow_json        TEXT NOT NULL,
    origin_task_id       TEXT,
    origin_kind          TEXT NOT NULL DEFAULT 'compiled' CHECK (origin_kind IN ('compiled','taught','migrated')),
    success_runs         INTEGER NOT NULL DEFAULT 0,
    failed_runs          INTEGER NOT NULL DEFAULT 0,
    distinct_param_sets  INTEGER NOT NULL DEFAULT 0,
    negative_checked     INTEGER NOT NULL DEFAULT 0,
    last_verified_at     TEXT,
    last_failed_at       TEXT,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_workflows_status ON agent_workflows(status, updated_at DESC);

CREATE TABLE IF NOT EXISTS agent_dependencies (
    id               TEXT PRIMARY KEY,
    workflow_id      TEXT NOT NULL REFERENCES agent_workflows(id) ON DELETE CASCADE,
    step_index       INTEGER,
    kind             TEXT NOT NULL CHECK (kind IN ('fact','file','binding','app','tool_contract','model','workflow')),
    key              TEXT NOT NULL,
    revision         TEXT NOT NULL,
    origin           TEXT NOT NULL CHECK (origin IN ('observed','declared','inferred')),
    scope            TEXT NOT NULL DEFAULT 'profile',
    valid            INTEGER NOT NULL DEFAULT 1,
    invalidated_at   TEXT,
    invalidated_by   TEXT,
    reason           TEXT,
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_deps_workflow ON agent_dependencies(workflow_id, valid);
CREATE INDEX IF NOT EXISTS idx_agent_deps_key ON agent_dependencies(kind, key);

CREATE TABLE IF NOT EXISTS agent_bindings (
    host_id      TEXT NOT NULL,
    role         TEXT NOT NULL,
    kind         TEXT NOT NULL,
    target       TEXT NOT NULL,
    verified_at  TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (host_id, role)
);

CREATE TABLE IF NOT EXISTS agent_workflow_runs (
    id               TEXT PRIMARY KEY,
    workflow_id      TEXT NOT NULL,
    task_id          TEXT NOT NULL,
    host_id          TEXT NOT NULL,
    decision         TEXT NOT NULL,
    reason           TEXT,
    params_hash      TEXT,
    outcome          TEXT,
    llm_calls        INTEGER NOT NULL DEFAULT 0,
    reused_steps     INTEGER NOT NULL DEFAULT 0,
    repaired_steps   TEXT,
    skipped_steps    TEXT,
    gate_ms          INTEGER,
    created_at       TEXT NOT NULL,
    finished_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_agent_wf_runs ON agent_workflow_runs(workflow_id, created_at DESC);

ALTER TABLE agent_tasks ADD COLUMN success_json TEXT;
ALTER TABLE agent_tasks ADD COLUMN outcome TEXT;
ALTER TABLE agent_tasks ADD COLUMN outcome_json TEXT;
ALTER TABLE agent_tasks ADD COLUMN workflow_id TEXT;
ALTER TABLE agent_tasks ADD COLUMN project_id TEXT;
ALTER TABLE agent_tasks ADD COLUMN connection_id TEXT;
ALTER TABLE agent_tasks ADD COLUMN reuse_json TEXT;
ALTER TABLE agent_actions ADD COLUMN call_id TEXT;
ALTER TABLE agent_actions ADD COLUMN origin TEXT NOT NULL DEFAULT 'planner';
