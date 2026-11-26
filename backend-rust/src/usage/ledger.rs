use std::{
    fs::{self, File, OpenOptions},
    io::{self, Write},
    path::{Path, PathBuf},
};

use fs2::FileExt;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};
use uuid::Uuid;

use super::{
    CostEntry, Status, Tokens,
    money::{Usd, parse_amount},
    token_count,
};

/// One data root containing `api_costs/<conversation_id>.json`.
#[derive(Clone, Debug)]
pub struct Ledger {
    directory: PathBuf,
}

#[derive(Clone, Debug)]
pub struct Actor<'a> {
    pub id: &'a str,
    pub name: &'a str,
    pub kind: &'a str,
    pub historical_unpriced: bool,
    pub baseline: Tokens,
}

impl<'a> Actor<'a> {
    pub fn new(id: &'a str, name: &'a str, kind: &'a str) -> Self {
        Self {
            id,
            name,
            kind,
            historical_unpriced: false,
            baseline: Tokens::default(),
        }
    }
}

#[derive(Clone, Debug, Default, Serialize)]
pub struct RequestTiming {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub duration_ms: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ttft_ms: Option<u64>,
    /// Set to an empty sample to suppress fallback to whole-response timing.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub speed_sample: Option<SpeedSample>,
}

#[derive(Clone, Debug, Default, Serialize)]
pub struct SpeedSample {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub output_tokens: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub duration_ms: Option<u64>,
}

#[derive(Clone, Debug)]
pub struct SubAgent<'a> {
    pub task_id: &'a str,
    pub display_name: Option<&'a str>,
    pub summary: Option<&'a str>,
    pub cost_tracking_version: bool,
}

#[derive(Deserialize, Serialize)]
struct LedgerFile {
    actors: Map<String, Value>,
    requests: Map<String, Value>,
    #[serde(flatten)]
    extra: Map<String, Value>,
}

impl Ledger {
    pub fn new(data_dir: impl AsRef<Path>) -> Self {
        Self {
            directory: data_dir.as_ref().join("api_costs"),
        }
    }

    fn path(&self, conversation_id: &str) -> io::Result<PathBuf> {
        if conversation_id.is_empty()
            || !conversation_id
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-'))
        {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "invalid conversation identity for cost ledger",
            ));
        }
        Ok(self.directory.join(format!("{conversation_id}.json")))
    }

    /// Returns true only for a new commit. The caller can use that result to
    /// apply separate goal or runtime statistics after the ledger lock is released.
    pub fn record_request(
        &self,
        conversation_id: &str,
        request_id: &str,
        actor: &Actor<'_>,
        entry: &CostEntry,
        timing: &RequestTiming,
    ) -> io::Result<bool> {
        let path = self.path(conversation_id)?;
        if request_id.is_empty() || actor.id.is_empty() || actor.kind.is_empty() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "missing request or actor identity",
            ));
        }
        fs::create_dir_all(&self.directory)?;
        let _lock = exclusive_lock(&self.directory.join(".usage-ledger.lock"))?;
        let mut ledger = read_ledger(&path)?;
        if ledger.requests.contains_key(request_id) {
            return Ok(false);
        }
        let stats = ledger.actors.entry(actor.id).or_insert_with(|| json!({
            "id": actor.id, "name": actor.name, "kind": actor.kind, "cost_usd": "0",
            "input_tokens": actor.baseline.input_tokens, "cached_input_tokens": actor.baseline.cached_input_tokens,
            "output_tokens": actor.baseline.output_tokens, "total_tokens": actor.baseline.total_tokens,
            "requests": 0, "priced_requests": 0, "unpriced_requests": 0,
            "local_requests": 0, "subscription_requests": 0, "historical_unpriced": actor.historical_unpriced,
        }));
        update_actor(stats, actor.name, entry)?;
        #[derive(Serialize)]
        struct Request<'a> {
            actor_id: &'a str,
            recorded_at: String,
            #[serde(flatten)]
            entry: &'a CostEntry,
            #[serde(flatten)]
            timing: &'a RequestTiming,
        }
        ledger.requests.insert(
            request_id.to_owned(),
            serde_json::to_value(Request {
                actor_id: actor.id,
                recorded_at: jiff::Timestamp::now().to_string(),
                entry,
                timing,
            })?,
        );
        atomic_json(&path, &ledger)?;
        Ok(true)
    }

    pub fn actor_statistics(
        &self,
        conversation_id: &str,
        actor_id: &str,
    ) -> io::Result<Option<Value>> {
        Ok(read_ledger(&self.path(conversation_id)?)?
            .actors
            .remove(actor_id))
    }

    /// Summarize tracked actors and include markers for pre-ledger history.
    pub fn summarize(
        &self,
        conversation_id: &str,
        main_input: u64,
        sub_agents: &[SubAgent<'_>],
    ) -> io::Result<Value> {
        let mut actors = read_ledger(&self.path(conversation_id)?)?.actors;
        actors
            .entry("main")
            .or_insert_with(|| historical_actor("main", "", "main", main_input > 0));
        for task in sub_agents.iter().filter(|task| !task.task_id.is_empty()) {
            actors.entry(task.task_id).or_insert_with(|| {
                historical_actor(
                    task.task_id,
                    task.display_name
                        .filter(|name| !name.is_empty())
                        .or(task.summary.filter(|name| !name.is_empty()))
                        .unwrap_or(task.task_id),
                    "sub_agent",
                    !task.cost_tracking_version,
                )
            });
        }
        let mut actors: Vec<_> = actors.into_values().collect();
        actors.sort_by_key(|actor| actor["kind"] != "main");
        let mut total = Usd::default();
        let mut partial = false;
        let mut has_priced = false;
        for actor in &actors {
            let amount = parse_amount(&actor["cost_usd"])
                .ok_or_else(|| invalid("invalid actor USD amount"))?;
            total.add(&amount);
            partial |= token_count(&actor["unpriced_requests"]).unwrap_or_default() > 0
                || actor["historical_unpriced"] == true;
            has_priced |= token_count(&actor["priced_requests"]).unwrap_or_default() > 0;
        }
        Ok(
            json!({"currency": "USD", "total_usd": total, "partial": partial, "has_priced": has_priced, "actors": actors}),
        )
    }
}

