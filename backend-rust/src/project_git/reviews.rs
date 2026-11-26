use std::{
    collections::VecDeque,
    fs::{self, File, OpenOptions},
    io::{self, BufReader, Read, Write},
    path::{Path, PathBuf},
    sync::Mutex,
    time::{Duration, Instant},
};

use serde::Deserialize;
use serde_json::{Map, Value, json};
use time::{OffsetDateTime, format_description::well_known::Rfc3339};
use uuid::Uuid;

use crate::{
    catalog::CatalogSource,
    runtime::{GitReview, ModelConfig, Runtime, RuntimeError},
};

use super::{
    GitResult, ProjectGit, ReviewTools, Workspace, canonicalize,
    discovery::short_hash,
    history::{self, Comparison},
};

const INDEX_LIMIT: u64 = 32 * 1024 * 1024;
const MAX_ROWS: usize = 200;
type ReviewRow = Map<String, Value>;

pub(super) struct ReviewRuntime {
    runtime: Runtime,
    catalog: CatalogSource,
    fixed_model: Option<ModelConfig>,
    attempts: Mutex<VecDeque<Instant>>,
    index_writes: Mutex<()>,
}

impl ReviewRuntime {
    pub(super) fn new(
        runtime: Runtime,
        catalog: CatalogSource,
        fixed_model: Option<ModelConfig>,
    ) -> Self {
        Self {
            runtime,
            catalog,
            fixed_model,
            attempts: Mutex::new(VecDeque::new()),
            index_writes: Mutex::new(()),
        }
    }

    fn check_rate(&self) -> Result<(), RuntimeError> {
        let mut attempts = self.attempts.lock().map_err(storage_error)?;
        let now = Instant::now();
        while attempts
            .front()
            .is_some_and(|time| now.duration_since(*time) >= Duration::from_secs(60))
        {
            attempts.pop_front();
        }
        if attempts.len() >= 15 {
            return Err(failure(429, "评审请求过多，请稍后再试。"));
        }
        attempts.push_back(now);
        Ok(())
    }

    fn model(&self, run_mode: &str) -> Result<ModelConfig, RuntimeError> {
        if let Some(model) = &self.fixed_model {
            return Ok(model.clone());
        }
        self.catalog
            .load()
            .select(None, run_mode == "thinking")
            .and_then(ModelConfig::from_catalog)
            .map_err(|message| failure(503, message))
    }
}

#[derive(Deserialize)]
pub(super) struct ReviewRequest {
    pub(super) workspace_id: Option<String>,
    #[serde(default)]
    sha: String,
    #[serde(default)]
    parent: String,
    #[serde(default)]
    base: String,
    head: Option<String>,
}

pub(super) fn launch(
    app: &ProjectGit,
    repo_id: &str,
    request: ReviewRequest,
) -> Result<Value, RuntimeError> {
    let dispatch = app
        .reviews
        .as_ref()
        .ok_or_else(|| failure(503, "评审运行时不可用。"))?;
    dispatch.check_rate()?;
    let workspace = &app.workspace;
    let repository = workspace
        .repository(repo_id)
        .map_err(|error| failure(400, error.0))?;
    let compare = history::compare(
        &repository,
        &request.sha,
        &request.parent,
        &request.base,
        request.head.as_deref().unwrap_or("HEAD"),
    )
    .map_err(|error| failure(400, error.0))?;
    // A shallow clone may know a parent ID without holding the object needed to review it.
    repository
        .git()
        .read(["cat-file", "-e", &compare.base_sha])
        .map_err(|error| failure(400, error.0))?;
    let index = index_path(workspace, repo_id);
    let _writer = dispatch.index_writes.lock().map_err(storage_error)?;
    verify_review_index(&workspace.data_dir, &index).map_err(storage_error)?;
    let mut rows = read_rows(&workspace.data_dir, &index).map_err(|error| failure(400, error.0))?;
    // The caller's JSON contains only revision selection. Execution policy and
    // upstream model configuration come from trusted Host state.
    let run_mode = run_mode(&workspace.data_dir);
    let model = dispatch.model(run_mode)?;
    let name = repository
        .path
        .file_name()
        .unwrap_or_default()
        .to_string_lossy();
    let title: String = format!(
        "Review · {name} · {} · {}",
        &compare.head_sha[..8],
        compare.review_label()
    )
    .chars()
    .take(120)
    .collect();
    let metadata = compare.review_metadata(&repository);
    let mut row = json!({"workspace_id":workspace.id,"kind":compare.kind(),"title":title,
        "base_sha":compare.base_sha,"head_sha":compare.head_sha});
    let message = prompt(&repository.path, &compare);
    let tools = ReviewTools::new(
        workspace.root.clone(),
        workspace.data_dir.clone(),
        repository,
        compare,
    );
    let accepted = dispatch.runtime.create_git_review(
        &workspace.id,
        GitReview {
            title,
            message,
            metadata,
            run_mode: run_mode.into(),
        },
        model,
        tools,
    )?;
    let result = &accepted["data"];
    row["conversation_id"] = result["conversation_id"].clone();
    row["task_id"] = result["task_id"].clone();
    row["created_at"] = result["created_at"]
        .as_f64()
        .and_then(|seconds| {
            OffsetDateTime::from_unix_timestamp_nanos((seconds * 1_000_000_000.0) as i128).ok()
        })
        .and_then(|date| date.format(&Rfc3339).ok())
        .map(Value::String)
        .unwrap_or_else(|| result["created_at"].clone());
    rows.insert(0, row.as_object().expect("review row").clone());
    rows.truncate(MAX_ROWS);
    row["warning"] = match write_index(&workspace.data_dir, &index, &rows) {
        Ok(()) => Value::Null,
        Err(_) => json!("评审已启动，但关联记录保存失败，可在对话列表查看。"),
    };
    Ok(row)
}

