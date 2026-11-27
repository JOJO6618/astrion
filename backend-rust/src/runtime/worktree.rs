use std::path::{Path, PathBuf};

use serde_json::{Value, json};

use crate::{workspace_catalog::RegisteredWorkspace, worktree::{Target, Worktree}};

use super::{
    LegacyDocument, Runtime, RuntimeError, approval::task_scope, legacy_error, writable_task,
    store::{Status, Store, validate_workspace},
};

pub(super) const ENTER: &str = "enter_worktree";
pub(super) const EXIT: &str = "exit_worktree";

pub(super) fn supports(name: &str) -> bool { matches!(name, ENTER | EXIT) }

fn definitions() -> [Value; 2] {
    let intent = json!({"type":"string","description":"用不超过 15 个字说明操作目的。"});
    let create = json!({"type":"object","properties":{
        "action":{"type":"string","enum":["create"]},"branch_name":{"type":"string"},
        "start_ref":{"type":"string"},"include_uncommitted":{"type":"boolean"}},
        "required":["action","branch_name","start_ref","include_uncommitted"],"additionalProperties":false});
    let enter = json!({"type":"object","properties":{"action":{"type":"string","enum":["enter"]},
        "branch_name":{"type":"string"},"directory":{"type":"string"}},
        "required":["action"],"additionalProperties":false});
    [
        json!({"type":"function","function":{"name":ENTER,"description":"创建并进入工作树，或进入已注册工作树。请在单独的工具批次调用。",
            "parameters":{"type":"object","properties":{"intent":intent,"target":{"anyOf":[create,enter]}},
            "required":["intent","target"],"additionalProperties":false}}}),
        json!({"type":"function","function":{"name":EXIT,"description":"返回工作区主目录，保留工作树与修改。请在单独的工具批次调用。",
            "parameters":{"type":"object","properties":{"intent":intent},"required":["intent"],"additionalProperties":false}}}),
    ]
}

impl Runtime {
    pub(super) fn worktree_tools(&self, task_id: &str) -> Result<Vec<Value>, String> {
        let store = self.store().map_err(|error| error.message)?;
        let task = store.tasks.get(task_id).ok_or("Task is not available")?;
        let (header, _) = store.header(&task.workspace_id, &task.conversation_id).map_err(|error| error.message)?;
        Ok(if header["metadata"]["git_worktree_enabled"] == true {
            Vec::from(definitions())
        } else {
            Vec::new()
        })
    }

    pub(super) async fn execute_worktree_tool(
        &self, task_id: &str, name: &str, args: &Value,
    ) -> Result<(Value, Option<crate::tools::ToolSet>), String> {
        let target = if name == ENTER {
            match serde_json::from_value::<Target>(args["target"].clone()) {
                Ok(value) => Some(value),
                Err(error) => return Ok((json!({"success":false,"error":format!("无效工作树目标：{error}")}),None)),
            }
        } else {
            None
        };
        let runtime = self.clone();
        let task_id = task_id.to_owned();
        let result = tokio::task::spawn_blocking(move || runtime.switch_task_worktree(&task_id, target))
            .await.map_err(|error| error.to_string())?;
        Ok(result.unwrap_or_else(|error| (json!({"success":false,"error":error.message}),None)))
    }

    fn switch_task_worktree(&self, task_id: &str, target: Option<Target>)
        -> Result<(Value, Option<crate::tools::ToolSet>), RuntimeError>
    {
        let mut store = self.store()?;
        let task = writable_task(&mut store, task_id).map_err(RuntimeError::bad_request)?;
        let (workspace_id, conversation) = (task.workspace_id.clone(), task.conversation_id.clone());
        let root = registered_root(&store, &workspace_id)?.to_path_buf();
        let (_, settings) = task_scope(&store, task_id).map_err(RuntimeError::bad_request)?;
        let Some((permission, execution)) = settings.process_modes() else {
            return Err(RuntimeError::bad_request("工作树权限无效。"));
        };
        if permission == crate::approval::PermissionMode::ReadOnly {
            return Err(RuntimeError::bad_request("只读或计划模式不能切换工作树。"));
        }
        let doc = store.read(&workspace_id, &conversation)?;
        let mut raw = doc.into_raw();
        let meta = &mut raw["metadata"];
        if meta["git_worktree_enabled"] != true {
            return Err(RuntimeError::bad_request("当前对话未启用 Git worktree。"));
        }
        let worktree = Worktree::new(&root).map_err(invalid)?;
        let mut binding = match target {
            Some(target) => {
                let source = worktree.execution_root(meta).map_err(invalid)?;
                worktree.prepare(target, &source, "execute", "approval").map_err(invalid)?
            }
            None => {
                if let Some(source) = departing_tree(meta) {
                    self.require_idle_worktree(&store, &workspace_id, &conversation, &root, source)?;
                }
                worktree.main_binding().map_err(invalid)?
            }
        };
        if !super::host_policy::scheduled_worktree_authorized(&store, &root, &binding.directory,
            execution == crate::approval::ExecutionMode::Direct)?
        {
            return Err(RuntimeError::bad_request("工作树目录不在当前路径授权范围内。"));
        }
        let saved = &meta["git_worktree"];
        if saved["directory"] == json!(binding.directory) && saved["branch"] == binding.branch {
            return Ok((json!({"success":true,"state":saved}), None));
        }
        let execution_root = binding.active.then(|| binding.directory.clone());
        let base = store.workspace_tools.get(&workspace_id).unwrap_or(&self.tools);
        let tools = if let Some(directory) = &execution_root {
            base.with_execution_root(directory, &store.data_dir).map_err(RuntimeError::storage)?
        } else {
            base.clone()
        };
        binding.revision = saved["revision"].as_u64().unwrap_or_default().checked_add(1)
            .ok_or_else(|| RuntimeError::storage("工作树切换版本号超出上限。"))?;
        let state = json!(binding);
        meta["git_worktree"] = state.clone();
        // Keep a recovery notice if the run stops before committing the tool receipt.
        meta["git_worktree_notice"] = state.clone();
        let document = LegacyDocument::from_value(raw).map_err(legacy_error)?;
        store.persist(&workspace_id, document, false)?;
        store.tasks.get_mut(task_id).ok_or_else(RuntimeError::task_not_found)?.execution_root = execution_root;
        Ok((json!({"success":true,"state":state,"message":"当前执行目录已切换。"}), Some(tools)))
    }

