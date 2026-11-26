use std::{collections::HashMap, sync::Arc};

use astrion_headless::{
    runtime::{Runtime, RuntimeError},
    web_auth::BrowserSession,
    workspace_catalog::{
        HostCatalog,
        manage::{RegistryChange, RegistryError, WorkspaceRegistry},
    },
};
use axum::{
    Extension, Json, Router,
    extract::{FromRequest, Query, Request, State},
    http::{HeaderMap, Method, StatusCode, header::CONTENT_TYPE},
    routing::{get, post},
};
use serde::Deserialize;
use serde_json::{Value, json};

use crate::host_paths::HostPaths;

#[derive(Clone)]
struct WorkspaceApi {
    registry: WorkspaceRegistry,
    runtime: Runtime,
}

type Reply = (StatusCode, Json<Value>);

/// Mount inside Gateway authentication. Create accepts either Gateway credential.
/// Selection and administrative changes additionally require a browser session.
pub fn router(paths: &HostPaths, runtime: Runtime) -> Router {
    let state = Arc::new(WorkspaceApi {
        registry: WorkspaceRegistry::new(paths.workspace_config.clone(), paths.resources.clone()),
        runtime,
    });
    Router::new()
        .route(
            "/api/host/workspaces/create",
            get(create).head(reject_head).post(create),
        )
        .route(
            "/api/host/workspaces/select",
            get(select).head(reject_head).post(select),
        )
        .route("/api/host/workspaces/rename", post(rename))
        .route("/api/host/workspaces/delete", post(delete))
        .route("/api/host/workspaces/set-default", post(set_default))
        .with_state(state)
}

#[derive(Default, Deserialize)]
struct WorkspaceQuery {
    workspace_id: Option<String>,
    path: Option<String>,
    label: Option<String>,
    set_default: Option<String>,
}

async fn create(
    State(api): State<Arc<WorkspaceApi>>,
    Query(query): Query<WorkspaceQuery>,
    browser: Option<Extension<BrowserSession>>,
    request: Request,
) -> Reply {
    let requested = match workspace_header(request.headers()) {
        Ok(requested) => requested,
        Err(error) => return error_reply(error),
    };
    let body = match payload(request).await {
        Ok(body) => body,
        Err(error) => return error_reply(error),
    };
    blocking(move || {
        let path = nonempty(query.path.as_deref())
            .or_else(|| field(&body, "path"))
            .unwrap_or("");
        let label = nonempty(query.label.as_deref())
            .or_else(|| field(&body, "label"))
            .unwrap_or("");
        let set_default = nonempty(query.set_default.as_deref())
            .map(bool_text)
            .unwrap_or_else(|| {
                body["set_default"]
                    .as_bool()
                    .or_else(|| body["set_default"].as_str().map(bool_text))
                    .unwrap_or(body["set_default"] == 1)
            });
        let changed = api
            .registry
            .create(path, label, set_default)
            .map_err(registry_error)?;
        let selected = browser
            .and_then(|Extension(browser)| browser.selected_workspace_id())
            .or(requested);
        let current = resolved_id(&changed.catalog, selected.as_deref());
        api.response(&changed, current, false, true)
    })
    .await
}

async fn select(
    State(api): State<Arc<WorkspaceApi>>,
    Query(query): Query<WorkspaceQuery>,
    browser: Option<Extension<BrowserSession>>,
    request: Request,
) -> Reply {
    let Some(Extension(browser)) = browser else {
        return require_browser();
    };
    let body = match payload(request).await {
        Ok(body) => body,
        Err(error) => return error_reply(error),
    };
    blocking(move || {
        let id = nonempty(query.workspace_id.as_deref()).or_else(|| field(&body, "workspace_id"))
            .ok_or_else(missing_id)?;
        let catalog = api.registry.load().map_err(|error| registry_error(error.into()))?;
        let workspace = catalog.by_id(id).ok_or_else(|| RuntimeError {
            status: 404, message: "Workspace does not exist".into(),
        })?;
        let reloaded = browser.selected_workspace_id().as_deref() != Some(id);
        browser.select_workspace(Some(id));
        Ok(json!({
            "current_workspace_id":id, "project_path":workspace.path.to_string_lossy(),
            "default_workspace_id":catalog.default_workspace_id.as_deref().unwrap_or(""), "reloaded":reloaded
        }))
    }).await
}

