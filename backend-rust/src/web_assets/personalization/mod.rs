//! Host personalization settings for the browser and authenticated local Gateway.

mod metadata;
mod schema;
mod store;

pub(crate) use metadata::default_admin_tool_categories;

use crate::{catalog::CatalogSource, workspace_catalog::RegisteredWorkspace};
use axum::{
    Extension, Json, Router,
    extract::{State, rejection::JsonRejection},
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::get,
};
use metadata::{Policy, skill_selection, skills_catalog};
use schema::{Object, compression, compression_triggers, normalize, resolved_deep_trigger, schema};
use serde_json::{Map, Value, json};
use std::{
    io,
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
};

pub(crate) fn deferred_registry() -> &'static [Value] {
    &schema().tool_loading_registry
}

pub(crate) fn web_service_preferences(data: &Path) -> io::Result<Value> {
    Store::open(data)?.network_settings().map(Value::Object)
}

pub(crate) fn deferred_snapshot(data: &Path) -> io::Result<Option<Value>> {
    Store::open(data)?.agent_tool_loading()
}

fn tool_loading_snapshot(config: &Object) -> Option<Value> {
    if config.get("tool_loading_enabled") != Some(&Value::Bool(true)) {
        return None;
    }
    let deferred = schema::deferred_tools(config.get("tool_loading_deferred").unwrap_or(&Value::Null));
    Some(json!({
        "enabled": true, "deferred_set": deferred,
        "initial_exposed": [], "loaded": [], "pending": deferred,
    }))
}
use store::Store;

type SkillSync =
    dyn Fn(&RegisteredWorkspace, &Path, &Path, &[String]) -> io::Result<()> + Send + Sync;

#[derive(Clone)]
pub struct Personalization {
    preferences: Arc<HostPreferences>,
    skill_sync: Option<Arc<SkillSync>>,
}

struct HostPreferences {
    catalog: CatalogSource,
    global_skills: PathBuf,
    private_skills: PathBuf,
    updates: Mutex<()>,
}

impl Personalization {
    pub fn new(catalog: CatalogSource, global_skills: PathBuf, private_skills: PathBuf) -> Self {
        Self {
            preferences: Arc::new(HostPreferences {
                catalog,
                global_skills,
                private_skills,
                updates: Mutex::new(()),
            }),
            skill_sync: None,
        }
    }

    /// Refresh the selected, server-resolved workspace's skill mirror after a settings update.
    pub fn with_skill_sync(
        mut self,
        sync: impl Fn(&RegisteredWorkspace, &Path, &Path, &[String]) -> io::Result<()>
        + Send
        + Sync
        + 'static,
    ) -> Self {
        self.skill_sync = Some(Arc::new(sync));
        self
    }

    /// Snapshot the persisted Agent preferences used when creating a conversation.
    /// Call on a blocking thread. A validated browser choice may override the browser default.
    pub fn agent_creation_preferences(&self) -> io::Result<Value> {
        let prefs = &self.preferences;
        let _lock = prefs
            .updates
            .lock()
            .map_err(|_| io::Error::other("personalization settings lock is poisoned"))?;
        let store = Store::open(prefs.catalog.host_data_dir())?;
        let config = store.read(&prefs.catalog.load())?;
        let mut metadata = json!({
            "browser_tools_enabled": config["browser_tools_default"] == true,
            "git_worktree_enabled": config["git_worktree_enabled"] == true,
        });
        if let Some(state) = tool_loading_snapshot(&config) {
            metadata["tool_loading"] = state;
        }
        let tokens = resolved_deep_trigger(&config);
        if (1_000..=2_000_000).contains(&tokens) {
            metadata["compress_limit_tokens"] = json!(tokens);
            metadata["compress_limit_source"] = json!("snapshot");
        }
        Ok(metadata)
    }

    /// Account Chat settings share the Agent personalization document and its
    /// in-process write lock. The snapshot excludes secrets and runs no Agent
    /// schema normalization; PATCH preserves every unknown key in place.
    pub fn chat_preferences(&self) -> io::Result<Value> {
        let preferences = &self.preferences;
        let _lock = preferences
            .updates
            .lock()
            .map_err(|_| io::Error::other("personalization settings lock is poisoned"))?;
        let store = Store::open(preferences.catalog.host_data_dir())?;
        Ok(Value::Object(store.read_for_chat()?))
    }

