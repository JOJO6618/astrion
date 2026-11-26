use std::{
    env, fs,
    path::{Path, PathBuf},
    process::Command,
    sync::Arc,
    time::Duration,
};

use axum::{
    Json, Router, ServiceExt,
    body::{Body, to_bytes},
    extract::{Request, State},
    http::{Method, StatusCode, header::CONTENT_TYPE},
    middleware::{self, Next},
    response::{IntoResponse, Response},
    routing::post,
};
use serde_json::{Value, json};
use tokio::{
    net::TcpListener,
    sync::{Semaphore, mpsc},
    time::{sleep, timeout},
};
use uuid::Uuid;

use crate::{
    catalog::CatalogSource,
    runtime::{ModelConfig, Runtime},
};

use super::{ProjectGit, discovery::short_hash};

const ROOT: &str = "/api/project-git";
const WORKSPACE: &str = "fixture";

struct Fixture {
    temp: PathBuf,
    root: PathBuf,
    data: PathBuf,
}

impl Fixture {
    fn new() -> Self {
        let temp = env::temp_dir().join(format!("project_git_{}", Uuid::new_v4().simple()));
        let root = temp.join("workspace");
        let data = temp.join("data");
        fs::create_dir_all(&root).unwrap();
        fs::create_dir(&data).unwrap();
        Self { temp, root, data }
    }

    fn router(&self) -> Router {
        ProjectGit::new(
            WORKSPACE,
            Some("Fixture workspace".into()),
            &self.root,
            &self.data,
        )
        .unwrap()
        .router()
    }

    fn repository(&self, relative: &str) -> PathBuf {
        let root = self.root.join(relative);
        fs::create_dir_all(&root).unwrap();
        git(&root, &["init", "-b", "main"]);
        git(&root, &["config", "core.autocrlf", "false"]);
        root
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.temp);
    }
}

fn git(repo: &Path, args: &[&str]) -> String {
    let mut git = Command::new("git");
    for (key, _) in env::vars_os() {
        if key.to_string_lossy().starts_with("GIT_") {
            git.env_remove(key);
        }
    }
    let output = git
        .env("GIT_CONFIG_GLOBAL", super::runner::NULL_DEVICE)
        .env("GIT_CONFIG_NOSYSTEM", "1")
        .env("GIT_AUTHOR_NAME", "Fixture")
        .env("GIT_AUTHOR_EMAIL", "fixture@local.invalid")
        .env("GIT_COMMITTER_NAME", "Fixture")
        .env("GIT_COMMITTER_EMAIL", "fixture@local.invalid")
        .env("GIT_TERMINAL_PROMPT", "0")
        .args([
            "-c",
            "commit.gpgsign=false",
            "-c",
            "core.hooksPath=nonexistent",
            "-C",
        ])
        .arg(repo)
        .args(args)
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "git {args:?}: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    String::from_utf8(output.stdout).unwrap().trim().to_owned()
}

fn commit(repo: &Path, subject: &str, body: &str) -> String {
    git(repo, &["add", "-A", "--"]);
    git(
        repo,
        &["commit", "--allow-empty", "-m", subject, "-m", body],
    );
    git(repo, &["rev-parse", "HEAD"])
}

async fn response(router: &Router, method: Method, path: &str, query: &[(&str, &str)]) -> Response {
    send(router, method, path, query, None).await
}

async fn send(
    router: &Router,
    method: Method,
    path: &str,
    query: &[(&str, &str)],
    payload: Option<Value>,
) -> Response {
    async fn route<S>(mut service: S, request: Request) -> Response
    where
        S: ServiceExt<Request, Response = Response, Error = std::convert::Infallible>,
    {
        service.call(request).await.unwrap()
    }
    let mut url = reqwest::Url::parse(&format!("http://localhost{path}")).unwrap();
    url.query_pairs_mut().extend_pairs(query.iter().copied());
    let uri = url.as_str().strip_prefix("http://localhost").unwrap();
    let mut request = Request::builder().method(method).uri(uri);
    let body = if let Some(payload) = payload {
        request = request.header(CONTENT_TYPE, "application/json");
        Body::from(payload.to_string())
    } else {
        Body::empty()
    };
    route(router.clone(), request.body(body).unwrap()).await
}

async fn request(
    router: &Router,
    method: Method,
    path: &str,
    query: &[(&str, &str)],
) -> (StatusCode, Value) {
    read_json_response(response(router, method, path, query).await).await
}

async fn read_json_response(response: Response) -> (StatusCode, Value) {
    let status = response.status();
    let bytes = to_bytes(response.into_body(), 32 * 1024 * 1024)
        .await
        .unwrap();
    let body = serde_json::from_slice(&bytes).unwrap_or_else(|error| {
        panic!(
            "Response {status} {error}: {}",
            String::from_utf8_lossy(&bytes)
        )
    });
    (status, body)
}

