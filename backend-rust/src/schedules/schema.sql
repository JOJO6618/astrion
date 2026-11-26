CREATE TABLE IF NOT EXISTS plans (
    username TEXT NOT NULL, id TEXT NOT NULL, revision INTEGER NOT NULL,
    definition TEXT NOT NULL, principal TEXT NOT NULL, source TEXT NOT NULL,
    enabled INTEGER NOT NULL, next_at TEXT, created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL, deleted_at TEXT,
    PRIMARY KEY(username,id)
);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY, username TEXT NOT NULL, schedule_id TEXT NOT NULL,
    revision INTEGER NOT NULL, scheduled_for TEXT NOT NULL, trigger TEXT NOT NULL,
    occurrence_key TEXT NOT NULL UNIQUE, state TEXT NOT NULL,
    definition TEXT NOT NULL, principal TEXT NOT NULL,
    task_id TEXT, conversation_id TEXT, reason TEXT, summary TEXT,
    created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
    waiting_since TEXT, stop_requested INTEGER NOT NULL DEFAULT 0,
    notification_status TEXT NOT NULL DEFAULT 'pending', notification_error TEXT
);
CREATE INDEX IF NOT EXISTS plans_due ON plans(enabled,next_at);
CREATE INDEX IF NOT EXISTS runs_owner ON runs(username,schedule_id,created_at DESC);
CREATE INDEX IF NOT EXISTS runs_state ON runs(state);
