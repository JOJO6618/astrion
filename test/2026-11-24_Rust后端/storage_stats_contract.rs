use std::{
    fs::{self, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
    sync::mpsc,
    time::{Duration, SystemTime},
};

use astrion_headless::storage_stats;
use axum::{
    Router,
    body::{Body, to_bytes},
    http::{Method, Request, StatusCode},
};
use cap_fs_ext::{DirExt, SystemTimeSpec};
use cap_std::{ambient_authority, fs::Dir};
use jiff::{SignedDuration, Timestamp, tz::TimeZone};
use serde_json::{Value, json};
use tower::ServiceExt;
use uuid::Uuid;

const STATS: &str = "/api/storage-stats";
const REFRESH: &str = "/api/storage-stats/refresh";
const LOGS: &str = "/api/storage-stats/clean/logs";
const BACKUPS: &str = "/api/storage-stats/clean/backups";
const BUCKETS: [&str; 8] = [
    "backups",
    "logs",
    "mcp_profiles",
    "agent_conversations",
    "media",
    "sub_agents",
    "chat",
    "other",
];

struct Fixture {
    root: PathBuf,
    data: PathBuf,
    logs: PathBuf,
}

impl Fixture {
    fn new() -> Self {
        let source = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join(file!())
            .canonicalize()
            .unwrap();
        let root = source
            .parent()
            .unwrap()
            .join(format!("storage_stats_tmp_{}", Uuid::new_v4().simple()));
        fs::create_dir(&root).unwrap();
        let data = root.join("data");
        let logs = root.join("logs");
        fs::create_dir(&data).unwrap();
        fs::create_dir(&logs).unwrap();
        Self { root, data, logs }
    }

    fn app(&self) -> Router {
        storage_stats::router(self.data.clone(), self.logs.clone())
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        if let Err(error) = fs::remove_dir_all(&self.root) {
            eprintln!("could not remove storage statistics fixture: {error}");
        }
    }
}

fn write(path: &Path, file: &str, contents: impl AsRef<[u8]>) -> PathBuf {
    let path = path.join(file);
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(&path, contents).unwrap();
    path
}

fn set_mtime(path: &Path, days_ago: u64) {
    let age = SystemTime::now() - Duration::from_secs(days_ago * 86400);
    let parent = Dir::open_ambient_dir(path.parent().unwrap(), ambient_authority()).unwrap();
    parent
        .set_mtime(
            path.file_name().unwrap(),
            SystemTimeSpec::Absolute(cap_std::time::SystemTime::from_std(age)),
        )
        .unwrap();
}

fn bytes(path: &Path) -> u64 {
    let entry = fs::metadata(path).unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if entry.blocks() > 0 {
            return entry.blocks() * 512;
        }
    }
    entry.len()
}

async fn request(
    app: &Router,
    method: Method,
    route: &str,
    body: Option<&str>,
) -> (StatusCode, Value) {
    let mut request = Request::builder().method(method).uri(route);
    if body.is_some() {
        request = request.header("content-type", "application/json");
    }
    let response = app
        .clone()
        .oneshot(
            request
                .body(Body::from(body.unwrap_or_default().to_owned()))
                .unwrap(),
        )
        .await
        .unwrap();
    let status = response.status();
    let body = to_bytes(response.into_body(), 1024 * 1024).await.unwrap();
    (status, serde_json::from_slice(&body).unwrap())
}

async fn get(app: &Router) -> Value {
    let (status, body) = request(app, Method::GET, STATS, None).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["success"], true);
    body["data"].clone()
}

async fn post(app: &Router, path: &str, body: Option<&str>) -> Value {
    let (status, response) = request(app, Method::POST, path, body).await;
    assert_eq!(status, StatusCode::OK, "{response}");
    assert_eq!(response["success"], true);
    response["data"].clone()
}

