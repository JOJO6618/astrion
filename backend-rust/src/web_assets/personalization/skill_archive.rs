//! Validate a workspace skill before archiving it to the private Host library.
use std::{
    fs,
    io::{self, ErrorKind},
    path::Path,
};

use cap_fs_ext::DirExt;
use cap_std::fs::Dir;
use serde_json::{Value, json};
use uuid::Uuid;

use crate::{
    conversation_transfer::assets::{components, subdir},
    knowledge::storage::{ensure_dir, open_canonical_dir, publish, read_bytes},
    tools::read_file::ReadFile,
};

use super::Personalization;

pub(crate) const CREATE_SKILL: &str = "create_skill";
const MAX_BYTES: u64 = 64 * 1024 * 1024;
const MAX_FILES: usize = 4096;
const MAX_DEPTH: usize = 24;

pub(crate) fn create_skill_definition() -> Value {
    json!({"type":"function","function":{"name":CREATE_SKILL,
        "description":"验证技能目录中的 SKILL.md，并归档到个人技能库。目录名以英文字母或数字开头，可包含下划线和连字符。",
        "parameters":{"type":"object","properties":{"source_dir":{"type":"string"},"intent":{"type":"string"}},
            "required":["source_dir"]}}})
}

impl Personalization {
    pub(crate) fn create_workspace_skill(
        &self,
        workspace: &Path,
        root: &Path,
        source: &str,
    ) -> io::Result<Value> {
        let prefs = &self.preferences;
        let mut prepared = prefs
            .updates
            .lock()
            .map_err(|_| io::Error::other("Skill archive is unavailable"))?;
        let data = prefs.catalog.host_data_dir();
        let reader = ReadFile::open(root, data)?;
        let relative = reader
            .checked_relative_path(source)
            .map_err(io::Error::other)?;
        let check = |name: &str| {
            reader
                .checked_relative_path(&relative.join(name).to_string_lossy())
                .map(|_| ())
                .map_err(io::Error::other)
        };
        let names = relative
            .iter()
            .map(|name| {
                name.to_str()
                    .ok_or_else(|| invalid("技能目录必须使用 UTF-8 路径。"))
            })
            .collect::<io::Result<Vec<_>>>()?;
        let (id, parent) = names
            .split_last()
            .ok_or_else(|| invalid("缺少技能目录。"))?;
        if !crate::knowledge::valid_skill_id(id) {
            return Err(invalid("技能目录名不符合规则。"));
        }
        let root = open_canonical_dir(root)?;
        let parent = if parent.is_empty() {
            root
        } else {
            subdir(&root, &parent.join("/"), false)?
        };
        let source = parent.open_dir_nofollow(id)?;
        let markdown = String::from_utf8(read_bytes(&source, "SKILL.md", 1024 * 1024)?)
            .map_err(io::Error::other)?;
        let meta = super::metadata::parse_frontmatter(&markdown);
        if !meta.is_some_and(|fields| {
            ["name", "description"].into_iter().all(|name| {
                fields
                    .get(name)
                    .and_then(Value::as_str)
                    .is_some_and(|text| !text.trim().is_empty())
            })
        }) {
            return Err(invalid("SKILL.md 必须包含 name 和 description。"));
        }
        let source_before = inspect_tree(&source, &check)?;
        fs::create_dir_all(&prefs.private_skills)?;
        let private = open_canonical_dir(&prefs.private_skills)?;
        require_absent(&private, id)?;
        match parent.rename(id, &private, id) {
            Ok(()) => {}
            Err(error) if error.kind() == ErrorKind::CrossesDevices => {
                copy_atomic(&source, &private, id, &check)?;
                // A source modified during a cross-volume copy remains available for recovery.
                let source_now = inspect_tree(&source, &check)?;
                let archived = inspect_tree(&private.open_dir_nofollow(id)?, &check)?;
                if source_now != source_before
                    || archived != source_before
                    || inspect_tree(&parent.open_dir_nofollow(id)?, &check)? != source_before
                {
                    return Ok(json!({"success":false,"skill_name":id,
                        "error":"复制期间技能目录发生变化，已保留原目录和归档副本。"}));
                }
                parent.remove_dir_all(id)?;
            }
            Err(error) => return Err(error),
        }
        // A newly archived library entry must reach other workspaces on their
        // next use, not only the current workspace copy.
        prepared.clear();
        let mut result =
            json!({"success":true,"skill_name":id,"summary":format!("已归档技能 {id}。")});
        let mirrored = (|| -> io::Result<()> {
            if self.workspace_skill_mirror {
                let config = super::Store::open(data)?.read(&prefs.catalog.load())?;
                let catalog = super::skills_catalog(&prefs.global_skills, &prefs.private_skills);
                let (selected, _) = super::skill_selection(&config, &catalog);
                let enabled = selected.into_iter().map(str::to_owned).collect::<Vec<_>>();
                let record = prefs.mirror(workspace, enabled, &catalog, &config)?;
                prepared.insert(workspace.to_owned(), record);
                return Ok(());
            }
            let root = open_canonical_dir(workspace)?;
            let target = ensure_dir(&ensure_dir(&root, ".astrion")?, "skills")?;
            let source = private.open_dir_nofollow(id)?;
            copy_atomic(&source, &target, id, &check)
        })();
        if mirrored.is_err() {
            result["sync_warning"] = json!("技能已归档，当前工作区副本无法刷新。");
        }
        Ok(result)
    }
}

