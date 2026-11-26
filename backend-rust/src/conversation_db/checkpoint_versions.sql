CREATE TABLE checkpoint_revisions (
    conversation TEXT PRIMARY KEY NOT NULL,
    revision INTEGER NOT NULL CHECK (revision > 0)
) WITHOUT ROWID;

CREATE TABLE checkpoint_message_versions (
    conversation TEXT NOT NULL,
    seq INTEGER NOT NULL,
    revision INTEGER NOT NULL,
    body TEXT NOT NULL,
    PRIMARY KEY (conversation, seq, revision)
) WITHOUT ROWID;

-- Keep the first preimage of each changed message after a checkpoint. The
-- archive survives conversation deletion because restored copies can share it.
CREATE TRIGGER checkpoint_message_updated BEFORE UPDATE OF body ON messages
WHEN OLD.body <> NEW.body
BEGIN
    INSERT OR IGNORE INTO checkpoint_message_versions
        SELECT OLD.conversation, OLD.seq, revision, OLD.body
        FROM checkpoint_revisions WHERE conversation = OLD.conversation;
END;

CREATE TRIGGER checkpoint_message_deleted BEFORE DELETE ON messages
BEGIN
    INSERT OR IGNORE INTO checkpoint_message_versions
        SELECT OLD.conversation, OLD.seq, revision, OLD.body
        FROM checkpoint_revisions WHERE conversation = OLD.conversation;
END;

PRAGMA user_version = 3;
