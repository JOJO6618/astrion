use std::{
    ffi::{OsStr, OsString},
    io::Read,
    path::{Component, Path, PathBuf},
    time::Duration,
};

use cap_std::{
    ambient_authority,
    fs::{Dir, OpenOptions},
};

use crate::worktree::Worktree;

use super::super::{GitResult, canonicalize, discovery::git_path, runner::Git};

const QUERY_TIMEOUT: u64 = 2;
const PATCH_TIMEOUT: u64 = 8;
const FILE_TIMEOUT: u64 = 3;
const MAX_TEXT_BYTES: u64 = 32 * 1024 * 1024;

const DIFF: &[&str] = &[
    "diff",
    "--no-ext-diff",
    "--no-textconv",
    "--no-color",
    "--no-relative",
    "--submodule=short",
];

pub(super) struct Checkout {
    root: PathBuf,
    git_dir: Option<PathBuf>,
}

pub(super) struct Changes {
    pub(super) entries: Vec<Change>,
    filters: Option<Vec<String>>,
}

pub(super) struct Change {
    pub(super) path: PathBuf,
    old_path: Option<PathBuf>,
    additions: u64,
    deletions: u64,
}

impl Change {
    pub(super) fn paths(&self) -> impl Iterator<Item = &Path> {
        self.old_path
            .as_deref()
            .into_iter()
            .chain([self.path.as_path()])
    }
}

impl Changes {
    pub(super) fn totals(&self) -> (u64, u64) {
        self.entries
            .iter()
            .fold((0u64, 0u64), |(additions, deletions), change| {
                (
                    additions.saturating_add(change.additions),
                    deletions.saturating_add(change.deletions),
                )
            })
    }
}

impl Checkout {
    pub(super) fn discover(workspace: &Path, execution: &Path) -> Option<Self> {
        let git_dir = mapped_git_dir(workspace, execution).ok()?;
        let bound = execution != workspace;
        if bound && git_dir.is_none() {
            // A persisted binding cannot turn an arbitrary folder into an execution root.
            Worktree::new(workspace)
                .and_then(|worktree| worktree.inspect(execution))
                .ok()?;
        }
        let mut checkout = Self {
            root: execution.to_owned(),
            git_dir,
        };
        let top = checkout
            .read(QUERY_TIMEOUT, ["rev-parse", "--show-toplevel"], &[])
            .ok()?;
        let root = canonicalize(&git_path(without_line_ending(&top)).ok()?).ok()?;
        if (bound && root != execution) || !execution.starts_with(&root) {
            return None;
        }
        checkout.root = root;
        Some(checkout)
    }

    pub(super) fn root(&self) -> &Path {
        &self.root
    }

    pub(super) fn branch(&self) -> String {
        let branch = self.text(["branch", "--show-current"]);
        if !branch.is_empty() {
            return branch;
        }
        let head = self.text(["rev-parse", "--short", "HEAD"]);
        if head.is_empty() {
            "HEAD".into()
        } else {
            format!("HEAD {head}")
        }
    }

    pub(super) fn upstream(&self) -> String {
        self.text(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
    }

    pub(super) fn changes(&self) -> Changes {
        let filters = self.filter_overrides().ok();
        let output = filters.as_deref().and_then(|filters| {
            self.read(
                QUERY_TIMEOUT,
                DIFF.iter()
                    .copied()
                    .chain(["--numstat", "-z", "HEAD", "--"]),
                filters,
            )
            .ok()
        });
        let entries = output
            .as_deref()
            .and_then(parse_numstat)
            .unwrap_or_default();
        Changes { entries, filters }
    }

    pub(super) fn diff<'a>(
        &self,
        changes: &Changes,
        context: usize,
        paths: impl IntoIterator<Item = &'a Path>,
    ) -> Vec<u8> {
        let Some(filters) = &changes.filters else {
            return Vec::new();
        };
        let args = DIFF
            .iter()
            .copied()
            .chain(["--src-prefix=a/", "--dst-prefix=b/"])
            .map(OsString::from)
            .chain([
                OsString::from(format!("--unified={context}")),
                "HEAD".into(),
                "--".into(),
            ])
            .chain(paths.into_iter().map(|path| path.as_os_str().to_owned()));
        self.read(PATCH_TIMEOUT, args, filters).unwrap_or_default()
    }