async fn wait_for_scan(app: &Router, baseline: &Value) -> Value {
    tokio::time::timeout(Duration::from_secs(8), async {
        loop {
            let data = get(app).await;
            if data["scanning"] == false
                && !data["scanned_at"].is_null()
                && &data["scanned_at"] != baseline
            {
                return data;
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .expect("storage scan completed")
}

#[tokio::test]
async fn first_get_populates_all_buckets_and_python_cache() {
    let fixture = Fixture::new();
    let paths = [
        write(&fixture.data, "save/conv_1/snapshots/1.json", vec![0; 7000]),
        write(&fixture.logs, "nested/server.log", b"server log"),
        write(&fixture.data, "mcp_profiles/mcp/State", b"browser profile"),
        write(&fixture.data, "conversations/ws/agent.json", b"agent"),
        write(
            &fixture.data,
            "conversations/media_store/photo.png",
            b"media",
        ),
        write(&fixture.data, "sub_agent_runtime.json", b"runtime"),
        write(&fixture.data, "chat/ws/chat.json", b"chat"),
        write(&fixture.data, "api_costs/file.json", b"costs"),
    ];
    let empty_backup = fixture.data.join("save/conv_empty");
    fs::create_dir(&empty_backup).unwrap();
    write(
        &fixture.root,
        "worktree/.astrion/save/conv_external/backup.json",
        vec![0; 10000],
    );
    let app = fixture.app();
    let first = get(&app).await;
    assert_eq!(
        first,
        json!({
            "available": true, "platform": null,
            "root": fixture.data, "logs_root": fixture.logs,
            "scanned_at": null, "duration_ms": 0, "total_bytes": 0,
            "categories": [], "backups": [], "scanning": true,
        })
    );
    let data = wait_for_scan(&app, &Value::Null).await;
    assert_eq!(data["root"], json!(fixture.data));
    assert_eq!(data["logs_root"], json!(fixture.logs));
    assert_eq!(
        data["platform"],
        match std::env::consts::OS {
            "macos" => "darwin",
            "windows" => "win32",
            other => other,
        }
    );
    Timestamp::strptime(
        "%Y-%m-%dT%H:%M:%S%.9f%:z",
        data["scanned_at"].as_str().unwrap(),
    )
    .unwrap();
    let categories = data["categories"].as_array().unwrap();
    let sum: u64 = paths.iter().map(|path| bytes(path)).sum();
    assert_eq!(data["total_bytes"], sum);
    assert_eq!(categories.len(), 8);
    for ((category, expected), path) in categories.iter().zip(BUCKETS).zip(paths.iter()) {
        assert_eq!(
            *category,
            json!({"id": expected, "bytes": bytes(path), "files": 1})
        );
    }
    assert_eq!(data["backups"][0]["id"], "conv_1");
    assert_eq!(data["backups"][0]["bytes"], bytes(&paths[0]));
    assert_eq!(data["backups"][1]["id"], "conv_empty");
    assert_eq!(data["backups"][1]["bytes"], 0);
    let cache: Value =
        serde_json::from_slice(&fs::read(fixture.data.join("storage_stats.json")).unwrap())
            .unwrap();
    assert_eq!(cache["version"], 1);
    assert_eq!(cache["scanned_at"], data["scanned_at"]);
    assert_eq!(cache["data_root"], json!(fixture.data));
    assert!(cache.get("root").is_none());
    assert_eq!(get(&fixture.app()).await, data);
}

#[tokio::test]
async fn version_guard_and_legacy_snapshot() {
    let fixture = Fixture::new();
    let cache = write(&fixture.data, "storage_stats.json", br#"{"version":1,"scanned_at":"2026-11-15T03:00:00+08:00","total_bytes":123,"categories":[]}"#);
    let app = fixture.app();
    let legacy = get(&app).await;
    assert_eq!(legacy["scanning"], false);
    assert_eq!(legacy["scanned_at"], "2026-11-15T03:00:00+08:00");
    assert_eq!(legacy["total_bytes"], 123);
    assert_eq!(legacy["backups"], json!([]));
    assert_eq!(legacy["duration_ms"], 0);
    assert_eq!(legacy["root"], Value::Null);

    for bad in [
        br#"{"version":999,"scanned_at":"invalid"}"#.as_slice(),
        b"{",
    ] {
        fs::write(&cache, bad).unwrap();
        let refreshing = get(&app).await;
        assert_eq!(refreshing["scanned_at"], Value::Null);
        assert_eq!(refreshing["scanning"], true);
        wait_for_scan(&app, &Value::Null).await;
    }
}

#[test]
fn concurrent_refresh_requests_use_one_worker() {
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_time()
        .max_blocking_threads(1)
        .build()
        .unwrap();
    runtime.block_on(async {
        let fixture = Fixture::new();
        let app = fixture.app();
        let (started_tx, started_rx) = tokio::sync::oneshot::channel();
        let (release_tx, release_rx) = mpsc::channel();
        let worker = tokio::task::spawn_blocking(move || {
            started_tx.send(()).unwrap();
            release_rx.recv_timeout(Duration::from_secs(5)).unwrap();
        });
        started_rx.await.unwrap();
        assert_eq!(
            post(&app, REFRESH, Some("{}")).await,
            json!({"started": true, "scanning": true})
        );
        assert_eq!(
            post(&app, REFRESH, Some("{}")).await,
            json!({"started": false, "scanning": true})
        );
        assert_eq!(
            post(&app, REFRESH, None).await,
            json!({"started": false, "scanning": true})
        );
        release_tx.send(()).unwrap();
        worker.await.unwrap();
        let first = wait_for_scan(&app, &Value::Null).await;
        assert_eq!(
            post(&app, REFRESH, None).await,
            json!({"started": true, "scanning": true})
        );
        wait_for_scan(&app, &first["scanned_at"]).await;
    });
}

#[tokio::test]
async fn cleaning_logs_keeps_open_writer_and_updates_cached_totals() {
    let fixture = Fixture::new();
    let active = write(&fixture.logs, "active.log", vec![0; 2500]);
    let old = write(&fixture.logs, "active.log.12", vec![0; 2000]);
    let debug = write(&fixture.logs, "debug/request.json", vec![0; 100]);
    let protected = write(&fixture.data, "chat/chat.log", b"keep chat history");
    let mut writer = OpenOptions::new().append(true).open(&active).unwrap();
    let app = fixture.app();
    let before = wait_for_scan(&app, &Value::Null).await;
    let total = [&active, &old, &debug]
        .into_iter()
        .map(|path| bytes(path))
        .sum::<u64>();
    assert_eq!(before["categories"][1]["bytes"], total);
    assert_eq!(
        post(&app, LOGS, None).await,
        json!({
            "freed_bytes": total, "removed": 2, "truncated": 1, "failed": []
        })
    );
    assert_eq!(fs::metadata(&active).unwrap().len(), 0);
    assert!(!old.exists());
    assert!(!debug.exists());
    assert_eq!(fs::read(&protected).unwrap(), b"keep chat history");
    let after = wait_for_scan(&app, &before["scanned_at"]).await;
    assert_eq!(
        after["categories"][1],
        json!({"id":"logs", "bytes": 0, "files": 1})
    );
    writer
        .write_all(b"continues via the same handle\n")
        .unwrap();
    writer.flush().unwrap();
    assert_eq!(
        fs::read(&active).unwrap(),
        b"continues via the same handle\n"
    );
}

fn backup(fixture: &Fixture, name: &str, metadata: Option<&str>, mtime_days: u64) -> PathBuf {
    let dir = fixture.data.join("save").join(name);
    write(&dir, "snapshots/1.json", b"snapshot");
    if let Some(metadata) = metadata {
        write(&dir, "meta.json", metadata);
    }
    set_mtime(&dir, mtime_days);
    dir
}

#[tokio::test]
async fn backup_age_uses_metadata_with_local_or_explicit_offset_and_falls_back() {
    let fixture = Fixture::new();
    let boundary = Timestamp::now() - SignedDuration::from_hours(30 * 24);
    let zone = TimeZone::get("Asia/Tokyo").unwrap();
    let old_offset = (boundary - SignedDuration::from_hours(2))
        .to_zoned(zone.clone())
        .strftime("%Y-%m-%dT%H:%M:%S%:z")
        .to_string();
    let fresh_offset = (boundary + SignedDuration::from_hours(2))
        .to_zoned(zone)
        .strftime("%Y-%m-%dT%H:%M:%S%:z")
        .to_string();
    let local_old = (boundary - SignedDuration::from_hours(2))
        .to_zoned(TimeZone::system())
        .strftime("%Y-%m-%d %H:%M:%S%.6f")
        .to_string();
    let with_date = |date: &str| json!({"updated_at": date}).to_string();
    let old = backup(&fixture, "conv_old", Some(&with_date(&old_offset)), 0);
    let local = backup(&fixture, "conv_local", Some(&with_date(&local_old)), 0);
    let fallback = backup(&fixture, "conv_fallback", None, 40);
    let invalid = backup(
        &fixture,
        "conv_invalid",
        Some(r#"{"updated_at":"invalid"}"#),
        40,
    );
    let fresh = backup(&fixture, "conv_fresh", Some(&with_date(&fresh_offset)), 40);
    let unrelated = backup(&fixture, "other", Some(&with_date(&old_offset)), 40);
    let nested = backup(
        &fixture,
        "other/conv_nested",
        Some(&with_date(&old_offset)),
        40,
    );
    let regular = write(
        &fixture.data,
        "save/conv_regular",
        b"do not remove the regular file",
    );
    let app = fixture.app();
    let before = wait_for_scan(&app, &Value::Null).await;
    let entries = before["backups"].as_array().unwrap();
    assert_eq!(entries.len(), 5);
    for (name, date) in [
        ("conv_old", &old_offset),
        ("conv_fresh", &fresh_offset),
        ("conv_local", &local_old),
    ] {
        assert_eq!(
            entries.iter().find(|value| value["id"] == name).unwrap()["updated_at"],
            *date
        );
    }
    for name in ["conv_fallback", "conv_invalid"] {
        let entry = entries.iter().find(|entry| entry["id"] == name).unwrap();
        assert!(
            entry["updated_at"]
                .as_str()
                .unwrap()
                .parse::<Timestamp>()
                .unwrap()
                < boundary
        );
    }
    let expected_freed: u64 = ["conv_old", "conv_local", "conv_fallback", "conv_invalid"]
        .into_iter()
        .map(|name| {
            entries.iter().find(|entry| entry["id"] == name).unwrap()["bytes"]
                .as_u64()
                .unwrap()
        })
        .sum();
    let data = post(&app, BACKUPS, Some(r#"{"keep_days":"30"}"#)).await;
    assert_eq!(data["removed_count"], 4);
    assert_eq!(data["keep_days"], 30);
    assert_eq!(data["freed_bytes"], expected_freed);
    assert_eq!(data["failed"], json!([]));
    let mut removed = data["removed"]
        .as_array()
        .unwrap()
        .iter()
        .map(|name| name.as_str().unwrap())
        .collect::<Vec<_>>();
    removed.sort_unstable();
    assert_eq!(
        removed,
        ["conv_fallback", "conv_invalid", "conv_local", "conv_old"]
    );
    for path in [old, local, fallback, invalid] {
        assert!(!path.exists());
    }
    for path in [fresh, unrelated, nested, regular] {
        assert!(path.exists());
    }
    let after = wait_for_scan(&app, &before["scanned_at"]).await;
    assert_eq!(after["backups"].as_array().unwrap().len(), 1);
    assert_eq!(after["backups"][0]["id"], "conv_fresh");
}

#[tokio::test]
async fn keep_days_errors_and_non_host_contract() {
    let fixture = Fixture::new();
    let protected = backup(
        &fixture,
        "conv_1",
        Some(r#"{"updated_at":"2000-01-02T00:00:00Z"}"#),
        0,
    );
    let app = fixture.app();
    for (input, expected) in [
        (None, "invalid keep_days"),
        (Some("{}"), "invalid keep_days"),
        (Some("{"), "invalid keep_days"),
        (Some(r#"{"keep_days":"1.5"}"#), "invalid keep_days"),
        (Some(r#"{"keep_days":null}"#), "invalid keep_days"),
        (Some(r#"{"keep_days":0}"#), "keep_days out of range"),
        (Some(r#"{"keep_days":3651}"#), "keep_days out of range"),
        (
            Some(r#"{"keep_days":"999999999999999999999999999999"}"#),
            "keep_days out of range",
        ),
    ] {
        let (status, body) = request(&app, Method::POST, BACKUPS, input).await;
        assert_eq!(status, StatusCode::BAD_REQUEST);
        assert_eq!(body, json!({"success": false, "error": expected}));
    }
    assert!(protected.exists());
    assert!(!fixture.data.join("storage_stats.json").exists());
    let non_host = storage_stats::unsupported_router();
    for (method, route) in [
        (Method::GET, STATS),
        (Method::POST, REFRESH),
        (Method::POST, LOGS),
        (Method::POST, BACKUPS),
    ] {
        let (status, body) = request(&non_host, method, route, None).await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(
            body,
            json!({"success": true, "data": {"available": false, "reason":"host_only"}})
        );
    }
    assert!(protected.exists());
}

#[tokio::test]
async fn overlapping_roots_count_logs_once_and_preserve_the_data_subtree() {
    let fixture = Fixture::new();
    let data_file = write(&fixture.data, "chat/chat.json", b"chat");
    let nested = write(&fixture.data, "logs/service.log", b"logs");
    let app = storage_stats::router(fixture.data.clone(), nested.parent().unwrap().into());
    let snapshot = wait_for_scan(&app, &Value::Null).await;
    assert_eq!(snapshot["total_bytes"], bytes(&nested) + bytes(&data_file));
    assert_eq!(snapshot["categories"][1]["files"], 1);
    assert_eq!(snapshot["categories"][6]["files"], 1);
    assert_eq!(snapshot["categories"][7]["files"], 0);

    let unrelated = write(&fixture.root, "workspace/outside.log", b"workspace");
    let app = storage_stats::router(fixture.data.clone(), fixture.root.clone());
    assert_eq!(
        post(&app, LOGS, None).await,
        json!({ "freed_bytes": 0, "removed": 0, "truncated": 0, "failed": [] })
    );
    let after = wait_for_scan(&app, &snapshot["scanned_at"]).await;
    assert_eq!(after["categories"][1]["files"], 0);
    assert_eq!(fs::read(&data_file).unwrap(), b"chat");
    assert_eq!(fs::read(nested).unwrap(), b"logs");
    assert_eq!(fs::read(unrelated).unwrap(), b"workspace");

    let mut previous = after;
    for logs in [fixture.data.join("chat"), fixture.data.join("CHAT")]
        .into_iter()
        .filter(|path| path.is_dir())
    {
        let app = storage_stats::router(fixture.data.clone(), logs);
        assert_eq!(
            post(&app, LOGS, None).await,
            json!({ "freed_bytes": 0, "removed": 0, "truncated": 0, "failed": [] })
        );
        previous = wait_for_scan(&app, &previous["scanned_at"]).await;
        assert_eq!(previous["categories"][1]["files"], 0);
        assert_eq!(previous["categories"][6]["files"], 1);
        assert_eq!(fs::read(&data_file).unwrap(), b"chat");
    }
}

#[cfg(unix)]
#[tokio::test]
async fn symlinks_and_hard_linked_active_logs_cannot_touch_outside_files() {
    use std::os::unix::fs::symlink;

    let fixture = Fixture::new();
    let outside = write(&fixture.root, "outside/keep.log", b"outside log contents");
    let outside_dir = outside.parent().unwrap();
    let outside_backup = write(
        outside_dir,
        "conv_external/meta.json",
        br#"{"updated_at":"2000-01-01T00:00:00Z"}"#,
    );
    let active_link = fixture.logs.join("active.log");
    symlink(&outside, &active_link).unwrap();
    symlink(outside_dir, fixture.logs.join("nested")).unwrap();
    let hard_link = fixture.logs.join("hard-linked.log");
    fs::hard_link(&outside, &hard_link).unwrap();
    fs::create_dir(fixture.data.join("save")).unwrap();
    symlink(
        outside_backup.parent().unwrap(),
        fixture.data.join("save/conv_link"),
    )
    .unwrap();
    let old = backup(
        &fixture,
        "conv_old",
        Some(r#"{"updated_at":"2000-01-01T00:00:00Z"}"#),
        0,
    );
    symlink(outside_dir, old.join("dir-link")).unwrap();
    symlink(&outside, old.join("file-link")).unwrap();
    let safe = backup(&fixture, "conv_with_meta_link", None, 0);
    symlink(&outside_backup, safe.join("meta.json")).unwrap();
    let app = fixture.app();
    let snapshot = wait_for_scan(&app, &Value::Null).await;
    assert_eq!(snapshot["categories"][1]["files"], 1);
    assert_eq!(snapshot["backups"].as_array().unwrap().len(), 2);
    let cleaned = post(&app, LOGS, None).await;
    assert_eq!(cleaned["removed"], 0);
    assert_eq!(cleaned["truncated"], 0);
    assert_eq!(cleaned["failed"].as_array().unwrap().len(), 1);
    assert_eq!(cleaned["failed"][0]["path"], json!(hard_link));
    let rescanned = wait_for_scan(&app, &snapshot["scanned_at"]).await;
    assert_eq!(
        post(&app, BACKUPS, Some(r#"{"keep_days":1}"#)).await["removed"],
        json!(["conv_old"])
    );
    wait_for_scan(&app, &rescanned["scanned_at"]).await;
    assert!(!old.exists());
    assert!(safe.is_dir());
    assert!(fs::symlink_metadata(active_link).unwrap().is_symlink());
    assert_eq!(fs::read(outside).unwrap(), b"outside log contents");
    assert!(outside_backup.exists());
}

#[cfg(unix)]
#[tokio::test]
async fn symlinked_log_and_save_roots_are_skipped() {
    use std::os::unix::fs::symlink;

    let fixture = Fixture::new();
    let outside_log = write(&fixture.root, "outside-logs/server.log", b"outside");
    let outside_backup = write(
        &fixture.root,
        "outside-save/conv_1/meta.json",
        br#"{"updated_at":"2000-01-01T00:00:00Z"}"#,
    );
    fs::remove_dir(&fixture.logs).unwrap();
    symlink(outside_log.parent().unwrap(), &fixture.logs).unwrap();
    symlink(
        outside_backup.parent().unwrap().parent().unwrap(),
        fixture.data.join("save"),
    )
    .unwrap();
    let app = fixture.app();
    let before = wait_for_scan(&app, &Value::Null).await;
    assert_eq!(before["total_bytes"], 0);
    assert_eq!(before["backups"], json!([]));
    assert_eq!(
        post(&app, LOGS, None).await,
        json!({ "freed_bytes": 0, "removed": 0, "truncated": 0, "failed": [] })
    );
    let after_logs = wait_for_scan(&app, &before["scanned_at"]).await;
    let cleaned = post(&app, BACKUPS, Some(r#"{"keep_days":1}"#)).await;
    assert_eq!(
        cleaned,
        json!({"freed_bytes": 0, "removed_count": 0, "removed": [], "failed": [], "keep_days": 1})
    );
    wait_for_scan(&app, &after_logs["scanned_at"]).await;
    assert_eq!(fs::read(outside_log).unwrap(), b"outside");
    assert!(outside_backup.exists());
}

#[tokio::test]
async fn first_scan_creates_only_the_missing_data_root_and_cache() {
    let fixture = Fixture::new();
    let missing_data = fixture.root.join("new-data/nested");
    let missing_logs = fixture.root.join("new-logs/nested");
    let app = storage_stats::router(missing_data.clone(), missing_logs.clone());
    let snapshot = wait_for_scan(&app, &Value::Null).await;
    assert_eq!(snapshot["total_bytes"], 0);
    assert!(missing_data.join("storage_stats.json").is_file());
    assert!(!missing_logs.parent().unwrap().exists());
}

#[cfg(unix)]
#[test]
fn symlinked_ancestors_block_cleanup_and_cache_creation() {
    use std::os::unix::fs::symlink;

    let fixture = Fixture::new();
    let external = fixture.root.join("external");
    let log = write(&external, "logs/active.log", b"outside log");
    let backup = write(
        &external,
        "data/save/conv_old/meta.json",
        br#"{"updated_at":"2000-01-01T00:00:00Z"}"#,
    );
    let alias = fixture.root.join("alias");
    symlink(&external, &alias).unwrap();
    {
        let runtime = tokio::runtime::Builder::new_current_thread()
            .build()
            .unwrap();
        runtime.block_on(async {
            let app = storage_stats::router(alias.join("data"), alias.join("logs"));
            let logs = post(&app, LOGS, None).await;
            assert_eq!(logs["truncated"], 0);
            assert_eq!(logs["failed"], json!([]));
            let backups = post(&app, BACKUPS, Some(r#"{"keep_days":1}"#)).await;
            assert_eq!(backups["removed_count"], 0);
            assert_eq!(backups["failed"], json!([]));
            let missing = storage_stats::router(alias.join("new/data"), fixture.logs.clone());
            assert_eq!(post(&missing, REFRESH, None).await["started"], true);
        });
        // Dropping the runtime joins its blocking scan workers before checking.
    }
    assert_eq!(fs::read(log).unwrap(), b"outside log");
    assert!(backup.exists());
    assert!(!external.join("data/storage_stats.json").exists());
    assert!(!external.join("new").exists());
}

#[tokio::test]
#[ignore = "set ASTRION_STORAGE_STATS_PYTHON to an interpreter with Flask"]
async fn matches_python_storage_blueprint_on_the_same_fixture() {
    let fixture = Fixture::new();
    write(&fixture.logs, "main.log", b"active log");
    write(&fixture.logs, "main.log.1", b"archive");
    write(&fixture.logs, "nested/debug.json", b"log dump");
    write(
        &fixture.data,
        "conversations/ws/agent.json",
        b"agent history",
    );
    write(
        &fixture.data,
        "conversations/media_store/photo.png",
        b"media",
    );
    write(&fixture.data, "chat/a.json", b"chat history");
    write(
        &fixture.data,
        "mcp_profiles/profile/State",
        b"browser profile",
    );
    write(&fixture.data, "sub_agents.json", b"child state");
    write(&fixture.data, "settings.json", b"settings");
    backup(
        &fixture,
        "conv_old",
        Some(r#"{"updated_at":"2000-01-02T03:04:05+08:00"}"#),
        0,
    );
    backup(
        &fixture,
        "conv_fallback",
        Some(r#"{"updated_at":"2020-02-30T00:00:00"}"#),
        45,
    );
    backup(
        &fixture,
        "conv_new",
        Some(r#"{"updated_at":"2100-01-01T00:00:00.123456"}"#),
        45,
    );
    backup(
        &fixture,
        "conv_basic_date",
        Some(r#"{"updated_at":"20000102X03:04:05+083000.2"}"#),
        0,
    );
    backup(
        &fixture,
        "conv_week_date",
        Some(r#"{"updated_at":"2100-W01"}"#),
        45,
    );
    backup(
        &fixture,
        "conv_invalid_offset",
        Some(r#"{"updated_at":"2000-01-02T00:00:00+29:00"}"#),
        0,
    );

    let python =
        std::env::var_os("ASTRION_STORAGE_STATS_PYTHON").unwrap_or_else(|| "python3".into());
    let output = std::process::Command::new(python)
        .arg("-B")
        .arg(
            fixture
                .root
                .parent()
                .unwrap()
                .join("storage_stats_python_oracle.py"),
        )
        .arg(&fixture.root)
        .output()
        .expect("launch Python storage oracle");
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let mut expected: Value = serde_json::from_slice(&output.stdout).unwrap();

    let app = fixture.app();
    let (_, first) = request(&app, Method::GET, STATS, None).await;
    let snapshot = wait_for_scan(&app, &Value::Null).await;
    let (_, refresh) = request(&app, Method::POST, REFRESH, None).await;
    let refreshed = wait_for_scan(&app, &snapshot["scanned_at"]).await;
    let log = fixture.logs.join("main.log");
    let mut writer = OpenOptions::new().append(true).open(&log).unwrap();
    let (_, clean_logs) = request(&app, Method::POST, LOGS, None).await;
    writer.write_all(b"same-handle append").unwrap();
    drop(writer);
    let logs_cleaned = wait_for_scan(&app, &refreshed["scanned_at"]).await;
    let (_, clean_backups) =
        request(&app, Method::POST, BACKUPS, Some(r#"{"keep_days":"30"}"#)).await;
    wait_for_scan(&app, &logs_cleaned["scanned_at"]).await;
    let (bad_status, bad_days) =
        request(&app, Method::POST, BACKUPS, Some(r#"{"keep_days":0}"#)).await;
    let (_, unsupported) = request(
        &storage_stats::unsupported_router(),
        Method::GET,
        STATS,
        None,
    )
    .await;
    let mut actual = json!({
        "first": first,
        "stats": {"success": true, "data": snapshot},
        "refresh": refresh,
        "clean_logs": clean_logs,
        "clean_backups": clean_backups,
        "bad_days": [bad_status.as_u16(), bad_days],
        "unsupported": unsupported,
        "continued_log": fs::read_to_string(log).unwrap(),
    });
    for document in [&mut actual, &mut expected] {
        for key in ["first", "stats"] {
            let data = document[key]["data"].as_object_mut().unwrap();
            for field in ["root", "logs_root", "scanned_at", "duration_ms"] {
                data.remove(field);
            }
            let entries = data.get_mut("backups").unwrap().as_array_mut().unwrap();
            entries.sort_by(|left, right| left["id"].as_str().cmp(&right["id"].as_str()));
        }
        document["clean_backups"]["data"]["removed"]
            .as_array_mut()
            .unwrap()
            .sort_by(|left, right| left.as_str().cmp(&right.as_str()));
    }
    assert_eq!(actual, expected);
}