async fn get(router: &Router, path: &str, parameters: &[(&str, &str)]) -> Value {
    let mut query = vec![("workspace_id", WORKSPACE)];
    query.extend_from_slice(parameters);
    let (status, body) = request(router, Method::GET, path, &query).await;
    assert_eq!(status, StatusCode::OK, "{path} {query:?}: {body}");
    assert_eq!(body["success"], true);
    body["data"].clone()
}

async fn repo_id(router: &Router, relative: &str) -> String {
    let data = get(router, &format!("{ROOT}/repositories"), &[]).await;
    let row = data["repositories"]
        .as_array()
        .unwrap()
        .iter()
        .find(|row| row["workspaces"][0]["relative_path"] == relative)
        .unwrap_or_else(|| panic!("missing {relative} in {data}"));
    row["id"].as_str().unwrap().to_owned()
}

async fn bad_request(router: &Router, path: &str, query: &[(&str, &str)]) -> String {
    let (status, body) = request(router, Method::GET, path, query).await;
    assert_eq!(status, StatusCode::BAD_REQUEST, "{path} {query:?}: {body}");
    assert_eq!(body["success"], false);
    body["error"].as_str().unwrap().to_owned()
}

#[tokio::test]
async fn repository_discovery_is_bound_to_one_workspace_and_caches_until_refresh() {
    let fixture = Fixture::new();
    let repo = fixture.repository("project");
    fixture.repository("blank");
    fixture.repository("node_modules/hidden");
    let external = fixture.temp.join("external");
    fs::create_dir(&external).unwrap();
    git(&external, &["init", "-b", "main"]);
    let forged = fixture.root.join("forged");
    fs::create_dir(&forged).unwrap();
    fs::write(
        forged.join(".git"),
        format!("gitdir: {}\n", external.join(".git").display()),
    )
    .unwrap();
    #[cfg(unix)]
    std::os::unix::fs::symlink(&external, fixture.root.join("linked")).unwrap();

    let router = fixture.router();
    let route = format!("{ROOT}/repositories");
    bad_request(&router, &route, &[]).await;
    bad_request(&router, &route, &[("workspace_id", "other")]).await;
    let outside = external.to_str().unwrap();
    let first = get(
        &router,
        &route,
        &[("workspace_root", outside), ("data_dir", outside)],
    )
    .await;
    assert_eq!(first["workspaces"][0]["label"], "Fixture workspace");
    assert_eq!(first["repositories"].as_array().unwrap().len(), 2);
    assert_eq!(first["warnings"].as_array().unwrap().len(), 1);
    fixture.repository("added");
    assert_eq!(get(&router, &route, &[]).await, first);
    assert_eq!(
        get(&router, &route, &[("refresh", "1")]).await["repositories"]
            .as_array()
            .unwrap()
            .len(),
        3
    );
    let id = repo_id(&router, "blank").await;
    let history = get(&router, &format!("{ROOT}/{id}/history"), &[]).await;
    assert_eq!(history, json!({"commits": [], "has_more": false}));
    let id = repo_id(&router, "project").await;
    let expected = short_hash(
        super::canonicalize(&repo)
            .unwrap()
            .as_os_str()
            .as_encoded_bytes(),
    );
    assert_eq!(id, expected);
    fs::rename(&repo, fixture.temp.join("moved")).unwrap();
    #[cfg(unix)]
    std::os::unix::fs::symlink(&external, &repo).unwrap();
    bad_request(
        &router,
        &format!("{ROOT}/{id}/branches"),
        &[("workspace_id", WORKSPACE)],
    )
    .await;
    bad_request(
        &router,
        &format!("{ROOT}/unknown/branches"),
        &[("workspace_id", WORKSPACE), ("path", outside)],
    )
    .await;
}

