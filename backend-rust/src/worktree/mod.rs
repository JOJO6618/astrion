//! Conversation worktrees on a trusted local Host checkout.

mod carry;
mod git;
mod routes;

use std::{
    collections::HashMap,
    ffi::OsString,
    fmt,
    fs::{self, File, OpenOptions},
    io::{self, Read, Write},
    path::{Path, PathBuf},
    sync::{LazyLock, Mutex},
};

use ring::digest::{SHA256, digest};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

use crate::project_git::{GitError, canonicalize, runner::object_id};

pub use routes::router_with_scope;

const MANAGED: &str = ".astrion/worktrees";

// Git locks its refs and worktree records independently. Serialize the full
// prepare operation across conversations that share a common admin directory.
static PREPARATION: Mutex<()> = Mutex::new(());

pub type Result<T> = std::result::Result<T, Error>;

#[derive(Debug)]
pub struct Error(String);

impl Error {
    fn invalid(message: impl Into<String>) -> Self {
        Self(message.into())
    }
}

impl fmt::Display for Error {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for Error {}

impl From<io::Error> for Error {
    fn from(error: io::Error) -> Self {
        Self(error.to_string())
    }
}

impl From<GitError> for Error {
    fn from(error: GitError) -> Self {
        Self(error.0)
    }
}

#[derive(Clone, Deserialize, Serialize)]
pub struct Binding {
    pub directory: PathBuf,
    pub branch: String,
    pub head: String,
    pub active: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    include_uncommitted: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    grant: Option<Grant>,
    #[serde(default)]
    pub revision: u64,
}

#[derive(Clone, Deserialize, Serialize)]
struct Grant {
    path: PathBuf,
    added: bool,
}

#[derive(Deserialize)]
#[serde(tag = "action", rename_all = "lowercase", deny_unknown_fields)]
pub enum Target {
    Enter {
        branch_name: Option<String>,
        directory: Option<PathBuf>,
    },
    Create {
        branch_name: String,
        start_ref: String,
        include_uncommitted: bool,
    },
}

pub struct Worktree {
    workspace: PathBuf,
    common: PathBuf,
}

impl Worktree {
    pub fn new(workspace: &Path) -> Result<Self> {
        let workspace = canonicalize(workspace)?;
        git::require_checkout(&workspace)?;
        let common = git::canonical_git_path(&workspace, "--git-common-dir")?;
        if !common.is_dir() {
            return Err(Error::invalid("Git 公共目录不可用。"));
        }
        Ok(Self { workspace, common })
    }

    pub fn prepare(
        &self,
        target: Target,
        source: &Path,
        work_mode: &str,
        permission: &str,
    ) -> Result<Binding> {
        match target {
            Target::Enter {
                branch_name: None,
                directory: Some(directory),
            } => self.inspect(&directory),
            Target::Enter {
                branch_name: Some(branch),
                directory: None,
            } => self.enter_branch(&branch),
            Target::Enter { .. } => Err(Error::invalid(
                "enter 只能提供 branch_name 或 directory 其中一个。",
            )),
            Target::Create {
                branch_name,
                start_ref,
                include_uncommitted,
            } => {
                if work_mode == "plan"
                    || !matches!(permission, "approval" | "auto_approval" | "unrestricted")
                {
                    return Err(Error::invalid("只读或计划模式不能创建工作树。"));
                }
                let _guard = PREPARATION
                    .lock()
                    .map_err(|_| Error::invalid("工作树准备服务不可用。"))?;
                self.create(source, &branch_name, &start_ref, include_uncommitted)
            }
        }
    }

    pub fn main_binding(&self) -> Result<Binding> {
        Ok(Binding {
            directory: self.workspace.clone(),
            branch: git::branch(&self.workspace)?,
            head: git::verify_ref(&self.workspace, "HEAD").unwrap_or_default(),
            active: false,
            include_uncommitted: None,
            grant: None,
            revision: 0,
        })
    }