async fn rename(
    State(api): State<Arc<WorkspaceApi>>,
    browser: Option<Extension<BrowserSession>>,
    request: Request,
) -> Reply {
    browser_change(api, browser, request, |api, browser, body| {
        let id = field(body, "workspace_id").ok_or_else(missing_id)?;
        let label = field(body, "label")
            .or_else(|| field(body, "name"))
            .unwrap_or("");
        let changed = api.registry.rename(id, label).map_err(registry_error)?;
        let selected = browser.selected_workspace_id();
        api.response(
            &changed,
            resolved_id(&changed.catalog, selected.as_deref()),
            true,
            true,
        )
    })
    .await
}

async fn delete(
    State(api): State<Arc<WorkspaceApi>>,
    browser: Option<Extension<BrowserSession>>,
    request: Request,
) -> Reply {
    browser_change(api, browser, request, |api, browser, body| {
        let id = field(body, "workspace_id").ok_or_else(missing_id)?;
        let changed = api
            .runtime
            .remove_idle_workspace(id, || api.registry.delete(id).map_err(registry_error))?;
        let selected = browser.selected_workspace_id();
        let current = resolved_id(&changed.catalog, selected.as_deref());
        browser.compare_and_select_workspace(selected.as_deref(), current);
        api.response(&changed, current, true, true)
    })
    .await
}

async fn set_default(
    State(api): State<Arc<WorkspaceApi>>,
    browser: Option<Extension<BrowserSession>>,
    request: Request,
) -> Reply {
    browser_change(api, browser, request, |api, browser, body| {
        let id = field(body, "workspace_id").ok_or_else(missing_id)?;
        let changed = api.registry.set_default(id).map_err(registry_error)?;
        let selected = browser.selected_workspace_id();
        api.response(
            &changed,
            resolved_id(&changed.catalog, selected.as_deref()),
            true,
            false,
        )
    })
    .await
}

async fn reject_head() -> StatusCode {
    StatusCode::METHOD_NOT_ALLOWED
}

async fn browser_change(
    api: Arc<WorkspaceApi>,
    browser: Option<Extension<BrowserSession>>,
    request: Request,
    change: impl FnOnce(&WorkspaceApi, &BrowserSession, &Value) -> Result<Value, RuntimeError>
    + Send
    + 'static,
) -> Reply {
    let Some(Extension(browser)) = browser else {
        return require_browser();
    };
    let body = match payload(request).await {
        Ok(body) => body,
        Err(error) => return error_reply(error),
    };
    blocking(move || change(&api, &browser, &body)).await
}

impl WorkspaceApi {
    fn response(
        &self,
        change: &RegistryChange,
        current: Option<&str>,
        include_counts: bool,
        include_source: bool,
    ) -> Result<Value, RuntimeError> {
        let mut counts = HashMap::new();
        if include_counts {
            for task in self
                .runtime
                .navigation_runs()?
                .into_iter()
                .filter(|run| run.running)
            {
                *counts.entry(task.workspace_id).or_insert(0_u64) += 1;
            }
        }
        let workspaces = change.catalog.workspaces.iter().map(|workspace| {
            let mut row = json!({"workspace_id":workspace.id,"label":workspace.label,
                "path":workspace.path.to_string_lossy(),"is_current":current == Some(&workspace.id)});
            if include_counts {
                row["running_task_count"] = counts.get(&workspace.id).copied().unwrap_or_default().into();
            }
            row
        }).collect();
        let mut response = change.details.clone();
        response["default_workspace_id"] = change
            .catalog
            .default_workspace_id
            .as_deref()
            .unwrap_or("")
            .into();
        response["current_workspace_id"] = json!(current);
        response["workspaces"] = Value::Array(workspaces);
        if include_source {
            response["source_path"] = json!(self.registry.path().to_string_lossy());
        }
        Ok(response)
    }
}

