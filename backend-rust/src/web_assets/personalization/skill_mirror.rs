//! Prepare the account library in each registered workspace without replacing local skills.
use std::{
    collections::{BTreeMap, BTreeSet},
    io::{self, ErrorKind},
    path::Path,
};

use cap_fs_ext::DirExt;
use cap_std::fs::Dir;
use serde_json::Value;
use uuid::Uuid;

use crate::{
    conversation_transfer::sha256,
    knowledge::storage::{ensure_dir, open_canonical_dir, publish, read_bytes},
};

use super::skill_archive::{copy_atomic, inspect_tree};

type Manifest = BTreeMap<String, String>;

pub(super) fn sync(
    workspace: &Path, data: &Path, global: &Path, private: &Path,
    selected: &[String], catalog: &[Value],
) -> io::Result<()> {
    let destination = workspace.join(".astrion").join("skills");
    if [global, private, data].iter().any(|root| {
        destination.starts_with(root) || root.starts_with(&destination)
    }) {
        return Err(io::Error::new(ErrorKind::PermissionDenied, "Skill mirror overlaps a Host data or library directory"));
    }
    let root = open_canonical_dir(workspace)?;
    let state = open_canonical_dir(data)?;
    if catalog.is_empty() && state.symlink_metadata("workspace_skill_mirrors").is_err() {
        return Ok(());
    }
    let state = ensure_dir(&state, "workspace_skill_mirrors")?;
    let name = format!("{}.json", sha256(workspace.as_os_str().as_encoded_bytes()));
    let (previous, exists): (Manifest, bool) = match read_bytes(&state, &name, 1024 * 1024) {
        Ok(bytes) => (serde_json::from_slice(&bytes)?, true),
        Err(error) if error.kind() == ErrorKind::NotFound => (Manifest::new(), false),
        Err(error) => return Err(error),
    };
    if catalog.is_empty() && previous.is_empty() {
        return Ok(());
    }
    let target = ensure_dir(&ensure_dir(&root, ".astrion")?, "skills")?;
    let chosen: BTreeSet<&str> = selected.iter().map(String::as_str).collect();
    let mut ids = BTreeMap::new();
    for item in catalog {
        let Some(id) = item["id"].as_str().filter(|id| crate::knowledge::valid_skill_id(id)) else {
            continue;
        };
        ids.insert(id, Some(if item["source"] == "private" { private } else { global }));
    }
    for id in previous.keys().filter(|id| crate::knowledge::valid_skill_id(id)) {
        ids.entry(id).or_insert(None);
    }

    // Store ownership immediately after each atomic change. If writing the
    // journal fails, an untracked copy cannot be overwritten on the next run.
    let mut now = previous.clone();
    let mut first_error = None;
    for (id, library) in ids {
        let prior = previous.get(id);
        let result = (|| {
            let source = library
                .filter(|_| chosen.contains(id))
                .map(|path| open_canonical_dir(path)?.open_dir_nofollow(id))
                .transpose()?;
            update(&target, id, source.as_ref(), prior)
        })();
        match result {
            Ok(Some(hash)) => { now.insert(id.to_owned(), hash); }
            Ok(None) => { now.remove(id); }
            Err(error) => { first_error.get_or_insert(error); continue; }
        }
        if now.get(id) != prior {
            let state_exists = exists || state.symlink_metadata(&name).is_ok();
            publish(&state, &name, &serde_json::to_vec(&now)?, state_exists)?;
        }
    }
    first_error.map_or(Ok(()), Err)
}

fn fingerprint(dir: &Dir) -> io::Result<String> {
    Ok(sha256(&serde_json::to_vec(&inspect_tree(dir, &|_| Ok(()))?)?))
}

fn update(
    target: &Dir, id: &str, source: Option<&Dir>, prior: Option<&String>,
) -> io::Result<Option<String>> {
    let installed = match target.open_dir_nofollow(id) {
        Ok(dir) => Some(dir),
        Err(error) if error.kind() == ErrorKind::NotFound => None,
        // Local non-directory entries and links are not managed.
        Err(error) => match target.symlink_metadata(id) {
            Ok(meta) if !meta.is_dir() => return Ok(None),
            _ => return Err(error),
        },
    };
    if installed.is_some() && prior.is_none() {
        return Ok(None);
    }
    let desired = source.map(fingerprint).transpose()?;
    if let Some(installed) = installed {
        // A local directory or a mirror with local additions/edits/links is
        // never overwritten or removed.
        let current = match fingerprint(&installed) {
            Ok(current) => current,
            Err(_) => return Ok(None),
        };
        if Some(&current) != prior {
            return Ok(None);
        }
        if Some(&current) == desired.as_ref() {
            return Ok(Some(current));
        }
        let backup = format!(".astrion-skill-old-{}", Uuid::new_v4().simple());
        target.rename(id, target, &backup)?;
        let verify = target.open_dir_nofollow(&backup).and_then(|dir| fingerprint(&dir));
        if !matches!(verify, Ok(ref hash) if hash == &current) {
            restore(target, &backup, id)?;
            return Ok(None);
        }
        if let Some(source) = source
            && let Err(error) = copy_atomic(source, target, id, &|_| Ok(()))
        {
            restore(target, &backup, id)?;
            return Err(error);
        }
        target.remove_dir_all(backup)?;
    } else if let Some(source) = source {
        copy_atomic(source, target, id, &|_| Ok(()))?;
    }
    Ok(desired)
}

fn restore(target: &Dir, backup: &str, id: &str) -> io::Result<()> {
    match target.symlink_metadata(id) {
        Err(error) if error.kind() == ErrorKind::NotFound => target.rename(backup, target, id),
        // Do not touch a new local directory. Leave the backup for recovery.
        Ok(_) => Ok(()),
        Err(error) => Err(error),
    }
}