    pub(super) fn head_bytes(&self, path: &Path) -> Option<Vec<u8>> {
        let mut object = OsString::from("HEAD:");
        object.push(path);
        self.read(
            FILE_TIMEOUT,
            [OsStr::new("show"), OsStr::new("--no-textconv"), &object],
            &[],
        )
        .ok()
    }

    pub(super) fn working_bytes(&self, path: &Path) -> Option<Vec<u8>> {
        if !path
            .components()
            .all(|component| matches!(component, Component::Normal(_)))
        {
            return None;
        }
        // Resolve relative links inside the checkout through a directory capability.
        let checkout = Dir::open_ambient_dir(&self.root, ambient_authority()).ok()?;
        if !checkout.metadata(path).ok()?.is_file() {
            return None;
        }
        let mut options = OpenOptions::new();
        options.read(true);
        #[cfg(unix)]
        {
            use cap_std::fs::OpenOptionsExt;
            options.custom_flags(libc::O_NONBLOCK);
        }
        let file = checkout.open_with(path, &options).ok()?;
        let metadata = file.metadata().ok()?;
        if !metadata.is_file() || metadata.len() > MAX_TEXT_BYTES {
            return None;
        }
        let mut bytes = Vec::new();
        file.take(MAX_TEXT_BYTES + 1).read_to_end(&mut bytes).ok()?;
        (bytes.len() as u64 <= MAX_TEXT_BYTES).then_some(bytes)
    }

    fn text(&self, args: impl IntoIterator<Item = &'static str>) -> String {
        let bytes = self.read(QUERY_TIMEOUT, args, &[]).unwrap_or_default();
        String::from_utf8_lossy(&bytes).trim().to_owned()
    }

    fn read(
        &self,
        seconds: u64,
        args: impl IntoIterator<Item = impl AsRef<OsStr>>,
        config: &[String],
    ) -> GitResult<Vec<u8>> {
        Git::new(&self.root)
            .with_timeout(Duration::from_secs(seconds))
            .with_config(config)
            .read(
                self.git_args()
                    .map(OsStr::to_owned)
                    .chain(args.into_iter().map(|arg| arg.as_ref().to_owned())),
            )
    }

    fn git_args(&self) -> impl Iterator<Item = &OsStr> {
        self.git_dir.iter().flat_map(|git_dir| {
            [
                OsStr::new("--git-dir"),
                git_dir.as_os_str(),
                OsStr::new("--work-tree"),
                self.root.as_os_str(),
            ]
        })
    }

    fn filter_overrides(&self) -> GitResult<Vec<String>> {
        // Git may run configured clean filters while comparing working files to HEAD.
        let args = [
            "config",
            "--includes",
            "--null",
            "--name-only",
            "--get-regexp",
            "^filter\\.",
        ];
        let git = Git::new(&self.root).with_timeout(Duration::from_secs(QUERY_TIMEOUT));
        let output = git.read_optional(self.git_args().chain(args.map(OsStr::new)))?;
        let Some(output) = output else {
            return Ok(Vec::new());
        };
        let mut overrides = Vec::new();
        for field in output
            .split(|&byte| byte == 0)
            .filter(|field| !field.is_empty())
        {
            let key = std::str::from_utf8(field).map_err(|_| "无效的 Git 过滤器配置。")?;
            if key.contains(['=', '\r', '\n']) {
                return Err("无效的 Git 过滤器配置。".into());
            }
            let override_value = match key.rsplit_once('.').map(|(_, property)| property) {
                Some("required") => "false",
                Some("process" | "clean" | "smudge") => "",
                _ => continue,
            };
            overrides.push(format!("{key}={override_value}"));
        }
        Ok(overrides)
    }
}