async fn payload(request: Request) -> Result<Value, RuntimeError> {
    if request.method() != Method::POST || !request.headers().contains_key(CONTENT_TYPE) {
        return Ok(Value::Null);
    }
    let Json(body) = Json::<Value>::from_request(request, &())
        .await
        .map_err(|error| RuntimeError {
            status: error.status().as_u16(),
            message: error.body_text(),
        })?;
    Ok(body)
}

fn field<'a>(body: &'a Value, key: &str) -> Option<&'a str> {
    nonempty(body[key].as_str())
}

fn nonempty(value: Option<&str>) -> Option<&str> {
    value.map(str::trim).filter(|value| !value.is_empty())
}

fn bool_text(value: &str) -> bool {
    ["1", "true", "yes", "on"]
        .iter()
        .any(|candidate| value.eq_ignore_ascii_case(candidate))
}

fn resolved_id<'a>(catalog: &'a HostCatalog, selected: Option<&str>) -> Option<&'a str> {
    selected
        .and_then(|id| catalog.by_id(id))
        .or_else(|| catalog.default_workspace())
        .map(|workspace| workspace.id.as_str())
}

fn workspace_header(headers: &HeaderMap) -> Result<Option<String>, RuntimeError> {
    let mut values = headers.get_all("x-astrion-workspace-id").iter();
    match values.next().map(|value| value.to_str()).transpose() {
        Ok(id) if values.next().is_none() => Ok(nonempty(id).map(str::to_owned)),
        _ => Err(RuntimeError {
            status: 400,
            message: "Invalid workspace header".into(),
        }),
    }
}

fn missing_id() -> RuntimeError {
    RuntimeError {
        status: 400,
        message: "Workspace ID is required".into(),
    }
}

fn registry_error(error: RegistryError) -> RuntimeError {
    let status = match error {
        RegistryError::Invalid(_) | RegistryError::Missing => 400,
        RegistryError::Storage(_) => 500,
    };
    RuntimeError {
        status,
        message: error.to_string(),
    }
}

async fn blocking(
    operation: impl FnOnce() -> Result<Value, RuntimeError> + Send + 'static,
) -> Reply {
    match tokio::task::spawn_blocking(operation).await {
        Ok(Ok(data)) => (StatusCode::OK, Json(json!({"success":true,"data":data}))),
        Ok(Err(error)) => error_reply(error),
        Err(_) => error_reply(RuntimeError {
            status: 500,
            message: "Workspace operation interrupted".into(),
        }),
    }
}

fn require_browser() -> Reply {
    error_reply(RuntimeError {
        status: 401,
        message: "Browser session required".into(),
    })
}

