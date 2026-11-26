//! `providers.json` storage: schema migration, atomic writes, and connection IDs.
//!
//! The file is the single authority for API-key connections. It is a Host data
//! file (`<DATA_DIR>/providers.json`, mode 0600) and is never placed in git.
//! Records keep unknown fields so a newer Python/JS writer is not damaged by a
//! Rust round-trip.

use std::{
    collections::{HashMap, HashSet},
    fs::{self, OpenOptions},
    io::{self, Write},
    path::{Path, PathBuf},
    sync::{Mutex, MutexGuard, PoisonError},
};

use serde_json::{Map, Value, json};
use uuid::Uuid;

pub(super) const VERSION: i64 = 2;
const FILE: &str = "providers.json";

/// Serializes every read-modify-write in this process. The Host data root is
/// owned by one server instance, so no cross-process lock is taken (matching
/// the Python manager).
pub(super) struct Store {
    path: PathBuf,
    lock: Mutex<()>,
}

pub(super) struct Data {
    root: Map<String, Value>,
}

impl Data {
    pub(super) fn records(&self) -> &Map<String, Value> {
        self.root
            .get("providers")
            .and_then(Value::as_object)
            .expect("providers is normalized to an object")
    }

    pub(super) fn records_mut(&mut self) -> &mut Map<String, Value> {
        self.root
            .get_mut("providers")
            .and_then(Value::as_object_mut)
            .expect("providers is normalized to an object")
    }

    pub(super) fn record(&self, provider_id: &str) -> Option<&Value> {
        self.records().get(provider_id)
    }
}

impl Store {
    pub(super) fn new(data_dir: &Path) -> Self {
        Self {
            path: data_dir.join(FILE),
            lock: Mutex::new(()),
        }
    }

    pub(super) fn load(&self, catalog_names: &HashMap<String, String>) -> Data {
        let _guard = self.guard();
        self.load_unlocked(catalog_names)
    }

    pub(super) fn snapshot(&self, catalog_names: &HashMap<String, String>) -> Data {
        let mut data = self.read();
        ensure_schema(&mut data, catalog_names);
        data
    }

    /// Reload, apply `change`, and persist only when it succeeds. A business
    /// error leaves the file untouched.
    pub(super) fn transact<R>(
        &self,
        catalog_names: &HashMap<String, String>,
        change: impl FnOnce(&mut Data) -> Result<R, String>,
    ) -> io::Result<Result<R, String>> {
        let _guard = self.guard();
        let mut data = self.load_unlocked(catalog_names);
        match change(&mut data) {
            Ok(value) => {
                self.write(&data)?;
                Ok(Ok(value))
            }
            Err(error) => Ok(Err(error)),
        }
    }

    fn guard(&self) -> MutexGuard<'_, ()> {
        self.lock.lock().unwrap_or_else(PoisonError::into_inner)
    }

    fn load_unlocked(&self, catalog_names: &HashMap<String, String>) -> Data {
        let mut data = self.read();
        if ensure_schema(&mut data, catalog_names) {
            // A read-only data root (sandboxed test) must still answer queries.
            // A later write reports the real failure.
            let _ = self.write(&data);
        }
        data
    }

    fn read(&self) -> Data {
        let mut root = match fs::read(&self.path) {
            Ok(bytes) => match serde_json::from_slice::<Value>(&bytes) {
                Ok(Value::Object(root)) => root,
                _ => Map::new(),
            },
            Err(_) => Map::new(),
        };
        if !root.get("providers").is_some_and(Value::is_object) {
            root.insert("providers".into(), Value::Object(Map::new()));
        }
        Data { root }
    }

    fn write(&self, data: &Data) -> io::Result<()> {
        let directory = self.path.parent().unwrap_or(Path::new("."));
        fs::create_dir_all(directory)?;
        let temporary = directory.join(format!(".providers-{}.tmp", Uuid::new_v4()));
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut file = options.open(&temporary)?;
        let result = (|| {
            let mut writer = io::BufWriter::new(&mut file);
            serde_json::to_writer_pretty(&mut writer, &data.root)?;
            writer.flush()?;
            drop(writer);
            file.sync_all()?;
            replace_file(&temporary, &self.path)
        })();
        if result.is_err() {
            let _ = fs::remove_file(&temporary);
        }
        result
    }
}

