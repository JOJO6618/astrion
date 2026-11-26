use super::*;
use axum::{
    Router,
    http::{StatusCode, Uri},
    routing::get,
};
use reqwest::Client;
use std::{
    path::PathBuf,
    sync::atomic::{AtomicUsize, Ordering as AtomicOrdering},
};

#[test]
fn force_and_version_match_the_route_contract() {
    assert!(force_refresh("asset=other&force=%31"));
    assert!(!force_refresh("force=0&force=1"));
    assert!(!force_refresh("force=true"));
    assert!(newer_version("v0.10.00", "0.9.99"));
    assert!(newer_version("0.9.4.0", "0.9.4"));
    assert!(newer_version("0.99999999999999999999999999", "0.10"));
    assert!(!newer_version("0.9.4", "0.9.4.0"));
    assert!(!newer_version("v02.0.0", "2.0.0"));
}

#[tokio::test]
async fn check_uses_build_identity_policy_and_success_cache() {
    let seen = Arc::new(Mutex::new(Vec::new()));
    let manifest_calls = Arc::new(AtomicUsize::new(0));
    let mock = Router::new()
        .route("/update/policy", get({
            let seen = seen.clone();
            move |uri: Uri| {
                let seen = seen.clone();
                async move {
                    seen.lock().await.push(uri.to_string());
                    let delivery = if uri.query() == Some("force=1") { "manual" } else { "automatic" };
                    Json(json!({"policy": {"delivery": delivery, "latest_version": "0.9.1", "required": false}}))
                }
            }
        }))
        .route("/manifest", get({
            let seen = seen.clone();
            let manifest_calls = manifest_calls.clone();
            move |uri: Uri| {
                let seen = seen.clone();
                let manifest_calls = manifest_calls.clone();
                async move {
                    seen.lock().await.push(uri.to_string());
                    match manifest_calls.fetch_add(1, AtomicOrdering::Relaxed) {
                        0 | 2 => (StatusCode::SERVICE_UNAVAILABLE, Json(json!({"error":"fixture unavailable"}))),
                        _ => (StatusCode::OK, Json(json!({"version":"0.10.0", "notes":"fixture notes", "pub_date":"fixture date"}))),
                    }
                }
            }
        }));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = listener.local_addr().unwrap().port();
    let (stop, stopped) = tokio::sync::oneshot::channel();
    let server = tokio::spawn(async move {
        axum::serve(listener, mock)
            .with_graceful_shutdown(async {
                let _ = stopped.await;
            })
            .await
            .unwrap();
    });
    let client = Client::builder().no_proxy().build().unwrap();
    let bridge = Bridge {
        data: PathBuf::new(),
        desktop: Some(Desktop {
            version: "0.9.0".into(),
            port: Some(port),
            build_id: "fixture build".into(),
            platform: "darwin".into(),
            arch: "arm64".into(),
            policy_protocol: true,
        }),
        local: client.clone(),
        manifest: client,
        update_cache: Cache::default(),
    };
    let desktop = bridge.desktop().unwrap();
    let url = format!("http://127.0.0.1:{port}/manifest");
    let check = |force| check_channel(&bridge, desktop, force, "darwin-aarch64", &url);
    assert!(matches!(
        check(false).await,
        Err(BridgeError::CheckFailed(_))
    ));
    let fresh = check(false).await.unwrap();
    assert!(fresh["data"]["checked_at"].is_u64());
    assert_eq!(fresh["data"]["current_version"], "0.9.0");
    assert_eq!(fresh["data"]["current_build_id"], "fixture build");
    assert_eq!(fresh["data"]["latest_version"], "0.10.0");
    assert_eq!(fresh["data"]["notes"], "fixture notes");
    assert_eq!(fresh["data"]["pub_date"], "fixture date");
    assert_eq!(fresh["data"]["policy"]["latest_version"], "0.9.1");
    assert_eq!(fresh["data"]["update_available"], true);
    assert_eq!(fresh["data"]["bridge_available"], true);
    assert_eq!(fresh["data"]["channel"], "darwin-aarch64");
    assert_eq!(check(false).await.unwrap(), fresh);

    let forced = check(true).await.unwrap();
    assert_eq!(forced["data"]["latest_version"], "0.9.1");
    assert_eq!(forced["data"]["notes"], "");
    assert_eq!(forced["data"]["policy"]["delivery"], "manual");
    assert_eq!(forced["data"]["update_available"], true);
    assert_eq!(check(false).await.unwrap(), forced);

    let manifest =
        "/manifest?current_version=0.9.0&build_id=fixture+build&platform=darwin&arch=arm64";
    assert_eq!(
        &*seen.lock().await,
        &[
            "/update/policy",
            manifest,
            "/update/policy",
            manifest,
            "/update/policy?force=1",
            manifest,
        ]
    );
    stop.send(()).unwrap();
    server.await.unwrap();
}
