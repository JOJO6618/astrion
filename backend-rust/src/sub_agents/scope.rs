use std::{io, path::{Component, Path, PathBuf}, sync::Arc};

use cap_fs_ext::{DirExt, MetadataExt};
use cap_std::fs::Dir;
use serde_json::{Value, json};

use crate::{
    approval::{ApprovalPrincipal, ApprovalType, ExecutionMode, TaskScope, ToolRequest, exempt_workspace_write},
    knowledge::storage::open_canonical_dir,
    tools::{read_file::ReadFile, write_file::WorkspaceFileWriter},
};

use super::{Record, same_path, string};

const INVALID_SCOPE: &str = "子智能体的原工作区、工作目录或固定权限不可用。";

fn mkdir(parent: &Dir, path: impl AsRef<Path>) -> io::Result<Dir> {
    let path = path.as_ref();
    match parent.create_dir(path) {
        Ok(()) => (),
        Err(error) if error.kind() == io::ErrorKind::AlreadyExists => (),
        Err(error) => return Err(error),
    }
    parent.open_dir_nofollow(path)
}

#[derive(Clone, Copy)]
pub(crate) struct ParentAccess {
    pub limited: bool,
    pub direct: bool,
    pub unrestricted: bool,
}

#[derive(Clone, Copy)]
pub(super) enum Access {
    WorkDir,
    Workspace,
    Sandbox,
    Full,
}

impl Access {
    pub(super) fn parse(raw: &Value) -> Result<Self, String> {
        match raw.as_str() {
            Some("working_dir_write") => Ok(Self::WorkDir),
            Some("workspace_write") => Ok(Self::Workspace),
            Some("sandbox_write") => Ok(Self::Sandbox),
            Some("full_access") => Ok(Self::Full),
            _ => Err("创建子智能体必须明确选择 access_level。".into()),
        }
    }

    pub(super) fn check_parent(self, parent: ParentAccess) -> Result<(), String> {
        let allowed = match self {
            Self::WorkDir | Self::Workspace => true,
            Self::Sandbox => !parent.limited,
            Self::Full => !parent.limited && parent.direct && parent.unrestricted,
        };
        allowed.then_some(()).ok_or_else(|| "子智能体所选权限超过当前允许创建的权限范围。".into())
    }
}

pub(super) fn working_directory(root: &Path, execution: Option<&Path>, requested: &Value, number: u64) -> io::Result<PathBuf> {
    if let Some(requested) = requested.as_str().filter(|value| !value.is_empty()) {
        let path = execution.unwrap_or(root).join(requested);
        let contents = path.strip_prefix(root).map_err(io::Error::other)?;
        if contents.components().any(|part| !matches!(part, Component::Normal(_))) {
            return Err(io::Error::new(io::ErrorKind::InvalidInput, INVALID_SCOPE));
        }
        let mut dir = open_canonical_dir(root)?;
        for part in contents {
            dir = mkdir(&dir, &part)?;
        }
        return Ok(path);
    }
    if !requested.is_null() && !requested.is_string() {
        return Err(io::Error::new(io::ErrorKind::InvalidInput, "working_dir 必须为目录路径。"));
    }
    let base = root.join(".astrion/sub_agent_work");
    let anchor = open_canonical_dir(root)?;
    let directory = mkdir(&mkdir(&anchor, &".astrion")?, &"sub_agent_work")?;
    for suffix in 1.. {
        let name = if suffix == 1 { format!("agent_{number}") } else { format!("agent_{number}_{suffix}") };
        match directory.create_dir(&name) {
            Ok(()) => return Ok(base.join(name)),
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => (),
            Err(error) => return Err(error),
        }
    }
    unreachable!()
}

fn directory_identity(path: &Path) -> io::Result<Value> {
    let metadata = open_canonical_dir(path)?.dir_metadata()?;
    Ok(json!([metadata.dev(), metadata.ino()]))
}

pub(super) fn identity(workspace: &Path, working: &Path, execution: &Path) -> io::Result<Value> {
    Ok(json!({"workspace":directory_identity(workspace)?,"working":directory_identity(working)?,"execution":directory_identity(execution)?}))
}