fn read_ledger(path: &Path) -> io::Result<LedgerFile> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.is_file() => Ok(serde_json::from_slice(&fs::read(path)?)?),
        Ok(_) => Err(invalid("invalid cost ledger file type")),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(LedgerFile {
            actors: Map::new(),
            requests: Map::new(),
            extra: Map::from_iter([("version".into(), json!(1))]),
        }),
        Err(error) => Err(error),
    }
}

fn historical_actor(id: &str, name: &str, kind: &str, unpriced: bool) -> Value {
    json!({"id": id, "name": name, "kind": kind, "cost_usd": "0", "requests": 0,
           "priced_requests": 0, "unpriced_requests": 0, "historical_unpriced": unpriced})
}

fn update_actor(stats: &mut Value, name: &str, entry: &CostEntry) -> io::Result<()> {
    if !stats.is_object() {
        return Err(invalid("invalid cost ledger actor"));
    }
    let mut total =
        parse_amount(&stats["cost_usd"]).ok_or_else(|| invalid("invalid actor USD amount"))?;
    let status_field = match (entry.status, &entry.cost_usd) {
        (Status::Priced, Some(text)) => {
            total.add(
                &text
                    .parse()
                    .map_err(|()| invalid("invalid request USD amount"))?,
            );
            "priced_requests"
        }
        (Status::Unpriced, None) => "unpriced_requests",
        _ => return Err(invalid("inconsistent request pricing status")),
    };
    for (key, value) in [
        ("input_tokens", entry.tokens.input_tokens),
        ("cached_input_tokens", entry.tokens.cached_input_tokens),
        ("output_tokens", entry.tokens.output_tokens),
        ("total_tokens", entry.tokens.total_tokens),
        ("requests", 1),
        (status_field, 1),
    ] {
        let count = token_count(&stats[key])
            .unwrap_or_default()
            .checked_add(value)
            .ok_or_else(|| invalid("cost ledger counter overflow"))?;
        stats[key] = json!(count);
    }
    stats["cost_usd"] = json!(total);
    stats["name"] = json!(name);
    stats["current_context_tokens"] = json!(entry.tokens.current_context_tokens);
    Ok(())
}

fn invalid(message: &str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}

pub(super) fn exclusive_lock(path: &Path) -> io::Result<File> {
    let file = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open(path)?;
    FileExt::lock_exclusive(&file)?;
    Ok(file)
}

pub(super) fn atomic_json(path: &Path, data: &impl Serialize) -> io::Result<()> {
    let temp = path.with_extension(format!("{}.tmp", Uuid::new_v4()));
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(&temp)?;
    let result = (|| {
        serde_json::to_writer(&mut file, data)?;
        file.flush()?;
        file.sync_all()?;
        drop(file);
        replace(&temp, path)
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temp);
    }
    result
}

fn replace(temp: &Path, target: &Path) -> io::Result<()> {
    #[cfg(windows)]
    for delay in [50, 100, 200, 400, 800, 1600, 1600] {
        match fs::rename(temp, target) {
            Err(error) if matches!(error.raw_os_error(), Some(5 | 32)) => {
                std::thread::sleep(std::time::Duration::from_millis(delay))
            }
            result => return result,
        }
    }
    fs::rename(temp, target)
}