    pub fn worktree_view(
        &self,
        workspace_id: &str,
        conversation_id: Option<&str>,
        enabled_default: bool,
    ) -> Result<Value, RuntimeError> {
        let store = self.store()?;
        let root = registered_root(&store, workspace_id)?;
        let metadata = match conversation_id {
            Some(id) => store.read(workspace_id, id)?.raw()["metadata"].clone(),
            None => json!({"git_worktree_enabled":enabled_default}),
        };
        if metadata["git_worktree_enabled"] != true {
            return Ok(json!({"enabled":false}));
        }
        Worktree::new(root)
            .and_then(|service| service.view(&metadata))
            .map_err(invalid)
    }

    pub fn inspect_worktree(
        &self,
        workspace_id: &str,
        directory: &Path,
    ) -> Result<Value, RuntimeError> {
        let root = self
            .bound_workspace(workspace_id)?
            .ok_or_else(|| RuntimeError::bad_request("Workspace is not bound"))?;
        Ok(
            match Worktree::new(&root).and_then(|worktree| worktree.inspect(directory)) {
                Ok(binding) => {
                    json!({"valid":true,"directory":binding.directory,"branch":binding.branch,"head":binding.head,
                "inside_workspace":binding.directory.starts_with(root)})
                }
                Err(error) => json!({"valid":false,"error":error.to_string()}),
            },
        )
    }

    pub fn change_worktree(
        &self,
        workspace_id: &str,
        conversation_id: &str,
        target: Option<Target>,
    ) -> Result<Value, RuntimeError> {
        let store = self.store()?;
        let root = registered_root(&store, workspace_id)?;
        if store.tasks.values().any(|task| {
            task.workspace_id == workspace_id
                && task.conversation_id == conversation_id
                && task.status == Status::Running
        }) {
            return Err(RuntimeError::new(409, "主智能体运行中，不能切换工作树。"));
        }
        let doc = store.read(workspace_id, conversation_id)?;
        let mut raw = doc.into_raw();
        let meta = &mut raw["metadata"];
        if meta["git_worktree_enabled"] != true {
            return Err(RuntimeError::bad_request("当前对话未启用 Git worktree。"));
        }
        let worktree = Worktree::new(root).map_err(invalid)?;
        let mut binding = match target {
            Some(target) => {
                let source = if matches!(target, Target::Create { .. }) {
                    worktree.execution_root(meta).map_err(invalid)?
                } else {
                    root.to_path_buf()
                };
                worktree
                    .prepare(
                        target,
                        &source,
                        meta["work_mode"].as_str().unwrap_or("plan"),
                        meta["permission_mode"].as_str().unwrap_or("readonly"),
                    )
                    .map_err(invalid)?
            }
            None => {
                if let Some(source) = departing_tree(meta) {
                    self.require_idle_worktree(&store, workspace_id, conversation_id, root, source)?;
                }
                worktree.main_binding().map_err(invalid)?
            }
        };
        let saved = &meta["git_worktree"];
        if saved["directory"] == json!(binding.directory) && saved["branch"] == binding.branch {
            return Ok(json!({"success":true,"state":saved,"warnings":[]}));
        }
        let revision = saved["revision"].as_u64().unwrap_or(0);
        binding.revision = revision
            .checked_add(1)
            .ok_or_else(|| RuntimeError::storage("工作树切换版本号超出上限。"))?;
        let state = json!(binding);
        meta["git_worktree"] = state.clone();
        if meta["frozen_system_prompt"]
            .as_str()
            .is_some_and(|prompt| !prompt.is_empty())
        {
            meta["git_worktree_notice"] = state.clone();
        }
        let doc = LegacyDocument::from_value(raw).map_err(legacy_error)?;
        store
            .persist(workspace_id, doc, false)
            .map_err(|mut error| {
                if binding.active {
                    error.message.push_str(&format!(
                        "\n已保留分支 {}，目录 {}。",
                        binding.branch,
                        binding.directory.display()
                    ));
                }
                error
            })?;
        Ok(
            json!({"success":true,"state":state,"warnings":[],"message":"执行目录已切换；工作树和修改已保留。"}),
        )
    }

