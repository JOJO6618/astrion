//! Version checkpoints share unchanged messages with the live conversation.

use rusqlite::{OptionalExtension, TransactionBehavior, params};
use serde::{Deserialize, Serialize};
use serde_json::Value;

use super::{Database, error};

#[derive(Serialize, Deserialize)]
pub(crate) struct MessageRevision {
    pub(crate) workspace: String,
    pub(crate) conversation: String,
    revision: u64,
    count: usize,
}

impl Database {
    pub(crate) fn checkpoint_anchor(
        &self,
        conversation: &str,
        known_id: Option<&str>,
        timestamp: &str,
        message: &str,
    ) -> Result<Option<(usize, Option<String>)>, String> {
        let anchor = |row: &rusqlite::Row<'_>| Ok((row.get(0)?, row.get(1)?));
        if let Some(id) = known_id {
            let row = self.conn.query_row(
                "SELECT seq,message_id FROM messages WHERE conversation=? AND role='user' AND message_id=? ORDER BY seq LIMIT 1",
                params![conversation, id], anchor,
            ).optional().map_err(error)?;
            if row.is_some() {
                return Ok(row);
            }
        }
        self.conn.query_row(
            "SELECT seq,message_id FROM messages WHERE conversation=? AND role='user'
             AND json_extract(body,'$.timestamp')=? AND json_extract(body,'$.content')=? ORDER BY seq LIMIT 1",
            params![conversation, timestamp, message], anchor,
        ).optional().map_err(error)
    }

    pub(crate) fn capture_checkpoint(
        &mut self,
        workspace: &str,
        conversation: &str,
    ) -> Result<MessageRevision, String> {
        let tx = self
            .conn
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(error)?;
        let count = tx
            .query_row(
                "SELECT message_count FROM conversations WHERE id=?",
                [conversation],
                |row| row.get(0),
            )
            .map_err(error)?;
        let revision = tx
            .query_row(
                "INSERT INTO checkpoint_revisions VALUES(?, 1)
             ON CONFLICT(conversation) DO UPDATE SET revision=revision+1 RETURNING revision",
                [conversation],
                |row| row.get(0),
            )
            .map_err(error)?;
        tx.commit().map_err(error)?;
        Ok(MessageRevision {
            workspace: workspace.into(),
            conversation: conversation.into(),
            revision,
            count,
        })
    }

    pub(crate) fn checkpoint_messages(
        &self,
        snapshot: &MessageRevision,
    ) -> Result<Vec<Value>, String> {
        let tx = self.conn.unchecked_transaction().map_err(error)?;
        let latest: Option<u64> = tx
            .query_row(
                "SELECT revision FROM checkpoint_revisions WHERE conversation=?",
                [&snapshot.conversation],
                |row| row.get(0),
            )
            .optional()
            .map_err(error)?;
        if snapshot.revision == 0 || latest.is_none_or(|latest| latest < snapshot.revision) {
            return Err("Checkpoint message revision is unavailable".into());
        }
        let mut messages = Vec::new();
        {
            let mut statement = tx.prepare(
                "WITH preimages AS (
                    SELECT seq, MIN(revision) AS revision FROM checkpoint_message_versions
                    WHERE conversation=?1 AND seq<?2 AND revision>=?3 GROUP BY seq
                )
                SELECT changed.seq, changed.body FROM checkpoint_message_versions AS changed
                JOIN preimages USING(seq,revision) WHERE conversation=?1
                UNION ALL
                SELECT live.seq, live.body FROM messages AS live
                WHERE conversation=?1 AND seq<?2 AND NOT EXISTS (SELECT 1 FROM preimages WHERE preimages.seq=live.seq)
                ORDER BY 1"
            ).map_err(error)?;
            let mut rows = statement
                .query(params![
                    snapshot.conversation,
                    snapshot.count,
                    snapshot.revision
                ])
                .map_err(error)?;
            while let Some(row) = rows.next().map_err(error)? {
                let index: usize = row.get(0).map_err(error)?;
                if index != messages.len() {
                    return Err("Checkpoint message sequence is incomplete".into());
                }
                let body: String = row.get(1).map_err(error)?;
                messages.push(serde_json::from_str(&body).map_err(error)?);
            }
        }
        if messages.len() != snapshot.count {
            return Err("Checkpoint message sequence is incomplete".into());
        }
        tx.commit().map_err(error)?;
        Ok(messages)
    }
}