fn prompt(repo: &Path, compare: &Comparison) -> String {
    format!(
        "请评审 {} 目录下的{}。\n目标版本：{}\n对比基准：{}\n\n\
        这是已授权立即开始的代码评审任务。使用 git_review_changes 列举变更，git_review_diff 读取历史差异，\
        git_review_read 读取 base 或 head 的相关历史代码。比较范围已固定。请按变更和影响范围核对适用的 AGENTS.md、调用方、接口契约、边界条件、并发和数据完整性。\
        只报告本次改动引入、证据充分且值得修复的具体问题。每条说明 P0/P1/P2/P3 优先级、目标版本文件和行号、触发条件、影响和证据。\
        如果未发现明确问题，说明实际检查范围与未验证项。本轮使用只读证据工具，把未执行的测试标为未验证。",
        repo.display(),
        compare.review_description(),
        compare.head_sha,
        compare.base_sha
    )
}

fn run_mode(data: &Path) -> &'static str {
    let file = File::open(data.join("personalization.json")).ok();
    match file.and_then(|file| serde_json::from_reader::<_, Value>(BufReader::new(file)).ok()) {
        Some(preferences) if preferences["default_run_mode"] == "fast" => "fast",
        _ => "thinking",
    }
}

fn failure(status: u16, message: impl Into<String>) -> RuntimeError {
    RuntimeError {
        status,
        message: message.into(),
    }
}

fn storage_error(error: impl std::fmt::Display) -> RuntimeError {
    failure(503, format!("无法访问评审索引：{error}"))
}

pub(super) fn list(app: &ProjectGit, repo_id: &str) -> GitResult<Value> {
    let workspace = &app.workspace;
    let root = &workspace.data_dir;
    let rows = read_rows(root, &index_path(workspace, repo_id))?;
    let conversations = root.join("conversations").join(&workspace.id);
    let mut result = Vec::new();
    if rows.is_empty() { return Ok(json!(result)); }
    check_directory(&root.join("conversations"), false)?;
    check_directory(&conversations, false)?;
    let db = if conversations.join(crate::conversation_db::FILENAME).exists() {
        Some(crate::conversation_db::Database::open(&conversations).map_err(super::GitError)?)
    } else { None };
    for mut row in rows.into_iter().take(MAX_ROWS) {
        if row.get("workspace_id").and_then(Value::as_str) != Some(&workspace.id) {
            continue;
        }
        let Some(conversation_id) = row.get("conversation_id").and_then(Value::as_str) else {
            continue;
        };
        let Some(task_id) = row.get("task_id").and_then(Value::as_str) else {
            continue;
        };
        if !valid_conversation_id(conversation_id) {
            continue;
        }
        let Some(db) = &db else { continue };
        let Some(summary) = db.summary(conversation_id).map_err(super::GitError)? else { continue };
        let title = db.field(conversation_id, "metadata", "title").map_err(super::GitError)?
            .as_ref().and_then(Value::as_str).filter(|s| !s.is_empty()).map(str::to_owned)
            .or_else(|| summary["title"].as_str().filter(|s| !s.is_empty()).map(str::to_owned))
            .or_else(|| row.get("title").and_then(Value::as_str).map(str::to_owned))
            .unwrap_or_default();
        let status = if let Some(dispatch) = &app.reviews {
            dispatch
                .runtime
                .review_status(&workspace.id, conversation_id, task_id)
                .map_err(|error| super::GitError(error.message))?
        } else {
            None
        }
        .unwrap_or_else(|| "saved".to_owned());
        row.insert("status".into(), json!(status));
        row.insert("title".into(), json!(title));
        result.push(row);
    }
    Ok(json!(result))
}