#[tokio::test]
async fn discovers_a_real_linked_worktree_with_its_own_head() {
    let fixture = Fixture::new();
    let primary = fixture.temp.join("primary");
    fs::create_dir(&primary).unwrap();
    git(&primary, &["init", "-b", "main"]);
    let original = commit(&primary, "original", "");
    git(
        &primary,
        &[
            "worktree",
            "add",
            "-b",
            "linked",
            fixture.root.to_str().unwrap(),
        ],
    );
    let linked = commit(&fixture.root, "linked", "");
    let router = fixture.router();
    let id = repo_id(&router, ".").await;
    let branches = get(&router, &format!("{ROOT}/{id}/branches"), &[]).await;
    assert_eq!(branches["current_branch"], "linked");
    let history = get(&router, &format!("{ROOT}/{id}/history"), &[]).await;
    assert_eq!(history["commits"][0]["sha"], linked);
    assert_eq!(history["commits"][1]["sha"], original);
    let copy = fixture.root.join("copy");
    fs::create_dir(&copy).unwrap();
    fs::copy(fixture.root.join(".git"), copy.join(".git")).unwrap();
    let discovered = get(
        &router,
        &format!("{ROOT}/repositories"),
        &[("refresh", "1")],
    )
    .await;
    assert_eq!(discovered["repositories"].as_array().unwrap().len(), 1);
    assert_eq!(discovered["warnings"].as_array().unwrap().len(), 1);
}

#[tokio::test]
async fn commit_history_lists_branches_searches_and_paginates() {
    let fixture = Fixture::new();
    let repo = fixture.repository("project");
    let first = commit(&repo, "初始提交", "find [THIS] literally");
    for index in 1..42 {
        git(
            &repo,
            &["commit", "--allow-empty", "-m", &format!("commit {index}")],
        );
    }
    let tip = git(&repo, &["rev-parse", "HEAD"]);
    git(&repo, &["update-ref", "refs/remotes/origin/main", &tip]);
    git(
        &repo,
        &[
            "symbolic-ref",
            "refs/remotes/origin/HEAD",
            "refs/remotes/origin/main",
        ],
    );
    let router = fixture.router();
    let id = repo_id(&router, "project").await;
    let branches = get(&router, &format!("{ROOT}/{id}/branches"), &[]).await;
    assert_eq!(
        branches,
        json!({"branches": [
        {"ref":"refs/heads/main", "name":"main"},
        {"ref":"refs/remotes/origin/main", "name":"origin/main"}
    ], "current_branch":"main"})
    );
    let route = format!("{ROOT}/{id}/history");
    let first_page = get(&router, &route, &[("ref", "refs/remotes/origin/main")]).await;
    assert_eq!(first_page["commits"].as_array().unwrap().len(), 40);
    assert_eq!(first_page["commits"][0]["sha"], tip);
    assert_eq!(first_page["has_more"], true);
    let next_page = get(&router, &route, &[("offset", "40")]).await;
    assert_eq!(next_page["commits"].as_array().unwrap().len(), 2);
    assert_eq!(next_page["commits"][1]["subject"], "初始提交");
    assert_eq!(next_page["commits"][1]["author"], "Fixture");
    assert_eq!(next_page["has_more"], false);
    let matched = get(&router, &route, &[("search", "[this]"), ("offset", "-10")]).await;
    assert_eq!(matched["commits"].as_array().unwrap().len(), 1);
    assert_eq!(matched["commits"][0]["sha"], first);
    assert_eq!(
        get(&router, &route, &[("offset", "99999999999999999999999999")]).await["commits"],
        json!([])
    );
    for reference in ["--help", "-C", "main\nHEAD", "main\0HEAD", "main..HEAD"] {
        bad_request(
            &router,
            &route,
            &[("workspace_id", WORKSPACE), ("ref", reference)],
        )
        .await;
    }
    bad_request(
        &router,
        &route,
        &[("workspace_id", WORKSPACE), ("offset", "broken")],
    )
    .await;
    let head = response(
        &router,
        Method::HEAD,
        &route,
        &[("workspace_id", WORKSPACE)],
    )
    .await;
    assert_eq!(head.status(), StatusCode::OK);
    assert!(to_bytes(head.into_body(), 1024).await.unwrap().is_empty());
}

