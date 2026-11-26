//! Work with the legacy hidden Git object store. The real workspace index and
//! ref state are never passed to a Git mutation.

use std::{
    collections::{BTreeMap, BTreeSet},
    ffi::OsStr,
    io,
    path::{Path, PathBuf},
    time::Duration,
};

use cap_std::fs::Dir;
use serde_json::{Map, Value, json};
use uuid::Uuid;

use super::{
    RuntimeError, Store, files, open_data_directory, shallow_write, storage,
    transaction::{self, FileState, Scope, Transaction},
};
use crate::project_git::runner::{Git, object_id};

const EMPTY_TREE: &str = "4b825dc642cb6eb9a060e54bf8d69288fbee4904";
const PRIVATE: &[&str] = &[
    ".git",
    ".astrion",
    ".ssh",
    ".aws",
    ".azure",
    ".gcp",
    ".google",
    ".kube",
    ".docker",
    ".gnupg",
    ".npmrc",
    ".netrc",
    ".pypirc",
    ".git-credentials",
    ".bash_history",
    ".zsh_history",
    ".psql_history",
    ".mysql_history",
    ".pgpass",
    "host_api_token",
    "rundata_settings.json",
];

pub(super) struct Snapshot {
    pub tree: String,
    pub index: Vec<u8>,
    pub summary: Map<String, Value>,
}

pub(super) fn baseline_tree(
    store: &Store,
    session: &Dir,
    id: &str,
    root: &Path,
) -> Result<String, RuntimeError> {
    let (path, _) = repository(store, session, id, root, true)?;
    git_id(git(root, &path, ["write-tree"])?)
}

pub(super) fn checkpoint(
    store: &Store,
    session: &Dir,
    id: &str,
    root: &Path,
    prior: &str,
) -> Result<Snapshot, RuntimeError> {
    let (path, existing) = repository(store, session, id, root, true)?;
    let stage = Stage::open(store, session, id, root, &path, &existing)?;
    if files::read(&existing, "index").map_err(storage)?.is_some() {
        let index = files::read(&existing, "index")
            .map_err(storage)?
            .expect("index exists");
        files::write(&stage.dir, "index", &index).map_err(storage)?;
    } else if prior != EMPTY_TREE {
        stage.run(["read-tree", oid(prior)?])?;
    }
    // A template-free staging repository has no custom clean filters, hooks or fsmonitor.
    stage.run(["add", "-A", "--", "."])?;
    let tree = git_id(stage.run(["write-tree"])?)?;
    let entries = tree_entries(root, &stage.path, &tree)?;
    for path in entries.keys() {
        safe_path(root, &store.data_dir, path)?;
    }
    let summary = diff(&stage, prior, &tree)?;
    let index = stage.index()?;
    // Git objects are immutable. A failed metadata transaction can leave only unreferenced objects.
    let objects = files::child(&stage.dir, "objects", false)
        .map_err(storage)?
        .expect("git objects exists");
    let dest = files::child(&existing, "objects", false)
        .map_err(storage)?
        .expect("git objects exists");
    for entry in object_files(&objects)? {
        if entry.0.starts_with("pack/") {
            continue;
        }
        let (folder, name) = entry
            .0
            .split_once('/')
            .expect("object path includes prefix");
        let parent = files::child(&dest, folder, true)
            .map_err(storage)?
            .expect("object parent created");
        if files::read(&parent, name).map_err(storage)?.is_none() {
            files::write(&parent, name, &entry.1).map_err(storage)?;
        }
    }
    Ok(Snapshot {
        tree,
        index,
        summary,
    })
}

