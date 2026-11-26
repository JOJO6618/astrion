use super::{Result, Source, Workflow, WorkflowError, files};
use serde_json::{Value, json};
use std::{
    collections::BTreeMap,
    fs,
    path::{Path, PathBuf},
    sync::Mutex,
};

static REGISTRY_LOCK: Mutex<()> = Mutex::new(());

#[derive(Clone, Debug)]
pub struct WorkflowRegistry {
    builtin: PathBuf,
    user: PathBuf,
}

impl WorkflowRegistry {
    /// Accept only roots from host configuration. The user registry is Host-wide,
    /// and is shared by all workspaces, just like the Python registry.
    pub fn new(builtin: impl AsRef<Path>, user: impl AsRef<Path>) -> Result<Self> {
        let builtin = files::resolve_root(builtin.as_ref())?;
        let user = files::resolve_root(user.as_ref())?;
        if user.starts_with(&builtin) || builtin.starts_with(&user) {
            return Err(files::invalid_path());
        }
        Ok(Self { builtin, user })
    }

    pub fn list_response(&self) -> Result<Value> {
        let _guard = registry_lock()?;
        let mut merged = BTreeMap::<String, Value>::new();
        for (root, source) in [(&self.builtin, Source::Builtin), (&self.user, Source::User)] {
            if !files::checked_root(root)? {
                continue;
            }
            let mut entries = fs::read_dir(root)?.collect::<std::io::Result<Vec<_>>>()?;
            entries.sort_by_key(|entry| entry.file_name());
            for entry in entries {
                let file_name = entry.file_name();
                let Some(slug) = file_name.to_str().filter(|name| valid_name(name)) else {
                    continue;
                };
                // A link may point at a different registry or an unrelated workspace.
                if !matches!(files::directory(&entry.path()), Ok(true)) {
                    continue;
                }
                let path = entry.path().join(files::MARKDOWN_FILE);
                let result = files::read(&path);
                let (name, description, updated_at, node_count) = match result {
                    Ok(None) => continue,
                    Ok(Some(text)) => match Workflow::from_markdown(&text, source) {
                        Ok(wf) => (
                            if wf.name.is_empty() {
                                slug.to_owned()
                            } else {
                                wf.name
                            },
                            wf.description,
                            wf.updated_at,
                            wf.nodes.len(),
                        ),
                        Err(_) => invalid_list_entry(slug),
                    },
                    Err(err) if err.status == 400 => continue,
                    Err(_) => invalid_list_entry(slug),
                };
                let item = json!({"name": name, "description": description, "source": source, "updatedAt": updated_at, "nodeCount": node_count});
                merged.insert(name, item);
            }
        }
        Ok(json!({"workflows": merged.into_values().collect::<Vec<_>>()}))
    }

    pub fn get_response(&self, name: &str) -> Result<Value> {
        Ok(json!({"workflow": self.load(name)?}))
    }

    pub fn put_response(&self, name: &str, mut body: Value) -> Result<Value> {
        let name = safe_name(name)?;
        let workflow = body
            .get_mut("workflow")
            .and_then(Value::as_object_mut)
            .ok_or_else(|| WorkflowError::new(400, "请求体缺少 workflow 对象"))?;
        workflow.insert("name".into(), json!(name));
        workflow.shift_remove("source");
        workflow.shift_remove("updatedAt");
        let wf: Workflow = serde_json::from_value(Value::Object(std::mem::take(workflow)))
            .map_err(|error| WorkflowError::new(400, format!("工作流定义无效：{error}")))?;
        self.save(wf)?;
        Ok(json!({"ok": true}))
    }

    pub fn delete_response(&self, name: &str) -> Result<Value> {
        let name = safe_name(name)?;
        let _guard = registry_lock()?;
        if let Some(dir) = child_directory(&self.user, name)? {
            fs::remove_dir_all(dir)?;
            return Ok(json!({"ok": true}));
        }
        if let Some(dir) = child_directory(&self.builtin, name)?
            && files::file(&dir.join(files::MARKDOWN_FILE))?
        {
            return Err(WorkflowError::new(
                400,
                "内置示例不可删除（可复制为用户工作流后删除副本）",
            ));
        }
        Err(not_found(name))
    }

    pub fn load(&self, name: &str) -> Result<Workflow> {
        let (text, source) = self.markdown(name)?;
        Workflow::from_markdown(&text, source)
    }

    pub fn read_markdown(&self, name: &str) -> Result<String> {
        self.markdown(name).map(|(text, _)| text)
    }

    pub fn save(&self, mut workflow: Workflow) -> Result<()> {
        workflow.name = safe_name(&workflow.name)?.into();
        workflow.validate()?;
        workflow.updated_at = files::now_minute();
        workflow.source = Source::User;
        let content = workflow.to_markdown();
        let _guard = registry_lock()?;
        if !files::checked_root(&self.user)? {
            fs::create_dir_all(&self.user)?;
            files::checked_root(&self.user)?;
        }
        let dir = self.user.join(&workflow.name);
        if !files::directory(&dir)? {
            fs::create_dir(&dir)?;
        }
        let path = dir.join(files::MARKDOWN_FILE);
        files::file(&path)?;
        files::write_atomic(&path, content.as_bytes())?;
        Ok(())
    }

    pub(super) fn markdown(&self, name: &str) -> Result<(String, Source)> {
        let name = safe_name(name)?;
        let _guard = registry_lock()?;
        for (root, source) in [(&self.user, Source::User), (&self.builtin, Source::Builtin)] {
            if let Some(dir) = child_directory(root, name)?
                && let Some(text) = files::read(&dir.join(files::MARKDOWN_FILE))?
            {
                return Ok((text, source));
            }
        }
        Err(not_found(name))
    }
}

fn child_directory(root: &Path, name: &str) -> Result<Option<PathBuf>> {
    if !files::checked_root(root)? {
        return Ok(None);
    }
    let dir = root.join(name);
    Ok(files::directory(&dir)?.then_some(dir))
}

fn registry_lock() -> Result<std::sync::MutexGuard<'static, ()>> {
    REGISTRY_LOCK
        .lock()
        .map_err(|_| WorkflowError::new(500, "无法锁定工作流库"))
}

fn invalid_list_entry(name: &str) -> (String, String, String, usize) {
    (name.into(), "（文件解析失败）".into(), String::new(), 0)
}

fn not_found(name: &str) -> WorkflowError {
    WorkflowError::new(404, format!("工作流不存在：{name}"))
}

fn safe_name(name: &str) -> Result<&str> {
    let name = name.trim();
    if valid_name(name) {
        Ok(name)
    } else {
        Err(WorkflowError::new(
            400,
            format!("工作流名称不合法：{name:?}（仅限小写字母/数字/连字符，1-64 字符）"),
        ))
    }
}

fn valid_name(name: &str) -> bool {
    let bytes = name.as_bytes();
    !bytes.is_empty()
        && bytes.len() <= 64
        && bytes[0].is_ascii_alphanumeric()
        && bytes
            .iter()
            .copied()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
}
