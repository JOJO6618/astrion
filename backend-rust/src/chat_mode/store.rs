use std::{
    fs::{self, DirBuilder, File, OpenOptions},
    io::{self, Write},
    path::{Path, PathBuf},
};

#[cfg(unix)]
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt};

use serde_json::{Value, json};
use time::OffsetDateTime;
use uuid::Uuid;

use super::{ChatError, ChatMode};
use crate::{
    conversation_db::Database,
    knowledge::storage::{open_canonical_dir, read_bytes},
    legacy::LegacyDocument,
};

const LEGACY_DOCUMENT_LIMIT: u64 = 128 * 1024 * 1024;

impl ChatMode {
    /// Runs one bounded Chat storage operation while holding the Host Store
    /// mutex. Never call this from inside another `with_chat_storage` closure.
    pub(super) fn storage<T>(
        &self,
        action: impl FnOnce(&ChatPaths) -> Result<T, ChatError>,
    ) -> Result<T, ChatError> {
        self.runtime
            .with_chat_storage(|root| action(&ChatPaths::new(root)))
    }
}

/// Inert path helper. All calls run inside `Runtime::with_chat_storage`, which
/// holds the Host Store mutex and therefore the single-writer gate.
pub(super) struct ChatPaths {
    root: PathBuf,
}

impl ChatPaths {
    pub(super) fn new(root: &Path) -> Self {
        Self {
            root: root.to_path_buf(),
        }
    }

    pub(super) fn root(&self) -> &Path {
        &self.root
    }

    pub(super) fn conversations(&self) -> PathBuf {
        self.root.join("conversations")
    }

    fn database(&self) -> Result<Database, ChatError> {
        let directory = self.conversations();
        create_private_dir(&directory)?;
        Database::open(&directory).map_err(ChatError::storage)
    }

    fn legacy_file_exists(&self, id: &str) -> Result<bool, ChatError> {
        regular_file_exists(&self.conversations().join(format!("{id}.json")))
    }

    fn missing(&self, database: &Database, id: &str) -> Result<ChatError, ChatError> {
        if !database.imported(id).map_err(ChatError::storage)? && self.legacy_file_exists(id)? {
            let mut error = ChatError::conflict("不兼容的对话记录");
            error.code = Some("conversation_migration_required");
            return Ok(error);
        }
        Ok(ChatError::not_found("Chat conversation unavailable"))
    }

    pub(super) fn read_raw(&self, id: &str) -> Result<Value, ChatError> {
        if !valid_conversation_id(id) {
            return Err(ChatError::bad_request("Invalid Chat conversation ID"));
        }
        let database = self.database()?;
        let raw = match database.read(id).map_err(ChatError::storage)? {
            Some(raw) => raw,
            None => return Err(self.missing(&database, id)?),
        };
        validate_document(&raw, id)?;
        Ok(raw)
    }

    pub(super) fn read_document(&self, id: &str) -> Result<LegacyDocument, ChatError> {
        LegacyDocument::from_value(self.read_raw(id)?).map_err(ChatError::from)
    }

    pub(super) fn write_raw(&self, id: &str, raw: Value, create: bool) -> Result<(), ChatError> {
        if !valid_conversation_id(id) {
            return Err(ChatError::bad_request("Invalid Chat conversation ID"));
        }
        validate_document(&raw, id)?;
        let mut database = self.database()?;
        let exists = database.contains(id).map_err(ChatError::storage)?;
        if create
            && (exists
                || database.imported(id).map_err(ChatError::storage)?
                || self.legacy_file_exists(id)?)
        {
            return Err(ChatError::conflict("Chat conversation already exists"));
        }
        if !create && !exists {
            return Err(self.missing(&database, id)?);
        }
        database
            .save(&raw, &index_row(&raw))
            .map_err(ChatError::storage)
    }

    pub(super) fn create(&self, id: &str, raw: Value) -> Result<(), ChatError> {
        self.write_raw(id, raw, true)
    }

