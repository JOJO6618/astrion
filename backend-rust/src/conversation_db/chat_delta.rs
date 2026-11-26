//! Incremental commits for Chat's append-ordered message tree.

use rusqlite::{TransactionBehavior, params};
use serde_json::Value;

use super::{Database, error, sync_header, tool_count, write_message};

pub(crate) enum Messages<'a> {
    Unchanged,
    Append(&'a [Value]),
    Replace { seq: usize, body: &'a Value },
}

impl Database {
    pub(crate) fn commit_chat_delta(
        &mut self,
        id: &str,
        before_count: usize,
        header: &Value,
        summary: &Value,
        messages: Messages<'_>,
    ) -> Result<(), String> {
        let tx = self
            .conn
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(error)?;
        let (count, tools): (usize, usize) = tx
            .query_row(
                "SELECT message_count,tool_count FROM conversations WHERE id=?",
                [id],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .map_err(error)?;
        if count != before_count {
            return Err("Chat message count changed during commit".into());
        }
        let (count, tools) =
            match messages {
                Messages::Unchanged => (count, tools),
                Messages::Append(rows) => {
                    for (offset, row) in rows.iter().enumerate() {
                        write_message(&tx, id, count + offset, row).map_err(error)?;
                    }
                    (
                        count + rows.len(),
                        tools + rows.iter().map(tool_count).sum::<usize>(),
                    )
                }
                Messages::Replace { seq, body } => {
                    let previous: String = tx.query_row(
                    "SELECT body FROM messages WHERE conversation=? AND seq=? AND message_id=?",
                    params![id, seq, body["message_id"].as_str()],
                    |row| row.get(0),
                ).map_err(error)?;
                    let previous: Value = serde_json::from_str(&previous).map_err(error)?;
                    let tools = tools
                        .checked_sub(tool_count(&previous))
                        .ok_or("Invalid Chat tool count")?
                        + tool_count(body);
                    write_message(&tx, id, seq, body).map_err(error)?;
                    (count, tools)
                }
            };
        sync_header(&tx, id, header).map_err(error)?;
        tx.execute(
            "UPDATE conversations SET message_count=?,tool_count=?,summary=? WHERE id=?",
            params![count, tools, summary.to_string(), id],
        )
        .map_err(error)?;
        tx.commit().map_err(error)
    }
}