#[tokio::test]
async fn merge_base_and_diff_keep_the_selected_objects_after_ref_changes() {
    let fixture = Fixture::new();
    let repo = fixture.repository("project");
    let old = "旧 文件.txt";
    let new = "新 文件.txt";
    fs::write(repo.join(old), "one\ntwo\nthree\nfour\nfive\nsix\n").unwrap();
    let root = commit(&repo, "root", "first body");
    git(&repo, &["switch", "-c", "feature"]);
    git(&repo, &["mv", "--", old, new]);
    fs::write(repo.join(new), "one\ntwo\nchanged\nfour\nfive\nsix\n").unwrap();
    fs::write(repo.join("image.bin"), [0, 1, 2, 3]).unwrap();
    fs::write(repo.join("--no-index"), "literal option-like path\n").unwrap();
    fs::write(repo.join(".gitattributes"), "*.txt diff=external\n").unwrap();
    let feature = commit(&repo, "feature change", "full message body");
    git(&repo, &["switch", "main"]);
    fs::write(repo.join("base-only"), "unrelated base change\n").unwrap();
    let base_tip = commit(&repo, "main change", "");
    git(
        &repo,
        &[
            "config",
            "diff.external",
            "astrion-git-fixture-command-not-found",
        ],
    );
    git(
        &repo,
        &[
            "config",
            "diff.external.textconv",
            "astrion-git-fixture-command-not-found",
        ],
    );
    let router = fixture.router();
    let id = repo_id(&router, "project").await;
    let compare = format!("{ROOT}/{id}/comparison");
    let diff = format!("{ROOT}/{id}/diff");
    let initial = get(&router, &compare, &[("sha", &root)]).await;
    assert_eq!(
        initial["base_sha"],
        "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
    );
    assert_eq!(initial["commit"]["message"], "root\n\nfirst body");
    assert_eq!(initial["files"][0]["status"], "A");
    let range = get(
        &router,
        &compare,
        &[("base", "refs/heads/main"), ("head", "refs/heads/feature")],
    )
    .await;
    assert_eq!(range["kind"], "branch");
    assert_eq!(range["base_tip"], base_tip);
    assert_eq!(range["base_sha"], root);
    assert_eq!(range["head_sha"], feature);
    let files = range["files"].as_array().unwrap();
    assert!(!files.iter().any(|file| file["path"] == "base-only"));
    assert!(
        files
            .iter()
            .any(|file| file["path"] == "image.bin" && file["binary"] == true)
    );
    let rename = files.iter().find(|file| file["path"] == new).unwrap();
    assert_eq!(
        rename,
        &json!({"path":new,"old_path":old,"status":"R","additions":1,"deletions":1,"binary":false})
    );
    git(&repo, &["branch", "-f", "feature", &base_tip]);
    fs::write(repo.join(new), "uncommitted noise\n").unwrap();
    let patch = get(
        &router,
        &diff,
        &[("base", &base_tip), ("head", &feature), ("path", new)],
    )
    .await;
    let lines = patch["lines"].as_array().unwrap();
    assert!(lines.contains(&json!({"kind":"delete","text":"three","old_line":3,"new_line":null})));
    assert!(lines.contains(&json!({"kind":"add","text":"changed","old_line":null,"new_line":3})));
    assert!(!lines.iter().any(|line| line["text"] == "uncommitted noise"));
    let literal = get(&router, &diff, &[("sha", &feature), ("path", "--no-index")]).await;
    assert_eq!(literal["file"]["path"], "--no-index");
    for path in ["../external", ":(glob)*", "--output=outside"] {
        bad_request(
            &router,
            &diff,
            &[
                ("workspace_id", WORKSPACE),
                ("sha", &feature),
                ("path", path),
            ],
        )
        .await;
    }
    assert!(!repo.join("outside").exists());
    for (key, value) in [
        ("parent", base_tip.as_str()),
        ("sha", "--output=outside"),
        ("base", "--all"),
    ] {
        let mut query = vec![("workspace_id", WORKSPACE), (key, value)];
        if key == "parent" {
            query.push(("sha", feature.as_str()));
        }
        bad_request(&router, &compare, &query).await;
    }
    fs::remove_file(repo.join(new)).unwrap();
    git(&repo, &["merge", "--no-ff", &feature, "-m", "merge"]);
    let merge = git(&repo, &["rev-parse", "HEAD"]);
    let selected = get(&router, &compare, &[("sha", &merge), ("parent", &feature)]).await;
    assert_eq!(selected["base_sha"], feature);
    assert_eq!(selected["commit"]["parents"], json!([base_tip, feature]));
}

#[tokio::test]
async fn file_diff_pagination_and_context_are_bounded() {
    let fixture = Fixture::new();
    let repo = fixture.repository("project");
    let contents: String = (1..=1300).map(|line| format!("line {line}\n")).collect();
    fs::write(repo.join("large.txt"), contents).unwrap();
    let sha = commit(&repo, "large diff", "");
    let router = fixture.router();
    let id = repo_id(&router, "project").await;
    let route = format!("{ROOT}/{id}/diff");
    let params = [("sha", sha.as_str()), ("path", "large.txt")];
    let first = get(&router, &route, &params).await;
    assert_eq!(first["lines"].as_array().unwrap().len(), 1200);
    assert_eq!(first["has_more"], true);
    assert_eq!(first["next_offset"], 1200);
    let continuation = get(
        &router,
        &route,
        &[
            params[0],
            params[1],
            ("offset", "1200"),
            ("context", "10000"),
        ],
    )
    .await;
    assert_eq!(continuation["has_more"], false);
    assert_eq!(continuation["next_offset"], first["total_lines"]);
    let lines = continuation["lines"].as_array().unwrap();
    assert_eq!(
        first["lines"][1199]["new_line"].as_u64().unwrap() + 1,
        lines[0]["new_line"].as_u64().unwrap()
    );
    assert_eq!(lines.last().unwrap()["new_line"], 1300);
    let past_end = get(
        &router,
        &route,
        &[params[0], params[1], ("offset", "99999")],
    )
    .await;
    assert_eq!(past_end["lines"], json!([]));
    assert_eq!(past_end["next_offset"], first["total_lines"]);
    bad_request(
        &router,
        &route,
        &[
            ("workspace_id", WORKSPACE),
            params[0],
            params[1],
            ("context", "--output"),
        ],
    )
    .await;
}

