use std::{
    collections::HashSet,
    fs, io,
    path::{Path, PathBuf},
};

use serde_json::{Value, json};

use crate::{
    approval::ApprovalPrincipal,
    catalog::{Catalog, CatalogSource},
    runtime::{ModelConfig, Runtime, RuntimeError, ScheduledStart, resolve_task_approval_settings},
    tools::ToolSet,
    workspace_catalog::{HostCatalog, RegisteredWorkspace},
};

use super::{
    Admission, HostPrincipal, ModelOption, Observation, Result, ScheduleAdapter, ScheduleError,
    ScheduleInvocation, ScheduleOptions, StartError, WorkspaceOption, scheduler::host_scope,
};

// Sent through the normal user-message pipeline so it is available to the
// model without becoming a separate interaction in the conversation.
const ASK_USER_NOTICE: &str = concat!(
    "【定时任务】本条消息由定时任务自动发送，用户此刻不在场，无法回答提问。",
    "不要调用 ask_user 工具；遇到不确定的细节时，在当前权限内自行做出合理判断，",
    "并在最终回复中说明你所做的假设。",
);

/// Dispatch through the shared Runtime using current Host registrations and matching tool bindings.
pub struct RuntimeScheduleAdapter {
    runtime: Runtime,
    standalone: RegisteredWorkspace,
    registry: Option<(PathBuf, PathBuf)>,
    catalog: CatalogSource,
    fixed_model: Option<ModelConfig>,
}

impl RuntimeScheduleAdapter {
    /// The supplied runtime must already be bound to this workspace by startup code.
    pub fn new(
        runtime: Runtime,
        workspace: WorkspaceOption,
        project: impl AsRef<Path>,
        catalog: CatalogSource,
        fixed_model: Option<ModelConfig>,
    ) -> Result<Self> {
        let project = project
            .as_ref()
            .canonicalize()
            .map_err(ScheduleError::storage)?;
        if !project.is_dir() {
            return Err(ScheduleError::invalid("定时任务工作区目录不存在"));
        }
        Ok(Self {
            runtime,
            standalone: RegisteredWorkspace {
                id: workspace.id,
                label: workspace.label,
                path: project,
            },
            registry: None,
            catalog,
            fixed_model,
        })
    }

    /// Use only entries from the registry. A missing or empty registry has no dispatchable scope.
    pub fn with_registry(
        mut self,
        registry: impl AsRef<Path>,
        resources: impl AsRef<Path>,
    ) -> Self {
        self.registry = Some((registry.as_ref().to_owned(), resources.as_ref().to_owned()));
        self
    }

    fn workspaces(&self) -> Result<Vec<RegisteredWorkspace>> {
        match &self.registry {
            Some((registry, resources)) => HostCatalog::load(registry, resources)
                .map_err(ScheduleError::storage)
                .map(|catalog| {
                    catalog
                        .map(|catalog| catalog.workspaces)
                        .unwrap_or_default()
                }),
            None => Ok(vec![self.standalone.clone()]),
        }
    }

    fn scope(&self, definition: &Value, principal: &Value) -> Result<RegisteredWorkspace> {
        let workspace = target_workspace(definition, principal)?;
        let entry = self
            .workspaces()?
            .into_iter()
            .find(|entry| entry.id == workspace && entry.path.is_dir())
            .ok_or_else(|| ScheduleError::unavailable("定时任务目标工作区尚不可用"))?;
        match self
            .runtime
            .bound_workspace(workspace)
            .map_err(from_runtime)?
        {
            Some(bound) if bound == entry.path => {}
            None if self.registry.is_some() => self.bind_registered(&entry)?,
            _ => {
                return Err(ScheduleError::unavailable(
                    "定时任务目标工作区尚未绑定到当前运行时",
                ));
            }
        }
        Ok(entry)
    }

    fn bind_registered(&self, entry: &RegisteredWorkspace) -> Result<()> {
        let tools = ToolSet::for_workspace(&entry.path, self.catalog.host_data_dir())
            .map_err(ScheduleError::storage)?;
        self.runtime
            .bind_registered_workspace(&entry.id, &entry.path, tools, || {
                let current = self.workspaces().map_err(|error| RuntimeError {
                    status: error.status,
                    message: error.to_string(),
                })?;
                if current
                    .iter()
                    .any(|current| current.id == entry.id && current.path == entry.path)
                {
                    Ok(())
                } else {
                    Err(RuntimeError {
                        status: 503,
                        message: "定时任务运行前的工作区注册已变更".into(),
                    })
                }
            })
            .map_err(|error| {
                if error.status == 409 {
                    ScheduleError::unavailable("定时任务运行前的工作区绑定已变更")
                } else {
                    from_runtime(error)
                }
            })
    }