    pub fn chat_patch(&self, patch: &Map<String, Value>) -> io::Result<Value> {
        let preferences = &self.preferences;
        let _lock = preferences
            .updates
            .lock()
            .map_err(|_| io::Error::other("personalization settings lock is poisoned"))?;
        let store = Store::open(preferences.catalog.host_data_dir())?;
        Ok(Value::Object(store.write_chat_patch(patch)?))
    }

    /// Mount under the protection used for Host cookie+CSRF and local Host Bearer requests.
    pub fn router(&self) -> Router {
        Router::new()
            .route("/api/personalization", get(load).post(save))
            .with_state(self.clone())
    }
}

pub(crate) fn runtime_categories(data: &Path) -> io::Result<(Vec<Value>,bool)> {
    let config = store::read_object(&data.join("personalization.json"))?.unwrap_or_default();
    let policy = Policy::load(data)?;
    Ok((policy.tool_categories(data,&config),policy.blocks_tool_toggles()))
}



pub(crate) fn require_read_before_edit(data:&Path)->io::Result<bool>{
    Ok(store::read_object(&data.join("personalization.json"))?.and_then(|cfg|cfg.get("require_read_before_edit").cloned())==Some(Value::Bool(true)))
}

pub(crate) fn shallow_options(data: &Path) -> io::Result<Option<crate::runtime::shallow::Settings>> {
    let prefs = store::read_object(&data.join("personalization.json"))?.unwrap_or_default();
    if prefs.get("auto_shallow_compress_enabled") != Some(&Value::Bool(true)) { return Ok(None); }
    let number = |key:&str, default| prefs.get(key).and_then(Value::as_u64).unwrap_or(default);
    Ok(Some(crate::runtime::shallow::Settings {
        threshold:number("shallow_compress_trigger_tokens",80000).max(1),
        interval:number("shallow_compress_trigger_tool_calls_interval",10).max(1),
        batch:number("shallow_compress_max_replace_per_round",10).clamp(1,10000) as usize,
        recent_tools:number("shallow_compress_keep_recent_tools",15) as usize,
        user_turns:number("shallow_compress_keep_user_turn_tools",3) as usize,
    }))
}

pub(crate) fn automatic_deep_limit(data: &Path, metadata: &Value) -> io::Result<Option<u64>> {
    let prefs=store::read_object(&data.join("personalization.json"))?.unwrap_or_default();
    if prefs.get("auto_deep_compress_enabled")==Some(&Value::Bool(false)){ return Ok(None); }
    let configured=metadata["compress_limit_tokens"].as_u64()
        .or_else(||prefs.get("deep_compress_trigger_tokens").and_then(Value::as_u64)).unwrap_or(250000);
    Ok(Some(configured.saturating_add(metadata["context_baseline_tokens"].as_u64().unwrap_or(0))))
}
pub(crate) fn versioning_default(data: &Path) -> io::Result<bool> {
    Ok(store::read_object(&data.join("personalization.json"))?
        .and_then(|config| config.get("versioning_enabled_by_default").and_then(Value::as_bool))
        .unwrap_or(true))
}

pub(crate) fn english(data: &Path) -> bool {
    store::english(data)
}

async fn load(State(preferences): State<Personalization>) -> Response {
    run(preferences, None, None).await
}

async fn save(
    State(preferences): State<Personalization>,
    workspace: Option<Extension<RegisteredWorkspace>>,
    body: Result<Json<Option<Object>>, JsonRejection>,
) -> Response {
    let patch = match body {
        Ok(Json(patch)) => patch.unwrap_or_default(),
        Err(error) => {
            let status = match error.status() {
                StatusCode::UNPROCESSABLE_ENTITY => StatusCode::BAD_REQUEST,
                status => status,
            };
            return reply(
                status,
                json!({"success":false,"error":"Expected a JSON object for personalization settings"}),
            );
        }
    };
    run(
        preferences,
        Some(patch),
        workspace.map(|Extension(workspace)| workspace),
    )
    .await
}