#[tokio::test]
async fn reviews_read_existing_rows_and_real_conversation_titles() {
    let fixture = Fixture::new();
    let repo = fixture.repository("project");
    let head = commit(&repo, "review target", "");
    let router = fixture.router();
    let id = repo_id(&router, "project").await;
    let route = format!("{ROOT}/{id}/reviews");
    assert_eq!(get(&router, &route, &[]).await, json!([]));
    assert!(fs::read_dir(&fixture.data).unwrap().next().is_none());
    let hash = short_hash(WORKSPACE.as_bytes());
    assert_eq!(hash, "f16d05ec6b29248d2c61adb1");
    let index_dir = fixture.data.join("project_git_reviews").join(hash);
    let index = index_dir.join(format!("{}.json", short_hash(id.as_bytes())));
    let conversations = fixture.data.join("conversations").join(WORKSPACE);
    fs::create_dir_all(&index_dir).unwrap();
    fs::create_dir_all(&conversations).unwrap();
    let mut db = crate::conversation_db::Database::open(&conversations).unwrap();
    for (id,title) in [("conv_existing","Updated review title"),("conv_untitled","")] {
        db.save(&json!({"id":id,"metadata":{"title":title},"messages":[]}), &json!({"title":title})).unwrap();
    }
    let rows = json!([
        {"workspace_id":WORKSPACE,"conversation_id":"conv_existing","task_id":"old_task","status":"completed","title":"stale","base_sha":"base","head_sha":head},
        {"workspace_id":WORKSPACE,"conversation_id":"conv_untitled","task_id":"live_task","title":"stored fallback"},
        {"workspace_id":WORKSPACE,"conversation_id":"conv_removed","task_id":"was_deleted"},
        {"workspace_id":"other","conversation_id":"conv_existing","task_id":"wrong_scope"},
        {"workspace_id":WORKSPACE,"conversation_id":"../../elsewhere","task_id":"invalid"},
    ]);
    let original = serde_json::to_vec(&rows).unwrap();
    fs::write(&index, &original).unwrap();
    let reviews = get(
        &router,
        &route,
        &[("data_dir", fixture.temp.to_str().unwrap())],
    )
    .await;
    assert_eq!(reviews.as_array().unwrap().len(), 2);
    assert_eq!(reviews[0]["title"], "Updated review title");
    assert_eq!(reviews[0]["status"], "saved");
    assert_eq!(reviews[0]["head_sha"], head);
    assert_eq!(reviews[1]["title"], "stored fallback");
    assert_eq!(reviews[1]["status"], "saved");
    let denied = response(
        &router,
        Method::POST,
        &route,
        &[("workspace_id", WORKSPACE)],
    )
    .await;
    assert_eq!(denied.status(), StatusCode::METHOD_NOT_ALLOWED);
    assert_eq!(fs::read(&index).unwrap(), original);
    fs::write(&index, b"{}").unwrap();
    bad_request(&router, &route, &[("workspace_id", WORKSPACE)]).await;
    #[cfg(unix)]
    {
        let external = fixture.temp.join("external.json");
        fs::write(&external, original).unwrap();
        fs::remove_file(&index).unwrap();
        std::os::unix::fs::symlink(external, &index).unwrap();
        assert_eq!(get(&router, &route, &[]).await, json!([]));
    }
}

