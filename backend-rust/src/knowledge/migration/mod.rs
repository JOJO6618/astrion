//! Authenticated, single-flight migration of indexed Host workspace memories.

mod files;

use std::{
    collections::HashSet,
    io,
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
};

use axum::{
    Json, Router,
    extract::{Query, State},
    http::StatusCode,
    routing::{get, post},
};
use serde::Deserialize;
use serde_json::{Value, json};
use uuid::Uuid;

use crate::workspace_catalog::manage::WorkspaceRegistry;

use self::files::Workspace;

#[derive(Clone)]
struct Migration {
    data: PathBuf,
    registry: WorkspaceRegistry,
    state: Arc<Mutex<JobState>>,
}

#[derive(Default)]
struct JobState {
    startup_shown: bool,
    job: Option<Value>,
}

impl JobState {
    fn active(&self) -> Option<&Value> {
        self.job
            .as_ref()
            .filter(|job| matches!(job["phase"].as_str(), Some("copying" | "verifying")))
    }
}

type Reply = (StatusCode, Json<Value>);

/// Mount on the authenticated Host browser API with its CSRF guard.
pub fn router(data: PathBuf, registry: WorkspaceRegistry) -> Router {
    let migration = Migration {
        data,
        registry,
        state: Arc::new(Mutex::new(JobState::default())),
    };
    Router::new()
        .route("/api/project-memory-migration", get(overview).post(start))
        .route("/api/project-memory-migration/startup", post(startup))
        .with_state(migration)
}

#[derive(Deserialize)]
struct OverviewQuery {
    job: Option<String>,
}

async fn overview(State(migration): State<Migration>, Query(query): Query<OverviewQuery>) -> Reply {
    blocking(move || {
        {
            let state = migration.state.lock().unwrap();
            if let Some(id) = query.job.filter(|id| !id.is_empty()) {
                return Ok(match state.job.as_ref().filter(|job| job["id"] == id) {
                    Some(job) => reply(StatusCode::OK, json!({"job": job})),
                    None => reply(
                        StatusCode::NOT_FOUND,
                        json!({"error": "迁移记录已过期，请重新检查待迁移的记忆。"}),
                    ),
                });
            }
            if let Some(job) = state.active() {
                return Ok(reply(StatusCode::OK, json!({"job": job})));
            }
        }
        let scan = migration.scan()?;
        Ok(reply(
            StatusCode::OK,
            json!({"overview": scan.overview(&migration.data)}),
        ))
    })
    .await
}

async fn startup(State(migration): State<Migration>) -> Reply {
    blocking(move || {
        let mut state = migration.state.lock().unwrap();
        if state.startup_shown || state.active().is_some() {
            return Ok(reply(StatusCode::OK, json!({})));
        }
        let scan = migration.scan()?;
        state.startup_shown = true;
        let body = if scan.total() > 0 {
            json!({"overview": scan.overview(&migration.data)})
        } else {
            json!({})
        };
        Ok(reply(StatusCode::OK, body))
    })
    .await
}

async fn start(State(migration): State<Migration>) -> Reply {
    blocking(move || {
        let mut state = migration.state.lock().unwrap();
        if let Some(job) = state.active() {
            return Ok(reply(StatusCode::OK, json!({"job": job})));
        }
        let scan = migration.scan()?;
        if scan.total() == 0 {
            return Ok(reply(StatusCode::OK, json!({"overview": scan.overview(&migration.data)})));
        }
        let job = json!({
            "id": Uuid::new_v4().simple().to_string(), "mode": "host", "phase": "copying", "current": "",
            "total": scan.total(), "copied": 0, "verified": 0, "migrated": 0,
            "completed_workspaces": 0, "errors": scan.errors, "conflicts": [],
        });
        let runner = migration.clone();
        std::thread::Builder::new().name("project-memory-migration".into()).spawn(move || runner.run(scan))?;
        state.job = Some(job.clone());
        Ok(reply(StatusCode::ACCEPTED, json!({"job": job})))
    }).await
}

struct Scan {
    data: PathBuf,
    workspaces: Vec<Workspace>,
    errors: Vec<Value>,
}