pub(super) fn restore(
    store: &Store,
    session: &Dir,
    source: &str,
    target: &str,
    root: &Path,
    tree: &str,
    tx: &mut Transaction<'_>,
) -> Result<(), RuntimeError> {
    let tree = oid(tree)?;
    let (repo, existing) = repository(store, session, source, root, false)?;
    let stage = Stage::open(store, session, source, root, &repo, &existing)?;
    stage.run(["read-tree", tree])?;
    let entries = tree_entries(root, &stage.path, tree)?;
    let mut visible = candidates(root, &repo)?;
    for path in visible.iter() {
        if let Err(error) = safe_path(root, &store.data_dir, path)
            && !is_protected(root, &store.data_dir, path)
        {
            return Err(error);
        }
    }
    visible.retain(|path| !is_protected(root, &store.data_dir, path));
    for (path, (mode, blob)) in &entries {
        safe_path(root, &store.data_dir, path)?;
        visible.remove(path);
        let bytes = stage.run(["cat-file", "blob", blob])?;
        #[cfg(unix)]
        let mode = Some(*mode);
        #[cfg(not(unix))]
        let mode = {
            let _ = mode;
            None
        };
        tx.change(
            Scope::Workspace,
            path,
            Some(FileState::workspace(bytes, mode)),
        )
        .map_err(storage)?;
    }
    for path in visible {
        tx.change(Scope::Workspace, &path, None).map_err(storage)?;
    }
    if source != target {
        create_copy_shell(store, target, root)?;
        if let Some(info) = files::child(&existing, "info", false).map_err(storage)?
            && let Some(excludes) = files::read(&info, "exclude").map_err(storage)?
        {
            tx.put(&format!("{target}/git/info/exclude"), excludes)
                .map_err(storage)?;
        }
        let objects = files::child(&existing, "objects", false)
            .map_err(storage)?
            .expect("git objects exists");
        for (name, bytes) in object_files(&objects)? {
            tx.put(&format!("{target}/git/objects/{name}"), bytes)
                .map_err(storage)?;
        }
    }
    tx.put(&format!("{target}/git/index"), stage.index()?)
        .map_err(storage)
}

struct Stage {
    parent: Dir,
    name: String,
    path: PathBuf,
    root: PathBuf,
    dir: Dir,
}

impl Stage {
    fn open(
        store: &Store,
        session: &Dir,
        id: &str,
        root: &Path,
        repo: &Path,
        existing: &Dir,
    ) -> Result<Self, RuntimeError> {
        let parent = session.try_clone().map_err(storage)?;
        let name = format!(".versioning-git-{}", Uuid::new_v4().simple());
        parent.create_dir(&name).map_err(storage)?;
        let dir = files::child(&parent, &name, false)
            .map_err(storage)?
            .expect("temporary Git dir exists");
        let path = store.data_dir.join("save").join(id).join(&name);
        let stage = Self {
            parent,
            name,
            path,
            root: root.to_owned(),
            dir,
        };
        ensure_init(root, &stage.path)?;
        let objects = files::child(&stage.dir, "objects", false)
            .map_err(storage)?
            .expect("staged objects exists");
        let info = files::child(&objects, "info", false)
            .map_err(storage)?
            .expect("object info exists");
        let alternate = repo.join("objects");
        let alternate = alternate
            .to_str()
            .filter(|string| !string.contains(['\n', '\r']))
            .ok_or_else(invalid_tree)?;
        files::write(&info, "alternates", format!("{alternate}\n").as_bytes()).map_err(storage)?;
        let mut ignores =
            if let Some(info) = files::child(existing, "info", false).map_err(storage)? {
                files::read(&info, "exclude")
                    .map_err(storage)?
                    .unwrap_or_default()
            } else {
                Vec::new()
            };
        for protected in PRIVATE {
            ignores.extend(format!("\n{protected}\n").as_bytes());
        }
        if let Ok(storage) = store.data_dir.strip_prefix(root) {
            let mut pattern = String::new();
            for ch in storage.to_string_lossy().chars() {
                if matches!(ch, '[' | ']' | '*' | '?' | '\\') {
                    pattern.push('\\');
                }
                pattern.push(ch);
            }
            if pattern.contains(['\n', '\r']) {
                return Err(invalid_tree());
            }
            ignores.extend(format!("\n/{pattern}/\n").as_bytes());
        }
        let info = files::child(&stage.dir, "info", true)
            .map_err(storage)?
            .ok_or_else(invalid_tree)?;
        files::write(&info, "exclude", &ignores).map_err(storage)?;
        Ok(stage)
    }

    fn run<const N: usize>(&self, args: [&str; N]) -> Result<Vec<u8>, RuntimeError> {
        git(&self.root, &self.path, args)
    }

