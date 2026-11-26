CREATE TABLE child_task (
    id TEXT PRIMARY KEY,
    parent_id TEXT NOT NULL,
    record TEXT NOT NULL,
    context TEXT,
    stats TEXT,
    output TEXT,
    activity_tail INTEGER NOT NULL DEFAULT 0
) STRICT;
CREATE INDEX child_task_parent ON child_task(parent_id, id);

CREATE TABLE child_state (
    parent_id TEXT PRIMARY KEY,
    state TEXT NOT NULL
) STRICT;

CREATE TABLE child_activity (
    task_id TEXT NOT NULL REFERENCES child_task(id) ON DELETE CASCADE,
    start_offset INTEGER NOT NULL,
    end_offset INTEGER NOT NULL,
    event TEXT,
    PRIMARY KEY (task_id, end_offset)
) STRICT, WITHOUT ROWID;

CREATE TABLE imported_child (
    task_id TEXT PRIMARY KEY
) STRICT;
CREATE TABLE imported_child_state (
    parent_id TEXT PRIMARY KEY
) STRICT;

PRAGMA user_version = 1;
