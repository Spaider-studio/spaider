-- Schema version 2: immediate-visibility facts, measured profiles, dynamic catalog.

-- A FACT node written synchronously at send time is 'raw' until the background
-- extraction has attached entities/relations ('extracted').
ALTER TABLE nodes ADD COLUMN processing TEXT NOT NULL DEFAULT 'extracted';

-- Link a queued extraction to the raw fact it must enrich (no duplicate facts).
ALTER TABLE ingest_queue ADD COLUMN fact_id TEXT;

-- Per-message phase timings (measured only).
ALTER TABLE messages ADD COLUMN timings_json TEXT;

CREATE TABLE IF NOT EXISTS hardware_profiles (
    fingerprint TEXT PRIMARY KEY,
    profile_json TEXT NOT NULL,
    detected_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_profiles (
    id            TEXT PRIMARY KEY,
    fingerprint   TEXT NOT NULL,
    model_sha256  TEXT,
    model_id      TEXT NOT NULL,
    runtime_build TEXT NOT NULL,
    backend       TEXT NOT NULL,
    threads       INTEGER,
    gpu_layers    INTEGER,
    context_tokens INTEGER,
    tokens_per_second REAL,
    time_to_first_token_ms INTEGER,
    prompt_tokens_per_second REAL,
    load_seconds  REAL,
    kind          TEXT NOT NULL DEFAULT 'calibration',
    measured_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_model_profiles_lookup ON model_profiles(fingerprint, model_sha256, backend);

-- Catalog entries that are not built in: resolved from a Hugging Face repo or added by the user.
CREATE TABLE IF NOT EXISTS catalog_entries (
    id          TEXT PRIMARY KEY,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('search','user')),
    entry_json  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- Cached network responses (search results, refresh checks) with their fetch time.
CREATE TABLE IF NOT EXISTS catalog_cache (
    key        TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
