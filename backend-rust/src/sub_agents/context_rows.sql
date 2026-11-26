ALTER TABLE child_task ADD COLUMN message_count INTEGER;
CREATE TABLE child_message (
    task_id TEXT NOT NULL REFERENCES child_task(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    body TEXT NOT NULL,
    PRIMARY KEY (task_id, seq)
) STRICT, WITHOUT ROWID;
PRAGMA user_version = 2;