fn index_path(workspace: &Workspace, repo_id: &str) -> PathBuf {
    let scope = short_hash(workspace.id.as_bytes());
    let key = short_hash(repo_id.as_bytes());
    workspace
        .data_dir
        .join("project_git_reviews")
        .join(scope)
        .join(format!("{key}.json"))
}

fn read_rows(root: &Path, index: &Path) -> GitResult<Vec<ReviewRow>> {
    let Some(file) = open_scoped_file(root, index)? else {
        return Ok(Vec::new());
    };
    let mut contents = Vec::new();
    file.take(INDEX_LIMIT + 1).read_to_end(&mut contents)?;
    if contents.len() as u64 > INDEX_LIMIT {
        return Err("评审索引超过读取上限。".into());
    }
    serde_json::from_slice(&contents).map_err(|_| "评审记录格式损坏。".into())
}

fn verify_review_index(root: &Path, index: &Path) -> io::Result<()> {
    let dir = index.parent().expect("review index parent");
    check_directory(root, false)?;
    check_directory(dir.parent().expect("review index root"), false)?;
    check_directory(dir, false)?;
    match fs::symlink_metadata(index) {
        Ok(metadata) if metadata.is_file() && canonicalize(index)? == index => Ok(()),
        Ok(_) => Err(io::Error::other("Expected a regular review index file")),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error),
    }
}

fn check_directory(path: &Path, create: bool) -> io::Result<()> {
    if create {
        let mut builder = fs::DirBuilder::new();
        #[cfg(unix)]
        {
            use std::os::unix::fs::DirBuilderExt;
            builder.mode(0o700);
        }
        match builder.create(path) {
            Ok(()) => {}
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => {}
            Err(error) => return Err(error),
        }
    }
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.is_dir() && canonicalize(path)? == path => Ok(()),
        Ok(_) => Err(io::Error::other("Expected a directory in the review index")),
        Err(error) if error.kind() == io::ErrorKind::NotFound && !create => Ok(()),
        Err(error) => Err(error),
    }
}

fn write_index(root: &Path, index: &Path, rows: &[ReviewRow]) -> io::Result<()> {
    verify_review_index(root, index)?;
    let dir = index.parent().expect("review index parent");
    check_directory(dir.parent().expect("review index root"), true)?;
    check_directory(dir, true)?;
    let bytes = serde_json::to_vec(rows)?;
    if bytes.len() as u64 > INDEX_LIMIT {
        return Err(io::Error::other("Review index exceeds its size limit"));
    }
    let temporary = dir.join(format!(".review-{}.tmp", Uuid::new_v4()));
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(&temporary)?;
    let result = file.write_all(&bytes).and_then(|_| file.sync_all());
    drop(file);
    let result = result.and_then(|_| fs::rename(&temporary, index));
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result
}

fn open_scoped_file(root: &Path, path: &Path) -> io::Result<Option<File>> {
    let actual = match canonicalize(path) {
        Ok(path) => path,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error),
    };
    if !actual.starts_with(root) || actual != path || !fs::symlink_metadata(path)?.is_file() {
        return Ok(None);
    }
    match File::open(path) {
        Ok(file) => Ok(Some(file)),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error),
    }
}

fn valid_conversation_id(id: &str) -> bool {
    id.strip_prefix("conv_").is_some_and(|id| {
        !id.is_empty()
            && id
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-'))
    })
}
