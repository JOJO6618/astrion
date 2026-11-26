use std::{
    collections::{BTreeMap, HashMap, HashSet},
    fs, io,
    path::{Path, PathBuf},
    time::UNIX_EPOCH,
};

use jiff::Timestamp;
use serde::{Deserialize, Serialize};
use serde_json::Value;

use super::{
    ReportQuery,
    exchange_rates::cached_rate,
    ledger::{atomic_json, exclusive_lock},
    report::{self, Buckets},
};

const CHAT: &str = "chat";
const UNASSIGNED: &str = "ws:__unassigned__";
const CACHE_FILE: &str = "usage_stats_rollup.json";

#[derive(Clone, Debug)]
pub struct WorkspaceRef {
    pub workspace_id: String,
    pub label: String,
    pub data_dir: PathBuf,
    pub conversations_dir: PathBuf,
}

/// Resolve roots from an authenticated principal on the server side.
#[derive(Clone, Debug, Default)]
pub struct UsageConfig {
    pub workspaces: Vec<WorkspaceRef>,
    /// The shared Host root is scanned even with an empty workspace catalog.
    pub shared_agent_data_dir: Option<PathBuf>,
    pub chat_root: Option<PathBuf>,
    /// Directory containing the existing Frankfurter reference-rate cache.
    pub exchange_rate_dir: Option<PathBuf>,
}

impl UsageConfig {
    /// Load the Host's desktop/browser workspace catalog without opening or
    /// provisioning any project or conversation runtime.
    pub fn from_host_catalog(data_dir: &Path, catalog: &Path) -> io::Result<Self> {
        let entries = match fs::read(catalog) {
            Ok(bytes) => {
                let value: Value = serde_json::from_slice(&bytes)?;
                if !value.is_object() {
                    return Err(io::Error::new(
                        io::ErrorKind::InvalidData,
                        "invalid Host workspace catalog",
                    ));
                }
                value["workspaces"].as_array().cloned().unwrap_or_default()
            }
            Err(error) if error.kind() == io::ErrorKind::NotFound => Vec::new(),
            Err(error) => return Err(error),
        };
        let mut seen = HashSet::new();
        let workspaces = entries
            .iter()
            .filter_map(|entry| {
                let id = entry["workspace_id"].as_str()?.trim();
                let project = entry["path"].as_str()?.trim();
                if project.is_empty()
                    || id.is_empty()
                    || id.len() > 64
                    || matches!(id, "." | ".." | "__unassigned__")
                    || !id
                        .bytes()
                        .all(|byte| byte.is_ascii_alphanumeric() || b"._-".contains(&byte))
                    || !seen.insert(id)
                {
                    return None;
                }
                let label = ["label", "name"]
                    .into_iter()
                    .filter_map(|key| entry[key].as_str())
                    .map(str::trim)
                    .find(|label| !label.is_empty())
                    .unwrap_or(id);
                Some(WorkspaceRef {
                    workspace_id: id.to_owned(),
                    label: label.to_owned(),
                    data_dir: data_dir.to_owned(),
                    conversations_dir: data_dir.join("conversations").join(id),
                })
            })
            .collect();
        Ok(Self {
            workspaces,
            shared_agent_data_dir: Some(data_dir.to_owned()),
            chat_root: Some(data_dir.join(CHAT)),
            exchange_rate_dir: Some(data_dir.to_owned()),
        })
    }

    /// Builds the report directly once the request's authorized roots are resolved.
    pub fn report(&self, query: &ReportQuery, now: Timestamp) -> Result<Value, &'static str> {
        report::build(
            &gather(self),
            query,
            now,
            cached_rate(self.exchange_rate_dir.as_deref(), now),
        )
    }
}

pub(super) struct Source {
    pub(super) id: String,
    pub(super) label: String,
    pub(super) mode: &'static str,
    pub(super) conversations: Vec<Buckets>,
}

impl Source {
    fn new(id: String, label: String, mode: &'static str) -> Self {
        Self {
            id,
            label,
            mode,
            conversations: Vec::new(),
        }
    }
}

struct Group {
    directory: PathBuf,
    workspaces: Vec<usize>,
}

fn ledger_dir(root: &Path) -> PathBuf {
    let directory = root.join("api_costs");
    directory.canonicalize().unwrap_or(directory)
}