impl Scan {
    fn total(&self) -> usize {
        self.workspaces
            .iter()
            .map(|workspace| workspace.files.len())
            .sum()
    }

    fn overview(&self, data: &Path) -> Value {
        let workspaces: Vec<_> = self
            .workspaces
            .iter()
            .map(|w| json!({"label": w.label, "target": w.target, "count": w.files.len()}))
            .collect();
        json!({"mode": "host", "data_root": data.join("memory"), "users": 1,
            "total": self.total(), "workspaces": workspaces, "errors": self.errors})
    }
}

impl Migration {
    fn scan(&self) -> io::Result<Scan> {
        let data = self.data.canonicalize()?;
        let (mut workspaces, mut errors, mut seen) = (Vec::new(), Vec::new(), HashSet::new());
        for entry in self.registry.load()?.workspaces {
            let label = if entry.label.is_empty() {
                entry.id
            } else {
                entry.label
            };
            let result = (|| {
                let project = match entry.path.canonicalize() {
                    Ok(project) => project,
                    Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
                    Err(error) => return Err(error),
                };
                if !seen.insert(project.clone()) {
                    return Ok(None);
                }
                let files = files::list(&project)?;
                if files.is_empty() {
                    return Ok(None);
                }
                let target = files::target_path(&data, &project)?;
                Ok(Some(Workspace {
                    label: label.clone(),
                    project,
                    target,
                    files,
                }))
            })();
            match result {
                Ok(Some(workspace)) => workspaces.push(workspace),
                Ok(None) => {}
                Err(error) => errors.push(workspace_error(&label, error)),
            }
        }
        Ok(Scan {
            data,
            workspaces,
            errors,
        })
    }

    fn update(&self, action: impl FnOnce(&mut Value)) {
        action(
            self.state
                .lock()
                .unwrap()
                .job
                .as_mut()
                .expect("active migration job"),
        );
    }

    fn run(&self, scan: Scan) {
        let mut errors = scan.errors;
        let mut copies = Vec::new();
        for workspace in scan.workspaces {
            self.update(|job| job["current"] = json!(workspace.label));
            match files::copy(&scan.data, &workspace, || {
                self.update(|job| increment(job, "copied", 1))
            }) {
                Ok(result) => copies.push((workspace, result)),
                Err(error) => errors.push(workspace_error(&workspace.label, error)),
            }
        }
        self.update(|job| job["phase"] = json!("verifying"));
        let mut conflicts = Vec::new();
        for (workspace, result) in copies {
            self.update(|job| job["current"] = json!(workspace.label));
            match files::verify_and_clean(&scan.data, &workspace, &result, || {
                self.update(|job| increment(job, "verified", 1))
            }) {
                Ok(()) => {
                    conflicts.extend(result.iter().filter(|copy| copy.source != copy.target).map(|copy|
                        json!({"workspace": workspace.label, "source": copy.source, "target": copy.target})
                    ));
                    self.update(|job| {
                        increment(job, "completed_workspaces", 1);
                        increment(job, "migrated", result.len());
                    });
                }
                Err(error) => errors.push(workspace_error(&workspace.label, error)),
            }
        }
        self.update(|job| {
            job["phase"] = json!(if errors.is_empty() { "done" } else { "failed" });
            job["current"] = json!("");
            job["errors"] = json!(errors);
            job["conflicts"] = json!(conflicts);
        });
    }
}

fn increment(job: &mut Value, key: &str, count: usize) {
    job[key] = json!(job[key].as_u64().unwrap_or(0) + count as u64);
}

fn workspace_error(label: &str, error: io::Error) -> Value {
    json!({"workspace": label, "reason": error.to_string()})
}

async fn blocking(action: impl FnOnce() -> io::Result<Reply> + Send + 'static) -> Reply {
    match tokio::task::spawn_blocking(action).await {
        Ok(Ok(response)) => response,
        _ => reply(
            StatusCode::INTERNAL_SERVER_ERROR,
            json!({"error": "无法检查项目记忆。"}),
        ),
    }
}

fn reply(status: StatusCode, value: Value) -> Reply {
    (status, Json(value))
}
