mod lifecycle;

use crate::gateway::{Gateway, run};
use astrion_headless::{
    catalog::Catalog, navigation::Navigation, runtime::RuntimeError, web_status::admin_policy,
    workspace_catalog::RegisteredWorkspace,
    worktree::{Binding, Target, Worktree},
};
use axum::{
    Extension, Json, Router,
    extract::{Path, Query, State},
    http::StatusCode,
    middleware,
    routing::get,
};
use serde_json::{Map, Value, json};
use std::{collections::HashMap, sync::Arc};

type Reply = (StatusCode, Json<Value>);
const PERMISSIONS: &[&str] = &["readonly", "approval", "auto_approval", "unrestricted"];
const EXECUTION: &[&str] = &["sandbox", "direct"];

pub fn router(app: Arc<Gateway>, navigation: Navigation, selection: Arc<astrion_headless::runtime::ConversationSelection>) -> Router {
    Router::new()
        .route("/api/conversations", get(list).post(create))
        .route("/api/conversations/search", get(search))
        .route("/api/conversations/statistics", get(statistics))
        .route("/api/conversations/{id}/compress", axum::routing::post(compress))
        .route("/api/conversations/{id}/compression_status", get(compression_status))
        .route("/api/conversations/{id}/compression_cancel", axum::routing::post(compression_cancel))
        .route("/api/todo-list", get(read_todo))
        .route("/api/tool-settings", get(get_tool_settings).post(set_tool_settings))
        .route("/api/conversations/{id}", get(info))
        .route("/api/conversations/{id}/tokens", get(context_tokens))
        .route(
            "/api/input-draft",
            get(read_draft).put(save_draft).post(save_draft),
        )
        .with_state(app.clone())
        .route_layer(middleware::from_fn_with_state(app.clone(), crate::gateway::workspace_scope))
        .merge(lifecycle::router(app, navigation, selection))
}

#[derive(serde::Deserialize, Default)]
struct SearchQuery {
    q: Option<String>,
    limit: Option<String>,
    all_workspaces: Option<String>,
}

async fn search(
    State(app): State<Arc<Gateway>>,
    Extension(ws): Extension<RegisteredWorkspace>,
    Query(query): Query<SearchQuery>,
) -> Reply {
    let text = query.q.as_deref().unwrap_or_default().trim().to_owned();
    if text.is_empty() {
        return (StatusCode::BAD_REQUEST, Json(json!({"success":false,"error":"Missing query parameter","message":"请提供搜索关键词"})));
    }
    let limit = query.limit.as_deref().and_then(|s| s.parse::<i64>().ok()).unwrap_or(20).clamp(1, 50) as usize;
    run(StatusCode::OK, move || {
        let needle = text.to_lowercase();
        if !matches!(query.all_workspaces.as_deref(), Some("1" | "true" | "True")) {
            let rows = app.runtime.search_conversations(&ws.id, &needle, limit)?;
            return Ok(json!({"success":true,"data":{"results":rows,"count":rows.len(),"query":text}}));
        }
        let catalog = app.paths.catalog().map_err(|e| RuntimeError { status: 500, message: e.to_string() })?;
        let mut groups = Vec::new();
        let mut total = 0;
        for workspace in catalog.workspaces {
            let Ok(rows) = app.runtime.search_conversations(&workspace.id, &needle, limit) else { continue };
            if rows.is_empty() { continue; }
            total += rows.len();
            groups.push(json!({"workspace_id":workspace.id,"label":workspace.label,"count":rows.len(),"results":rows}));
        }
        Ok(json!({"success":true,"data":{"query":text,"groups":groups,"total":total}}))
    }).await
}

async fn statistics(
    State(app): State<Arc<Gateway>>,
    Extension(ws): Extension<RegisteredWorkspace>,
) -> Reply {
    run(StatusCode::OK, move || app.runtime.web_statistics(&ws.id)).await
}