    fn require_idle_worktree(
        &self, store: &Store, workspace_id: &str, conversation: &str, workspace: &Path, source: &Path,
    ) -> Result<(), RuntimeError> {
        if workspace == source { return Ok(()); }
        let mut reasons = Vec::new();
        if let Some(children) = &self.sub_agents {
            let bound = RegisteredWorkspace { id: workspace_id.into(), path: workspace.to_owned(), label: String::new() };
            if children.has_running_for_bound(&bound, conversation).map_err(RuntimeError::storage)? {
                reasons.push("还有子智能体在运行".to_owned());
            }
        }
        let (background, terminal) = store.process_resources.worktree_busy(workspace_id, workspace, conversation, source);
        if background {
            reasons.push("还有后台命令在运行".to_owned());
        }
        if let Some(terminal) = terminal {
            reasons.push(format!("终端 {terminal} 仍在当前执行目录运行"));
        }
        if reasons.is_empty() { return Ok(()); }
        Err(RuntimeError::new(409, format!(
            "当前执行目录仍有进行中的工作，暂不能退出：{}。", reasons.join("；")
        )))
    }

    /// The runtime workspace remains the identity for storage and knowledge.
    /// Only the task's filesystem capability and approval scope move.
    pub(super) fn worktree_execution_root(
        store: &Store,
        workspace_id: &str,
        metadata: &Value,
    ) -> Result<Option<PathBuf>, RuntimeError> {
        if metadata["git_worktree_enabled"] != true || metadata["git_worktree"].is_null() {
            return Ok(None);
        }
        let root = registered_root(store, workspace_id)?;
        let actual = Worktree::new(root)
            .and_then(|worktree| worktree.execution_root(metadata))
            .map_err(invalid)?;
        Ok((actual != root).then_some(actual))
    }
}

// Compare process roots with the saved path only; do not open or re-authorize
// that path. Exiting also recovers a binding whose tree was removed externally.
fn departing_tree(metadata: &Value) -> Option<&Path> {
    let state = &metadata["git_worktree"];
    (state["active"] == true).then(|| state["directory"].as_str().map(Path::new))?
        .filter(|path| path.is_absolute())
}

pub(super) fn complete_notice(raw: &Value, batch: &[Value]) -> bool {
    let Some(revision) = raw["metadata"]["git_worktree_notice"]["revision"].as_u64() else { return false; };
    let Some(calls) = batch.first().and_then(|message| message["tool_calls"].as_array()) else { return false; };
    batch.iter().skip(1).any(|row| row["role"] == "tool"
        && calls.iter().any(|call| call["id"] == row["tool_call_id"]
            && call["function"]["name"].as_str().is_some_and(supports))
        && row["content"].as_str().and_then(|value| serde_json::from_str::<Value>(value).ok())
            .is_some_and(|result| result["success"] == true && result["state"]["revision"].as_u64() == Some(revision)))
}

pub(super) fn notice(metadata: &mut Value) -> Option<Value> {
    let pending = metadata.as_object_mut()?.remove("git_worktree_notice")?;
    let directory = pending["directory"].as_str()?;
    let branch = pending["branch"].as_str().unwrap_or_default();
    let revision = pending["revision"].as_u64()?;
    Some(
        json!({"role":"user","content":format!("用户已切换当前对话的 Git 工作树。执行目录：{directory}。当前分支：{branch}。后续文件操作与命令以该目录为基准。"),
        "metadata":{"message_source":"git_worktree_change","worktree_revision":revision,"starts_work":false}}),
    )
}

fn registered_root<'a>(store: &'a Store, workspace_id: &str) -> Result<&'a Path, RuntimeError> {
    validate_workspace(workspace_id)?;
    store
        .workspaces
        .get(workspace_id)
        .map(PathBuf::as_path)
        .ok_or_else(|| RuntimeError::bad_request("Workspace is not bound"))
}

fn invalid(error: crate::worktree::Error) -> RuntimeError {
    RuntimeError::bad_request(error.to_string())
}