fn invalid(message: &str) -> io::Error {
    io::Error::new(ErrorKind::InvalidInput, message)
}

fn require_absent(parent: &Dir, name: &str) -> io::Result<()> {
    match parent.symlink_metadata(name) {
        Err(error) if error.kind() == ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error),
        Ok(_) => Err(io::Error::new(
            ErrorKind::AlreadyExists,
            "该技能目录已存在。",
        )),
    }
}

pub(super) fn inspect_tree(
    source: &Dir,
    check: &impl Fn(&str) -> io::Result<()>,
) -> io::Result<Vec<(String, String)>> {
    let mut snapshot = Vec::new();
    visit(
        source,
        None,
        "",
        0,
        &mut Budget::default(),
        &mut snapshot,
        check,
    )?;
    snapshot.sort_unstable();
    Ok(snapshot)
}

pub(super) fn copy_atomic(
    source: &Dir,
    target: &Dir,
    id: &str,
    check: &impl Fn(&str) -> io::Result<()>,
) -> io::Result<()> {
    require_absent(target, id)?;
    let staging = format!(".astrion-skill-{}", Uuid::new_v4().simple());
    target.create_dir(&staging)?;
    let result = (|| -> io::Result<()> {
        let folder = target.open_dir_nofollow(&staging)?;
        let mut snapshot = Vec::new();
        visit(
            source,
            Some(&folder),
            "",
            0,
            &mut Budget::default(),
            &mut snapshot,
            check,
        )?;
        require_absent(target, id)?;
        target.rename(&staging, target, id)
    })();
    if result.is_err() {
        let _ = target.remove_dir_all(&staging);
    }
    result
}

#[derive(Default)]
struct Budget {
    count: usize,
    bytes: u64,
}

fn visit(
    source: &Dir,
    destination: Option<&Dir>,
    prefix: &str,
    depth: usize,
    budget: &mut Budget,
    snapshot: &mut Vec<(String, String)>,
    check: &impl Fn(&str) -> io::Result<()>,
) -> io::Result<()> {
    if depth > MAX_DEPTH {
        return Err(invalid("技能目录层级过深。"));
    }
    for entry in source.entries()? {
        let entry = entry?;
        let filename = entry.file_name();
        let name = filename
            .to_str()
            .ok_or_else(|| invalid("技能文件名必须使用 UTF-8。"))?;
        let _ = components(name)?;
        budget.count += 1;
        if budget.count > MAX_FILES {
            return Err(invalid("技能目录文件过多。"));
        }
        let relative = format!("{prefix}{name}");
        check(&relative)?;
        let kind = entry.file_type()?;
        if kind.is_dir() {
            let next = source.open_dir_nofollow(name)?;
            let output = destination
                .map(|parent| ensure_dir(parent, name))
                .transpose()?;
            snapshot.push((relative.clone(), String::new()));
            visit(
                &next,
                output.as_ref(),
                &format!("{relative}/"),
                depth + 1,
                budget,
                snapshot,
                check,
            )?;
        } else if kind.is_file() {
            let bytes = read_bytes(source, name, MAX_BYTES - budget.bytes)?;
            budget.bytes += bytes.len() as u64;
            snapshot.push((relative, crate::conversation_transfer::sha256(&bytes)));
            if let Some(output) = destination {
                publish(output, name, &bytes, false)?;
            }
        } else {
            return Err(invalid("技能目录仅支持普通文件和目录。"));
        }
    }
    Ok(())
}