async fn context_tokens(
    State(app): State<Arc<Gateway>>,
    Extension(ws): Extension<RegisteredWorkspace>,
    Path(id): Path<String>,
) -> Reply {
    run(StatusCode::OK, move || app.runtime.web_context_tokens(&ws.id, &id)).await
}

#[derive(serde::Deserialize)]
struct DraftQuery {
    conversation_id: Option<String>,
}

fn normalized_id(id: String) -> String {
    if id.starts_with("conv_") { id } else { format!("conv_{id}") }
}

async fn compress(State(app): State<Arc<Gateway>>, Extension(ws): Extension<RegisteredWorkspace>, Extension(principal): Extension<astrion_headless::approval::ApprovalPrincipal>, Path(id): Path<String>) -> Reply {
    run(StatusCode::ACCEPTED, move || {
        let id=normalized_id(id);
        let policy=astrion_headless::web_status::admin_policy(app.catalog.host_data_dir(),&app.catalog.load()).map_err(|e| RuntimeError{status:503,message:e.to_string()})?;
        if policy["ui_blocks"]["block_compress_conversation"]==true {
            return Err(RuntimeError{status:403,message:"管理员已禁用对话压缩".into()});
        }
        let model=app.turn_model(&ws,&json!({"conversation_id":id}))?;
        let settings=astrion_headless::runtime::resolve_task_approval_settings(principal,&app.catalog)?;
        app.runtime.request_compression(&ws,&id,model,settings)
    }).await
}

async fn compression_status(State(app): State<Arc<Gateway>>, Extension(ws): Extension<RegisteredWorkspace>, Path(id): Path<String>) -> Reply {
    run(StatusCode::OK,move ||app.runtime.compression_status(&ws,&normalized_id(id))).await
}

async fn compression_cancel(State(app): State<Arc<Gateway>>, Extension(ws): Extension<RegisteredWorkspace>, Path(id): Path<String>) -> Reply {
    run(StatusCode::OK,move ||app.runtime.cancel_compression(&ws,&normalized_id(id))).await
}

async fn get_tool_settings(
    State(app): State<Arc<Gateway>>, Extension(ws): Extension<RegisteredWorkspace>, Query(q): Query<DraftQuery>,
) -> Reply {
    run(StatusCode::OK, move || app.runtime.tool_settings(&ws, q.conversation_id.as_deref(), None)).await
}

async fn set_tool_settings(
    State(app): State<Arc<Gateway>>, Extension(ws): Extension<RegisteredWorkspace>, Query(q): Query<DraftQuery>, Json(body): Json<Value>,
) -> Reply {
    run(StatusCode::OK, move || app.runtime.tool_settings(&ws, q.conversation_id.as_deref(), Some(&body))).await
}

async fn read_todo(
    State(app): State<Arc<Gateway>>,
    Extension(ws): Extension<RegisteredWorkspace>,
    Query(scope): Query<DraftQuery>,
) -> Reply {
    run(StatusCode::OK, move || app.runtime.web_todo_list(&ws, scope.conversation_id.as_deref())).await
}

async fn read_draft(
    State(app): State<Arc<Gateway>>,
    Extension(ws): Extension<RegisteredWorkspace>,
    Query(scope): Query<DraftQuery>,
) -> Reply {
    run(StatusCode::OK, move || {
        let cid = scope
            .conversation_id
            .as_deref()
            .ok_or_else(|| invalid("conversation_id is required (use new for an empty view)"))?;
        app.runtime.read_composer_draft(&ws, cid)
    })
    .await
}

async fn save_draft(
    State(app): State<Arc<Gateway>>,
    Extension(ws): Extension<RegisteredWorkspace>,
    Query(scope): Query<DraftQuery>,
    Json(body): Json<Value>,
) -> Reply {
    run(StatusCode::OK, move || {
        let cid = scope
            .conversation_id
            .as_deref()
            .ok_or_else(|| invalid("conversation_id is required (use new for an empty view)"))?;
        app.runtime.save_composer_draft(&ws, cid, &body)
    })
    .await
}

