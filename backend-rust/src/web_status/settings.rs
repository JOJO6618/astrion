use super::preferences::{
    Personalization, admin_policy, pending_modes, reasoning_effort, run_mode,
};
use super::{App, Result, Scope, WebError, conversation_id, optional_string, snapshot, success};
use axum::extract::{Query, State, rejection::JsonRejection};
use axum::http::{HeaderMap, StatusCode};
use axum::routing::get;
use axum::{Extension, Json, Router};
use serde_json::{Map, Value, json};
use std::collections::HashMap;
use std::sync::{Arc, Mutex};

const PERMISSIONS: &[&str] = &["readonly", "approval", "auto_approval", "unrestricted"];
const WORK_MODES: &[&str] = &["plan", "ask", "execute"];
const EXECUTION_MODES: &[&str] = &["sandbox", "direct"];
const NETWORK_MODES: &[&str] = &["restricted", "full", "none"];

#[derive(Clone, Default)]
struct DraftSettings(Arc<Mutex<HashMap<String, Map<String, Value>>>>);

pub(super) fn router() -> Router {
    let modes = [
        ("/api/work-mode", Mode::Work),
        ("/api/permission-mode", Mode::Permission),
        ("/api/execution-mode", Mode::Execution),
        ("/api/network-permission", Mode::Network),
    ];
    let mut router = Router::new();
    for (path, mode) in modes {
        router = router.route(path, get(get_mode).post(set_mode).layer(Extension(mode)));
    }
    for (path, setting) in [
        ("/api/thinking-mode", Setting::Thinking),
        ("/api/reasoning-effort", Setting::Reasoning),
        ("/api/model", Setting::Model),
        ("/api/conversation/compress-limit", Setting::Compression),
    ] {
        router = router.route(
            path,
            axum::routing::post(write_setting).layer(Extension(setting)),
        );
    }
    router.with_state(DraftSettings::default())
}

#[derive(Clone, Copy)]
enum Mode {
    Work,
    Permission,
    Execution,
    Network,
}