    fn conversation_metadata(&self, workspace: &str, project: &Path, id: &str) -> Result<Value> {
        let metadata = self.runtime.web_metadata(workspace, id).map_err(|error| {
            if error.status == 404 {
                ScheduleError::invalid("目标对话不存在或不属于所选工作区")
            } else {
                from_runtime(error)
            }
        })?;
        if metadata["conversation_kind"] == "chat" {
            return Err(ScheduleError::invalid("定时任务仅支持 Agent 对话"));
        }
        if let Some(root) = metadata["project_path"]
            .as_str()
            .filter(|root| !root.is_empty())
            && Path::new(root).canonicalize().ok().as_deref() != Some(project)
        {
            return Err(ScheduleError::invalid("目标对话不属于所选工作区"));
        }
        Ok(metadata)
    }

    fn resolve(
        &self,
        definition: &Value,
        principal: &Value,
    ) -> Result<(RegisteredWorkspace, Value, ModelConfig, Option<Value>)> {
        let workspace = self.scope(definition, principal)?;
        let target = &definition["target"]["conversation"];
        let configuration = &definition["configuration"];
        let catalog = self.catalog.load();
        let prefs = read_object(&self.catalog.host_data_dir().join("personalization.json"))?;
        let old_meta = target["id"]
            .as_str()
            .map(|id| self.conversation_metadata(&workspace.id, &workspace.path, id))
            .transpose()?;
        let snapshot = principal
            .get("configuration_snapshot")
            .filter(|_| old_meta.is_none());
        let field = |name: &str, pref: &str| {
            snapshot
                .and_then(|snapshot| snapshot.get(name))
                .or_else(|| old_meta.as_ref().and_then(|metadata| metadata.get(name)))
                .or_else(|| prefs.get(pref))
        };
        let mode_value =
            |name: &str, preference: &str, supported: &[&str], fallback: &str| -> Result<String> {
                let value = configuration
                    .get(name)
                    .or_else(|| field(name, preference))
                    .filter(|value| !value.is_null())
                    .map(|value| value.as_str().unwrap_or_default())
                    .unwrap_or(fallback)
                    .trim()
                    .to_ascii_lowercase();
                if !supported.contains(&value.as_str()) {
                    return Err(ScheduleError::invalid(format!("无效的运行设置 {name}")));
                }
                Ok(value)
            };
        let mut work = mode_value(
            "work_mode",
            "default_work_mode",
            &["execute", "ask", "plan"],
            "plan",
        )?;
        let mut permission = mode_value(
            "permission_mode",
            "default_permission_mode",
            &["readonly", "approval", "auto_approval", "unrestricted"],
            "approval",
        )?;
        let mut execution = mode_value("execution_mode", "", &["sandbox", "direct"], "sandbox")?;
        let mut network = mode_value(
            "network_permission",
            "",
            &["none", "restricted", "full"],
            "restricted",
        )?;
        if let Some(enabled) = configuration["network_enabled"].as_bool() {
            network = if enabled { "full" } else { "none" }.into();
        }
        if definition["goal"]["mode"] != "off" {
            work = "execute".into();
            if !matches!(permission.as_str(), "auto_approval" | "unrestricted") {
                permission = "auto_approval".into();
            }
        }
        if work == "plan" {
            if configuration.get("permission_mode").is_some() && permission != "readonly" {
                return Err(ScheduleError::invalid("计划模式必须使用只读权限"));
            }
            permission = "readonly".into();
        }
        if permission == "readonly" && execution == "direct" {
            if configuration.get("execution_mode").is_some() {
                return Err(ScheduleError::invalid("只读权限必须使用沙箱"));
            }
            execution = "sandbox".into();
        }
        if execution == "direct" && !self.runtime.host_direct_execution_available() {
            return Err(platform_unavailable("直接执行"));
        }
        if network == "full"
            && execution != "direct"
            && !self.runtime.host_network_permissions_available()
        {
            return Err(platform_unavailable("完整工具网络权限"));
        }
        let reasoning_value = configuration
            .get("reasoning_effort")
            .or_else(|| field("reasoning_effort", "default_reasoning_effort"));
        let reasoning = reasoning_value.and_then(Value::as_str);
        if reasoning_value.is_some_and(|value| !value.is_null())
            && !reasoning
                .is_some_and(|effort| matches!(effort, "low" | "medium" | "high" | "xhigh" | "max"))
        {
            return Err(ScheduleError::invalid("配置的推理强度无效"));
        }
        // Python's public YAML has no Fast field: existing targets inherit their
        // saved metadata, while new conversations always start with Fast off.
        let chatgpt_fast_mode = field("chatgpt_fast_mode", "")
            .and_then(Value::as_bool)
            .unwrap_or(false);
        let saved_model = target["id"]
            .as_str()
            .map(|id| self.runtime.session_model_key(&workspace.id, id))
            .transpose()
            .map_err(from_runtime)?
            .flatten();
        let model_key = configuration["model_key"]
            .as_str()
            .or_else(|| snapshot.and_then(|settings| settings["model_key"].as_str()))
            .or(saved_model.as_deref())
            .or_else(|| self.fixed_model.as_ref().map(ModelConfig::key))
            .or_else(|| catalog.default_key())
            .ok_or_else(|| {
                ScheduleError::invalid("尚未配置任何模型，请先在设置页的「提供商」中添加")
            })?;
        if self.disabled_models()?.contains(model_key) {
            return Err(ScheduleError::forbidden("此模型已被管理员禁用"));
        }
        let mode = field("run_mode", "default_run_mode").and_then(Value::as_str);
        let thinking = configuration["thinking"]
            .as_bool()
            .or_else(|| mode.map(|mode| matches!(mode, "thinking" | "deep")))
            .or_else(|| {
                old_meta
                    .as_ref()
                    .and_then(|meta| meta["thinking_mode"].as_bool())
            })
            .unwrap_or(false);
        if mode.is_some_and(|mode| !matches!(mode, "fast" | "thinking" | "deep"))
            && configuration.get("thinking").is_none()
        {
            return Err(ScheduleError::invalid("无效的模型运行模式"));
        }
        let model = if let Some(model) = &self.fixed_model {
            if thinking || reasoning.is_some() {
                return Err(ScheduleError::invalid(
                    "固定模型不支持切换思考模式或推理强度",
                ));
            }
            model
                .validate_model_key(&json!({"model_key":model_key}))
                .map_err(from_runtime)?;
            model.clone()
        } else {
            let profiles = catalog.models_response();
            let profile = profiles["items"]
                .as_array()
                .and_then(|items| items.iter().find(|item| item["model_key"] == model_key));
            let mismatch = profile.is_some_and(|item| {
                (thinking && item["fast_only"] == true)
                    || (!thinking && item["thinking_only"] == true)
            });
            if mismatch
                || (thinking
                    && !profile.is_some_and(|item| {
                        item["supports_thinking"] == true || item["thinking_only"] == true
                    }))
            {
                return Err(ScheduleError::invalid("所选模型不支持该思考模式"));
            }
            if let Some(effort) = reasoning
                && !profile.is_some_and(|item| {
                    item["supports_reasoning_effort"] == true
                        && (item["api_protocol"] == "responses" || thinking)
                        && item["supported_reasoning_levels"]
                            .as_array()
                            .is_none_or(|levels| {
                                levels.is_empty()
                                    || levels.iter().any(|level| level["effort"] == effort)
                            })
                })
            {
                return Err(ScheduleError::invalid("所选模型不支持该推理强度"));
            }
            let selected = catalog
                .select_for_turn(Some(model_key), thinking, reasoning)
                .map_err(ScheduleError::invalid)?;
            ModelConfig::from_catalog(selected).map_err(ScheduleError::invalid)?
        };
        let settings = json!({
            "model_key":model_key,"run_mode":if thinking {"thinking"} else {"fast"},"thinking_mode":thinking,
            "work_mode":work,"permission_mode":permission,"execution_mode":execution,"network_permission":network,
            "reasoning_effort":reasoning,"chatgpt_fast_mode":chatgpt_fast_mode,
        });
        self.runtime
            .check_scheduled_worktree(
                &workspace,
                old_meta.as_ref(),
                &settings,
                &configuration["worktree"],
            )
            .map_err(from_runtime)?;
        Ok((workspace, settings, model, old_meta))
    }

