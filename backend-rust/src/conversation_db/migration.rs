//! Explicit legacy import shared by the two Host browser surfaces.
use super::{Database, FILENAME};
use crate::{
    legacy::{LegacyDocument, LegacyKind},
    runtime::store::index_row,
};
use axum::{Json, Router, extract::State, http::StatusCode, routing::get};
use rusqlite::{Connection, OpenFlags};
use serde::Serialize;
use serde_json::{Value, json};
use std::{
    collections::{HashMap, HashSet},
    fs, io,
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
};

#[derive(Clone, Serialize, Default)]
struct Report {
    pending: usize,
    migrated: usize,
    failed: usize,
    errors: Vec<String>,
    running: bool,
    #[serde(skip)]
    run_generation: u64,
}
#[derive(Clone)]
struct Job {
    root: PathBuf,
    logs: PathBuf,
    state: Arc<Mutex<Report>>,
}
#[derive(Clone)]
struct Source {
    dir: PathBuf,
    id: String,
    chat: bool,
}
pub(crate) struct Issue {
    pub(crate) count: usize,
    pub(crate) message: String,
}

impl Issue {
    pub(crate) fn new(count: usize, domain: &str, detail: impl std::fmt::Display) -> Self {
        Self {
            count: count.max(1),
            message: format!("{domain}: {detail}"),
        }
    }
}

#[derive(Default)]
struct ConversationScan {
    sources: Vec<Source>,
    issues: Vec<Issue>,
}

struct Batch {
    sources: Vec<Source>,
    children: crate::sub_agents::ChildMigration,
    reviews: Vec<super::reviews::legacy::Source>,
    issues: Vec<Issue>,
}

impl Batch {
    fn discover(root: &Path, logs: &Path) -> Self {
        let ConversationScan {
            sources,
            mut issues,
        } = scan_conversations(root);
        let (reviews, review_issues) = super::reviews::legacy::discover(root, logs);
        issues.extend(review_issues);
        let (children, child_issues) = crate::sub_agents::discover_legacy(root);
        issues.extend(child_issues);
        Self {
            sources,
            children,
            reviews,
            issues,
        }
    }

    fn pending(&self) -> usize {
        self.sources.len()
            + self.children.count()
            + self.reviews.len()
            + self.issues.iter().map(|issue| issue.count).sum::<usize>()
    }
}

type Reply = (StatusCode, Json<Value>);
fn reply(report: &Report) -> Reply {
    (StatusCode::OK, Json(json!(report)))
}
fn failure(e: impl std::fmt::Display) -> Reply {
    (
        StatusCode::INTERNAL_SERVER_ERROR,
        Json(json!({"error":e.to_string()})),
    )
}

pub fn router(root: PathBuf, logs: PathBuf) -> Router {
    Router::new()
        .route(
            "/api/conversation-storage/migration",
            get(status).post(start),
        )
        .with_state(Job {
            root,
            logs,
            state: Default::default(),
        })
}
async fn status(State(job): State<Job>) -> Reply {
    tokio::task::spawn_blocking(move || {
        let snapshot = job.state.lock().unwrap().clone();
        if snapshot.running {
            return reply(&snapshot);
        }
        let batch = Batch::discover(&job.root, &job.logs);
        let mut current = job.state.lock().unwrap().clone();
        if !current.running && current.run_generation == snapshot.run_generation {
            current.pending = batch.pending();
            // Also report discovery when only damaged sources remain.
            let previous: HashSet<_> = current.errors.iter().cloned().collect();
            for issue in batch.issues {
                if !previous.contains(&issue.message) {
                    record_issue(&mut current, issue);
                }
            }
        }
        reply(&current)
    })
    .await
    .unwrap_or_else(failure)
}
async fn start(State(job): State<Job>) -> Reply {
    let mut current = job.state.lock().unwrap();
    if current.running {
        return reply(&current);
    }
    current.run_generation = current.run_generation.wrapping_add(1);
    current.running = true;
    current.failed = 0;
    current.migrated = 0;
    current.errors.clear();
    let runner = job.clone();
    match std::thread::Builder::new()
        .name("conversation-migration".into())
        .spawn(move || run(runner))
    {
        Ok(_) => reply(&current),
        Err(e) => {
            current.running = false;
            failure(e)
        }
    }
}
fn run(job: Job) {
    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| run_batch(&job)));
    let mut st = job.state.lock().unwrap_or_else(|p| p.into_inner());
    if result.is_err() {
        record_issue(&mut st, Issue::new(1, "迁移", "后台任务中断"));
    }
    st.running = false;
}
#[cfg(test)]
#[path = "../../../test/2026-11-27_Rust对话格式迁移_02/isolation.rs"]
mod isolation_tests;
#[cfg(test)]
#[path = "../../../test/2026-11-27_Rust对话格式迁移/storage.rs"]
mod tests;