pub(super) struct ChildScope {
    workspace: PathBuf,
    working: PathBuf,
    execution: PathBuf,
    writer_root: PathBuf,
    identity: Value,
    reader: ReadFile,
    writer: WorkspaceFileWriter,
    task: TaskScope,
}

impl ChildScope {
    pub(super) fn bind(record: &Record, workspace_id: &str, bound: &Path, data: &Path) -> Result<Self, String> {
        let workspace = PathBuf::from(string(record, "workspace_root").ok_or(INVALID_SCOPE)?);
        let working = PathBuf::from(string(record, "working_dir").ok_or(INVALID_SCOPE)?);
        let execution = string(record, "execution_root").map_or_else(|| working.clone(), PathBuf::from);
        let access = Access::parse(record.get("access_level").unwrap_or(&Value::Null))?;
        if !same_path(&workspace, bound) || !working.starts_with(bound) || !execution.starts_with(bound)
            || [&workspace, &working, &execution].into_iter().any(|path| {
                path.components().any(|part| matches!(part, Component::CurDir | Component::ParentDir))
                    || !path.canonicalize().is_ok_and(|resolved| same_path(path, &resolved))
            })
        {
            return Err(INVALID_SCOPE.into());
        }
        let current = identity(&workspace, &working, &execution).map_err(|_| INVALID_SCOPE)?;
        if record.get("rust_scope_identity").is_some_and(|saved| saved != &current) {
            return Err(INVALID_SCOPE.into());
        }
        // The native text writer carries only workspace-root capabilities.
        let writer_root = if matches!(access, Access::WorkDir) { working.clone() } else { workspace.clone() };
        let reader = ReadFile::open(&workspace, data).map_err(|_| INVALID_SCOPE)?;
        let writer = WorkspaceFileWriter::new(Arc::new(ReadFile::open(&writer_root, data).map_err(|_| INVALID_SCOPE)?));
        let task_id = string(record, "task_id").ok_or(INVALID_SCOPE)?;
        let principal = ApprovalPrincipal::new(string(record, "rust_owner").unwrap_or("host"), workspace_id)
            .map_err(|error| error.to_string())?;
        let task = TaskScope::new(principal, string(record, "conversation_id").ok_or(INVALID_SCOPE)?,
            task_id, task_id, writer_root.clone(), ExecutionMode::Sandbox).map_err(|error| error.to_string())?;
        Ok(Self { workspace, working, execution, writer_root, identity: current, reader, writer, task })
    }

    pub(super) fn check(&self) -> Result<(), String> {
        match identity(&self.workspace, &self.working, &self.execution) {
            Ok(current) if current == self.identity => Ok(()),
            _ => Err(INVALID_SCOPE.into()),
        }
    }

    pub(super) fn working_dir(&self) -> &Path { &self.working }

    fn path(&self, raw: &Value) -> Result<String, String> {
        let raw = raw.as_str().filter(|path| !path.trim().is_empty()).ok_or("缺少文件路径参数。")?;
        let path = if let Some(path) = raw.strip_prefix("/workspace/") {
            self.workspace.join(path)
        } else {
            self.execution.join(raw)
        };
        Ok(path.to_string_lossy().into_owned())
    }

    pub(super) fn read(&self, mut args: Value) -> Result<Value, String> {
        self.check()?;
        args["path"] = json!(self.path(&args["path"])?);
        let mut result = self.reader.execute(&args);
        absolute_result(&mut result, &self.workspace);
        Ok(result)
    }

    pub(super) fn write(&self, id: &str, name: &str, mut args: Value, history: &[Value]) -> Result<Value, String> {
        self.check()?;
        args["file_path"] = json!(self.path(&args["file_path"])?);
        let request = ToolRequest {
            tool_call_id: id.into(), tool_name: name.into(),
            arguments: args.as_object().cloned().ok_or("参数必须为对象。")?,
            preview: Default::default(), approval_type: ApprovalType::SandboxWrite, browser_site: None,
        };
        let scope = exempt_workspace_write(&self.task, &request).map_err(|error| error.to_string())?;
        let mut result = self.writer.execute(scope, self.task.conversation_id(), history);
        absolute_result(&mut result, &self.writer_root);
        Ok(result)
    }
}

fn absolute_result(result: &mut Value, base: &Path) {
    if let Some(path) = result["path"].as_str() {
        result["path"] = json!(base.join(path));
    }
}
