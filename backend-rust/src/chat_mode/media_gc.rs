//! Called under the shared Chat storage lock after an upload or conversation deletion.
use super::store::ChatPaths;
use crate::knowledge::storage::{open_canonical_dir, publish, read_bytes};
use cap_fs_ext::DirExt;
use cap_std::fs::Dir;
use serde_json::Value;
use std::{
    collections::HashSet,
    io::{self, ErrorKind},
};

const LIMIT: u64 = 100 * 1024 * 1024;

fn json(dir: &Dir, name: &str) -> io::Result<Value> {
    Ok(serde_json::from_slice(&read_bytes(dir, name, LIMIT)?)?)
}

fn optional_dir(parent: &Dir, name: &str) -> io::Result<Option<Dir>> {
    match parent.open_dir_nofollow(name) {
        Ok(dir) => Ok(Some(dir)),
        Err(err) if err.kind() == ErrorKind::NotFound => Ok(None),
        Err(err) => Err(err),
    }
}

fn references(value: &Value, live: &mut HashSet<String>) {
    match value {
        Value::Object(object) => {
            if let Some(id) = value["media_id"].as_str() {
                live.insert(id.to_owned());
            }
            for value in object.values() {
                references(value, live);
            }
        }
        Value::Array(values) => {
            for value in values {
                references(value, live);
            }
        }
        _ => (),
    }
}

fn expired(entry: &Value, now: i64) -> bool {
    let Some(date) = entry["updated_at"].as_str() else {
        return false;
    };
    let time = date.parse::<jiff::Timestamp>().ok().or_else(|| {
        date.parse::<jiff::civil::DateTime>()
            .ok()?
            .to_zoned(jiff::tz::TimeZone::system())
            .ok()
            .map(|date| date.timestamp())
    });
    time.is_some_and(|time| now - time.as_second() >= 86400)
}

pub(super) fn collect_after_write(paths: &ChatPaths) {
    if let Err(err) = collect(paths) {
        eprintln!("Chat media collection deferred: {err}");
    }
}

fn collect(paths: &ChatPaths) -> io::Result<()> {
    let root = open_canonical_dir(paths.root())?;
    let conversations = root.open_dir_nofollow("conversations")?;
    let Some(store) = optional_dir(&conversations, "media_store")? else {
        return Ok(());
    };
    let mut index = json(&store, "index.json")?;
    let entries = index["media"].as_object().ok_or(ErrorKind::InvalidData)?;
    let now = jiff::Timestamp::now().as_second();
    if !entries.values().any(|entry| expired(entry, now)) {
        return Ok(());
    }
    let mut live = HashSet::new();
    let mut documents = HashSet::new();
    let mut pending = HashSet::new();
    paths.visit_media_documents(|doc| {
        let id = doc["id"].as_str().ok_or(ErrorKind::InvalidData)?;
        documents.insert(id.to_owned());
        references(doc, &mut live);
        if let Some(states) = doc["metadata"]["chat"]["handoffs"].as_object() {
            for state in states.values() {
                if !matches!(
                    state["status"].as_str(),
                    Some("completed" | "declined" | "superseded")
                ) {
                    pending.insert((
                        id.to_owned(),
                        state["id"]
                            .as_str()
                            .ok_or(ErrorKind::InvalidData)?
                            .to_owned(),
                    ));
                }
            }
        }
        Ok(())
    })?;
    if let Some(drafts) = optional_dir(&root, "drafts")? {
        for entry in drafts.entries()? {
            let name = entry?.file_name();
            if let Some(name) = name.to_str().filter(|name| name.ends_with(".json")) {
                references(&json(&drafts, name)?, &mut live);
            }
        }
    }
    if let Some(handoffs) = optional_dir(&root, "handoffs")? {
        for entry in handoffs.entries()? {
            let entry = entry?;
            if !entry.file_type()?.is_dir() {
                continue;
            }
            let name = entry.file_name();
            let dir = handoffs.open_dir_nofollow(&name)?;
            let snap = match json(&dir, "snapshot.json") {
                Ok(value) => value,
                Err(err) if err.kind() == ErrorKind::NotFound => continue,
                Err(err) => return Err(err),
            };
            if let (Some(id), Some(name)) = (snap["id"].as_str(), name.to_str()) {
                if pending.contains(&(id.to_owned(), name.to_owned())) {
                    references(&snap, &mut live);
                }
            }
        }
    }
    let mut removed = Vec::new();
    let blobs = store.open_dir_nofollow("blobs")?;
    for (id, entry) in entries {
        if live.contains(id) || !expired(entry, now) {
            continue;
        }
        let sha = id
            .strip_prefix("sha256:")
            .filter(|sha| sha.len() == 64 && sha.bytes().all(|c| c.is_ascii_hexdigit()))
            .ok_or(ErrorKind::InvalidData)?;
        let path = entry["blob_rel_path"]
            .as_str()
            .ok_or(ErrorKind::InvalidData)?;
        let prefix = format!("blobs/{}/{sha}.", &sha[..2]);
        let ext = path
            .strip_prefix(&prefix)
            .filter(|ext| !ext.is_empty() && ext.bytes().all(|c| c.is_ascii_alphanumeric()))
            .ok_or(ErrorKind::InvalidData)?;
        removed.push((id.clone(), sha[..2].to_owned(), format!("{sha}.{ext}")));
    }
    if removed.is_empty() {
        return Ok(());
    }
    let entries = index["media"]
        .as_object_mut()
        .ok_or(ErrorKind::InvalidData)?;
    for (id, bucket, file) in removed {
        if let Some(dir) = optional_dir(&blobs, &bucket)? {
            match dir.remove_file(file) {
                Ok(()) => (),
                Err(err) if err.kind() == ErrorKind::NotFound => (),
                Err(err) => return Err(err),
            }
        }
        entries.remove(&id);
    }
    if let Some(map) = index["message_map"].as_object_mut() {
        map.retain(|id, _| documents.contains(id));
    }
    index["updated_at"] = Value::String(jiff::Timestamp::now().to_string());
    publish(&store, "index.json", &serde_json::to_vec(&index)?, true)
}
