use std::path::{Path, PathBuf};

use serde_json::{Value, json};

use crate::worktree::{Target, Worktree};

use super::{
    LegacyDocument, Runtime, RuntimeError, legacy_error,
    store::{Status, Store, validate_workspace},
};

impl Runtime {
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
            None => worktree.main_binding().map_err(invalid)?,
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
