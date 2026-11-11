-- Schema v4 (0.4): projects, provider connections, capability profiles,
-- sharing consents, memory connectors (MCP grants), per-message provider binding.

CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    local_only  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
INSERT OR IGNORE INTO projects(id, name, local_only, created_at, updated_at)
    VALUES ('default', 'Allgemein', 0, strftime('%Y-%m-%dT%H:%M:%SZ','now'), strftime('%Y-%m-%dT%H:%M:%SZ','now'));

ALTER TABLE conversations ADD COLUMN project_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE conversations ADD COLUMN binding_json TEXT;
ALTER TABLE messages ADD COLUMN connection_id TEXT;
ALTER TABLE messages ADD COLUMN provider_model TEXT;
ALTER TABLE documents ADD COLUMN project_id TEXT NOT NULL DEFAULT 'default';
CREATE INDEX IF NOT EXISTS idx_conversations_project ON conversations(project_id);
CREATE INDEX IF NOT EXISTS idx_documents_project ON documents(project_id);

CREATE TABLE IF NOT EXISTS provider_connections (
    id                 TEXT PRIMARY KEY,
    profile_id         TEXT NOT NULL DEFAULT '',
    provider           TEXT NOT NULL,
    protocol           TEXT NOT NULL,
    name               TEXT NOT NULL,
    base_url           TEXT NOT NULL,
    auth_method        TEXT NOT NULL,
    credential_ref     TEXT,
    credential_storage TEXT NOT NULL DEFAULT 'none',
    account_label      TEXT NOT NULL DEFAULT '',
    workspace          TEXT NOT NULL DEFAULT '',
    execution          TEXT NOT NULL DEFAULT 'cloud',
    data_sharing       TEXT NOT NULL DEFAULT 'ask',
    default_model      TEXT,
    extra_json         TEXT NOT NULL DEFAULT '{}',
    enabled            INTEGER NOT NULL DEFAULT 1,
    last_test_json     TEXT,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS capability_profiles (
    scope        TEXT NOT NULL,
    model        TEXT NOT NULL,
    profile_json TEXT NOT NULL,
    checked_at   TEXT NOT NULL,
    PRIMARY KEY (scope, model)
);

CREATE TABLE IF NOT EXISTS sharing_consents (
    connection_id TEXT NOT NULL,
    project_id    TEXT NOT NULL,
    scope         TEXT NOT NULL DEFAULT 'chat',
    granted_at    TEXT NOT NULL,
    PRIMARY KEY (connection_id, project_id)
);

CREATE TABLE IF NOT EXISTS connectors (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    client_kind   TEXT NOT NULL,
    profile_id    TEXT NOT NULL DEFAULT '',
    projects_json TEXT NOT NULL DEFAULT '["default"]',
    rights_json   TEXT NOT NULL DEFAULT '["read"]',
    token_hash    TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    last_used_at  TEXT,
    revoked_at    TEXT
);

CREATE TABLE IF NOT EXISTS connector_calls (
    connector_id      TEXT NOT NULL,
    client_request_id TEXT NOT NULL,
    tool              TEXT NOT NULL,
    result_json       TEXT NOT NULL,
    at                TEXT NOT NULL,
    PRIMARY KEY (connector_id, client_request_id)
);