/// Fold the shared Agent, Chat, and legacy account ledgers once per UUID.
/// A quota estimate uses existing prices and never adds a monetary record.
pub(crate) fn weekly_gpt_values(
    data_dir: &Path,
    start: i64,
    end: jiff::Timestamp,
    percent: f64,
) -> (String, Option<String>) {
    use super::money::{Usd, parse_amount};
    use std::collections::{BTreeSet, HashSet};

    let mut roots = BTreeSet::from([ledger_dir(data_dir), ledger_dir(&data_dir.join("chat"))]);
    if let Ok(entries) = fs::read_dir(data_dir) {
        for entry in entries.flatten().filter(|entry| entry.path().is_dir()) {
            roots.insert(ledger_dir(&entry.path()));
        }
    }
    let mut total = Usd::default();
    let mut counted = HashSet::new();
    for directory in roots {
        let Ok(files) = fs::read_dir(directory) else {
            continue;
        };
        for file in files.flatten() {
            if file
                .path()
                .extension()
                .and_then(|extension| extension.to_str())
                != Some("json")
                || !file.file_type().is_ok_and(|file_type| file_type.is_file())
            {
                continue;
            }
            let Some(payload) = fs::read(file.path())
                .ok()
                .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok())
            else {
                continue;
            };
            let Some(requests) = payload["requests"].as_object() else {
                continue;
            };
            for (id, request) in requests {
                let Some(model_key) = request["price"]["model_key"].as_str() else {
                    continue;
                };
                if !model_key.to_ascii_lowercase().contains("gpt") || counted.contains(id) {
                    continue;
                }
                let Some(recorded) = request["recorded_at"].as_str().and_then(quota_timestamp)
                else {
                    continue;
                };
                if recorded.as_second() >= start && recorded < end {
                    counted.insert(id.clone());
                    if let Some(amount) = parse_amount(&request["cost_usd"]) {
                        total.add(&amount);
                    }
                }
            }
        }
    }
    (
        total
            .weekly_value(None)
            .unwrap_or_else(|| "0.000000".into()),
        total.weekly_value(Some(percent)),
    )
}

pub(crate) fn quota_timestamp(value: &str) -> Option<jiff::Timestamp> {
    value.parse().ok().or_else(|| {
        let local = value.parse::<jiff::civil::DateTime>().ok()?;
        local
            .to_zoned(jiff::tz::TimeZone::system())
            .ok()
            .map(|zoned| zoned.timestamp())
    })
}