    pub fn inspect(&self, directory: &Path) -> Result<Binding> {
        if !directory.is_absolute() || !fs::symlink_metadata(directory)?.is_dir() {
            return Err(Error::invalid("工作树必须是非符号链接的绝对目录。"));
        }
        let directory = canonicalize(directory)?;
        if directory == self.workspace
            || directory.parent().is_none()
            || std::env::home_dir()
                .and_then(|home| canonicalize(&home).ok())
                .as_deref()
                == Some(directory.as_path())
            || !fs::symlink_metadata(directory.join(".git"))?.is_file()
        {
            return Err(Error::invalid("目标必须是本仓库的已注册工作树。"));
        }
        git::require_checkout(&directory)?;
        let admin = git::canonical_git_path(&directory, "--absolute-git-dir")?;
        if git::canonical_git_path(&directory, "--git-common-dir")? != self.common
            || admin.parent() != Some(self.common.join("worktrees").as_path())
        {
            return Err(Error::invalid("目标工作树不属于当前仓库。"));
        }
        let backlink = admin.join("gitdir");
        let pointer = read_pointer(&backlink)?;
        let marker = admin.join(git::local_path(git::line(&pointer))?);
        if canonicalize(&marker)? != directory.join(".git") {
            return Err(Error::invalid("Git 工作树登记记录与目标路径不匹配。"));
        }
        Ok(Binding {
            branch: git::branch(&directory)?,
            head: git::verify_ref(&directory, "HEAD")?,
            active: true,
            grant: (!directory.starts_with(&self.workspace)).then(|| Grant {
                path: directory.clone(),
                added: true,
            }),
            directory,
            include_uncommitted: None,
            revision: 0,
        })
    }

    fn enter_branch(&self, branch: &str) -> Result<Binding> {
        if branch.trim().is_empty() || branch.starts_with('-') {
            return Err(Error::invalid("必须提供合法的工作树分支名。"));
        }
        let mut entries = self
            .entries()?
            .into_iter()
            .filter(|entry| entry.branch == branch);
        let entry = entries
            .next()
            .ok_or_else(|| Error::invalid("找不到已注册的工作树分支。"))?;
        if entries.next().is_some() {
            return Err(Error::invalid("分支对应多个工作树，请按目录选择。"));
        }
        let binding = self.inspect(&entry.directory)?;
        if binding.branch != branch {
            return Err(Error::invalid("目标工作树分支已变化，请重新选择。"));
        }
        Ok(binding)
    }

    pub fn execution_root(&self, metadata: &Value) -> Result<PathBuf> {
        if metadata["git_worktree_enabled"] != true || metadata["git_worktree"].is_null() {
            return Ok(self.workspace.clone());
        }
        let state = &metadata["git_worktree"];
        let directory = state["directory"]
            .as_str()
            .map(Path::new)
            .ok_or_else(invalid_binding)?;
        let canonical = canonicalize(directory).map_err(|_| invalid_binding())?;
        if canonical != directory {
            return Err(invalid_binding());
        }
        if directory == self.workspace && state["active"] != true {
            return Ok(canonical);
        }
        if state["active"] != true
            || !directory.starts_with(&self.workspace)
                && state["grant"]["path"].as_str().map(Path::new) != Some(directory)
        {
            return Err(invalid_binding());
        }
        self.inspect(directory)
            .map(|binding| binding.directory)
            .map_err(|_| invalid_binding())
    }

