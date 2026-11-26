CREATE TABLE conversations (
    id TEXT PRIMARY KEY NOT NULL,
    summary TEXT NOT NULL,
    message_count INTEGER NOT NULL,
    tool_count INTEGER NOT NULL
);
CREATE TABLE fields (
    conversation TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    scope TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
    PRIMARY KEY (conversation,scope,key)
) WITHOUT ROWID;
CREATE TABLE messages (
    conversation TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL, message_id TEXT, role TEXT, body TEXT NOT NULL,
    PRIMARY KEY (conversation,seq)
) WITHOUT ROWID;
CREATE INDEX messages_by_id ON messages(conversation,message_id);
CREATE INDEX messages_by_role ON messages(conversation,role,seq);
CREATE TABLE legacy_imports (id TEXT PRIMARY KEY NOT NULL);
PRAGMA user_version = 1;