fn record_issue(report: &mut Report, issue: Issue) {
    report.failed += issue.count;
    report.errors.push(issue.message);
}

fn run_batch(job: &Job) {
    let batch = Batch::discover(&job.root, &job.logs);
    {
        let mut report = job.state.lock().unwrap();
        report.pending = batch.pending();
        for issue in batch.issues {
            record_issue(&mut report, issue);
        }
    }
    let mut indexes = HashMap::new();
    for source in batch.sources {
        let index = indexes.entry(source.dir.clone()).or_insert_with(|| {
            crate::runtime::store::read_index(&source.dir.join("index.json"))
                .map_err(|error| error.message)
        });
        let result = index
            .as_ref()
            .map_err(Clone::clone)
            .and_then(|index| convert(&source, index));
        let mut state = job.state.lock().unwrap();
        match result {
            Ok(inserted) => completed(&mut state, 1, usize::from(inserted)),
            Err(error) => record_issue(&mut state, Issue::new(1, &source.label(), error)),
        }
    }
    for source in batch.reviews {
        let result = source.import(&job.root);
        let mut state = job.state.lock().unwrap();
        match result {
            Ok(inserted) => completed(&mut state, 1, usize::from(inserted)),
            Err(error) => record_issue(
                &mut state,
                Issue::new(1, &format!("审核 {}/{}", source.kind, source.name), error),
            ),
        }
    }
    batch.children.import(&job.root, |result| {
        let mut report = job.state.lock().unwrap();
        match result {
            Ok((count, inserted)) => completed(&mut report, count, inserted),
            Err(issue) => record_issue(&mut report, issue),
        }
    });
}

fn completed(report: &mut Report, count: usize, inserted: usize) {
    report.migrated += inserted;
    report.pending = report.pending.saturating_sub(count);
}

fn directory(path: &Path) -> io::Result<Option<fs::ReadDir>> {
    match fs::symlink_metadata(path) {
        Ok(meta) if meta.is_dir() => fs::read_dir(path).map(Some),
        Ok(_) => Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "存储路径不是普通目录",
        )),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error),
    }
}

fn scan_conversations(root: &Path) -> ConversationScan {
    let mut result = ConversationScan::default();
    match directory(&root.join("conversations")) {
        Ok(entries) => {
            for entry in entries.into_iter().flatten() {
                match entry.and_then(|entry| Ok((entry.path(), entry.file_type()?))) {
                    Ok((path, kind)) if kind.is_dir() => result.scan(&path, false),
                    Ok(_) => (),
                    Err(error) => result.issues.push(Issue::new(1, "Agent 工作区列表", error)),
                }
            }
        }
        Err(error) => result.issues.push(Issue::new(1, "Agent 工作区列表", error)),
    }
    let chat = root.join("chat");
    match fs::symlink_metadata(&chat) {
        Ok(meta) if meta.is_dir() => result.scan(&chat.join("conversations"), true),
        Ok(_) => result
            .issues
            .push(Issue::new(1, "Chat", "存储路径不是普通目录")),
        Err(error) if error.kind() == io::ErrorKind::NotFound => (),
        Err(error) => result.issues.push(Issue::new(1, "Chat", error)),
    }
    result
        .sources
        .sort_by(|a, b| (&a.dir, &a.id).cmp(&(&b.dir, &b.id)));
    result
        .issues
        .sort_by(|left, right| left.message.cmp(&right.message));
    result
}