    pub fn view(&self, metadata: &Value) -> Result<Value> {
        let saved = &metadata["git_worktree"];
        let (directory, unavailable) = match self.execution_root(metadata) {
            Ok(root) => (root, String::new()),
            Err(error) => (
                saved["directory"]
                    .as_str()
                    .map(PathBuf::from)
                    .unwrap_or_else(|| self.workspace.clone()),
                error.to_string(),
            ),
        };
        let entries = self.entries()?;
        let selected = entries.iter().find(|entry| entry.directory == directory);
        let branch = selected
            .map(|entry| entry.branch.as_str())
            .or_else(|| saved["branch"].as_str())
            .unwrap_or_default();
        let head = selected
            .map(|entry| entry.head.as_str())
            .or_else(|| saved["head"].as_str())
            .unwrap_or_default();
        let trees: Vec<_> = entries.iter().map(|entry| {
            let inside = entry.directory.starts_with(&self.workspace);
            json!({"branch":entry.branch,"directory":entry.directory,"head":entry.head,"is_main":entry.directory==self.workspace,
                "detached":entry.detached,"inside_workspace":inside,"authorized":inside,
                "unavailable":entry.prunable||!entry.directory.is_dir(),"current":entry.directory==directory})
        }).collect();
        let refs = git::text(
            &self.workspace,
            [
                "for-each-ref",
                "--format=%(refname:short)",
                "refs/heads",
                "refs/tags",
            ],
        )?;
        Ok(
            json!({"enabled":true,"directory":directory,"branch":branch,"head":head,"active":directory!=self.workspace,
            "worktrees":trees,"refs":refs.lines().collect::<Vec<_>>(),"unavailable":unavailable}),
        )
    }

    fn entries(&self) -> Result<Vec<Entry>> {
        let raw = git::read(&self.workspace, ["worktree", "list", "--porcelain", "-z"])?;
        let mut entries = Vec::new();
        let mut fields: HashMap<&[u8], &[u8]> = HashMap::new();
        for field in raw.split(|byte| *byte == 0) {
            if field.is_empty() {
                if let Some(directory) = fields.get(&b"worktree"[..]) {
                    let path = git::local_path(directory)?;
                    entries.push(Entry {
                        branch: value(&fields, b"branch")
                            .trim_start_matches("refs/heads/")
                            .to_owned(),
                        head: value(&fields, b"HEAD").to_string(),
                        directory: canonicalize(&path).unwrap_or(path),
                        detached: fields.contains_key(&b"detached"[..]),
                        prunable: fields.contains_key(&b"prunable"[..]),
                    });
                }
                fields.clear();
            } else if let Some(split) = field.iter().position(|byte| *byte == b' ') {
                fields.insert(&field[..split], &field[split + 1..]);
            } else {
                fields.insert(field, &[]);
            }
        }
        Ok(entries)
    }

    fn create(
        &self,
        source: &Path,
        branch: &str,
        start_ref: &str,
        include: bool,
    ) -> Result<Binding> {
        if branch.is_empty()
            || branch.starts_with('-')
            || branch.starts_with('@')
            || branch.chars().count() > 255
        {
            return Err(Error::invalid("必须提供合法的新分支名。"));
        }
        git::read(&self.workspace, ["check-ref-format", "--branch", branch])?;
        let full_branch = format!("refs/heads/{branch}");
        if git::command(&self.workspace)
            .read_optional(["show-ref", "--verify", "--quiet", &full_branch])?
            .is_some()
        {
            return Err(Error::invalid(
                "分支已存在，请使用其它名称或进入该分支已有的工作树。",
            ));
        }
        if source != self.workspace {
            self.inspect(source)?;
        }
        let head = git::verify_ref(source, start_ref)?;
        let managed = self.workspace.join(MANAGED);
        ensure_directory(&self.workspace.join(".astrion"))?;
        ensure_directory(&managed)?;
        let directory = managed.join(branch_directory(branch));
        match fs::symlink_metadata(&directory) {
            Ok(_) => return Err(Error::invalid("工作树目录已存在，请选择其它分支。")),
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => return Err(error.into()),
        }
        self.ensure_exclude()?;
        let filters = git::safe_filters(&self.workspace)?;
        let carry = include.then(|| carry::Carry::prepare(source)).transpose()?;
        let command = [
            OsString::from("worktree"),
            "add".into(),
            "-b".into(),
            branch.into(),
            "--".into(),
            directory.as_os_str().to_owned(),
            head.as_str().into(),
        ];
        git::run_edit(&self.workspace, &filters, command)?;
        let apply = carry.map_or(Ok(()), |carry| carry.apply(&directory, &managed, &filters));
        if let Err(error) = apply {
            return Err(Error::invalid(format!(
                "{error}\n已保留分支 {branch}，目录 {}；可进入已有工作树处理。",
                directory.display()
            )));
        }
        let mut binding = self.inspect(&directory).map_err(|error| {
            Error::invalid(format!(
                "{error}\n已保留分支 {branch}，目录 {}。",
                directory.display()
            ))
        })?;
        binding.include_uncommitted = Some(include);
        Ok(binding)
    }