    fn disabled_models(&self) -> Result<HashSet<String>> {
        let policy = read_object(&self.catalog.host_data_dir().join("admin_policy.json"))?;
        let mut disabled = HashSet::new();
        for scope in [
            &policy["global"],
            &policy["roles"]["admin"],
            &policy["invites"][""],
            &policy["users"]["host"],
        ] {
            let values = &scope["disabled_models"];
            if values.is_null() {
                continue;
            }
            let values = values
                .as_array()
                .ok_or_else(|| ScheduleError::storage("Invalid disabled_models policy"))?;
            for value in values {
                disabled.insert(
                    value
                        .as_str()
                        .ok_or_else(|| ScheduleError::storage("Invalid disabled model key"))?
                        .to_owned(),
                );
            }
        }
        Ok(disabled)
    }
}

impl ScheduleAdapter for RuntimeScheduleAdapter {
    fn validate(&self, definition: &Value, principal: &Value) -> Result<Option<Value>> {
        self.resolve(definition, principal)
            .map(|(_, snapshot, _, _)| {
                (definition["target"]["conversation"]["mode"] == "new").then_some(snapshot)
            })
    }

    fn check_dispatch(&self, definition: &Value, principal: &Value) -> Result<()> {
        match self.resolve(definition, principal) {
            Err(error) if defer_error(&error) => Err(error),
            // An invalid conversation or model should produce a normal failed run when due.
            _ => Ok(()),
        }
    }

