use std::{
    fs,
    path::{Path, PathBuf},
    sync::{
        Arc,
        atomic::{AtomicUsize, Ordering},
    },
};

use axum::{
    Router,
    body::Body,
    extract::{Request, State},
    http::StatusCode,
    response::Response,
};
use serde_json::Value;
use tokio::{net::TcpListener, task::JoinHandle};
use uuid::Uuid;

pub(crate) struct Scratch(PathBuf);

impl Scratch {
    pub(crate) fn new() -> Self {
        let root = std::env::temp_dir().join(format!("astrion-usage-test-{}", Uuid::new_v4()));
        fs::create_dir(&root).unwrap();
        Self(root)
    }

    pub(crate) fn root(&self) -> &Path {
        &self.0
    }

    pub(crate) fn path(&self, path: &str) -> PathBuf {
        self.0.join(path)
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

pub(crate) fn put(path: &Path, value: Value) {
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(path, serde_json::to_vec(&value).unwrap()).unwrap();
}

pub(crate) fn read(path: &Path) -> Value {
    serde_json::from_slice(&fs::read(path).unwrap()).unwrap()
}

pub(crate) struct MockSource {
    pub(crate) url: String,
    calls: Arc<AtomicUsize>,
    server: JoinHandle<()>,
}

impl MockSource {
    pub(crate) async fn start(status: StatusCode, value: Value) -> Self {
        let calls = Arc::new(AtomicUsize::new(0));
        let state = (calls.clone(), status, value.to_string());
        let router = Router::new().fallback(mock_response).with_state(state);
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let url = format!("http://{}/reference", listener.local_addr().unwrap());
        let server = tokio::spawn(async move { axum::serve(listener, router).await.unwrap() });
        Self { url, calls, server }
    }

    pub(crate) fn calls(&self) -> usize {
        self.calls.load(Ordering::SeqCst)
    }
}

impl Drop for MockSource {
    fn drop(&mut self) {
        self.server.abort();
    }
}

async fn mock_response(
    State((calls, status, payload)): State<(Arc<AtomicUsize>, StatusCode, String)>,
    request: Request,
) -> Response {
    assert!(request.headers().get("authorization").is_none());
    assert_eq!(request.uri().path(), "/reference");
    calls.fetch_add(1, Ordering::SeqCst);
    Response::builder()
        .status(status)
        .header("content-type", "application/json")
        .body(Body::from(payload))
        .unwrap()
}