    pub(super) fn delete(&self, id: &str) -> Result<bool, ChatError> {
        if !valid_conversation_id(id) {
            return Err(ChatError::bad_request("Invalid Chat conversation ID"));
        }
        let mut database = self.database()?;
        if !database.contains(id).map_err(ChatError::storage)? {
            let error = self.missing(&database, id)?;
            if error.status != 404 {
                return Err(error);
            }
            return Ok(false);
        }
        database.delete(id).map_err(ChatError::storage)
    }

    pub(super) fn list_ids(&self) -> Result<Vec<String>, ChatError> {
        let mut ids = self
            .database()?
            .rows()
            .map_err(ChatError::storage)?
            .into_iter()
            .filter_map(|row| row["id"].as_str().map(str::to_owned))
            .collect::<Vec<_>>();
        ids.sort();
        Ok(ids)
    }

    // Media may still belong to a record waiting for migration. Once an ID
    // has been imported, its archival source cannot keep deleted media alive.
    pub(super) fn visit_media_documents(
        &self,
        mut visit: impl FnMut(&Value) -> io::Result<()>,
    ) -> io::Result<()> {
        let database = self.database().map_err(io::Error::other)?;
        for row in database.rows().map_err(io::Error::other)? {
            let id = row["id"].as_str().ok_or(io::ErrorKind::InvalidData)?;
            let document = database
                .read(id)
                .map_err(io::Error::other)?
                .ok_or(io::ErrorKind::InvalidData)?;
            visit(&document)?;
        }
        let directory = open_canonical_dir(&self.conversations())?;
        for entry in directory.entries()? {
            let entry = entry?;
            let name = entry.file_name();
            let Some(id) = legacy_id(&name) else {
                continue;
            };
            if database.imported(id).map_err(io::Error::other)?
                || database.contains(id).map_err(io::Error::other)?
            {
                continue;
            }
            let document: Value = serde_json::from_slice(&read_bytes(
                &directory,
                name.to_str().ok_or(io::ErrorKind::InvalidData)?,
                LEGACY_DOCUMENT_LIMIT,
            )?)?;
            if document["id"] != id {
                return Err(io::ErrorKind::InvalidData.into());
            }
            visit(&document)?;
        }
        Ok(())
    }