impl Mode {
    fn options(self) -> &'static [&'static str] {
        match self {
            Self::Work => WORK_MODES,
            Self::Permission => PERMISSIONS,
            Self::Execution => EXECUTION_MODES,
            Self::Network => NETWORK_MODES,
        }
    }

    fn target(self, body: &Value) -> Result<&'static str> {
        let value = body["mode"].as_str().map(str::trim).unwrap_or_default();
        self.options()
            .iter()
            .copied()
            .find(|mode| mode.eq_ignore_ascii_case(value))
            .ok_or_else(|| WebError::invalid("Invalid mode"))
    }

    fn pending_key(self) -> Option<&'static str> {
        match self {
            Self::Work => None,
            Self::Permission => Some("pending_permission_mode"),
            Self::Execution => Some("pending_execution_mode"),
            Self::Network => Some("pending_network_permission"),
        }
    }

    fn validate_target(self, prefs: &Personalization, metadata: &Value, target: &str) -> std::result::Result<(), crate::runtime::RuntimeError> {
        if matches!(self, Self::Execution) && target == "direct" {
            let current = prefs.modes(metadata);
            if current.work_mode == "plan" || current.permission_mode == "readonly" {
                return Err(crate::runtime::RuntimeError::new(409, "Direct execution is unavailable in plan or read-only mode"));
            }
        }
        Ok(())
    }

    fn response(self, prefs: &Personalization, metadata: &Value, id: Option<&str>, direct_enabled: bool, network_enabled: bool) -> Value {
        let effective = prefs.modes(metadata);
        let pending = pending_modes(metadata);
        match self {
            Self::Work => json!({ "success": true, "mode": effective.work_mode,
                "permission_mode": effective.permission_mode, "execution_mode": effective.execution_mode,
                "options": WORK_MODES, "conversation_id": id }),
            Self::Permission => json!({ "success": true, "mode": effective.permission_mode,
                "pending_mode": pending["permission_mode"], "options": PERMISSIONS,
                "state": {"mode": effective.execution_mode, "default_mode": "sandbox"}, "conversation_id": id }),
            Self::Execution => json!({ "success": true, "enabled": direct_enabled,
                "state": {"mode": effective.execution_mode, "default_mode": "sandbox"},
                "pending_mode": pending["execution_mode"], "options": EXECUTION_MODES }),
            Self::Network => json!({ "success": true, "enabled": network_enabled,
                "mode": effective.network_permission, "pending_mode": pending["network_permission"], "options": NETWORK_MODES }),
        }
    }

    fn apply(
        self,
        metadata: &mut Map<String, Value>,
        prefs: &Personalization,
        target: &'static str,
    ) {
        let current = prefs.modes(&Value::Object(metadata.clone()));
        match self {
            Self::Work => {
                if target == "plan" && current.work_mode != "plan" {
                    metadata.insert(
                        "pre_plan_permission_mode".into(),
                        json!(current.permission_mode),
                    );
                    metadata.insert(
                        "pre_plan_execution_mode".into(),
                        json!(current.execution_mode),
                    );
                    metadata.insert("permission_mode".into(), json!("readonly"));
                    metadata.insert("execution_mode".into(), json!("sandbox"));
                } else if target != "plan" && current.work_mode == "plan" {
                    let prior = metadata.remove("pre_plan_permission_mode");
                    let permission = prior
                        .as_ref()
                        .and_then(Value::as_str)
                        .filter(|mode| PERMISSIONS.contains(mode))
                        .unwrap_or_else(|| prefs.modes(&json!({"work_mode":"execute"})).permission_mode);
                    let before = metadata.remove("pre_plan_execution_mode");
                    let execution = if permission != "readonly" && before.is_some_and(|mode| mode == "direct") {
                        "direct"
                    } else {
                        "sandbox"
                    };
                    metadata.insert("permission_mode".into(), json!(permission));
                    metadata.insert("execution_mode".into(), json!(execution));
                }
                metadata.insert("work_mode".into(), json!(target));
            }
            Self::Permission => {
                let target = if current.work_mode == "plan" {
                    "readonly"
                } else {
                    target
                };
                if target == "readonly" && current.permission_mode != "readonly" {
                    metadata.insert(
                        "pre_readonly_execution_mode".into(),
                        json!(current.execution_mode),
                    );
                    metadata.insert("execution_mode".into(), json!("sandbox"));
                } else if target != "readonly" && current.permission_mode == "readonly" {
                    let prior = metadata.remove("pre_readonly_execution_mode");
                    if prior.is_some_and(|mode| mode == "direct") {
                        metadata.insert("execution_mode".into(), json!("direct"));
                    }
                }
                metadata.insert("permission_mode".into(), json!(target));
                metadata.remove("pending_permission_mode");
            }
            Self::Execution => {
                metadata.insert("execution_mode".into(), json!(target));
                metadata.remove("pending_execution_mode");
            }
            Self::Network => {
                metadata.insert("network_permission".into(), json!(target));
                metadata.remove("pending_network_permission");
            }
        }
    }
}

pub(crate) fn apply_pending_host_modes(data: &std::path::Path, before: &Value, direct: bool, network: bool) -> Value {
    let Some(mut metadata) = before.as_object().cloned() else { return before.clone(); };
    let prefs = Personalization::load(data);
    for mode in [Mode::Permission, Mode::Execution, Mode::Network] {
        let key = mode.pending_key().expect("queueable mode");
        let Some(pending) = metadata.remove(key) else { continue; };
        let Some(target) = mode.options().iter().copied().find(|target| pending == *target) else { continue; };
        if matches!(mode, Mode::Execution) && target == "direct" && !direct
            || matches!(mode, Mode::Network) && !network
            || mode.validate_target(&prefs, &Value::Object(metadata.clone()), target).is_err()
        {
            continue;
        }
        mode.apply(&mut metadata, &prefs, target);
    }
    Value::Object(metadata)
}