#[tokio::test]
async fn unrelated_branches_and_missing_shallow_parents_return_errors() {
    let fixture = Fixture::new();
    let repo = fixture.repository("project");
    fs::write(repo.join("readme"), "initial\n").unwrap();
    commit(&repo, "first", "");
    fs::write(repo.join("readme"), "updated\n").unwrap();
    let tip = commit(&repo, "second", "");
    git(&repo, &["switch", "--orphan", "unrelated"]);
    commit(&repo, "unrelated", "");
    let router = fixture.router();
    let id = repo_id(&router, "project").await;
    let route = format!("{ROOT}/{id}/comparison");
    let error = bad_request(
        &router,
        &route,
        &[
            ("workspace_id", WORKSPACE),
            ("base", "main"),
            ("head", "unrelated"),
        ],
    )
    .await;
    assert_eq!(error, "两个分支没有共同祖先。");

    let source = reqwest::Url::from_directory_path(&repo).unwrap();
    git(
        &fixture.root,
        &[
            "clone",
            "--depth=1",
            "--branch=main",
            source.as_str(),
            "shallow",
        ],
    );
    let clone = get(
        &router,
        &format!("{ROOT}/repositories"),
        &[("refresh", "1")],
    )
    .await;
    assert_eq!(clone["repositories"].as_array().unwrap().len(), 2);
    let id = repo_id(&router, "shallow").await;
    let data = get(&router, &format!("{ROOT}/{id}/history"), &[]).await;
    assert_eq!(data["commits"].as_array().unwrap().len(), 1);
    assert_eq!(data["commits"][0]["sha"], tip);
    bad_request(
        &router,
        &format!("{ROOT}/{id}/comparison"),
        &[("workspace_id", WORKSPACE), ("sha", &tip)],
    )
    .await;
}

#[tokio::test]
async fn a_git_query_over_32_mib_returns_an_explicit_error() {
    let fixture = Fixture::new();
    let repo = fixture.repository("project");
    fs::write(repo.join("large.txt"), vec![b'a'; 32 * 1024 * 1024 + 1]).unwrap();
    let sha = commit(&repo, "over output limit", "");
    let router = fixture.router();
    let id = repo_id(&router, "project").await;
    let error = bad_request(
        &router,
        &format!("{ROOT}/{id}/diff"),
        &[
            ("workspace_id", WORKSPACE),
            ("sha", &sha),
            ("path", "large.txt"),
        ],
    )
    .await;
    assert!(error.contains("超过单次读取上限"), "{error}");
}

const REVIEW_TIMEOUT: Duration = Duration::from_secs(10);

#[derive(Clone)]
struct ReviewModel {
    requests: mpsc::UnboundedSender<Value>,
    allow_evidence: Arc<Semaphore>,
}

async fn review_model(State(state): State<ReviewModel>, Json(body): Json<Value>) -> Response {
    let messages = body["messages"].as_array().expect("model messages");
    let has_evidence = messages.iter().any(|message| message["role"] == "tool");
    state.requests.send(body).unwrap();
    let (delta, finish) = if has_evidence {
        (json!({"content":"Reviewed the fixed revisions."}), "stop")
    } else {
        state.allow_evidence.acquire().await.unwrap().forget();
        let calls = [
            ("git_review_changes", json!({})),
            ("git_review_diff", json!({"path":"source.txt"})),
            (
                "git_review_read",
                json!({"revision":"base","path":"source.txt"}),
            ),
            (
                "git_review_read",
                json!({"revision":"head","path":"source.txt"}),
            ),
            (
                "git_review_read",
                json!({"revision":"head","path":"../outside"}),
            ),
        ]
        .into_iter()
        .enumerate()
        .map(|(index, (tool, arguments))| {
            json!({
                "index":index,"id":format!("evidence_{index}"),"type":"function",
                "function":{"name":tool,"arguments":arguments.to_string()},
            })
        })
        .collect::<Vec<_>>();
        (json!({"tool_calls":calls}), "tool_calls")
    };
    let event = json!({"choices":[{"index":0,"delta":delta,"finish_reason":finish}]});
    (
        [(CONTENT_TYPE, "text/event-stream")],
        format!("data: {event}\n\ndata: [DONE]\n\n"),
    )
        .into_response()
}

