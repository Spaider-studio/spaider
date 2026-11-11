-- SpAIder Foldable schema, version 1.
-- Conventions: ids are UUID4 strings, timestamps ISO-8601 UTC, JSON in *_json
-- columns, paths relative to the data directory (forward slashes).

CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    deleted_at    TEXT,
    last_model_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_conversations_updated ON conversations(updated_at DESC);

CREATE TABLE IF NOT EXISTS messages (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    seq             INTEGER NOT NULL,
    role            TEXT NOT NULL CHECK (role IN ('user','assistant','system')),
    content         TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'complete'
                    CHECK (status IN ('complete','streaming','stopped','error')),
    model_id        TEXT,
    error           TEXT,
    usage_json      TEXT,
    sources_json    TEXT NOT NULL DEFAULT '[]',
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv_seq ON messages(conversation_id, seq);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content, content='messages', content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
END;
CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES ('delete', old.rowid, old.content);
END;
CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE OF content ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES ('delete', old.rowid, old.content);
    INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
END;

CREATE TABLE IF NOT EXISTS documents (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    content_type    TEXT NOT NULL,
    size_bytes      INTEGER NOT NULL,
    stored_path     TEXT NOT NULL,
    sha256          TEXT NOT NULL,
    text_chars      INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'imported'
                    CHECK (status IN ('imported','indexing','indexed','failed')),
    warnings_json   TEXT NOT NULL DEFAULT '[]',
    conversation_id TEXT,
    progress_done   INTEGER NOT NULL DEFAULT 0,
    progress_total  INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    deleted_at      TEXT
);

CREATE TABLE IF NOT EXISTS document_text (
    document_id TEXT PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
    text        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    id          TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ord         INTEGER NOT NULL,
    text        TEXT NOT NULL,
    char_start  INTEGER NOT NULL,
    char_end    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(document_id, ord);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text, content='chunks', content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, text) VALUES (new.rowid, new.text);
END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
END;

-- Knowledge graph (SpAIder GraphPayload persisted relationally).
CREATE TABLE IF NOT EXISTS nodes (
    id              TEXT PRIMARY KEY,
    agent_id        TEXT NOT NULL DEFAULT 'local',
    label           TEXT NOT NULL,
    type            TEXT NOT NULL,
    description     TEXT,
    source_text     TEXT,
    properties_json TEXT NOT NULL DEFAULT '{}',
    confidence      REAL NOT NULL DEFAULT 1.0,
    superseded      INTEGER NOT NULL DEFAULT 0,
    superseded_by   TEXT,
    role_superseded INTEGER NOT NULL DEFAULT 0,
    utility_weight  REAL NOT NULL DEFAULT 1.0,
    origin_kind     TEXT,
    origin_id       TEXT,
    origin_title    TEXT,
    origin_deleted  INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(agent_id, type);
CREATE INDEX IF NOT EXISTS idx_nodes_label ON nodes(agent_id, label COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_nodes_origin ON nodes(origin_kind, origin_id);

CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
    label, description, source_text,
    content='nodes', content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS nodes_ai AFTER INSERT ON nodes BEGIN
    INSERT INTO nodes_fts(rowid, label, description, source_text)
    VALUES (new.rowid, new.label, new.description, new.source_text);
END;
CREATE TRIGGER IF NOT EXISTS nodes_ad AFTER DELETE ON nodes BEGIN
    INSERT INTO nodes_fts(nodes_fts, rowid, label, description, source_text)
    VALUES ('delete', old.rowid, old.label, old.description, old.source_text);
END;
CREATE TRIGGER IF NOT EXISTS nodes_au AFTER UPDATE OF label, description, source_text ON nodes BEGIN
    INSERT INTO nodes_fts(nodes_fts, rowid, label, description, source_text)
    VALUES ('delete', old.rowid, old.label, old.description, old.source_text);
    INSERT INTO nodes_fts(rowid, label, description, source_text)
    VALUES (new.rowid, new.label, new.description, new.source_text);
END;

CREATE TABLE IF NOT EXISTS node_embeddings (
    node_id   TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    model_id  TEXT NOT NULL,
    dims      INTEGER NOT NULL,
    vector    BLOB NOT NULL,
    PRIMARY KEY (node_id, model_id)
);

CREATE TABLE IF NOT EXISTS edges (
    id              TEXT PRIMARY KEY,
    agent_id        TEXT NOT NULL DEFAULT 'local',
    source_id       TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    target_id       TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    relation        TEXT NOT NULL,
    properties_json TEXT NOT NULL DEFAULT '{}',
    superseded      INTEGER NOT NULL DEFAULT 0,
    superseded_by   TEXT,
    utility_weight  REAL NOT NULL DEFAULT 1.0,
    origin_kind     TEXT,
    origin_id       TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_edges_unique ON edges(source_id, target_id, relation);

CREATE TABLE IF NOT EXISTS notes (
    id         TEXT PRIMARY KEY,
    text       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    text, content='notes', content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS notes_ai AFTER INSERT ON notes BEGIN
    INSERT INTO notes_fts(rowid, text) VALUES (new.rowid, new.text);
END;
CREATE TRIGGER IF NOT EXISTS notes_ad AFTER DELETE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
END;
CREATE TRIGGER IF NOT EXISTS notes_au AFTER UPDATE OF text ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
    INSERT INTO notes_fts(rowid, text) VALUES (new.rowid, new.text);
END;

CREATE TABLE IF NOT EXISTS models (
    id                   TEXT PRIMARY KEY,
    name                 TEXT NOT NULL,
    role                 TEXT NOT NULL CHECK (role IN ('chat','embedding')),
    catalog_id           TEXT,
    source               TEXT NOT NULL CHECK (source IN ('catalog','import')),
    rel_path             TEXT NOT NULL,
    size_bytes           INTEGER NOT NULL,
    sha256               TEXT,
    sha256_verified      INTEGER NOT NULL DEFAULT 0,
    architecture         TEXT,
    quantization         TEXT,
    context_length       INTEGER,
    embedding_dimensions INTEGER,
    license_json         TEXT,
    metadata_json        TEXT NOT NULL DEFAULT '{}',
    state                TEXT NOT NULL DEFAULT 'ready' CHECK (state IN ('ready','broken')),
    created_at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_state (
    key        TEXT PRIMARY KEY,
    value_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id              TEXT PRIMARY KEY,
    kind            TEXT NOT NULL,
    state           TEXT NOT NULL,
    catalog_id      TEXT,
    model_id        TEXT,
    phase           TEXT,
    progress_json   TEXT NOT NULL DEFAULT '{}',
    message_de      TEXT NOT NULL DEFAULT '',
    error_json      TEXT,
    result_model_id TEXT,
    journal_json    TEXT NOT NULL DEFAULT '[]',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    finished_at     TEXT
);

CREATE TABLE IF NOT EXISTS ingest_queue (
    id         TEXT PRIMARY KEY,
    kind       TEXT NOT NULL CHECK (kind IN ('message','chunk')),
    ref_id     TEXT NOT NULL,
    state      TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','running','done','failed')),
    attempts   INTEGER NOT NULL DEFAULT 0,
    error      TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ingest_state ON ingest_queue(state, created_at);
