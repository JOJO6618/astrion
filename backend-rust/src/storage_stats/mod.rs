mod cleanup;
mod fs;
mod scanner;
mod timestamps;

use std::{
    ffi::OsStr,
    io::{self, BufReader},
    path::{Path, PathBuf},
    sync::{
        Arc, Mutex, PoisonError,
        atomic::{AtomicBool, Ordering},
    },
};

use axum::{
    Json, Router,
    extract::{State, rejection::JsonRejection},
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::{get, post},
};
use serde::Serialize;
use serde_json::{Value, json};
use uuid::Uuid;

use self::{
    cleanup::{clean_backups, clean_logs},
    fs::{Dir, missing_or_unsafe_root},
    scanner::{Snapshot, scan},
};

const CACHE_FILE: &str = "storage_stats.json";
const CACHE_VERSION: u32 = 1;
const STATS: &str = "/api/storage-stats";
const REFRESH: &str = "/api/storage-stats/refresh";
const CLEAN_LOGS: &str = "/api/storage-stats/clean/logs";
const CLEAN_BACKUPS: &str = "/api/storage-stats/clean/backups";

/// Mount on the authenticated host Web router, with CSRF protection for POST.
/// Both paths come from trusted server configuration.
pub fn router(data_root: PathBuf, logs_dir: PathBuf) -> Router {
    let state = Arc::new(StorageStats {
        data_root,
        logs_dir,
        running: AtomicBool::new(false),
        filesystem: Mutex::new(()),
    });
    Router::new()
        .route(STATS, get(get_stats))
        .route(REFRESH, post(refresh))
        .route(CLEAN_LOGS, post(clean_logs_route))
        .route(CLEAN_BACKUPS, post(clean_backups_route))
        .with_state(state)
}

/// The corresponding routes for deployments that cannot access host data.
pub fn unsupported_router() -> Router {
    Router::new()
        .route(STATS, get(unsupported))
        .route(REFRESH, post(unsupported))
        .route(CLEAN_LOGS, post(unsupported))
        .route(CLEAN_BACKUPS, post(unsupported))
}

struct StorageStats {
    data_root: PathBuf,
    logs_dir: PathBuf,
    running: AtomicBool,
    filesystem: Mutex<()>,
}

impl StorageStats {
    fn start_scan(self: &Arc<Self>) -> bool {
        if self
            .running
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .is_err()
        {
            return false;
        }
        let state = self.clone();
        tokio::task::spawn_blocking(move || {
            let _filesystem = state
                .filesystem
                .lock()
                .unwrap_or_else(PoisonError::into_inner);
            let _reset = ResetScan(&state.running);
            let snapshot = scan(&state.data_root, &state.logs_dir);
            if let Err(error) = store_cache(&state.data_root, &snapshot) {
                eprintln!("storage stats: cache write failed: {error}");
            }
        });
        true
    }

    fn clean<T: Serialize>(self: Arc<Self>, operation: impl FnOnce(&Self) -> T) -> Json<Value> {
        let _filesystem = self
            .filesystem
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        let data = operation(&self);
        self.start_scan();
        success(data)
    }
}

struct ResetScan<'a>(&'a AtomicBool);

impl Drop for ResetScan<'_> {
    fn drop(&mut self) {
        self.0.store(false, Ordering::Release);
    }
}

fn load_cache(path: &Path) -> Option<Snapshot> {
    let result = Dir::open(path)
        .and_then(|dir| dir.read_file(OsStr::new(CACHE_FILE)))
        .map(BufReader::new);
    let file = match result {
        Ok(file) => file,
        Err(error) if missing_or_unsafe_root(&error) => return None,
        Err(error) => {
            eprintln!("storage stats: cache read failed: {error}");
            return None;
        }
    };
    match serde_json::from_reader::<_, Snapshot>(file) {
        Ok(snapshot) if snapshot.version == CACHE_VERSION => Some(snapshot),
        Ok(_) => None,
        Err(error) => {
            eprintln!("storage stats: invalid cache: {error}");
            None
        }
    }
}