    fn start(&self, request: &ScheduleInvocation) -> std::result::Result<Admission, StartError> {
        let definition = &request.definition;
        let (workspace, snapshot, model, saved_metadata) = self
            .resolve(definition, &request.principal)
            .map_err(|error| {
                if defer_error(&error) {
                    StartError::Deferred(error)
                } else {
                    StartError::Failed(error)
                }
            })?;
        let configuration = &definition["configuration"];
        let target = &definition["target"]["conversation"];
        let is_new = target["mode"] == "new";
        let custom = configuration["mode"] == "custom";
        let mut payload = json!({
            "message":definition["message"],"message_source":"schedule","model_key":snapshot["model_key"],
            "run_mode":snapshot["run_mode"],"thinking_mode":snapshot["thinking_mode"],
            "reasoning_effort":snapshot["reasoning_effort"],"chatgpt_fast_mode":snapshot["chatgpt_fast_mode"],
        });
        if let Some(conversation_id) = target["id"].as_str() {
            payload["conversation_id"] = json!(conversation_id);
        }
        let goal = &definition["goal"];
        if goal["mode"] != "off" {
            payload["goal_action"] = goal["mode"].clone();
            let options: serde_json::Map<String, Value> = ["max_turns", "max_tokens"]
                .into_iter()
                .filter_map(|key| goal.get(key).map(|value| (key.into(), value.clone())))
                .collect();
            if !options.is_empty() {
                payload["goal_options"] = Value::Object(options);
            }
        }
        let mut metadata = snapshot;
        if is_new {
            metadata["schedule_id"] = json!(request.schedule_id);
            metadata["schedule_run_id"] = json!(request.run_id);
            metadata["skip_auto_title_generation"] = json!(true);
        }
        if custom {
            for key in [
                "pending_permission_mode",
                "pending_execution_mode",
                "pending_network_permission",
            ] {
                metadata[key] = Value::Null;
            }
        }
        let hidden_user_notice = (custom
            && configuration["ask_user"] != true
            && goal["mode"] == "off"
            && matches!(metadata["work_mode"].as_str(), Some("execute" | "plan")))
        .then_some(ASK_USER_NOTICE);
        let principal = ApprovalPrincipal::new("host", &workspace.id)
            .map_err(|error| StartError::Failed(ScheduleError::storage(error)))?;
        let approval =
            resolve_task_approval_settings(principal, &self.catalog).map_err(start_error)?;
        let response = self
            .runtime
            .create_scheduled_task(
                &workspace,
                ScheduledStart {
                    payload: &payload,
                    saved_metadata: saved_metadata.as_ref(),
                    metadata,
                    title: definition["name"]
                        .as_str()
                        .filter(|_| is_new)
                        .map(str::to_owned),
                    worktree: &configuration["worktree"],
                    hidden_user_notice,
                },
                model,
                approval,
            )
            .map_err(start_error)?;
        let data = &response["data"];
        let parse = |key| {
            data[key]
                .as_str()
                .filter(|id| !id.is_empty())
                .map(str::to_owned)
                .ok_or_else(|| {
                    StartError::Failed(ScheduleError::storage("Invalid Runtime task admission"))
                })
        };
        Ok(Admission {
            task_id: parse("task_id")?,
            conversation_id: parse("conversation_id")?,
        })
    }