pub(crate) fn recover_pending_host_modes(
    data: &std::path::Path, before: &Value, direct: bool, network: bool,
) -> std::result::Result<Value, crate::runtime::RuntimeError> {
    let prefs = Personalization::load(data);
    let current = prefs.modes(before);
    let mut pending = before.clone();
    for (mode, effective, ordered) in [
        (Mode::Permission, current.permission_mode, PERMISSIONS),
        (Mode::Execution, current.execution_mode, EXECUTION_MODES),
        (Mode::Network, current.network_permission, &["none", "restricted", "full"][..]),
    ] {
        let key = mode.pending_key().expect("host mode can be queued");
        if pending[key].is_null() || pending[key] == false { continue; }
        let value = pending[key].as_str().ok_or_else(|| crate::runtime::RuntimeError::new(422, "Invalid previous host mode"))?;
        let target = value.trim().to_ascii_lowercase();
        if target.is_empty() {
            pending[key] = Value::Null;
            continue;
        }
        let rank = |name: &str| ordered.iter().position(|value| *value == name);
        let requested = rank(&target).ok_or_else(|| crate::runtime::RuntimeError::new(422, "Invalid previous host mode"))?;
        // A stale request has no live approval context. Apply restrictions and
        // leave any wider authority for a new authenticated settings change.
        pending[key] = if requested <= rank(effective).unwrap_or_default() { json!(target) } else { Value::Null };
    }
    Ok(apply_pending_host_modes(data, &pending, direct, network))
}

struct Settings {
    app: App,
    drafts: DraftSettings,
}

impl Settings {
    fn read(&self, id: Option<&str>) -> Result<Value> {
        match id {
            Some(id) => Ok(self.app.runtime.web_metadata(&self.app.workspace_id, id)?),
            None => Ok(Value::Object(
                self.drafts
                    .0
                    .lock()
                    .map_err(|_| draft_unavailable())?
                    .get(&self.app.workspace_id)
                    .cloned()
                    .unwrap_or_default(),
            )),
        }
    }

    fn edit<T>(
        &self,
        id: Option<&str>,
        update: impl FnOnce(&mut Map<String, Value>) -> T,
    ) -> Result<T> {
        match id {
            Some(id) => {
                Ok(self
                    .app
                    .runtime
                    .update_idle_web_metadata(&self.app.workspace_id, id, update)?)
            }
            None => Ok(update(
                self.drafts
                    .0
                    .lock()
                    .map_err(|_| draft_unavailable())?
                    .entry(self.app.workspace_id.clone())
                    .or_default(),
            )),
        }
    }
}

fn draft_unavailable() -> WebError {
    WebError(
        StatusCode::SERVICE_UNAVAILABLE,
        "Workspace settings are unavailable",
    )
}

fn requested_id(scope: &Scope, body: &Value) -> Result<Option<String>> {
    let query_id = scope
        .conversation_id
        .as_deref()
        .filter(|id| !id.trim().is_empty())
        .map(conversation_id)
        .transpose()?;
    let body_id = optional_string(body, "conversation_id")?
        .filter(|id| !id.trim().is_empty())
        .map(conversation_id)
        .transpose()?;
    if query_id.is_some() && body_id.is_some() && query_id != body_id {
        return Err(WebError::invalid("Conflicting conversation IDs"));
    }
    Ok(query_id.or(body_id))
}

pub(super) fn request_body(
    payload: std::result::Result<Option<Json<Value>>, JsonRejection>,
) -> Result<Value> {
    let body = payload
        .map_err(|_| WebError::invalid("Expected a JSON object"))?
        .map_or_else(|| json!({}), |Json(body)| body);
    body.is_object()
        .then_some(body)
        .ok_or_else(|| WebError::invalid("Expected a JSON object"))
}