async fn run(
    preferences: Personalization,
    patch: Option<Object>,
    workspace: Option<RegisteredWorkspace>,
) -> Response {
    let result = tokio::task::spawn_blocking(move || {
        preferences.preferences.update(
            patch,
            workspace.as_ref().zip(preferences.skill_sync.as_deref()),
        )
    })
    .await;
    match result {
        Ok(Ok(response)) => response,
        Ok(Err(error)) => failure(error),
        Err(error) => failure(error),
    }
}

impl HostPreferences {
    fn update(
        &self,
        patch: Option<Object>,
        skill_sync: Option<(&RegisteredWorkspace, &SkillSync)>,
    ) -> io::Result<Response> {
        let _lock = self
            .updates
            .lock()
            .map_err(|_| io::Error::other("personalization settings lock is poisoned"))?;
        let data = self.catalog.host_data_dir();
        let policy = Policy::load(data)?;
        if policy.blocks_personalization() {
            let message = localized(
                english(data),
                "个人空间已被管理员禁用",
                "Personal space has been disabled by the admin",
            );
            return Ok(reply(
                StatusCode::FORBIDDEN,
                json!({"success":false,"error":message}),
            ));
        }
        let store = Store::open(data)?;
        let catalog = self.catalog.load();
        let mut config = store.read(&catalog)?;
        let is_update = patch.is_some();
        if let Some(patch) = patch {
            let english = config["ui_locale"] == "en-US";
            config = match normalize(&patch, &config, &catalog, true) {
                Ok(config) => config,
                Err(message) => {
                    return Ok(reply(
                        StatusCode::BAD_REQUEST,
                        json!({"success":false,"error":message}),
                    ));
                }
            };
            let (shallow, deep) = compression_triggers(&config);
            if deep <= shallow {
                let message = localized(
                    english,
                    "深压缩触发上下文必须大于浅压缩触发上下文",
                    "Deep compression trigger context must be greater than shallow compression trigger context",
                );
                return Ok(reply(
                    StatusCode::BAD_REQUEST,
                    json!({"success":false,"error":message}),
                ));
            }
        }
        let skills = skills_catalog(&self.global_skills, &self.private_skills);
        let (selected, snapshot) = skill_selection(&config, &skills);
        if is_update {
            config["enabled_skills"] = if selected == snapshot {
                Value::Null
            } else {
                json!(&selected)
            };
            config["skills_catalog_snapshot"] = json!(snapshot);
            store.write(&config)?;
            if let Some((workspace, sync)) = skill_sync {
                let enabled = selected
                    .iter()
                    .map(|id| (*id).to_owned())
                    .collect::<Vec<_>>();
                if let Err(error) = sync(
                    workspace,
                    &self.global_skills,
                    &self.private_skills,
                    &enabled,
                ) {
                    eprintln!("Host skill mirror: {error}");
                }
            }
        }
        config["enabled_skills"] = json!(selected);
        let context_compression = compression(&config, &catalog);
        let categories = policy.tool_categories(data, &config);
        let mut response = json!({
            "success": true, "data": config, "tool_categories": categories, "skills_catalog": skills,
            "context_compression_settings": context_compression,
            "recent_conversations_prompt_limit_range": {"min":1,"max":30},
            "project_memory_inject_limit_range": {"min":5,"max":null},
        });
        if !is_update {
            response["tool_loading_registry"] = json!(schema().tool_loading_registry);
        }
        Ok(reply(StatusCode::OK, response))
    }
}

fn localized(english: bool, chinese: &'static str, en_us: &'static str) -> &'static str {
    if english { en_us } else { chinese }
}

fn failure(error: impl std::fmt::Display) -> Response {
    eprintln!("Host personalization: {error}");
    reply(
        StatusCode::INTERNAL_SERVER_ERROR,
        json!({"success":false,"error":"Personalization settings are unavailable."}),
    )
}

fn reply(status: StatusCode, document: Value) -> Response {
    super::no_store((status, Json(document)).into_response())
}