    fn index(&self) -> Result<Vec<u8>, RuntimeError> {
        files::read(&self.dir, "index")
            .map_err(storage)?
            .ok_or_else(invalid_tree)
    }
}

impl Drop for Stage {
    fn drop(&mut self) {
        let _ = self.parent.remove_dir_all(&self.name);
    }
}

fn create_copy_shell(store: &Store, id: &str, root: &Path) -> Result<(), RuntimeError> {
    let data = open_data_directory(&store.data_dir).map_err(storage)?;
    let save = files::child(&data, "save", false)
        .map_err(storage)?
        .expect("save exists");
    let record = files::child(&save, id, true)
        .map_err(storage)?
        .expect("target record created");
    repository(store, &record, id, root, true).map(|_| ())
}

fn repository(
    store: &Store,
    record: &Dir,
    id: &str,
    root: &Path,
    create: bool,
) -> Result<(PathBuf, Dir), RuntimeError> {
    if root.starts_with(&store.data_dir) {
        return Err(RuntimeError::bad_request(
            "Workspace is inside runtime storage",
        ));
    }
    open_data_directory(root).map_err(storage)?;
    let directory = files::child(record, "git", create)
        .map_err(storage)?
        .ok_or_else(|| RuntimeError::bad_request("Legacy checkpoint Git storage is missing"))?;
    let path = store.data_dir.join("save").join(id).join("git");
    if files::read(&directory, "HEAD").map_err(storage)?.is_none() && create {
        ensure_init(root, &path)?;
    }
    for entry in ["HEAD", "config"] {
        files::read(&directory, entry)
            .map_err(storage)?
            .ok_or_else(invalid_tree)?;
    }
    let objects = files::child(&directory, "objects", false)
        .map_err(storage)?
        .ok_or_else(invalid_tree)?;
    if let Some(info) = files::child(&objects, "info", false).map_err(storage)?
        && files::read(&info, "alternates").map_err(storage)?.is_some()
    {
        return Err(RuntimeError::bad_request(
            "Legacy checkpoint relies on an external Git object store",
        ));
    }
    for entry in objects.entries().map_err(storage)? {
        let entry = entry.map_err(storage)?;
        if entry.file_type().map_err(storage)?.is_symlink() {
            return Err(invalid_tree());
        }
    }
    Ok((path, directory))
}

fn ensure_init(root: &Path, repo: &Path) -> Result<(), RuntimeError> {
    Git::new(root)
        .read([
            OsStr::new("init"),
            OsStr::new("--bare"),
            OsStr::new("--template="),
            repo.as_os_str(),
        ])
        .map(|_| ())
        .map_err(|error| storage(io::Error::other(error)))
}

fn git<const N: usize>(root: &Path, repo: &Path, args: [&str; N]) -> Result<Vec<u8>, RuntimeError> {
    let args = [
        OsStr::new("--git-dir"),
        repo.as_os_str(),
        OsStr::new("--work-tree"),
        root.as_os_str(),
    ]
    .into_iter()
    .chain(args.map(OsStr::new));
    Git::new(root)
        .with_timeout(Duration::from_secs(120))
        .with_config(["core.splitIndex=false", "core.untrackedCache=false"])
        .read(args)
        .map_err(|error| storage(io::Error::other(error)))
}

fn tree_entries(
    root: &Path,
    repo: &Path,
    tree: &str,
) -> Result<BTreeMap<String, (u32, String)>, RuntimeError> {
    let listing = git(root, repo, ["ls-tree", "-r", "-z", oid(tree)?])?;
    let mut entries = BTreeMap::new();
    for record in nul_rows(&listing)? {
        let (header, path) = record.split_once('\t').ok_or_else(invalid_tree)?;
        let mut header = header.split(' ');
        let mode = match (header.next(), header.next()) {
            (Some("100644"), Some("blob")) => 0o644,
            (Some("100755"), Some("blob")) => 0o755,
            _ => {
                return Err(RuntimeError::bad_request(
                    "Checkpoint contains a symbolic link or special Git entry",
                ));
            }
        };
        let object = oid(header.next().ok_or_else(invalid_tree)?)?;
        if entries.insert(path.into(), (mode, object.into())).is_some() {
            return Err(invalid_tree());
        }
    }
    Ok(entries)
}