async fn get_mode(
    State(drafts): State<DraftSettings>,
    Extension(app): Extension<App>,
    Extension(mode): Extension<Mode>,
    Query(scope): Query<Scope>,
    headers: HeaderMap,
) -> Result<Json<Value>> {
    app.check_workspace(&headers, &scope, &Value::Null)?;
    let settings = Settings { app, drafts };
    let id = requested_id(&scope, &Value::Null)?;
    snapshot(move || {
        let prefs = Personalization::load(&settings.app.data_dir);
        let network = settings.app.runtime.host_network_permissions_available();
        let direct = settings.app.runtime.host_direct_execution_available();
        Ok(mode.response(&prefs, &settings.read(id.as_deref())?, id.as_deref(), direct, network))
    })
    .await
}

async fn set_mode(
    State(drafts): State<DraftSettings>,
    Extension(app): Extension<App>,
    Extension(mode): Extension<Mode>,
    Query(scope): Query<Scope>,
    headers: HeaderMap,
    payload: std::result::Result<Option<Json<Value>>, JsonRejection>,
) -> Result<Json<Value>> {
    let body = request_body(payload)?;
    app.check_workspace(&headers, &scope, &body)?;
    let settings = Settings { app, drafts };
    let id = requested_id(&scope, &body)?;
    let target = mode.target(&body)?;
    let network_enabled = settings.app.runtime.host_network_permissions_available();
    let direct_enabled = settings.app.runtime.host_direct_execution_available();
    if matches!(mode, Mode::Network) && !network_enabled || matches!(mode, Mode::Execution) && target == "direct" && !direct_enabled {
        return Err(WebError(
            StatusCode::NOT_IMPLEMENTED,
            "This runtime cannot apply that execution setting",
        ));
    }
    snapshot(move || {
        let prefs = Personalization::load(&settings.app.data_dir);
        if let Some(id) = id.as_deref()
            && let Some(key) = mode.pending_key()
            && let Some(metadata) = settings.app.runtime.queue_running_host_mode(
                &settings.app.workspace_id, id, key, target,
                |metadata| mode.validate_target(&prefs, metadata, target),
            )?
        {
            let mut response = mode.response(&prefs, &metadata, Some(id), direct_enabled, network_enabled);
            if matches!(mode, Mode::Execution) {
                response["state"]["mode"] = json!(target);
            } else {
                response["mode"] = json!(target);
            }
            return Ok(response);
        }
        settings.edit(id.as_deref(), |metadata| {
            mode.validate_target(&prefs, &Value::Object(metadata.clone()), target)?;
            mode.apply(metadata, &prefs, target);
            Ok::<_, crate::runtime::RuntimeError>(mode.response(&prefs, &Value::Object(metadata.clone()), id.as_deref(), direct_enabled, network_enabled))
        })?.map_err(Into::into)
    })
    .await
}

#[derive(Clone, Copy)]
enum Setting {
    Thinking,
    Reasoning,
    Model,
    Compression,
}

async fn write_setting(
    State(drafts): State<DraftSettings>,
    Extension(app): Extension<App>,
    Extension(setting): Extension<Setting>,
    Query(scope): Query<Scope>,
    headers: HeaderMap,
    payload: std::result::Result<Option<Json<Value>>, JsonRejection>,
) -> Result<Json<Value>> {
    let body = request_body(payload)?;
    app.check_workspace(&headers, &scope, &body)?;
    let settings = Settings { app, drafts };
    let id = requested_id(&scope, &body)?;
    snapshot(move || match setting {
        Setting::Thinking => update_thinking(&settings, id.as_deref(), &body),
        Setting::Reasoning => update_reasoning(&settings, id.as_deref(), &body),
        Setting::Model => update_model(&settings, id.as_deref(), &body),
        Setting::Compression => update_compression(&settings, id.as_deref(), &body),
    })
    .await
}