fn parse_numstat(output: &[u8]) -> Option<Vec<Change>> {
    let mut fields = output.split(|&byte| byte == 0);
    let mut changes = Vec::new();
    while let Some(row) = fields.next().filter(|row| !row.is_empty()) {
        let mut parts = row.splitn(3, |&byte| byte == b'\t');
        let additions = count(parts.next()?);
        let deletions = count(parts.next()?);
        let path = parts.next()?;
        let (old_path, path) = if path.is_empty() {
            (
                Some(git_path(fields.next()?).ok()?),
                git_path(fields.next()?).ok()?,
            )
        } else {
            (None, git_path(path).ok()?)
        };
        changes.push(Change {
            path,
            old_path,
            additions,
            deletions,
        });
    }
    Some(changes)
}

fn count(bytes: &[u8]) -> u64 {
    std::str::from_utf8(bytes)
        .ok()
        .and_then(|text| text.parse().ok())
        .unwrap_or_default()
}

fn without_line_ending(bytes: &[u8]) -> &[u8] {
    let bytes = bytes.strip_suffix(b"\n").unwrap_or(bytes);
    if cfg!(windows) {
        bytes.strip_suffix(b"\r").unwrap_or(bytes)
    } else {
        bytes
    }
}

#[cfg(not(windows))]
fn mapped_git_dir(_workspace: &Path, _execution: &Path) -> GitResult<Option<PathBuf>> {
    Ok(None)
}

#[cfg(windows)]
fn mapped_git_dir(workspace: &Path, execution: &Path) -> GitResult<Option<PathBuf>> {
    let managed = workspace.join(".astrion/worktrees");
    if execution.parent() != Some(managed.as_path()) {
        return Ok(None);
    }
    let gitfile = execution.join(".git");
    let pointer = read_git_pointer(&gitfile)?;
    let raw = pointer
        .strip_prefix("gitdir: ")
        .ok_or("工作树 Git 目录记录无效。")?;
    let Some(admin) = wsl_path(raw) else {
        return Ok(None);
    };
    let expected_parent = workspace.join(".git/worktrees");
    if admin.parent() != Some(expected_parent.as_path())
        || canonicalize(&admin)? != admin
        || !admin.is_dir()
    {
        return Err("工作树 Git 管理目录已失效。".into());
    }
    let backlink = read_git_pointer(&admin.join("gitdir"))?;
    if wsl_path(&backlink).as_deref() != Some(gitfile.as_path()) {
        return Err("Git 工作树记录与当前目录不匹配。".into());
    }
    if canonicalize(&admin.join(read_git_pointer(&admin.join("commondir"))?))?
        != workspace.join(".git")
    {
        return Err("目标工作树不属于当前仓库。".into());
    }
    Ok(Some(admin))
}

#[cfg(windows)]
fn read_git_pointer(path: &Path) -> GitResult<String> {
    use std::fs::{self, File};
    const POINTER_LIMIT: u64 = 8192;
    let metadata = fs::symlink_metadata(path)?;
    if !metadata.is_file() || metadata.len() > POINTER_LIMIT {
        return Err("工作树 Git 目录记录无效。".into());
    }
    let mut text = String::new();
    File::open(path)?
        .take(POINTER_LIMIT + 1)
        .read_to_string(&mut text)?;
    if text.len() as u64 > POINTER_LIMIT || text.contains('\0') {
        return Err("工作树 Git 目录记录无效。".into());
    }
    Ok(text.trim().to_owned())
}

#[cfg(windows)]
fn wsl_path(raw: &str) -> Option<PathBuf> {
    let rest = raw.strip_prefix("/mnt/")?;
    let (drive, path) = rest.split_once('/').unwrap_or((rest, ""));
    (drive.len() == 1 && drive.as_bytes()[0].is_ascii_alphabetic())
        .then(|| PathBuf::from(format!("{}:/{}", drive.to_ascii_uppercase(), path)))
}
