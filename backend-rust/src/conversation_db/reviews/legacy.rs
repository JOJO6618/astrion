use crate::{conversation_db::migration::Issue, knowledge::storage};
use rusqlite::{Connection, OpenFlags, TransactionBehavior, params};
use serde_json::Value;
use std::{
    collections::HashSet,
    fs, io,
    path::{Path, PathBuf},
};

pub(in crate::conversation_db) struct Source {
    pub kind: &'static str,
    logs: PathBuf,
    folder: &'static str,
    pub name: String,
    id: String,
    timestamp: i64,
}

pub(in crate::conversation_db) fn discover(root: &Path, logs: &Path) -> (Vec<Source>, Vec<Issue>) {
    let mut sources = Vec::new();
    let mut issues = Vec::new();
    for (kind, folder, prefix) in [
        ("auto_approval", "approval_agent", "approval_"),
        ("goal_review", "goal_review_agent", "goal_review_"),
        (
            "workflow_review",
            "workflow_review_agent",
            "workflow_review_",
        ),
    ] {
        discover_folder(logs, kind, folder, prefix, &mut sources, &mut issues);
    }
    if !sources.is_empty() {
        match imported(root) {
            Ok(imported) => sources.retain(|source| !imported.contains(&source.id)),
            Err(error) => {
                issues.push(Issue::new(sources.len(), "审核记录数据库", error));
                sources.clear();
            }
        }
    }
    sources.sort_by(|a, b| a.id.cmp(&b.id));
    issues.sort_by(|a, b| a.message.cmp(&b.message));
    (sources, issues)
}

fn discover_folder(
    logs: &Path,
    kind: &'static str,
    folder: &'static str,
    prefix: &'static str,
    sources: &mut Vec<Source>,
    issues: &mut Vec<Issue>,
) {
    let domain = format!("审核记录 {folder}");
    let directory = logs.join(folder);
    let entries = match fs::symlink_metadata(&directory) {
        Ok(meta) if meta.is_dir() => fs::read_dir(&directory),
        Ok(_) => Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "审核记录路径不是普通目录",
        )),
        Err(error) if error.kind() == io::ErrorKind::NotFound => return,
        Err(error) => Err(error),
    };
    let (entries, trusted_logs) =
        match entries.and_then(|entries| Ok((entries, logs.canonicalize()?))) {
            Ok(result) => result,
            Err(error) => {
                issues.push(Issue::new(1, &domain, error));
                return;
            }
        };
    for entry in entries {
        let entry = match entry {
            Ok(entry) => entry,
            Err(error) => {
                issues.push(Issue::new(1, &domain, error));
                continue;
            }
        };
        let Some(name) = entry.file_name().to_str().map(str::to_owned) else {
            continue;
        };
        let Some(timestamp) = name
            .strip_prefix(prefix)
            .and_then(|value| value.strip_suffix(".json"))
            .filter(|value| !value.is_empty() && value.bytes().all(|byte| byte.is_ascii_digit()))
            .and_then(|value| value.parse::<i64>().ok())
        else {
            continue;
        };
        match entry.file_type() {
            Ok(meta) if meta.is_file() => sources.push(Source {
                kind,
                logs: trusted_logs.clone(),
                folder,
                id: format!("legacy:{kind}:{name}"),
                name,
                timestamp,
            }),
            Ok(_) => issues.push(Issue::new(
                1,
                &format!("{domain}/{name}"),
                "审核记录不是普通文件",
            )),
            Err(error) => issues.push(Issue::new(1, &format!("{domain}/{name}"), error)),
        }
    }
}

fn imported(root: &Path) -> Result<HashSet<String>, String> {
    match fs::symlink_metadata(root.join(super::FILE)) {
        Ok(meta) if meta.is_file() => (),
        Ok(_) => return Err("审核记录数据库不是普通文件".into()),
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(HashSet::new()),
        Err(error) => return Err(super::super::error(error)),
    }
    let db = Connection::open_with_flags(
        root.canonicalize()
            .map_err(super::super::error)?
            .join(super::FILE),
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NOFOLLOW,
    )
    .map_err(super::super::error)?;
    let exists: bool = db
        .query_row(
            "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name='reviews')",
            [],
            |row| row.get(0),
        )
        .map_err(super::super::error)?;
    if !exists {
        return Ok(HashSet::new());
    }
    let mut query = db
        .prepare("SELECT id FROM reviews WHERE id GLOB 'legacy:*'")
        .map_err(super::super::error)?;
    query
        .query_map([], |row| row.get(0))
        .map_err(super::super::error)?
        .collect::<Result<HashSet<_>, _>>()
        .map_err(super::super::error)
}

impl Source {
    pub(in crate::conversation_db) fn import(&self, root: &Path) -> Result<bool, String> {
        let dir = storage::open_canonical_dir(&self.logs.join(self.folder))
            .map_err(super::super::error)?;
        let bytes =
            storage::read_bytes(&dir, &self.name, u64::MAX - 1).map_err(super::super::error)?;
        let doc: Value = serde_json::from_slice(&bytes).map_err(super::super::error)?;
        if !doc.is_object() || !doc["messages"].is_array() || !doc["trace"].is_array() {
            return Err("Invalid historical reviewer transcript".into());
        }
        let mut db = super::open(root).map_err(super::super::error)?;
        let tx = db
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(super::super::error)?;
        let stamp = doc["created_at"].as_i64().unwrap_or(self.timestamp) / 1000;
        let verdict = &doc["final_result"];
        let outcome = verdict["decision"]
            .as_str()
            .or(verdict["status"].as_str())
            .unwrap_or("recorded");
        // Historical dumps did not require conversation identity. Never infer it from transcript text.
        let changed=tx.execute("INSERT INTO reviews(id,kind,conversation,trigger_id,workspace,created_at,finished_at,outcome,result) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING",params![self.id,self.kind,doc["conversation_id"].as_str().unwrap_or(""),self.name,doc["workspace"].as_str().unwrap_or(""),stamp,stamp,outcome,verdict.to_string()]).map_err(super::super::error)?;
        if changed != 0 {
            tx.execute(
                "INSERT INTO review_events(review,body) VALUES(?,?)",
                params![
                    self.id,
                    serde_json::json!({"type":"legacy_transcript","transcript":doc}).to_string()
                ],
            )
            .map_err(super::super::error)?;
        }
        tx.commit().map_err(super::super::error)?;
        Ok(changed != 0)
    }
}