fn update_thinking(settings: &Settings, id: Option<&str>, body: &Value) -> Result<Value> {
    let desired = if body["mode"].is_string() {
        let mode = run_mode(&body["mode"], "");
        if mode.is_empty() {
            return Err(WebError::invalid("Invalid run mode"));
        }
        Some(mode)
    } else {
        body["thinking_mode"]
            .as_bool()
            .map(|thinking| if thinking { "thinking" } else { "fast" })
    };
    let prefs = Personalization::load(&settings.app.data_dir);
    let catalog = settings.app.catalog.load();
    settings.edit(id, |metadata| {
        let model = metadata
            .get("model_key")
            .and_then(Value::as_str)
            .or_else(|| prefs.model_key(&catalog))
            .unwrap_or_default();
        let fallback = metadata
            .get("run_mode")
            .map_or(prefs.run_mode, |value| run_mode(value, prefs.run_mode));
        let run = super::preferences::model_run_mode(&catalog, model, desired.unwrap_or(fallback));
        metadata.insert("run_mode".into(), json!(run));
        metadata.insert("thinking_mode".into(), json!(run == "thinking"));
        success(json!({ "mode": run, "thinking_mode": run == "thinking" }))
    })
}

fn update_reasoning(settings: &Settings, id: Option<&str>, body: &Value) -> Result<Value> {
    let value = &body["effort"];
    let effort = if value.is_null() || value.as_str().is_some_and(|text| text.trim().is_empty()) {
        None
    } else {
        Some(reasoning_effort(value).ok_or_else(|| WebError::invalid("Invalid reasoning effort"))?)
    };
    settings.edit(id, |metadata| {
        metadata.insert("reasoning_effort".into(), json!(effort));
        success(json!({ "reasoning_effort": effort }))
    })
}

fn update_model(settings: &Settings, id: Option<&str>, body: &Value) -> Result<Value> {
    let model = body["model_key"]
        .as_str()
        .map(str::trim)
        .filter(|key| !key.is_empty())
        .ok_or_else(|| WebError::invalid("Missing model key"))?;
    let catalog = settings.app.catalog.load();
    let model = catalog
        .resolve_key(Some(model), false)
        .map_err(|_| WebError::invalid("Unknown model"))?;
    let policy = admin_policy(&settings.app.data_dir, &catalog).map_err(|_| {
        WebError(
            StatusCode::SERVICE_UNAVAILABLE,
            "Host administrator policy is unavailable",
        )
    })?;
    if policy["disabled_models"]
        .as_array()
        .is_some_and(|list| list.iter().any(|key| key == model))
    {
        return Err(WebError(
            StatusCode::FORBIDDEN,
            "This model is disabled by the Host administrator",
        ));
    }
    let prefs = Personalization::load(&settings.app.data_dir);
    settings.edit(id, |metadata| {
        crate::runtime::adaptive_context::invalidate_if_model_changed(metadata, model);
        let current = metadata
            .get("run_mode")
            .map_or(prefs.run_mode, |value| run_mode(value, prefs.run_mode));
        let run = super::preferences::model_run_mode(&catalog, model, current);
        metadata.insert("model_key".into(), json!(model));
        metadata.insert("run_mode".into(), json!(run));
        metadata.insert("thinking_mode".into(), json!(run == "thinking"));
        success(json!({ "model_key": model, "run_mode": run, "thinking_mode": run == "thinking" }))
    })
}

fn update_compression(settings: &Settings, id: Option<&str>, body: &Value) -> Result<Value> {
    let id = id
        .filter(|id| body["conversation_id"].as_str() == Some(*id))
        .ok_or(WebError(
            StatusCode::NOT_FOUND,
            "Conversation not found in workspace",
        ))?;
    let (tokens, source) = if body["tokens"].is_null() {
        (
            Personalization::load(&settings.app.data_dir).compress_limit,
            "snapshot",
        )
    } else {
        (
            super::preferences::compress_limit(&body["tokens"]).ok_or_else(|| {
                WebError::invalid("Compression limit must be an integer from 1000 to 2000000")
            })?,
            "custom",
        )
    };
    settings.edit(Some(id), |metadata| {
        metadata.insert("compress_limit_tokens".into(), json!(tokens));
        metadata.insert("compress_limit_source".into(), json!(source));
        success(json!({ "compress_limit_tokens": tokens, "compress_limit_source": source }))
    })
}