fn gather(config: &UsageConfig) -> Vec<Source> {
    let mut result = Vec::with_capacity(config.workspaces.len() + 2);
    let mut groups: Vec<Group> = Vec::new();
    let shared = config.shared_agent_data_dir.as_deref().map(ledger_dir);
    if let Some(directory) = &shared {
        groups.push(Group {
            directory: directory.clone(),
            workspaces: Vec::new(),
        });
    }
    let mut source_indexes = HashMap::new();
    for (workspace_index, workspace) in config.workspaces.iter().enumerate() {
        let id = format!("ws:{}", workspace.workspace_id);
        source_indexes.entry(id.clone()).or_insert_with(|| {
            let index = result.len();
            let label = if workspace.label.is_empty() {
                workspace.workspace_id.clone()
            } else {
                workspace.label.clone()
            };
            result.push(Source::new(id, label, "agent"));
            index
        });
        let directory = ledger_dir(&workspace.data_dir);
        if let Some(group) = groups.iter_mut().find(|group| group.directory == directory) {
            group.workspaces.push(workspace_index);
        } else {
            groups.push(Group {
                directory,
                workspaces: vec![workspace_index],
            });
        }
    }
    let chat_dir = config.chat_root.as_deref().map(ledger_dir);
    let mut unassigned = Source::new(UNASSIGNED.into(), String::new(), "agent");
    let source_for =
        |index: usize| source_indexes[&format!("ws:{}", config.workspaces[index].workspace_id)];
    for group in &groups {
        let conversations = collect(&group.directory);
        if conversations.is_empty() {
            continue;
        }
        if let [workspace] = group.workspaces.as_slice()
            && shared.as_ref() != Some(&group.directory)
        {
            result[source_for(*workspace)]
                .conversations
                .extend(conversations.into_values());
            continue;
        }
        let mut owners = HashMap::<String, usize>::new();
        for &workspace in &group.workspaces {
            let dir = &config.workspaces[workspace].conversations_dir;
            let db = dir
                .join(crate::conversation_db::FILENAME)
                .is_file()
                .then(|| crate::conversation_db::Database::open(dir).ok())
                .flatten();
            if let Some(rows) = db.as_ref().and_then(|db| db.rows().ok()) {
                for row in rows {
                    if let Some(id) = row["id"].as_str() {
                        owners
                            .entry(id.to_owned())
                            .or_insert_with(|| source_for(workspace));
                    }
                }
            }
            if let Ok(entries) = fs::read_dir(dir) {
                for entry in entries.flatten() {
                    let name = entry.file_name();
                    let Some(name) = name.to_str() else { continue };
                    if name.starts_with("conv_")
                        && let Some(id) = name.strip_suffix(".json")
                        && !db
                            .as_ref()
                            .is_some_and(|db| db.imported(id).unwrap_or(false))
                        && entry.file_type().is_ok_and(|file_type| file_type.is_file())
                    {
                        owners
                            .entry(id.into())
                            .or_insert_with(|| source_for(workspace));
                    }
                }
            }
        }
        for (conversation_id, buckets) in conversations {
            let target = match owners.get(&conversation_id) {
                Some(&owner) => &mut result[owner],
                None => &mut unassigned,
            };
            target.conversations.push(buckets);
        }
    }
    if !unassigned.conversations.is_empty() {
        result.push(unassigned);
    }
    let mut chat = Source::new(CHAT.into(), String::new(), "chat");
    if let Some(directory) = chat_dir
        && !groups.iter().any(|group| group.directory == directory)
    {
        chat.conversations.extend(collect(&directory).into_values());
    }
    result.push(chat);
    result
}

#[derive(Deserialize, Serialize)]
struct Cache {
    version: u8,
    files: BTreeMap<String, CachedFile>,
}

#[derive(Deserialize, Serialize)]
struct CachedFile {
    sig: (i128, u64),
    buckets: Buckets,
}

fn collect(directory: &Path) -> BTreeMap<String, Buckets> {
    if !directory.is_dir() {
        return BTreeMap::new();
    }
    let Some(root) = directory.parent() else {
        return BTreeMap::new();
    };
    let path = root.join(CACHE_FILE);
    let lock = exclusive_lock(&root.join(".usage-stats-rollup.lock")).ok();
    let mut cached = fs::read(&path)
        .ok()
        .and_then(|bytes| serde_json::from_slice::<Cache>(&bytes).ok())
        .filter(|cache| cache.version == 1)
        .map(|cache| cache.files)
        .unwrap_or_default();
    let Ok(entries) = fs::read_dir(directory) else {
        return BTreeMap::new();
    };
    let mut files = BTreeMap::new();
    let mut changed = false;
    for entry in entries.flatten() {
        let name = entry.file_name();
        let Some(id) = name.to_str().and_then(|name| name.strip_suffix(".json")) else {
            continue;
        };
        let Ok(metadata) = entry.metadata() else {
            continue;
        };
        if !metadata.is_file() {
            continue;
        }
        let Ok(modified) = metadata.modified() else {
            continue;
        };
        let nanos = match modified.duration_since(UNIX_EPOCH) {
            Ok(elapsed) => elapsed.as_nanos() as i128,
            Err(error) => -(error.duration().as_nanos() as i128),
        };
        let sig = (nanos, metadata.len());
        if let Some(previous) = cached.remove(id)
            && previous.sig == sig
        {
            files.insert(id.to_owned(), previous);
            continue;
        }
        changed = true;
        let Some(payload) = fs::read(entry.path())
            .ok()
            .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok())
        else {
            continue;
        };
        files.insert(
            id.to_owned(),
            CachedFile {
                sig,
                buckets: report::rollup(&payload),
            },
        );
    }
    let cache = Cache { version: 1, files };
    if lock.is_some() && (changed || !cached.is_empty()) {
        let _ = atomic_json(&path, &cache);
    }
    cache
        .files
        .into_iter()
        .map(|(id, file)| (id, file.buckets))
        .collect()
}