pub(super) fn replace_file(from: &Path, to: &Path) -> io::Result<()> {
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

/// Python v1 (single connection) to v2 (multi connection) migration.
///
/// Old keys stay as internal connection IDs and old model references such as
/// `deepseek/deepseek-chat` keep resolving. The migration is idempotent.
fn ensure_schema(data: &mut Data, catalog_names: &HashMap<String, String>) -> bool {
    let mut changed = false;
    let mut groups: HashMap<String, Vec<String>> = HashMap::new();
    for (provider_id, record) in data.records() {
        let Some(record) = record.as_object() else {
            continue;
        };
        let catalog_id = text(&record.get("catalog_id").cloned().unwrap_or(Value::Null));
        let group = if catalog_id.is_empty() {
            format!("__custom__:{provider_id}")
        } else {
            catalog_id
        };
        groups.entry(group).or_default().push(provider_id.clone());
    }
    for ids in groups.values() {
        let mut used = HashSet::new();
        let mut missing = Vec::new();
        for provider_id in ids {
            let record = &data.records()[provider_id];
            match record.get("display_index").and_then(Value::as_i64) {
                Some(index) => {
                    used.insert(index);
                }
                None => missing.push(provider_id.clone()),
            }
        }
        if missing.is_empty() {
            continue;
        }
        // Old records are ordered by connection time so migration is stable.
        missing.sort_by_key(|provider_id| {
            (
                data.records()[provider_id]
                    .get("connected_at")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_owned(),
                provider_id.clone(),
            )
        });
        let mut next = 0;
        for provider_id in missing {
            while used.contains(&next) {
                next += 1;
            }
            if let Some(record) = data
                .records_mut()
                .get_mut(&provider_id)
                .and_then(Value::as_object_mut)
            {
                record.insert("display_index".into(), json!(next));
            }
            used.insert(next);
            next += 1;
            changed = true;
        }
    }
    for (provider_id, record) in data.records_mut() {
        let Some(record) = record.as_object_mut() else {
            continue;
        };
        let catalog_id = text(&record.get("catalog_id").cloned().unwrap_or(Value::Null));
        if !record.contains_key("requested_id") {
            let requested = if catalog_id.is_empty() {
                provider_id.clone()
            } else {
                catalog_id.clone()
            };
            record.insert("requested_id".into(), Value::String(requested));
            changed = true;
        }
        if !record.contains_key("base_name") {
            let base = catalog_names
                .get(&catalog_id)
                .filter(|name| !name.is_empty())
                .cloned()
                .or_else(|| {
                    record
                        .get("name")
                        .and_then(Value::as_str)
                        .filter(|name| !name.is_empty())
                        .map(str::to_owned)
                })
                .unwrap_or_else(|| provider_id.clone());
            record.insert("base_name".into(), Value::String(base));
            changed = true;
        }
        let base = record
            .get("base_name")
            .and_then(Value::as_str)
            .filter(|name| !name.is_empty())
            .unwrap_or(provider_id)
            .to_owned();
        let index = record
            .get("display_index")
            .and_then(Value::as_i64)
            .unwrap_or_default();
        let expected = display_name(&base, index);
        if record.get("name").and_then(Value::as_str) != Some(expected.as_str()) {
            record.insert("name".into(), Value::String(expected));
            changed = true;
        }
    }
    let version = data
        .root
        .get("version")
        .and_then(Value::as_i64)
        .unwrap_or(1);
    if version < VERSION {
        data.root.insert("version".into(), json!(VERSION));
        changed = true;
    }
    changed
}

pub(super) fn text(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        Value::Null => String::new(),
        Value::Bool(value) => value.to_string(),
        Value::Number(value) => value.to_string(),
        _ => String::new(),
    }
}

pub(super) fn trimmed(value: &Value) -> String {
    text(value).trim().to_owned()
}

pub(super) fn display_name(base: &str, index: i64) -> String {
    if index <= 0 {
        base.to_owned()
    } else {
        format!("{base}-{index}")
    }
}

/// New connections take the smallest free display index in their catalog group.
/// Deleting a connection never renumbers the survivors.
pub(super) fn allocate_display_index(records: &Map<String, Value>, catalog_id: &str) -> i64 {
    let used: HashSet<i64> = records
        .values()
        .filter(|record| {
            record
                .get("catalog_id")
                .and_then(Value::as_str)
                .is_some_and(|value| value == catalog_id)
        })
        .filter_map(|record| record.get("display_index").and_then(Value::as_i64))
        .collect();
    let mut index = 0;
    while used.contains(&index) {
        index += 1;
    }
    index
}

/// Internal connection IDs are random and never reused, so a deleted model key
/// cannot silently resolve to a newer credential.
pub(super) fn new_connection_id(records: &Map<String, Value>, catalog_id: &str) -> Option<String> {
    for _ in 0..32 {
        let candidate = format!("{catalog_id}-{}", short_id());
        if !records.contains_key(&candidate) {
            return Some(candidate);
        }
    }
    None
}

pub(super) fn new_custom_connection_id(
    records: &Map<String, Value>,
    catalog_ids: &HashSet<String>,
    requested_id: &str,
) -> Option<String> {
    let slug: String = requested_id
        .to_ascii_lowercase()
        .chars()
        .filter(|character| character.is_ascii_alphanumeric() || matches!(character, '_' | '-'))
        .take(32)
        .collect();
    let slug = if slug.is_empty() {
        "provider".into()
    } else {
        slug
    };
    for _ in 0..32 {
        let candidate = format!("custom-{slug}-{}", short_id());
        if !records.contains_key(&candidate) && !catalog_ids.contains(&candidate) {
            return Some(candidate);
        }
    }
    None
}

fn short_id() -> String {
    Uuid::new_v4().simple().to_string()[..8].to_owned()
}

pub(super) fn now_iso() -> String {
    let now = time::OffsetDateTime::now_utc();
    format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}.{:06}Z",
        now.year(),
        u8::from(now.month()),
        now.day(),
        now.hour(),
        now.minute(),
        now.second(),
        now.microsecond()
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn display_names_follow_the_display_index() {
        assert_eq!(display_name("DeepSeek", 0), "DeepSeek");
        assert_eq!(display_name("DeepSeek", 2), "DeepSeek-2");
    }

    #[test]
    fn masks_never_expose_a_full_key() {
        assert_eq!(
            super::super::catalog::mask_key(Some("sk-abcdefghijklmnop")),
            Some("sk-ab...mnop".into())
        );
        assert_eq!(super::super::catalog::mask_key(Some("not-required")), None);
        assert_eq!(
            super::super::catalog::mask_key(Some("abc")),
            Some("ab...".into())
        );
        assert_eq!(super::super::catalog::mask_key(None), None);
    }
}
