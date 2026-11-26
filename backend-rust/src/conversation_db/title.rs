use super::{Database, TransactionBehavior, Value, error, json, load_header, params};
use rusqlite::OptionalExtension;

impl Database {
    /// Publish a title and its catalog row together. A generated title may only
    /// replace the original provisional title while it is still unlocked.
    pub(crate) fn save_title(
        &mut self, id: &str, title: &str, expected_title: Option<&str>,
    ) -> Result<bool, String> {
        let tx = self.conn.transaction_with_behavior(TransactionBehavior::Immediate).map_err(error)?;
        let summary: Option<String> = tx.query_row(
            "SELECT summary FROM conversations WHERE id=?", [id], |row| row.get(0),
        ).optional().map_err(error)?;
        let Some(summary) = summary else { return Ok(false) };
        let mut summary: Value = serde_json::from_str(&summary).map_err(error)?;
        let header = load_header(&tx, id).map_err(error)?;
        let metadata = &header["metadata"];
        if let Some(expected) = expected_title {
            if header["title"] != expected
                || metadata["title_locked"] == true
                || metadata["skip_auto_title_generation"] == true
            {
                return Ok(false);
            }
        }
        let mut write = tx.prepare_cached(
            "INSERT INTO fields VALUES(?,?,?,?) ON CONFLICT(conversation,scope,key) \
             DO UPDATE SET value=excluded.value WHERE fields.value<>excluded.value",
        ).map_err(error)?;
        for (scope, key, value) in [
            ("root", "metadata", json!({})),
            ("root", "title", json!(title)),
            ("metadata", "title", json!(title)),
            ("metadata", "title_locked", json!(true)),
        ] {
            write.execute(params![id, scope, key, value.to_string()]).map_err(error)?;
        }
        drop(write);
        summary["title"] = json!(title);
        tx.execute(
            "UPDATE conversations SET summary=? WHERE id=?", params![summary.to_string(), id],
        ).map_err(error)?;
        tx.commit().map_err(error)?;
        Ok(true)
    }
}