async fn list(
    State(app): State<Arc<Gateway>>,
    Extension(ws): Extension<RegisteredWorkspace>,
    Query(query): Query<HashMap<String, String>>,
) -> Reply {
    let number = |name: &str, fallback| {
        query
            .get(name)
            .and_then(|s| s.parse::<i64>().ok())
            .unwrap_or(fallback)
    };
    let limit = number("limit", 20).clamp(1, 10_000) as usize;
    let offset = number("offset", 0).max(0) as usize;
    let non_empty = matches!(
        query.get("non_empty").map(String::as_str),
        Some("1" | "true" | "True")
    );
    run(StatusCode::OK, move || {
        app.runtime
            .web_conversation_list(&ws.id, limit, offset, non_empty)
    })
    .await
}

async fn info(
    State(app): State<Arc<Gateway>>,
    Extension(ws): Extension<RegisteredWorkspace>,
    Path(id): Path<String>,
) -> Reply {
    run(StatusCode::OK, move || {
        app.runtime.web_conversation_info(&ws.id, &id)
    })
    .await
}

async fn create(
    State(app): State<Arc<Gateway>>,
    Extension(ws): Extension<RegisteredWorkspace>,
    body: Result<Json<Value>, axum::extract::rejection::JsonRejection>,
) -> Reply {
    run(StatusCode::CREATED, move || {
        let Json(body) = body.map_err(|_| invalid("Expected an object"))?;
        let catalog = app.catalog.load();
        let policy = admin_policy(app.catalog.host_data_dir(), &catalog).map_err(storage)?;
        let preferences = app
            .preferences
            .agent_creation_preferences()
            .map_err(storage)?;
        let settings = &body["settings"];
        let mut metadata = creation_metadata(settings, preferences, &catalog, &policy)?;
        let binding = prepare_worktree_creation(&ws, settings, &mut metadata)?;
        app.runtime.create_web_conversation(&ws, metadata).map_err(|mut error| {
            if let Some(binding) = binding {
                error.message.push_str(&format!("\n已保留分支 {}，目录 {}；可进入已有工作树。", binding.branch, binding.directory.display()));
            }
            error
        })
    })
    .await
}

fn invalid(message: impl Into<String>) -> RuntimeError {
    RuntimeError {
        status: 400,
        message: message.into(),
    }
}
fn storage(error: std::io::Error) -> RuntimeError {
    RuntimeError {
        status: 500,
        message: error.to_string(),
    }
}