async fn post_review(
    router: &Router,
    path: &str,
    query: &[(&str, &str)],
    payload: Value,
) -> (StatusCode, Value) {
    read_json_response(send(router, Method::POST, path, query, Some(payload)).await).await
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn review_dispatch_persists_and_executes_with_real_runtime_and_fixed_git_tools() {
    let fixture = Fixture::new();
    let repo = fixture.repository("project");
    fs::write(repo.join("source.txt"), "old revision\n").unwrap();
    let base = commit(&repo, "base", "");
    fs::write(repo.join("source.txt"), "new revision\n").unwrap();
    let head = commit(&repo, "change source", "");
    fs::write(repo.join("source.txt"), "uncommitted content\n").unwrap();
    fs::write(fixture.root.join("outside"), "outside source tree\n").unwrap();
    fs::write(
        fixture.data.join("personalization.json"),
        br#"{"default_run_mode":"fast"}"#,
    )
    .unwrap();

    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = format!(
        "http://{}/v1/chat/completions",
        listener.local_addr().unwrap()
    );
    let (requests, mut upstream) = mpsc::unbounded_channel();
    let allow_evidence = Arc::new(Semaphore::new(0));
    let provider = Router::new()
        .route("/v1/chat/completions", post(review_model))
        .with_state(ReviewModel {
            requests,
            allow_evidence: allow_evidence.clone(),
        });
    let mock = tokio::spawn(async move { axum::serve(listener, provider).await.unwrap() });
    let runtime = Runtime::open(&fixture.data).unwrap();
    runtime.bind_workspace(WORKSPACE, &fixture.root).unwrap();
    let catalog = CatalogSource::with_paths(&fixture.temp, &fixture.temp, &fixture.data);
    let model = ModelConfig::fixed(endpoint, None, "review-fixture".into());
    let scope_service = ProjectGit::new(WORKSPACE, None, &fixture.root, &fixture.data)
        .unwrap()
        .with_runtime(runtime.clone(), catalog, Some(model));
    let router = ProjectGit::router_with_scope().layer(axum::Extension(scope_service));
    let id = repo_id(&router, "project").await;
    let route = format!("{ROOT}/{id}/reviews");
    let scope = [("workspace_id", WORKSPACE)];
    let test_bad_query = [
        (json!(["invalid"]), scope.as_slice()),
        (json!({"sha":123}), scope.as_slice()),
        (json!({"sha":"--output=outside"}), scope.as_slice()),
        (json!({"sha":head,"workspace_id":"other"}), scope.as_slice()),
        (json!({"sha":head}), &[]),
    ];
    for (payload, query) in test_bad_query {
        let (status, body) = post_review(&router, &route, query, payload).await;
        assert_eq!(status, StatusCode::BAD_REQUEST, "{body}");
    }
    let index_dir = fixture
        .data
        .join("project_git_reviews")
        .join(short_hash(WORKSPACE.as_bytes()));
    let index = index_dir.join(format!("{}.json", short_hash(id.as_bytes())));
    fs::create_dir_all(&index_dir).unwrap();
    fs::write(&index, b"{}").unwrap();
    let (status, _) = post_review(&router, &route, &scope, json!({"sha":head})).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(
        runtime.list_sessions(WORKSPACE, 10, 0, None).unwrap()["total"],
        0
    );
    fs::remove_file(&index).unwrap();

    let (status, accepted) = post_review(&router, &route, &scope,
        json!({"sha":head,"permission_mode":"unrestricted","execution_mode":"direct","model_key":"untrusted"})).await;
    assert_eq!(status, StatusCode::ACCEPTED, "{accepted}");
    let accepted = &accepted["data"];
    assert_eq!(accepted["head_sha"], head);
    assert_eq!(accepted["base_sha"], base);
    assert!(accepted["warning"].is_null());
    assert!(
        time::OffsetDateTime::parse(
            accepted["created_at"].as_str().unwrap(),
            &time::format_description::well_known::Rfc3339
        )
        .is_ok()
    );
    let task = accepted["task_id"].as_str().unwrap();
    let conversation = accepted["conversation_id"].as_str().unwrap();
    let request = timeout(REVIEW_TIMEOUT, upstream.recv())
        .await
        .unwrap()
        .unwrap();
    assert_eq!(request["model"], "review-fixture");
    let model_tools: Vec<_> = request["tools"]
        .as_array()
        .unwrap()
        .iter()
        .map(|tool| tool["function"]["name"].as_str().unwrap())
        .collect();
    assert_eq!(
        model_tools,
        ["git_review_changes", "git_review_diff", "git_review_read"]
    );
    let history = runtime.session_history(WORKSPACE, conversation).unwrap();
    let persisted = &history["conversation"];
    assert_eq!(persisted["title"], accepted["title"]);
    let meta = &persisted["metadata"];
    assert_eq!(meta["title"], accepted["title"]);
    assert_eq!(meta["permission_mode"], "readonly");
    assert_eq!(meta["execution_mode"], "sandbox");
    assert_eq!(meta["work_mode"], "execute");
    assert_eq!(meta["run_mode"], "fast");
    assert_eq!(meta["git_review"]["repo_id"], id);
    assert_eq!(meta["git_review"]["base_sha"], base);
    assert_eq!(meta["git_review"]["head_sha"], head);
    let user_msg = persisted["messages"]
        .as_array()
        .unwrap()
        .iter()
        .find(|msg| msg["role"] == "user")
        .unwrap();
    let prompt = user_msg["content"].as_str().unwrap();
    assert!(prompt.contains(&base) && prompt.contains(&head));
    let polled = runtime.poll_task(WORKSPACE, task, 0).unwrap();
    assert_eq!(polled["data"]["message_source"], "git_review");
    assert_eq!(polled["data"]["status"], "running");
    assert_eq!(get(&router, &route, &[]).await[0]["status"], "running");
    assert_eq!(
        runtime.review_status("other", conversation, task).unwrap(),
        None
    );
    assert_eq!(
        runtime
            .review_status(WORKSPACE, "conv_other", task)
            .unwrap(),
        None
    );
    let (conflict, response) = post_review(&router, &route, &scope, json!({"sha":head})).await;
    assert_eq!(conflict, StatusCode::CONFLICT, "{response}");
    assert_eq!(
        runtime.list_sessions(WORKSPACE, 10, 0, None).unwrap()["total"],
        1
    );

    git(&repo, &["branch", "moved-head", &base]);
    git(&repo, &["update-ref", "refs/heads/main", &base]);
    allow_evidence.add_permits(1);
    let request = timeout(REVIEW_TIMEOUT, upstream.recv())
        .await
        .unwrap()
        .unwrap();
    let results: Vec<Value> = request["messages"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|message| message["role"] == "tool")
        .map(|message| serde_json::from_str(message["content"].as_str().unwrap()).unwrap())
        .collect();
    assert_eq!(results.len(), 5);
    assert_eq!(results[0]["files"][0]["path"], "source.txt");
    assert!(
        results[1]["lines"]
            .as_array()
            .unwrap()
            .iter()
            .any(|line| line["kind"] == "add" && line["text"] == "new revision")
    );
    assert_eq!(results[2]["lines"], json!(["old revision"]));
    assert_eq!(results[3]["lines"], json!(["new revision"]));
    assert_eq!(results[4]["success"], false);
    timeout(REVIEW_TIMEOUT, async {
        loop {
            let rows = get(&router, &route, &[]).await;
            assert_eq!(rows[0]["task_id"], task);
            if rows[0]["status"] != "running" {
                assert_eq!(rows[0]["status"], "succeeded", "{rows}");
                break;
            }
            sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .unwrap();
    let on_disk: Value = serde_json::from_slice(&fs::read(&index).unwrap()).unwrap();
    assert_eq!(on_disk.as_array().unwrap().len(), 1);
    assert_eq!(on_disk[0]["conversation_id"], conversation);
    assert_eq!(on_disk[0]["task_id"], task);
    assert_eq!(
        get(&fixture.router(), &route, &[]).await[0]["status"],
        "saved"
    );
    mock.abort();
}

#[tokio::test]
async fn browser_router_uses_the_trusted_workspace_in_each_request() {
    let first = Fixture::new();
    first.repository("first-project");
    let second = Fixture::new();
    second.repository("second-project");
    let browser = ProjectGit::router_with_scope();
    let route = format!("{ROOT}/repositories");
    let (status, body) = request(
        &browser,
        Method::GET,
        &route,
        &[("workspace_id", WORKSPACE)],
    )
    .await;
    assert_eq!(status, StatusCode::FORBIDDEN);
    assert_eq!(body["success"], false);
    let first_scope = ProjectGit::new(WORKSPACE, None, &first.root, &first.data).unwrap();
    let second_scope = ProjectGit::new("other", None, &second.root, &second.data).unwrap();
    let browser = browser.layer(middleware::from_fn(move |mut req: Request, next: Next| {
        // This fixture stands in for the authenticated Host workspace resolver.
        let scope = if req
            .uri()
            .query()
            .is_some_and(|query| query.contains("fixture_scope=second"))
        {
            second_scope.clone()
        } else {
            first_scope.clone()
        };
        async move {
            req.extensions_mut().insert(scope);
            next.run(req).await
        }
    }));
    let before = get(&browser, &route, &[]).await;
    assert_eq!(before["repositories"][0]["name"], "first-project");
    let other_scope = [("workspace_id", "other"), ("fixture_scope", "second")];
    let (status, other) = request(&browser, Method::GET, &route, &other_scope).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(other["data"]["repositories"][0]["name"], "second-project");
    bad_request(
        &browser,
        &route,
        &[("workspace_id", WORKSPACE), ("fixture_scope", "second")],
    )
    .await;
    let first_repo = before["repositories"][0]["id"].as_str().unwrap();
    bad_request(
        &browser,
        &format!("{ROOT}/{first_repo}/history"),
        &other_scope,
    )
    .await;
    first.repository("discovered-later");
    assert_eq!(get(&browser, &route, &[]).await, before);
    assert_eq!(
        get(&browser, &route, &[("refresh", "1")]).await["repositories"]
            .as_array()
            .unwrap()
            .len(),
        2
    );
    let (status, other_again) = request(&browser, Method::GET, &route, &other_scope).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(other_again, other);
}
