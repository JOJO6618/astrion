use std::{cell::OnceCell, io, path::Path, sync::{Mutex, MutexGuard}};

use base64::{Engine, engine::general_purpose::STANDARD};
use cap_fs_ext::DirExt;
use cap_std::fs::Dir;
use serde_json::{Value, json};

use crate::{
    knowledge::storage::{open_canonical_dir, read_bytes},
    tools::read_file::ReadFile,
};

const MAX_MEDIA_BYTES: u64 = 100 * 1024 * 1024;

static INDEX_LOCK: Mutex<()> = Mutex::new(());

pub(crate) fn index_lock() -> io::Result<MutexGuard<'static, ()>> {
    INDEX_LOCK.lock().map_err(|_| io::Error::other("Conversation media index is unavailable"))
}


pub(crate) struct BlobStore {
    blobs: Dir,
    index: Value,
}

impl BlobStore {
    pub(crate) fn open(data: &Path) -> Option<Self> {
        let root = open_canonical_dir(&data.join("conversations/media_store")).ok()?;
        let blobs = root.open_dir_nofollow("blobs").ok()?;
        let index = read_bytes(&root, "index.json", MAX_MEDIA_BYTES)
            .ok()
            .and_then(|bytes| serde_json::from_slice(&bytes).ok())
            .unwrap_or_default();
        Some(Self { blobs, index })
    }

    /// Return the trusted index entry for a registered media ID.
    pub(crate) fn entry(&self, media_id: &str) -> Option<&Value> {
        self.index["media"].get(media_id).filter(|entry| entry.is_object())
    }

    pub(crate) fn registered_bytes(&self, id: &str) -> Option<Vec<u8>> {
        self.read(&json!({"media_id": id}))
    }

    pub(crate) fn read(&self, reference: &Value) -> Option<Vec<u8>> {
        let entry = reference["media_id"]
            .as_str()
            .and_then(|id| self.index["media"].get(id));
        let indexed = entry.and_then(|e| e["blob_rel_path"].as_str());
        [indexed, reference["store_path"].as_str()]
            .into_iter()
            .flatten()
            .find_map(|path| {
                let path = path.strip_prefix("media_store/").unwrap_or(path);
                let (bucket, name) = path.strip_prefix("blobs/")?.split_once('/')?;
                let (digest, extension) = name.split_once('.')?;
                if digest.len() != 64
                    || !digest.bytes().all(|b| b.is_ascii_hexdigit())
                    || digest.get(..2) != Some(bucket)
                    || extension.is_empty()
                    || !extension.bytes().all(|b| b.is_ascii_alphanumeric())
                {
                    return None;
                }
                let dir = self.blobs.open_dir_nofollow(bucket).ok()?;
                read_bytes(&dir, name, MAX_MEDIA_BYTES)
                    .ok()
                    .filter(|bytes| !bytes.is_empty())
            })
    }
}

fn entries<'a>(msg: &'a Value, key: &str) -> &'a [Value] {
    msg[key]
        .as_array()
        .filter(|v| !v.is_empty())
        .or_else(|| msg["metadata"][key].as_array())
        .map(Vec::as_slice)
        .unwrap_or_default()
}

fn part(kind: &str, mime: &str, bytes: &[u8]) -> Value {
    let key = format!("{kind}_url");
    json!({"type":key, (key.clone()):{"url":format!("data:{mime};base64,{}", STANDARD.encode(bytes))}})
}

pub(crate) fn legacy_mime(path: &str) -> Option<(&'static str, &'static str)> {
    Some(
        match Path::new(path)
            .extension()?
            .to_str()?
            .to_ascii_lowercase()
            .as_str()
        {
            "png" => ("image", "image/png"),
            "jpg" | "jpeg" => ("image", "image/jpeg"),
            "webp" => ("image", "image/webp"),
            "gif" => ("image", "image/gif"),
            "avif" => ("image", "image/avif"),
            "bmp" => ("image", "image/bmp"),
            "mp4" => ("video", "video/mp4"),
            "webm" => ("video", "video/webm"),
            "mov" => ("video", "video/quicktime"),
            _ => return None,
        },
    )
}