fn creation_metadata(
    settings: &Value,
    preferences: Value,
    catalog: &Catalog,
    policy: &Value,
) -> Result<Map<String, Value>, RuntimeError> {
    let Some(input) = settings.as_object() else {
        return Err(invalid("Complete new conversation settings are required"));
    };
    let Value::Object(mut metadata) = preferences else {
        return Err(storage(std::io::Error::other(
            "Invalid creation preferences",
        )));
    };
    for (key, allowed) in [
        ("work_mode", &["plan", "ask", "execute"][..]),
        ("permission_mode", PERMISSIONS),
        ("execution_mode", EXECUTION),
        ("network_permission", &["restricted", "full", "none"]),
        ("run_mode", &["fast", "thinking"]),
    ] {
        if !settings[key].as_str().is_some_and(|v| allowed.contains(&v)) {
            return Err(invalid(format!("Invalid {key}")));
        }
        metadata.insert(key.into(), settings[key].clone());
    }
    if settings["thinking_mode"].as_bool() != Some(settings["run_mode"] == "thinking") {
        return Err(invalid("thinking_mode does not match run_mode"));
    }
    if settings["work_mode"] == "plan" && settings["permission_mode"] != "readonly"
        || settings["permission_mode"] == "readonly" && settings["execution_mode"] != "sandbox"
    {
        return Err(invalid(
            "Conversation settings violate plan or permission sandbox rules",
        ));
    }
    if !input.contains_key("reasoning_effort")
        || !(settings["reasoning_effort"].is_null()
            || matches!(
                settings["reasoning_effort"].as_str(),
                Some("low" | "medium" | "high" | "xhigh" | "max")
            ))
    {
        return Err(invalid("Invalid reasoning_effort"));
    }
    let models = catalog.models_response();
    let profiles = models["items"]
        .as_array()
        .map(Vec::as_slice)
        .unwrap_or_default();
    let profile = profiles
        .iter()
        .find(|p| p["model_key"] == settings["model_key"])
        .ok_or_else(|| {
            invalid(if profiles.is_empty() {
                "尚未配置任何模型，请先在设置页的「提供商」中添加"
            } else {
                "Selected model is unavailable"
            })
        })?;
    if profile["thinking_only"] == true && settings["run_mode"] != "thinking"
        || profile["fast_only"] == true && settings["run_mode"] != "fast"
    {
        return Err(invalid(
            "Selected model does not support the requested thinking mode",
        ));
    }
    if policy["disabled_models"]
        .as_array()
        .is_some_and(|keys| keys.contains(&settings["model_key"]))
    {
        return Err(RuntimeError {
            status: 403,
            message: "Selected model is disabled by administrator policy".into(),
        });
    }
    for key in ["thinking_mode", "model_key", "reasoning_effort"] {
        metadata.insert(key.into(), settings[key].clone());
    }
    metadata.insert(
        "chatgpt_fast_mode".into(),
        json!(match input.get("chatgpt_fast_mode") {
            Some(v) => v
                .as_bool()
                .ok_or_else(|| invalid("ChatGPT fast mode must be a boolean"))?,
            None => false,
        }),
    );
    if !settings["browser_tools"].is_null() {
        metadata.insert(
            "browser_tools_enabled".into(),
            json!(
                settings["browser_tools"]
                    .as_bool()
                    .ok_or_else(|| invalid("Invalid browser_tools"))?
            ),
        );
    }
    if input.get("restore_modes").is_some_and(|r| !r.is_object()) {
        return Err(invalid("Invalid restore_modes"));
    }
    for (key, allowed) in [
        ("pre_plan_permission_mode", PERMISSIONS),
        ("pre_plan_execution_mode", EXECUTION),
        ("pre_readonly_execution_mode", EXECUTION),
    ] {
        let value = &settings["restore_modes"][key];
        if !value.is_null() && !value.as_str().is_some_and(|v| allowed.contains(&v)) {
            return Err(invalid(format!("Invalid {key}")));
        }
        metadata.insert(key.into(), value.clone());
    }
    metadata.insert("pending_network_permission".into(), Value::Null);
    Ok(metadata)
}

fn prepare_worktree_creation(
    workspace: &RegisteredWorkspace,
    settings: &Value,
    metadata: &mut Map<String, Value>,
) -> Result<Option<Binding>, RuntimeError> {
    if settings["git_worktree_target"].is_null() {
        return Ok(None);
    }
    if metadata.get("git_worktree_enabled") != Some(&Value::Bool(true)) {
        return Err(invalid("当前新对话未启用 Git worktree。"));
    }
    let target = serde_json::from_value::<Target>(settings["git_worktree_target"].clone())
        .map_err(|error| invalid(format!("工作树目标无效：{error}")))?;
    let binding = Worktree::new(&workspace.path).and_then(|worktree| {
        worktree.prepare(target, &workspace.path,
            settings["work_mode"].as_str().expect("validated work mode"),
            settings["permission_mode"].as_str().expect("validated permission mode"))
    }).map_err(|error| invalid(error.to_string()))?;
    metadata.insert("git_worktree".into(), json!(binding));
    Ok(Some(binding))
}