    fn observe(&self, request: &ScheduleInvocation, admission: &Admission) -> Result<Observation> {
        let workspace = self.scope(&request.definition, &request.principal)?;
        let mut observed = self
            .runtime
            .observe_scheduled_task(&workspace, &admission.conversation_id, &admission.task_id)
            .map_err(from_runtime)?;
        if observed.state.terminal() {
            let response = self
                .runtime
                .session_history(&workspace.id, &admission.conversation_id)
                .map_err(from_runtime)?;
            observed.summary = latest_summary(&response["conversation"]);
        }
        Ok(observed)
    }

    fn cancel(&self, request: &ScheduleInvocation, admission: &Admission) -> Result<()> {
        let workspace = self.scope(&request.definition, &request.principal)?;
        self.runtime
            .cancel_scheduled_task(&workspace, &admission.conversation_id, &admission.task_id)
            .map_err(from_runtime)
    }

    fn options(&self, _: &HostPrincipal) -> Result<ScheduleOptions> {
        let disabled = self.disabled_models()?;
        let models = if let Some(model) = &self.fixed_model {
            let key = model.key().to_owned();
            if disabled.contains(&key) {
                Vec::new()
            } else {
                vec![ModelOption {
                    value: key.clone(),
                    label: key,
                    thinking: false,
                    reasoning: false,
                }]
            }
        } else {
            model_options(&self.catalog.load(), &disabled)
        };
        let workspaces = self
            .workspaces()?
            .into_iter()
            .map(|entry| WorkspaceOption {
                id: entry.id,
                label: entry.label,
            })
            .collect();
        Ok(ScheduleOptions { workspaces, models })
    }
}

fn target_workspace<'a>(definition: &'a Value, principal: &Value) -> Result<&'a str> {
    if !host_scope(principal) {
        return Err(ScheduleError::forbidden(
            "计划所属身份不受支持，仅允许宿主机身份",
        ));
    }
    definition["target"]["workspace_id"]
        .as_str()
        .filter(|id| !id.is_empty())
        .ok_or_else(|| ScheduleError::unavailable("定时任务目标工作区尚不可用"))
}

fn defer_error(error: &ScheduleError) -> bool {
    matches!(error.status, 500 | 501 | 503)
}

fn model_options(catalog: &Catalog, disabled: &HashSet<String>) -> Vec<ModelOption> {
    let response = catalog.models_response();
    let mut models = Vec::new();
    for model in response["items"].as_array().into_iter().flatten() {
        if let Some(key) = model["model_key"]
            .as_str()
            .filter(|key| !disabled.contains(*key))
        {
            models.push(ModelOption {
                value: key.to_owned(),
                label: model["name"].as_str().unwrap_or(key).to_owned(),
                thinking: model["supports_thinking"] == true,
                reasoning: model["supports_reasoning_effort"] == true,
            });
        }
    }
    models
}

fn read_object(path: &Path) -> Result<Value> {
    let bytes = match fs::read(path) {
        Ok(bytes) => bytes,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(json!({})),
        Err(error) => return Err(ScheduleError::storage(error)),
    };
    let parsed: Value = serde_json::from_slice(&bytes).map_err(ScheduleError::storage)?;
    if !parsed.is_object() {
        return Err(ScheduleError::storage("Expected Host configuration object"));
    }
    Ok(parsed)
}

fn latest_summary(conversation: &Value) -> Option<String> {
    let messages = conversation["messages"].as_array()?;
    for message in messages
        .iter()
        .rev()
        .take_while(|message| message["role"] != "user")
    {
        if message["role"] == "assistant" {
            let content = &message["content"];
            let text = content.as_str().map(str::to_owned).or_else(|| {
                content.as_array().map(|parts| {
                    parts
                        .iter()
                        .filter(|part| part["type"] == "text")
                        .filter_map(|part| part["text"].as_str())
                        .collect::<Vec<_>>()
                        .join("\n")
                })
            });
            if let Some(text) = text.filter(|text| !text.is_empty()) {
                return Some(text.chars().take(12_000).collect());
            }
        }
    }
    None
}

fn platform_unavailable(feature: &str) -> ScheduleError {
    ScheduleError::invalid(format!("当前平台不支持{feature}"))
}

fn start_error(error: RuntimeError) -> StartError {
    if error.status == 409 && error.message.starts_with("conversation_") {
        return StartError::Busy(error.message);
    }
    let error = from_runtime(error);
    if defer_error(&error) {
        StartError::Deferred(error)
    } else {
        StartError::Failed(error)
    }
}

fn from_runtime(error: RuntimeError) -> ScheduleError {
    ScheduleError::new(error.status, error.message)
}

#[cfg(test)]
#[path = "../../../test/2026-11-24_Rust后端/schedules_runtime.rs"]
mod tests;