impl Source {
    fn label(&self) -> String {
        format!("{}/{}", conversation_domain(&self.dir, self.chat), self.id)
    }
}

fn conversation_domain(path: &Path, chat: bool) -> String {
    if chat {
        "Chat".into()
    } else {
        format!(
            "Agent 工作区 {}",
            path.file_name().unwrap_or_default().to_string_lossy()
        )
    }
}

impl ConversationScan {
    fn scan(&mut self, dir: &Path, chat: bool) {
        let domain = conversation_domain(dir, chat);
        let entries = match directory(dir) {
            Ok(Some(entries)) => entries,
            Ok(None) => return,
            Err(error) => {
                self.issues.push(Issue::new(1, &domain, error));
                return;
            }
        };
        let mut candidates = Vec::new();
        for entry in entries {
            let entry = match entry {
                Ok(entry) => entry,
                Err(error) => {
                    self.issues.push(Issue::new(1, &domain, error));
                    continue;
                }
            };
            let name = entry.file_name();
            let Some(id) = name
                .to_str()
                .and_then(|n| n.strip_suffix(".json"))
                .filter(|id| {
                    id.starts_with("conv_")
                        && id
                            .bytes()
                            .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
                })
            else {
                continue;
            };
            match entry.file_type() {
                Ok(kind) if kind.is_file() => candidates.push(Source {
                    dir: dir.to_path_buf(),
                    id: id.into(),
                    chat,
                }),
                Ok(_) => self.issues.push(Issue::new(
                    1,
                    &format!("{domain}/{id}"),
                    "旧记录不是普通文件",
                )),
                Err(error) => self
                    .issues
                    .push(Issue::new(1, &format!("{domain}/{id}"), error)),
            }
        }
        if candidates.is_empty() {
            return;
        }
        match imported(dir) {
            Ok(imported) => self.sources.extend(
                candidates
                    .into_iter()
                    .filter(|source| !imported.contains(&source.id)),
            ),
            Err(error) => self
                .issues
                .push(Issue::new(candidates.len(), &domain, error)),
        }
    }
}

fn imported(dir: &Path) -> Result<HashSet<String>, String> {
    match fs::symlink_metadata(dir.join(FILENAME)) {
        Ok(meta) if meta.is_file() => {}
        Ok(_) => return Err("对话数据库不是普通文件".into()),
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(HashSet::new()),
        Err(error) => return Err(super::error(error)),
    }
    let path = dir.canonicalize().map_err(super::error)?.join(FILENAME);
    let db = Connection::open_with_flags(
        path,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NOFOLLOW,
    )
    .map_err(super::error)?;
    let version: i64 = db
        .query_row("PRAGMA user_version", [], |row| row.get(0))
        .map_err(super::error)?;
    if !(0..=3).contains(&version) {
        return Err(format!("不支持的对话数据库版本 {version}"));
    }
    let exists:bool = db.query_row(
        "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name='legacy_imports')",
        [],|row|row.get(0),
    ).map_err(super::error)?;
    if !exists && version == 0 {
        return Ok(HashSet::new());
    }
    let mut query = db
        .prepare("SELECT id FROM legacy_imports")
        .map_err(super::error)?;
    query
        .query_map([], |row| row.get(0))
        .map_err(super::error)?
        .collect::<Result<_, _>>()
        .map_err(super::error)
}
fn convert(src: &Source, prior: &Value) -> Result<bool, String> {
    let path = src.dir.join(format!("{}.json", src.id));
    let doc = LegacyDocument::read(&path).map_err(|e| e.message)?;
    if doc.source_id() != src.id || (doc.kind() == LegacyKind::Chat) != src.chat {
        return Err("Legacy record identity or type mismatch".into());
    }

    let mut summary = if src.chat {
        doc.chat_path(None).map_err(|e| e.message)?;
        crate::chat_mode::migration_summary(doc.raw())
    } else {
        index_row(doc.raw(), prior.get(&src.id))
    };
    if let Some(old) = prior[&src.id].as_object() {
        for (k, v) in old {
            if summary.get(k).is_none() {
                summary[k] = v.clone();
            }
        }
    }
    Database::open(&src.dir)?.import(doc.raw(), &summary)
}