pub(crate) fn attach(document: &Value, history: &mut [Value], data: &Path, workspace: &Path, execution_root: &Path) {
    let blobs = OnceCell::new();
    let execution_files = OnceCell::new();
    let workspace_files = OnceCell::new();
    let aliases = &document["metadata"]["conversation_transfer"]["paths"];
    for (source, projected) in document["messages"]
        .as_array()
        .into_iter()
        .flatten()
        .zip(history)
    {
        if !matches!(
            source["role"].as_str(),
            Some("user" | "assistant" | "system")
        ) || source["metadata"]["media_paths_only"] == true
        {
            continue;
        }
        let Some(text) = projected["content"].as_str() else {
            continue;
        };
        let mut parts = Vec::new();
        let mut notes = Vec::new();
        for reference in entries(source, "media_refs") {
            let Some(kind @ ("image" | "video" | "audio")) = reference["kind"].as_str() else {
                continue;
            };
            let Some(bytes) = blobs
                .get_or_init(|| BlobStore::open(data))
                .as_ref()
                .and_then(|store| store.read(reference))
            else {
                continue;
            };
            let mime = reference["mime_type"].as_str().unwrap_or(match kind {
                "image" => "image/png",
                "video" => "video/mp4",
                _ => "audio/*",
            });
            if kind == "audio" {
                let size = reference["size"]
                    .as_u64()
                    .map(|n| format!(", {n} bytes"))
                    .unwrap_or_default();
                notes.push(format!("[已附加音频 {mime}{size}]"));
            } else {
                let mut attachment = part(kind, mime, &bytes);
                if kind == "video" && !reference["fps"].is_null() {
                    attachment["fps"] = reference["fps"].clone();
                }
                parts.push(attachment);
            }
        }
        if parts.is_empty() && notes.is_empty() {
            for item in entries(source, "images")
                .iter()
                .chain(entries(source, "videos"))
            {
                let Some(original) = item.as_str().or_else(|| item["path"].as_str()) else {
                    continue;
                };
                let (path, root, files) = match aliases[original].as_str() {
                    Some(alias)
                        if alias.starts_with(".astrion/conversation_assets/")
                            && !alias.contains('\\')
                            && !alias.split('/').any(|p| p == "..") =>
                    {
                        (alias, workspace, &workspace_files)
                    }
                    Some(_) => continue,
                    None if Path::new(original).starts_with(workspace) => (original, workspace, &workspace_files),
                    None => (original, execution_root, &execution_files),
                };
                let Some((kind, mime)) = legacy_mime(path) else {
                    continue;
                };
                if let Some(bytes) = files
                    .get_or_init(|| ReadFile::open(root, data).ok())
                    .as_ref()
                    .and_then(|files| files.read_bytes(path).ok())
                    .filter(|b| !b.is_empty())
                {
                    let mut attachment = part(kind, mime, &bytes);
                    if kind == "video" && !item["fps"].is_null() {
                        attachment["fps"] = item["fps"].clone();
                    }
                    parts.push(attachment);
                }
            }
        }
        if parts.is_empty() && notes.is_empty() {
            continue;
        }
        let merged = std::iter::once(text)
            .chain(notes.iter().map(String::as_str))
            .filter(|s| !s.is_empty())
            .collect::<Vec<_>>()
            .join("\n");
        projected["content"] = if parts.is_empty() {
            Value::String(merged)
        } else {
            if !merged.is_empty() {
                parts.insert(0, json!({"type":"text", "text":merged}));
            }
            Value::Array(parts)
        };
    }
}


/// Chat has an account blob store but no workspace file authority.
pub(crate) fn attach_chat_blobs(messages: &[Value], projected: &mut [Value], account: &Path) -> Result<(), String> {
    let blobs = OnceCell::new();
    for (source, projected) in messages.iter().zip(projected) {
        let refs = entries(source, "media_refs");
        if refs.is_empty() || source["metadata"]["media_paths_only"] == true { continue; }
        if !matches!(source["role"].as_str(), Some("user" | "assistant")) { continue; }
        let store = blobs.get_or_init(|| BlobStore::open(account)).as_ref()
            .ok_or("Chat media store unavailable")?;
        let mut parts = Vec::new();
        if let Some(text) = projected["content"].as_str().filter(|text| !text.is_empty()) {
            parts.push(json!({"type":"text","text":text}));
        }
        for reference in refs {
            let id = reference["media_id"].as_str().ok_or("Invalid Chat media reference")?;
            let entry = store.entry(id).ok_or("Chat media unavailable")?;
            let kind = entry["kind"].as_str().filter(|kind| matches!(*kind, "image" | "video"))
                .ok_or("Invalid Chat media type")?;
            let mime = entry["mime_type"].as_str().filter(|mime| {
                mime.strip_prefix(kind).is_some_and(|suffix| suffix.starts_with('/'))
            }).ok_or("Invalid Chat media MIME type")?;
            let bytes = store.registered_bytes(id).ok_or("Chat media unavailable")?;
            let mut media = part(kind, mime, &bytes);
            if kind == "video" && let Some(fps) = reference["fps"].as_f64().filter(|fps| *fps > 0.0) {
                media["fps"] = json!(fps);
            }
            parts.push(media);
        }
        projected["content"] = Value::Array(parts);
    }
    Ok(())
}
