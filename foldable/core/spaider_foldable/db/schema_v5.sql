-- Schema v5 (0.4): ingest queue accepts kind 'fact' (raw FACT nodes written
-- directly, e.g. by memory connectors). SQLite cannot alter a CHECK constraint,
-- so the table is rebuilt inside the migration transaction; rows are kept.

CREATE TABLE ingest_queue_v5 (
    id         TEXT PRIMARY KEY,
    kind       TEXT NOT NULL CHECK (kind IN ('message','chunk','fact')),
    ref_id     TEXT NOT NULL,
    state      TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','running','done','failed')),
    attempts   INTEGER NOT NULL DEFAULT 0,
    error      TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    fact_id    TEXT
);
INSERT INTO ingest_queue_v5(id, kind, ref_id, state, attempts, error, created_at, updated_at, fact_id)
    SELECT id, kind, ref_id, state, attempts, error, created_at, updated_at, fact_id FROM ingest_queue;
DROP TABLE ingest_queue;
ALTER TABLE ingest_queue_v5 RENAME TO ingest_queue;
CREATE INDEX IF NOT EXISTS idx_ingest_state ON ingest_queue(state, created_at);