fn candidates(root: &Path, repo: &Path) -> Result<BTreeSet<String>, RuntimeError> {
    let bytes = git(
        root,
        repo,
        [
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
        ],
    )?;
    Ok(nul_rows(&bytes)?.map(str::to_owned).collect())
}

fn diff(stage: &Stage, prior: &str, tree: &str) -> Result<Map<String, Value>, RuntimeError> {
    let bytes = stage.run([
        "diff",
        "--numstat",
        "--no-renames",
        "--no-ext-diff",
        "-z",
        oid(prior)?,
        tree,
    ])?;
    let mut files = Vec::new();
    let mut insertions = 0_u64;
    let mut deletions = 0_u64;
    let previous = tree_entries(&stage.root, &stage.path, prior)?;
    let next = tree_entries(&stage.root, &stage.path, tree)?;
    for row in nul_rows(&bytes)? {
        let mut columns = row.splitn(3, '\t');
        let added = columns
            .next()
            .and_then(|part| part.parse::<u64>().ok())
            .unwrap_or(0);
        let removed = columns
            .next()
            .and_then(|part| part.parse::<u64>().ok())
            .unwrap_or(0);
        let path = columns.next().ok_or_else(invalid_tree)?;
        insertions = insertions.saturating_add(added);
        deletions = deletions.saturating_add(removed);
        let status = if !previous.contains_key(path) {
            "added"
        } else if !next.contains_key(path) {
            "deleted"
        } else {
            "modified"
        };
        files.push(json!({"path":path,"insertions":added,"deletions":removed,"status":status}));
    }
    let value = json!({"files_changed":files.len(),"files":files,"insertions":insertions,"deletions":deletions,"changed":tree != prior});
    Ok(value.as_object().expect("diff summary object").clone())
}

fn object_files(objects: &Dir) -> Result<Vec<(String, Vec<u8>)>, RuntimeError> {
    let mut result = Vec::new();
    for entry in objects.entries().map_err(storage)? {
        let entry = entry.map_err(storage)?;
        let name = entry.file_name();
        let Some(name) = name.to_str().filter(|name| {
            *name == "pack" || (name.len() == 2 && name.bytes().all(|b| b.is_ascii_hexdigit()))
        }) else {
            continue;
        };
        let folder = files::child(objects, name, false)
            .map_err(storage)?
            .ok_or_else(invalid_tree)?;
        for entry in folder.entries().map_err(storage)? {
            let entry = entry.map_err(storage)?;
            let name2 = entry.file_name();
            let name2 = name2.to_str().ok_or_else(invalid_tree)?;
            let bytes = files::read(&folder, name2)
                .map_err(storage)?
                .ok_or_else(invalid_tree)?;
            result.push((format!("{name}/{name2}"), bytes));
        }
    }
    Ok(result)
}

fn safe_path(root: &Path, data: &Path, path: &str) -> Result<(), RuntimeError> {
    if is_protected(root, data, path) || shallow_write::relative_path(root, data, path).is_err() {
        return Err(RuntimeError::bad_request(
            "Checkpoint contains a protected or invalid workspace path",
        ));
    }
    Ok(())
}

fn is_protected(root: &Path, data: &Path, path: &str) -> bool {
    root.join(path).starts_with(data)
        || path
            .split('/')
            .any(|part| PRIVATE.iter().any(|name| part.eq_ignore_ascii_case(name)))
}

fn nul_rows(bytes: &[u8]) -> Result<impl Iterator<Item = &str>, RuntimeError> {
    let text = std::str::from_utf8(bytes).map_err(|_| invalid_tree())?;
    Ok(text.split_terminator('\0'))
}

fn git_id(bytes: Vec<u8>) -> Result<String, RuntimeError> {
    object_id(bytes).map_err(|_| invalid_tree())
}

fn oid(value: &str) -> Result<&str, RuntimeError> {
    if !matches!(value.len(), 40 | 64) || !value.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err(invalid_tree());
    }
    Ok(value)
}

fn invalid_tree() -> RuntimeError {
    RuntimeError::bad_request("Invalid or unavailable checkpoint Git tree")
}
