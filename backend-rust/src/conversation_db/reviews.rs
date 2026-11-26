//! Reviewer decisions have independent lifetimes. They are never fed back as user messages.
use super::{Inner, private_connection};
use rusqlite::params;
use serde_json::Value;
use std::{
    io,
    path::{Path, PathBuf},
};
use uuid::Uuid;
pub(super) mod legacy;
const FILE: &str = "review_history.sqlite3";
fn open(root: &Path) -> Inner<rusqlite::Connection> {
    let db = private_connection(root, FILE)?;
    db.execute_batch("CREATE TABLE IF NOT EXISTS reviews(id TEXT PRIMARY KEY,kind TEXT NOT NULL,conversation TEXT NOT NULL,trigger_id TEXT NOT NULL,workspace TEXT NOT NULL,created_at INTEGER NOT NULL,finished_at INTEGER,outcome TEXT,result TEXT);
                CREATE INDEX IF NOT EXISTS review_conversation ON reviews(conversation,created_at);
                CREATE TABLE IF NOT EXISTS review_events(seq INTEGER PRIMARY KEY,review TEXT NOT NULL REFERENCES reviews(id),body TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS review_events_by_run ON review_events(review,seq);")?;
    Ok(db)
}
pub(crate) struct Record {
    root: PathBuf,
    id: String,
    detail: bool,
}
impl Record {
    pub(crate) fn begin(
        root: &Path,
        kind: &str,
        conversation: &str,
        trigger: &str,
        workspace: &Path,
        detail: bool,
    ) -> io::Result<Self> {
        let record = Self {
            root: root.into(),
            id: Uuid::new_v4().to_string(),
            detail,
        };
        record.write(|db|{
            db.execute("INSERT INTO reviews(id,kind,conversation,trigger_id,workspace,created_at) VALUES(?,?,?,?,?,strftime('%s','now'))",params![record.id,kind,conversation,trigger,workspace.to_string_lossy()])?;Ok(())
        })?;
        Ok(record)
    }
    pub(crate) fn event(&self, data: &Value) -> io::Result<()> {
        if !self.detail {
            return Ok(());
        }
        self.write(|db| {
            db.execute(
                "INSERT INTO review_events(review,body) VALUES(?,?)",
                params![self.id, data.to_string()],
            )?;
            Ok(())
        })
    }
    /// Record only the newly produced messages for a model round.
    pub(crate) fn messages(&self, messages: &[Value], round: usize) -> io::Result<()> {
        if messages.is_empty() {
            return Ok(());
        }
        self.event(&serde_json::json!({"type":"messages","round":round,"messages":messages}))
    }
    pub(crate) fn finish(&self, outcome: &str, data: &Value) -> io::Result<()> {
        self.write(|db|{db.execute("UPDATE reviews SET finished_at=strftime('%s','now'),outcome=?,result=? WHERE id=? AND finished_at IS NULL",
            params![outcome,data.to_string(),self.id])?;Ok(())})
    }
    fn write(&self, op: impl FnOnce(&rusqlite::Connection) -> Inner<()>) -> io::Result<()> {
        let result = (|| -> Inner<()> {
            let db = open(&self.root)?;
            op(&db)
        })();
        result.map_err(|e| io::Error::other(e.to_string()))
    }
}
