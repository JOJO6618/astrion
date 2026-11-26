use super::registry::sql;
#[cfg(test)]
#[path = "../../../test/2026-11-27_子智能体增量存储/context_rows.rs"]
mod tests;
use rusqlite::{Connection, OptionalExtension, Transaction, params};
use serde_json::{Value, json};
use std::io::{self, ErrorKind};

/// A NULL count is an inline context written by an earlier build. Read it
/// without migration and split it on the next successful writer transaction.
pub(super) fn read(tx: &Transaction<'_>, id: &str) -> io::Result<Option<Value>> {
    let version: u32 = tx
        .query_row("PRAGMA user_version", [], |r| r.get(0))
        .map_err(sql)?;
    let query = if version == 1 {
        "SELECT context,NULL FROM child_task WHERE id=?"
    } else {
        "SELECT context,message_count FROM child_task WHERE id=?"
    };
    let row = tx
        .query_row(query, [id], |r| {
            Ok((
                r.get::<_, Option<String>>(0)?,
                r.get::<_, Option<usize>>(1)?,
            ))
        })
        .optional()
        .map_err(sql)?;
    let Some((Some(header), count)) = row else {
        return Ok(None);
    };
    let mut doc: Value = serde_json::from_str(&header)?;
    if let Some(count) = count {
        let mut messages = Vec::new();
        let mut statement = tx
            .prepare("SELECT seq,body FROM child_message WHERE task_id=? ORDER BY seq")
            .map_err(sql)?;
        let mut rows = statement.query([id]).map_err(sql)?;
        while let Some(row) = rows.next().map_err(sql)? {
            if row.get::<_, usize>(0).map_err(sql)? != messages.len() {
                return Err(ErrorKind::InvalidData.into());
            }
            messages.push(serde_json::from_str(
                &row.get::<_, String>(1).map_err(sql)?,
            )?);
        }
        if messages.len() != count {
            return Err(ErrorKind::InvalidData.into());
        }
        doc["messages"] = Value::Array(messages);
    }
    Ok(Some(doc))
}

/// The runtime owns an append-only message sequence. Compare its cursor in the
/// same transaction as the suffix and header so a stale worker cannot overwrite it.
pub(super) fn write(
    db: &Connection,
    id: &str,
    doc: &Value,
    expected: usize,
    rewrite: bool,
) -> io::Result<()> {
    let source = doc.as_object().ok_or(ErrorKind::InvalidData)?;
    let Some(messages) = doc["messages"].as_array() else {
        // Legacy documents without an array must survive explicit import as-is.
        db.execute(
            "UPDATE child_task SET context=? WHERE id=?",
            params![doc.to_string(), id],
        )
        .map_err(sql)?;
        return Ok(());
    };
    let (count, previous): (Option<usize>, Option<usize>) = db.query_row(
        "SELECT message_count,CASE WHEN message_count IS NULL THEN json_array_length(context,'$.messages') END FROM child_task WHERE id=?",
        [id], |r| Ok((r.get(0)?, r.get(1)?))).map_err(sql)?;
    if count.or(previous).unwrap_or(0) != expected || (!rewrite && expected > messages.len()) {
        return Err(io::Error::other("子智能体消息游标已变更"));
    }
    let start = if rewrite {
        db.execute("DELETE FROM child_message WHERE task_id=?", [id])
            .map_err(sql)?;
        0
    } else {
        count.unwrap_or(0)
    };
    let mut insert = db
        .prepare_cached("INSERT INTO child_message(task_id,seq,body) VALUES(?,?,?)")
        .map_err(sql)?;
    for (seq, value) in messages.iter().enumerate().skip(start) {
        insert
            .execute(params![id, seq, value.to_string()])
            .map_err(sql)?;
    }
    let mut header = serde_json::Map::new();
    for (key, value) in source {
        if key != "messages" {
            header.insert(key.clone(), value.clone());
        }
    }
    header.insert("messages".into(), json!([]));
    db.execute(
        "UPDATE child_task SET context=?, message_count=? WHERE id=?",
        params![Value::Object(header).to_string(), messages.len(), id],
    )
    .map_err(sql)?;
    Ok(())
}