fn error_reply(error: RuntimeError) -> Reply {
    let status = StatusCode::from_u16(error.status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
    (status, Json(json!({"success":false,"error":error.message})))
}

#[cfg(test)]
mod tests {
    use super::*;
    use astrion_headless::{
        tools::ToolSet,
        web_auth::{WebAuth, WebAuthConfig},
    };
    use axum::{
        body::{Body, to_bytes},
        extract::ConnectInfo,
        http::header::{COOKIE, HOST, SET_COOKIE},
        response::Response,
    };
    use std::{fs, net::SocketAddr, path::PathBuf, time::Duration};
    use tower::ServiceExt;
    use uuid::Uuid;

    const ROOT_PATH: &str = "/api/host/workspaces";

    struct Scratch(PathBuf);

    impl Scratch {
        fn new() -> Self {
            let path = std::env::temp_dir().join(format!("astrion-workspaces-{}", Uuid::new_v4()));
            fs::create_dir(&path).unwrap();
            Self(path.canonicalize().unwrap())
        }

        fn paths(&self) -> HostPaths {
            HostPaths {
                deploy: self.0.clone(),
                logs: self.0.clone(),
                resources: self.0.clone(),
                host_libraries: self.0.clone(),
                workspace_config: self.0.join("host_workspaces.json"),
            }
        }
    }

    impl Drop for Scratch {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    struct Browser {
        cookie: String,
        csrf: String,
    }

    fn request(
        method: Method,
        path: &str,
        body: Option<Value>,
        browser: Option<&Browser>,
    ) -> Request {
        let mut request = Request::builder()
            .method(method)
            .uri(path)
            .header(HOST, "127.0.0.1:5501");
        if let Some(browser) = browser {
            request = request
                .header(COOKIE, &browser.cookie)
                .header("x-csrf-token", &browser.csrf);
        }
        let mut request = if let Some(body) = body {
            request
                .header(CONTENT_TYPE, "application/json")
                .body(Body::from(body.to_string()))
                .unwrap()
        } else {
            request.body(Body::empty()).unwrap()
        };
        request.extensions_mut().insert(ConnectInfo(
            "127.0.0.1:12345".parse::<SocketAddr>().unwrap(),
        ));
        request
    }

    async fn response_data(response: Response) -> Value {
        let body = to_bytes(response.into_body(), 1 << 20).await.unwrap();
        serde_json::from_slice(&body).unwrap()
    }

    fn cookie(response: &Response) -> String {
        response.headers()[SET_COOKIE]
            .to_str()
            .unwrap()
            .split(';')
            .next()
            .unwrap()
            .into()
    }

    async fn challenge(app: &Router, browser: Option<&Browser>) -> Browser {
        let response = app
            .clone()
            .oneshot(request(Method::GET, "/api/csrf-token", None, browser))
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let cookie = if response.headers().contains_key(SET_COOKIE) {
            cookie(&response)
        } else {
            browser.unwrap().cookie.clone()
        };
        let csrf = response_data(response).await["token"]
            .as_str()
            .unwrap()
            .to_owned();
        Browser { cookie, csrf }
    }

    async fn login(app: &Router) -> Browser {
        let initial = challenge(app, None).await;
        let response = app
            .clone()
            .oneshot(request(
                Method::POST,
                "/host-login",
                Some(json!({"password":""})),
                Some(&initial),
            ))
            .await
            .unwrap();
        assert!(response.status().is_success());
        challenge(
            app,
            Some(&Browser {
                cookie: cookie(&response),
                csrf: initial.csrf,
            }),
        )
        .await
    }

    async fn call(
        app: &Router,
        browser: &Browser,
        action: &str,
        body: Value,
        expected: StatusCode,
    ) -> Value {
        let response = app
            .clone()
            .oneshot(request(
                Method::POST,
                &format!("{ROOT_PATH}/{action}"),
                Some(body),
                Some(browser),
            ))
            .await
            .unwrap();
        let status = response.status();
        let reply = response_data(response).await;
        assert_eq!(status, expected, "{reply}");
        reply
    }

    #[tokio::test]
    async fn browser_workspace_writes_preserve_sessions_files_and_the_empty_catalog() {
        let scratch = Scratch::new();
        let paths = scratch.paths();
        let runtime = Runtime::open(scratch.0.join("data")).unwrap();
        let mut config = WebAuthConfig::new(scratch.0.join("data"), 5501);
        config.desktop = true;
        let auth = WebAuth::new(config).unwrap();
        let routes = router(&paths, runtime.clone());
        let app = auth.router().merge(auth.protect_api(routes.clone()));
        let first = login(&app).await;
        let second = login(&app).await;
        let forbidden = app
            .clone()
            .oneshot(request(
                Method::POST,
                &format!("{ROOT_PATH}/create"),
                Some(json!({"path":"denied"})),
                None,
            ))
            .await
            .unwrap();
        assert_eq!(forbidden.status(), StatusCode::FORBIDDEN);
        assert!(!paths.workspace_config.exists());
        let bearer_only = routes
            .oneshot(request(
                Method::POST,
                &format!("{ROOT_PATH}/delete"),
                Some(json!({"workspace_id":"anything"})),
                None,
            ))
            .await
            .unwrap();
        assert_eq!(bearer_only.status(), StatusCode::UNAUTHORIZED);

        let reply = call(
            &app,
            &first,
            "create",
            json!({"path":"alpha-dir","label":"Alpha"}),
            StatusCode::OK,
        )
        .await;
        assert_eq!(reply["data"]["workspace"]["workspace_id"], "alpha");
        assert!(scratch.0.join("alpha-dir").is_dir());
        let mut stale = request(
            Method::POST,
            &format!("{ROOT_PATH}/create"),
            Some(json!({"path":"beta-dir","label":"Beta"})),
            Some(&second),
        );
        stale.headers_mut().insert(
            "x-astrion-workspace-id",
            "old-registration".parse().unwrap(),
        );
        let second_created = app.clone().oneshot(stale).await.unwrap();
        assert_eq!(second_created.status(), StatusCode::OK);
        assert_eq!(
            response_data(second_created).await["data"]["workspace"]["workspace_id"],
            "beta"
        );
        let mut raw: Value =
            serde_json::from_slice(&fs::read(&paths.workspace_config).unwrap()).unwrap();
        raw["unknown_top"] = json!({"future":1});
        raw["workspaces"][0]["unknown_workspace"] = json!(["preserve me"]);
        fs::write(&paths.workspace_config, raw.to_string()).unwrap();
        let previous = fs::read(&paths.workspace_config).unwrap();
        let duplicate = call(
            &app,
            &first,
            "create",
            json!({"path":"alpha-dir","label":"ignore","set_default":true}),
            StatusCode::OK,
        )
        .await;
        assert_eq!(duplicate["data"]["created"], false);
        assert_eq!(fs::read(&paths.workspace_config).unwrap(), previous);
        let renamed = call(
            &app,
            &first,
            "rename",
            json!({"workspace_id":"alpha","name":"Renamed"}),
            StatusCode::OK,
        )
        .await;
        assert_eq!(
            renamed["data"]["workspace"]["unknown_workspace"],
            json!(["preserve me"])
        );
        call(
            &app,
            &first,
            "select",
            json!({"workspace_id":"alpha"}),
            StatusCode::OK,
        )
        .await;
        call(
            &app,
            &second,
            "select",
            json!({"workspace_id":"beta"}),
            StatusCode::OK,
        )
        .await;
        let default = call(
            &app,
            &second,
            "set-default",
            json!({"workspace_id":"alpha"}),
            StatusCode::OK,
        )
        .await;
        assert_eq!(default["data"]["current_workspace_id"], "beta");
        assert_eq!(default["data"]["workspaces"][1]["is_current"], true);

        let project = scratch.0.join("alpha-dir");
        runtime.bind_workspace("alpha", &project).unwrap();
        fs::write(project.join("keep.txt"), "project data").unwrap();
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let model = astrion_headless::runtime::ModelConfig::fixed(
            format!("http://{}/v1/responses", listener.local_addr().unwrap()),
            None,
            "offline".into(),
        );
        let admitted = runtime
            .create_task("alpha", &json!({"message":"wait"}), model)
            .unwrap();
        let task_id = admitted["data"]["task_id"].as_str().unwrap();
        let bytes = fs::read(&paths.workspace_config).unwrap();
        call(
            &app,
            &first,
            "delete",
            json!({"workspace_id":"alpha"}),
            StatusCode::CONFLICT,
        )
        .await;
        assert_eq!(fs::read(&paths.workspace_config).unwrap(), bytes);
        runtime.cancel_task("alpha", task_id).unwrap();
        tokio::time::timeout(Duration::from_secs(4), async {
            while runtime
                .navigation_runs()
                .unwrap()
                .iter()
                .any(|run| run.running)
            {
                tokio::time::sleep(Duration::from_millis(5)).await;
            }
        })
        .await
        .unwrap();
        let removed = call(
            &app,
            &first,
            "delete",
            json!({"workspace_id":"alpha"}),
            StatusCode::OK,
        )
        .await;
        assert_eq!(removed["data"]["current_workspace_id"], "beta");
        assert!(runtime.bound_workspace("alpha").unwrap().is_none());
        assert_eq!(
            fs::read_to_string(project.join("keep.txt")).unwrap(),
            "project data"
        );
        let confirmed = call(
            &app,
            &second,
            "select",
            json!({"workspace_id":"beta"}),
            StatusCode::OK,
        )
        .await;
        assert_eq!(confirmed["data"]["reloaded"], false);
        let empty = call(
            &app,
            &first,
            "delete",
            json!({"workspace_id":"beta"}),
            StatusCode::OK,
        )
        .await;
        assert_eq!(empty["data"]["current_workspace_id"], Value::Null);
        assert_eq!(empty["data"]["default_workspace_id"], "");
        assert_eq!(empty["data"]["workspaces"], json!([]));
        let raw: Value =
            serde_json::from_slice(&fs::read(&paths.workspace_config).unwrap()).unwrap();
        assert_eq!(raw["unknown_top"], json!({"future":1}));
        assert_eq!(raw["auto_default_workspace"], true);
    }

    #[test]
    fn an_old_registration_cannot_rebind_or_create_sessions_in_a_replacement() {
        let scratch = Scratch::new();
        let paths = scratch.paths();
        let registry = WorkspaceRegistry::new(paths.workspace_config, paths.resources);
        let runtime = Runtime::open(scratch.0.join("data")).unwrap();
        let original = registry
            .create("old-project", "Shared", false)
            .unwrap()
            .catalog
            .default_workspace()
            .unwrap()
            .clone();
        runtime
            .bind_workspace(&original.id, &original.path)
            .unwrap();
        std::thread::scope(|scope| {
            let (rebuilt, wait_for_rebuild) = std::sync::mpsc::channel();
            let (runtime, registry, stale) = (&runtime, &registry, &original);
            let stale_binding = scope.spawn(move || {
                if wait_for_rebuild.recv().is_err() {
                    return;
                }
                let result = runtime.bind_registered_workspace(
                    &stale.id,
                    &stale.path,
                    ToolSet::default(),
                    || {
                        let current = registry.load().unwrap();
                        if current
                            .by_id(&stale.id)
                            .is_some_and(|workspace| workspace.path == stale.path)
                        {
                            return Ok(());
                        }
                        Err(RuntimeError {
                            status: 404,
                            message: "Stale registration".into(),
                        })
                    },
                );
                assert_eq!(result.unwrap_err().status, 404);
            });
            runtime
                .remove_idle_workspace(&original.id, || {
                    registry.delete(&original.id).map_err(registry_error)
                })
                .unwrap();
            let replacement = registry
                .create("new-project", "Shared", false)
                .unwrap()
                .catalog
                .default_workspace()
                .unwrap()
                .clone();
            assert_eq!(original.id, replacement.id);
            assert_ne!(original.path, replacement.path);
            runtime
                .bind_workspace(&replacement.id, &replacement.path)
                .unwrap();
            rebuilt.send(()).unwrap();
            stale_binding.join().unwrap();
            assert_eq!(
                runtime.bound_workspace(&replacement.id).unwrap().as_ref(),
                Some(&replacement.path)
            );
            let rejected = runtime
                .create_registered_session(&original, &json!({}))
                .unwrap_err();
            assert_eq!(rejected.status, 409);
            let created = runtime
                .create_registered_session(&replacement, &json!({}))
                .unwrap();
            let sessions = runtime.list_sessions(&replacement.id, 10, 0, None).unwrap();
            assert_eq!(sessions["total"], 1);
            assert_eq!(
                sessions["conversations"][0]["id"],
                created["conversation_id"]
            );
        });
    }
}
