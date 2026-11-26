use super::*;

fn workspace(id: &str, path: &str) -> RegisteredWorkspace {
    RegisteredWorkspace {
        id: id.into(),
        label: id.into(),
        path: path.into(),
    }
}

#[test]
fn scan_failure_preserves_only_the_same_workspaces_last_good_snapshot() {
    let storage = Storage {
        registry: PathBuf::new(),
        resources: PathBuf::new(),
        data: PathBuf::new(),
        limit_mb: 1,
        ttl_seconds: 0.0,
        cache: Mutex::default(),
    };
    let original = workspace("first", "/fixture/first");
    let replacement = workspace("first", "/fixture/replaced");
    let unrelated = workspace("other", "/fixture/other");
    let failed_scan = |_: &Path| Err(io::Error::other("fixture scan failed"));
    let (status, Json(first)) = storage.measure(&original, |_| Ok(2 * 1024 * 1024));
    assert_eq!(status, StatusCode::OK);
    assert_eq!(
        first["data"],
        json!({
            "used_bytes": 2 * 1024 * 1024,
            "limit_bytes": 1024 * 1024,
            "limit_label": "1MB",
            "usage_percent": 200.0,
        })
    );
    let (status, Json(stale)) = storage.measure(&original, failed_scan);
    assert_eq!(status, StatusCode::OK);
    assert_eq!(
        stale,
        json!({"success": true, "data": first["data"], "stale": true})
    );
    for workspace in [&replacement, &unrelated] {
        let (status, Json(failed)) = storage.measure(workspace, failed_scan);
        assert_eq!(status, StatusCode::INTERNAL_SERVER_ERROR);
        assert_eq!(
            failed,
            json!({"success": false, "error": "fixture scan failed"})
        );
    }
    let (status, Json(rebuilt)) = storage.measure(&original, |_| Ok(1024));
    assert_eq!(status, StatusCode::OK);
    assert_eq!(rebuilt["data"]["used_bytes"], 1024);
    assert!(rebuilt.get("stale").is_none());
}