    fn ensure_exclude(&self) -> Result<()> {
        let info = self.common.join("info");
        ensure_directory(&info)?;
        let path = info.join("exclude");
        match fs::symlink_metadata(&path) {
            Ok(metadata) if !metadata.is_file() => {
                return Err(Error::invalid("Git 忽略文件必须是普通文件。"));
            }
            Ok(_) => {}
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => return Err(error.into()),
        }
        let mut file = OpenOptions::new()
            .read(true)
            .append(true)
            .create(true)
            .open(&path)?;
        let mut bytes = Vec::new();
        (&mut file).take(1_048_577).read_to_end(&mut bytes)?;
        if bytes.len() > 1_048_576 {
            return Err(Error::invalid("Git 忽略文件超过读取上限。"));
        }
        if !bytes
            .split(|byte| *byte == b'\n')
            .any(|line| line.strip_suffix(b"\r").unwrap_or(line) == b"/.astrion/worktrees/")
        {
            file.write_all(b"\n/.astrion/worktrees/\n")?;
        }
        Ok(())
    }
}

fn invalid_binding() -> Error {
    Error::invalid("工作树执行目录已失效或被重定向，请先返回主目录或选择有效工作树。")
}

struct Entry {
    directory: PathBuf,
    head: String,
    branch: String,
    detached: bool,
    prunable: bool,
}

fn value<'a>(fields: &HashMap<&[u8], &'a [u8]>, key: &[u8]) -> std::borrow::Cow<'a, str> {
    String::from_utf8_lossy(fields.get(key).copied().unwrap_or_default())
}

fn ensure_directory(path: &Path) -> Result<()> {
    match fs::create_dir(path) {
        Ok(()) => {}
        Err(error) if error.kind() == io::ErrorKind::AlreadyExists => {}
        Err(error) => return Err(error.into()),
    }
    if !fs::symlink_metadata(path)?.is_dir() || canonicalize(path)? != path {
        return Err(Error::invalid(format!(
            "工作树目录不能被重定向：{}",
            path.display()
        )));
    }
    Ok(())
}

fn read_pointer(path: &Path) -> Result<Vec<u8>> {
    let metadata = fs::symlink_metadata(path)?;
    if !metadata.is_file() || metadata.len() > 8_192 {
        return Err(Error::invalid("工作树 Git 登记记录无效。"));
    }
    let mut data = Vec::new();
    File::open(path)?.take(8_193).read_to_end(&mut data)?;
    if data.len() > 8_192 {
        return Err(Error::invalid("工作树 Git 登记记录无效。"));
    }
    Ok(data)
}

fn branch_directory(branch: &str) -> String {
    let mut leaf = String::new();
    for char in branch.chars() {
        if char.is_ascii_alphanumeric() || matches!(char, '.' | '-' | '_') {
            leaf.push(char);
        } else if !leaf.ends_with('-') {
            leaf.push('-');
        }
    }
    let leaf: String = leaf.trim_matches(['.', '-']).chars().take(64).collect();
    let leaf = if leaf.is_empty() { "worktree" } else { &leaf };
    let hash = digest(&SHA256, branch.as_bytes());
    let suffix: String = hash.as_ref()[..4]
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect();
    format!("{leaf}-{suffix}")
}

#[cfg(test)]
#[path = "../../../test/2026-11-24_Rust后端/project_git_worktree.rs"]
mod tests;
