-- Schema v3: agent tasks, action journal and reusable procedures (recipes).
-- Tasks are host-bound (host_id): a task started on another PC is never resumed
-- blindly; the user sees it as "interrupted" and may start it again.

CREATE TABLE IF NOT EXISTS agent_tasks (
    id                   TEXT PRIMARY KEY,
    goal                 TEXT NOT NULL,
    state                TEXT NOT NULL,
    step                 INTEGER NOT NULL DEFAULT 0,
    max_steps            INTEGER NOT NULL,
    max_seconds          INTEGER NOT NULL,
    scopes_json          TEXT NOT NULL,
    conversation_id      TEXT,
    live_session_id      TEXT,
    recipe_id            TEXT,
    parameters_json      TEXT,
    summary_de           TEXT NOT NULL DEFAULT '',
    result_json          TEXT,
    pending_question_json TEXT,
    last_error           TEXT,
    llm_calls            INTEGER NOT NULL DEFAULT 0,
    host_id              TEXT NOT NULL,
    cancel_generation    INTEGER NOT NULL DEFAULT 0,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    started_at           TEXT,
    finished_at          TEXT
);
CREATE INDEX IF NOT EXISTS idx_agent_tasks_created ON agent_tasks(created_at DESC);

CREATE TABLE IF NOT EXISTS agent_actions (
    action_id        TEXT PRIMARY KEY,
    task_id          TEXT NOT NULL REFERENCES agent_tasks(id) ON DELETE CASCADE,
    seq              INTEGER NOT NULL,
    tool             TEXT NOT NULL,
    arguments_json   TEXT NOT NULL,
    state            TEXT NOT NULL,
    reason_de        TEXT,
    expected_effect  TEXT,
    observation_ref  TEXT,
    side_effect      TEXT NOT NULL DEFAULT 'none',
    started_at       TEXT,
    finished_at      TEXT,
    duration_ms      INTEGER,
    result_json      TEXT,
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_actions_task ON agent_actions(task_id, seq);

CREATE TABLE IF NOT EXISTS agent_recipes (
    id               TEXT PRIMARY KEY,
    version          INTEGER NOT NULL DEFAULT 1,
    name             TEXT NOT NULL,
    recipe_json      TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    last_verified_at TEXT
);