    pub(super) fn list_rows(&self) -> Result<Vec<Value>, ChatError> {
        let database = self.database()?;
        let mut rows = database.rows().map_err(ChatError::storage)?;
        let directory = open_canonical_dir(&self.conversations())?;
        let index = read_index(&directory)?;
        for entry in directory.entries()? {
            let entry = entry?;
            let name = entry.file_name();
            let Some(id) = legacy_id(&name) else {
                continue;
            };
            if !entry.file_type()?.is_file()
                || database.contains(id).map_err(ChatError::storage)?
                || database.imported(id).map_err(ChatError::storage)?
            {
                continue;
            }
            rows.push(json!({
                "id": id,
                "title": index[id]["title"].as_str().unwrap_or(id),
                "created_at": index[id]["created_at"],
                "updated_at": index[id]["updated_at"],
                "incompatible": true,
            }));
        }
        rows.sort_by(|left, right| {
            let updated = |row: &Value| {
                row["updated_at"]
                    .as_str()
                    .and_then(parse_timestamp)
                    .unwrap_or_default()
            };
            updated(right)
                .total_cmp(&updated(left))
                .then_with(|| left["id"].as_str().cmp(&right["id"].as_str()))
        });
        Ok(rows
            .into_iter()
            .map(|row| {
                let mut public = json!({
                    "id": row["id"],
                    "title": row["title"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                });
                if row["incompatible"] == true {
                    public["incompatible"] = json!(true);
                }
                public
            })
            .collect())
    }

    pub(super) fn search(&self, query: &str, limit: usize) -> Result<Vec<Value>, ChatError> {
        let needle = query.trim().to_lowercase();
        let mut results = Vec::new();
        if needle.is_empty() {
            return Ok(results);
        }
        for id in self.list_ids()? {
            let document = match self.read_document(&id) {
                Ok(document) => document,
                Err(error) if matches!(error.status, 400 | 404) => continue,
                Err(error) => return Err(error),
            };
            let title = document.raw()["title"].as_str().unwrap_or_default();
            let mut hits = Vec::new();
            for message in document.chat_path(None)? {
                if message["metadata"]["visibility"] == "hidden" {
                    continue;
                }
                let text = content_text(&message["content"]);
                if text.to_lowercase().contains(&needle) {
                    hits.push(json!({
                        "message_id": message["message_id"],
                        "text": text,
                    }));
                }
            }
            if hits.is_empty() && !title.to_lowercase().contains(&needle) {
                continue;
            }
            results.push(json!({"id": id, "title": title, "matches": hits}));
            if results.len() >= limit.clamp(1, 100) {
                break;
            }
        }
        Ok(results)
    }

    pub(super) fn set_title(&self, id: &str, title: &str) -> Result<(), ChatError> {
        let mut raw = self.read_raw(id)?;
        raw["title"] = json!(title);
        raw["metadata"]["title"] = json!(title);
        self.write_raw(id, raw, false)
    }

    pub(super) fn next_conversation_id(&self, timestamp: f64) -> Result<String, ChatError> {
        let database = self.database()?;
        let mut milliseconds = (timestamp * 1000.0).round() as i64;
        for _ in 0..4096 {
            let date = OffsetDateTime::from_unix_timestamp(milliseconds / 1000)
                .map_err(|error| ChatError::storage(error))?;
            let id = format!(
                "conv_{:04}{:02}{:02}_{:02}{:02}{:02}_{:03}",
                date.year(),
                date.month() as u8,
                date.day(),
                date.hour(),
                date.minute(),
                date.second(),
                milliseconds.rem_euclid(1000)
            );
            if !database.contains(&id).map_err(ChatError::storage)?
                && !database.imported(&id).map_err(ChatError::storage)?
                && !self.legacy_file_exists(&id)?
            {
                return Ok(id);
            }
            milliseconds += 1;
        }
        Err(ChatError::storage(
            "Could not allocate a unique Chat conversation ID",
        ))
    }

    pub(super) fn initial_document(
        &self,
        id: &str,
        timestamp: f64,
        payload: &Value,
    ) -> Result<Value, ChatError> {
        let time = iso_time(timestamp)?;
        let thinking = payload["thinking_mode"].as_bool().unwrap_or(false);
        let network = payload["settings"]["network_enabled"]
            .as_bool()
            .unwrap_or(true);
        let chatgpt_fast_mode = payload["settings"]["chatgpt_fast_mode"]
            .as_bool()
            .unwrap_or(false);
        let reasoning_effort = payload["settings"]["reasoning_effort"].clone();
        Ok(json!({
            "id": id,
            "title": "新对话",
            "created_at": time,
            "updated_at": time,
            "messages": [],
            "todo_list": null,
            "metadata": {
                "conversation_kind": "chat",
                "chat": {
                    "version": 1,
                    "revision": 0,
                    "latest_leaf_id": null,
                    "active_turn_id": null,
                    "turns": [],
                    "commands": {},
                    "summaries": [],
                    "handoffs": {},
                    "handoff_count": 0,
                    "creation_operation_id": payload["operation_id"],
                },
                "project_path": null,
                "project_relative_path": null,
                "thinking_mode": thinking,
                "run_mode": if thinking { "thinking" } else { "fast" },
                "reasoning_effort": reasoning_effort,
                "chatgpt_fast_mode": chatgpt_fast_mode,
                "model_key": payload["model_key"].as_str(),
                "network_enabled": network,
                "network_revision": 0,
                "pending_network_notices": [],
                "quick_entry": false,
                "permission_mode": "unrestricted",
                "execution_mode": "sandbox",
                "workspace_memory_enabled": false,
                "has_images": false,
                "has_videos": false,
                "total_messages": 0,
                "total_tools": 0,
                "status": "active",
                "compression_count": 0,
                "deep_compression_records": [],
                "frozen_system_prompt": "",
            },
            "token_statistics": {},
        }))
    }
}

pub(super) fn public_document(document: LegacyDocument) -> Result<Value, ChatError> {
    let messages = document.public_chat_path(None)?;
    let mut raw = document.into_raw();
    raw["messages"] = json!(messages);
    super::handoff::public_document(&mut raw);
    Ok(raw)
}

pub(super) fn validate_document(raw: &Value, id: &str) -> Result<(), ChatError> {
    if !raw.is_object() {
        return Err(ChatError::bad_request(
            "Expected a Chat conversation JSON object",
        ));
    }
    if raw["id"].as_str() != Some(id) {
        return Err(ChatError::storage(
            "Chat conversation file ID does not match its path",
        ));
    }
    if raw["metadata"]["conversation_kind"].as_str() != Some("chat") {
        return Err(ChatError::bad_request(
            "Conversation is not a Chat document",
        ));
    }
    if raw["metadata"]["chat"]["version"].as_u64() != Some(1) {
        return Err(ChatError::bad_request("Unsupported Chat tree version"));
    }
    if !raw["messages"].is_array() {
        return Err(ChatError::bad_request("Chat messages must be an array"));
    }
    // Full tree validation happens when LegacyDocument is constructed.
    LegacyDocument::from_value(raw.clone()).map_err(ChatError::from)?;
    Ok(())
}

pub(super) fn content_text(content: &Value) -> String {
    match content {
        Value::String(text) => text.clone(),
        Value::Array(parts) => parts
            .iter()
            .filter_map(|part| part["text"].as_str())
            .collect::<Vec<_>>()
            .join("\n"),
        _ => String::new(),
    }
}

pub(super) fn timestamp(seconds: f64) -> Result<String, ChatError> {
    iso_time(seconds)
}

fn iso_time(seconds: f64) -> Result<String, ChatError> {
    if !seconds.is_finite() {
        return Err(ChatError::bad_request(
            "Expected a finite conversation timestamp",
        ));
    }
    OffsetDateTime::from_unix_timestamp_nanos((seconds * 1_000_000_000.0).round() as i128)
        .map_err(|error| ChatError::storage(error))
        .and_then(|value| {
            value
                .format(&time::format_description::well_known::Rfc3339)
                .map_err(|error| ChatError::storage(error))
        })
}

fn parse_timestamp(value: &str) -> Option<f64> {
    use time::PrimitiveDateTime;
    use time::format_description::well_known::{Iso8601, Rfc3339};
    OffsetDateTime::parse(value, &Rfc3339)
        .ok()
        .or_else(|| {
            PrimitiveDateTime::parse(value, &Iso8601::PARSING)
                .ok()
                .map(PrimitiveDateTime::assume_utc)
        })
        .map(|value| value.unix_timestamp_nanos() as f64 / 1_000_000_000.0)
}

pub(super) fn valid_conversation_id(id: &str) -> bool {
    id.strip_prefix("conv_").is_some_and(|rest| {
        !rest.is_empty()
            && id.len() <= 185
            && id
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-'))
    })
}

pub(super) fn index_row(document: &Value) -> Value {
    let mut row = json!({"id": document["id"]});
    for key in ["title", "created_at", "updated_at"] {
        row[key] = document[key].clone();
    }
    let meta = &document["metadata"];
    for key in [
        "model_key",
        "custom_prompt_name",
        "personalization_name",
        "reasoning_effort",
        "chatgpt_fast_mode",
        "network_enabled",
        "chat",
    ] {
        row[key] = meta[key].clone();
    }
    row["project_path"] = Value::Null;
    row["project_relative_path"] = Value::Null;
    row["conversation_kind"] = json!("chat");
    for (key, default) in [
        ("thinking_mode", json!(false)),
        (
            "run_mode",
            json!(if meta["thinking_mode"] == true {
                "thinking"
            } else {
                "fast"
            }),
        ),
        ("has_images", json!(false)),
        ("has_videos", json!(false)),
        ("total_messages", json!(0)),
        ("total_tools", json!(0)),
        ("status", json!("active")),
        ("quick_entry", json!(false)),
    ] {
        row[key] = meta
            .get(key)
            .filter(|value| !value.is_null())
            .cloned()
            .unwrap_or(default);
    }
    row
}

pub(super) fn read_json(path: &Path) -> Result<Value, ChatError> {
    match fs::read(path) {
        Ok(bytes) => Ok(serde_json::from_slice(&bytes)?),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(Value::Null),
        Err(error) => Err(error.into()),
    }
}

fn legacy_id(name: &std::ffi::OsStr) -> Option<&str> {
    name.to_str()?
        .strip_suffix(".json")
        .filter(|id| valid_conversation_id(id))
}

fn read_index(directory: &cap_std::fs::Dir) -> Result<Value, ChatError> {
    match read_bytes(directory, "index.json", LEGACY_DOCUMENT_LIMIT) {
        Ok(bytes) => Ok(serde_json::from_slice(&bytes).unwrap_or_default()),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(Value::Null),
        Err(error) => return Err(error.into()),
    }
}

pub(super) fn write_json(path: &Path, value: &impl serde::Serialize) -> Result<(), ChatError> {
    let directory = path
        .parent()
        .ok_or_else(|| ChatError::storage("Chat file has no parent"))?;
    let temp = directory.join(format!(".astrion-{}.tmp", Uuid::new_v4()));
    let result = (|| -> io::Result<()> {
        let mut file = private_file_options()
            .write(true)
            .create_new(true)
            .open(&temp)?;
        {
            let mut writer = io::BufWriter::new(&mut file);
            serde_json::to_writer(&mut writer, value)?;
            writer.write_all(b"\n")?;
            writer.flush()?;
        }
        file.sync_all()?;
        drop(file);
        replace_file(&temp, path)?;
        #[cfg(unix)]
        File::open(directory)?.sync_all()?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(temp);
    }
    result.map_err(ChatError::from)
}

fn replace_file(from: &Path, to: &Path) -> io::Result<()> {
    #[cfg(windows)]
    for delay in [10, 20, 40, 80] {
        match fs::rename(from, to) {
            Err(error) if matches!(error.raw_os_error(), Some(5 | 32 | 33)) => {
                std::thread::sleep(std::time::Duration::from_millis(delay));
            }
            result => return result,
        }
    }
    fs::rename(from, to)
}

fn create_private_dir(path: &Path) -> Result<(), ChatError> {
    let builder = &mut DirBuilder::new();
    #[cfg(unix)]
    builder.mode(0o700);
    if let Err(error) = builder.create(path)
        && error.kind() != io::ErrorKind::AlreadyExists
    {
        return Err(error.into());
    }
    let meta = fs::symlink_metadata(path)?;
    if !meta.is_dir() {
        return Err(ChatError::storage(
            "Expected a regular Chat conversation directory",
        ));
    }
    Ok(())
}

fn private_file_options() -> OpenOptions {
    let options = OpenOptions::new();
    #[cfg(unix)]
    let options = {
        let mut options = options;
        options.mode(0o600);
        options
    };
    options
}

fn regular_file_exists(path: &Path) -> Result<bool, ChatError> {
    match fs::symlink_metadata(path) {
        Ok(meta) if meta.is_file() => Ok(true),
        Ok(_) => Err(ChatError::storage(
            "Expected a regular Chat conversation file",
        )),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(false),
        Err(error) => Err(error.into()),
    }
}