fn store_cache(path: &Path, snapshot: &Snapshot) -> io::Result<()> {
    let dir = Dir::ensure(path)?;
    let name = format!("storage_stats.{}.tmp", Uuid::new_v4().simple());
    let temp = OsStr::new(&name);
    let result = (|| {
        let file = dir.create_file(temp)?;
        serde_json::to_writer(&file, snapshot)?;
        file.sync_all()?;
        drop(file);
        dir.replace_file(temp, OsStr::new(CACHE_FILE))
    })();
    if result.is_err()
        && let Ok(entry) = dir.entry(temp)
    {
        let _ = dir.remove(temp, &entry);
    }
    result
}

fn is_backup(name: &OsStr) -> bool {
    name.as_encoded_bytes().starts_with(b"conv_")
}

fn success(data: impl Serialize) -> Json<Value> {
    Json(json!({ "success": true, "data": data }))
}

fn error(status: StatusCode, reason: &str) -> Response {
    (status, Json(json!({ "success": false, "error": reason }))).into_response()
}

async fn unsupported() -> Json<Value> {
    success(json!({ "available": false, "reason": "host_only" }))
}

async fn get_stats(State(state): State<Arc<StorageStats>>) -> Response {
    let root = state.data_root.clone();
    match tokio::task::spawn_blocking(move || load_cache(&root)).await {
        Ok(Some(cache)) => success(json!({
            "available": true,
            "platform": cache.platform,
            "root": cache.data_root,
            "logs_root": cache.logs_root,
            "scanned_at": cache.scanned_at,
            "duration_ms": cache.duration_ms,
            "total_bytes": cache.total_bytes,
            "categories": cache.categories,
            "backups": cache.backups,
            "scanning": state.running.load(Ordering::Acquire),
        }))
        .into_response(),
        Ok(None) => {
            state.start_scan();
            success(json!({
                "available": true,
                "platform": null,
                "root": state.data_root.to_string_lossy(),
                "logs_root": state.logs_dir.to_string_lossy(),
                "scanned_at": null,
                "duration_ms": 0,
                "total_bytes": 0,
                "categories": [],
                "backups": [],
                "scanning": true,
            }))
            .into_response()
        }
        Err(join) => error(StatusCode::INTERNAL_SERVER_ERROR, &join.to_string()),
    }
}

async fn refresh(State(state): State<Arc<StorageStats>>) -> Json<Value> {
    success(json!({ "started": state.start_scan(), "scanning": true }))
}

async fn clean_logs_route(State(state): State<Arc<StorageStats>>) -> Response {
    match tokio::task::spawn_blocking(move || {
        state.clean(|state| clean_logs(&state.data_root, &state.logs_dir))
    })
    .await
    {
        Ok(result) => result.into_response(),
        Err(join) => error(StatusCode::INTERNAL_SERVER_ERROR, &join.to_string()),
    }
}

async fn clean_backups_route(
    State(state): State<Arc<StorageStats>>,
    body: Result<Json<Value>, JsonRejection>,
) -> Response {
    let days = match keep_days(
        body.ok()
            .and_then(|Json(body)| body.get("keep_days").cloned()),
    ) {
        Ok(value) => value,
        Err(reason) => return error(StatusCode::BAD_REQUEST, reason),
    };
    match tokio::task::spawn_blocking(move || {
        state.clean(|state| clean_backups(&state.data_root, days))
    })
    .await
    {
        Ok(result) => result.into_response(),
        Err(join) => error(StatusCode::INTERNAL_SERVER_ERROR, &join.to_string()),
    }
}

fn keep_days(value: Option<Value>) -> Result<u32, &'static str> {
    const INVALID: &str = "invalid keep_days";
    const OUT_OF_RANGE: &str = "keep_days out of range";
    // Match the old endpoint's integer conversion before applying the range.
    let number = match value {
        Some(Value::Bool(value)) => f64::from(u32::from(value)),
        Some(Value::Number(value)) => value.as_f64().ok_or(INVALID)?.trunc(),
        Some(Value::String(value)) => {
            let value = value.trim();
            let digits = value.strip_prefix(['+', '-']).unwrap_or(value);
            if digits
                .split('_')
                .any(|part| part.is_empty() || !part.bytes().all(|c| c.is_ascii_digit()))
            {
                return Err(INVALID);
            }
            match value.replace('_', "").parse::<i64>() {
                Ok(value) => value as f64,
                Err(_) => return Err(OUT_OF_RANGE),
            }
        }
        _ => return Err(INVALID),
    };
    if (1.0..=3650.0).contains(&number) {
        Ok(number as u32)
    } else {
        Err(OUT_OF_RANGE)
    }
}
