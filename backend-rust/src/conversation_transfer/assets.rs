use std::{collections::{BTreeSet, HashMap, HashSet}, io::{self, ErrorKind}, path::{Component, Path}};

use cap_fs_ext::{DirExt, FollowSymlinks, OpenOptionsFollowExt};
use cap_std::fs::{Dir, OpenOptions};
use serde_json::Value;

use crate::{knowledge::storage::{ensure_dir, open_canonical_dir, read_bytes}, runtime::RuntimeError, workspace_catalog::RegisteredWorkspace};

use super::{LEGACY_MEDIA, MAX_ASSETS, MAX_BYTES, archive::{Payloads, Resource, validate_resource}, bad, document::text, io_error, is_digest, local_media, media_kind, sha256, values};

pub(crate) const NAMESPACE: &str = ".astrion/conversation_assets";
pub(crate) const MEDIA_INDEX: &str = "conversations/media_store";

pub(crate) fn components(relative: &str) -> io::Result<impl Iterator<Item = &str>> {
    if relative.is_empty() || relative.contains(['\\', ':', '\0']) || relative.split('/').any(|part| {
        part.is_empty() || part == "." || part == ".." || part.ends_with(['.', ' '])
            || part.contains(['<', '>', '"', '|', '?', '*'])
            || reserved_windows_name(part)
    }) || !Path::new(relative).components().all(|part| matches!(part, Component::Normal(_))) {
        return Err(io::Error::new(ErrorKind::InvalidInput, "Invalid conversation resource path"));
    }
    Ok(relative.split('/'))
}

fn reserved_windows_name(part: &str) -> bool {
    let name = part.split('.').next().unwrap_or_default().to_ascii_uppercase();
    matches!(name.as_str(), "CON" | "PRN" | "AUX" | "NUL" | "CONIN$" | "CONOUT$")
        || ["COM", "LPT"].iter().any(|prefix| name.strip_prefix(prefix)
            .is_some_and(|suffix| matches!(suffix, "1" | "2" | "3" | "4" | "5" | "6" | "7" | "8" | "9" | "¹" | "²" | "³")))
}

pub(crate) fn subdir(root: &Dir, relative: &str, create: bool) -> io::Result<Dir> {
    let mut current = root.try_clone()?;
    for name in components(relative)? {
        current = if create { ensure_dir(&current, name)? } else { current.open_dir_nofollow(name)? };
    }
    Ok(current)
}

pub(crate) fn parent<'a>(root: &Dir, relative: &'a str, create: bool) -> io::Result<(Dir, &'a str)> {
    if let Some((directory, basename)) = relative.rsplit_once('/') {
        let _ = components(basename)?;
        Ok((subdir(root, directory, create)?, basename))
    } else {
        let _ = components(relative)?;
        Ok((root.try_clone()?, relative))
    }
}

pub(crate) fn read(root: &Dir, relative: &str, limit: u64) -> io::Result<Vec<u8>> {
    let (parent, name) = parent(root, relative, false)?;
    read_bytes(&parent, name, limit)
}

pub(crate) fn open_file(root: &Dir, relative: &str) -> io::Result<std::fs::File> {
    use cap_fs_ext::MetadataExt;
    let (parent, name) = parent(root, relative, false)?;
    let mut options = OpenOptions::new();
    options.read(true).follow(FollowSymlinks::No);
    #[cfg(unix)]
    {
        use cap_std::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NONBLOCK);
    }
    let file = parent.open_with(name, &options)?;
    let metadata = file.metadata()?;
    if !metadata.is_file() || metadata.nlink() != 1 {
        return Err(io::Error::new(ErrorKind::InvalidInput, "Expected a regular conversation resource"));
    }
    Ok(file.into_std())
}

pub(crate) fn read_media_index(root: &Dir) -> io::Result<Option<Value>> {
    let bytes = match read(root, &format!("{MEDIA_INDEX}/index.json"), MAX_BYTES) {
        Ok(bytes) => bytes,
        Err(error) if error.kind() == ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error),
    };
    let index: Value = serde_json::from_slice(&bytes)?;
    if !index["media"].is_object() || !index["message_map"].is_object() {
        return Err(io::Error::new(ErrorKind::InvalidData, "Invalid media store index"));
    }
    Ok(Some(index))
}

pub(crate) fn blob_path<'a>(entry: &'a Value, digest: &str) -> Option<&'a str> {
    let path = entry["blob_rel_path"].as_str()?;
    let path = path.strip_prefix("media_store/").unwrap_or(path);
    let (bucket, filename) = path.strip_prefix("blobs/")?.split_once('/')?;
    let (hash, extension) = filename.split_once('.')?;
    (hash == digest && digest.get(..2) == Some(bucket) && !extension.is_empty()
        && extension.bytes().all(|byte| byte.is_ascii_alphanumeric())).then_some(path)
}

#[derive(Default)]
pub(crate) struct Assets {
    pub resources: Vec<Resource>,
    pub files: Payloads,
    seen: HashSet<Resource>,
    size: u64,
}

impl Assets {
    fn add(&mut self, data: Vec<u8>, kind: &str, mime: Option<String>, media_id: Option<&str>, paths: Vec<String>) -> Result<(), RuntimeError> {
        let hash = sha256(&data);
        let file = format!("assets/{hash}");
        let row = Resource { kind: kind.into(), size: data.len() as u64, sha256: hash,
            file: file.clone(), mime_type: mime, media_id: media_id.map(str::to_owned), paths };
        validate_resource(&row)?;
        if !self.files.contains_key(&file) {
            self.size += row.size;
            if self.size > MAX_BYTES || self.files.len() >= MAX_ASSETS { return Err(limit()); }
            self.files.insert(file, data);
        }
        if self.seen.insert(row.clone()) {
            if self.resources.len() >= MAX_ASSETS { return Err(limit()); }
            self.resources.push(row);
        }
        Ok(())
    }

    pub(crate) fn collect(
        raw: &Value, data: &Path, project: Option<&Path>, shared_media: bool,
        workspace: &dyn Fn(&str) -> Result<RegisteredWorkspace, RuntimeError>,
    ) -> Result<Self, RuntimeError> {
        let data_dir = open_canonical_dir(data).map_err(io_error)?;
        let root = project.unwrap_or(data);
        let project_dir = open_canonical_dir(root).map_err(io_error)?;
        let aliases = raw["metadata"]["conversation_transfer"]["paths"].as_object().cloned().unwrap_or_default();
        let mut collected = Self::default();
        let mut index = None;
        let mut cached_media = HashMap::<String, String>::new();
        let mut legacy_seen = HashSet::new();
        for message in raw["messages"].as_array().expect("validated messages") {
            let mut covered = HashSet::new();
            for reference in values(message, "media_refs") {
                let Some(kind) = text(reference, "kind").filter(|kind| media_kind(kind)) else { continue; };
                let id = text(reference, "media_id").ok_or_else(invalid_media)?;
                let digest = id.strip_prefix("sha256:").filter(|digest| is_digest(digest)).ok_or_else(invalid_media)?;
                let paths: Vec<String> = ["source_path", "path"].into_iter()
                    .filter_map(|key| text(reference, key)).map(str::to_owned).collect();
                covered.extend(paths.iter().map(|path| (kind.to_owned(), path.clone())));
                if index.is_none() { index = Some(read_media_index(&data_dir).map_err(io_error)?.unwrap_or_default()); }
                let entry = &index.as_ref().expect("media index")["media"][id];
                let path = blob_path(entry, digest).ok_or_else(|| missing_media(id))?;
                let filename = format!("{MEDIA_INDEX}/{path}");
                if shared_media {
                    open_file(&data_dir, &filename).map_err(|_| missing_media(id))?;
                    continue;
                }
                let mime = text(reference, "mime_type").or_else(|| text(entry, "mime_type"))
                    .map(str::to_owned).unwrap_or_else(|| format!("{kind}/*"));
                let row = if let Some(file) = cached_media.get(id) {
                    Resource { kind: kind.into(), sha256: digest.into(), file: file.clone(),
                        size: collected.files[file].len() as u64, mime_type: Some(mime),
                        media_id: Some(id.into()), paths }
                } else {
                    let data = read(&data_dir, &filename, MAX_BYTES).map_err(io_error)?;
                    if sha256(&data) != digest { return Err(bad("Conversation resource checksum failed")); }
                    collected.add(data, kind, Some(mime), Some(id), paths)?;
                    cached_media.insert(id.into(), format!("assets/{digest}"));
                    continue;
                };
                validate_resource(&row)?;
                if collected.seen.insert(row.clone()) {
                    if collected.resources.len() >= MAX_ASSETS { return Err(limit()); }
                    collected.resources.push(row);
                }
            }
            for (field, kind) in LEGACY_MEDIA {
                for old in values(message, field).iter().filter_map(local_media) {
                    let key = (kind.to_owned(), old.to_owned());
                    if covered.contains(&key) || !legacy_seen.insert(key) { continue; }
                    if collected.resources.iter().any(|row| row.kind == kind && row.paths.iter().any(|path| path == old)) { continue; }
                    let actual = if let Some(mapping) = aliases.get(old).and_then(Value::as_str) {
                        mapped(mapping)?
                    } else {
                        relative_path(root, old)?
                    };
                    let data = read(&project_dir, &actual, MAX_BYTES).map_err(io_error)?;
                    let mime = mime_guess::from_path(old).first_raw().filter(|mime| mime.split('/').next() == Some(kind))
                        .map(str::to_owned).unwrap_or_else(|| format!("{kind}/*"));
                    collected.add(data, kind, Some(mime), None, vec![old.into()])?;
                }
            }
        }
        let mut materials = BTreeSet::new();
        scan(&raw["messages"], &mut materials)?;
        scan(&raw["metadata"]["deep_compression_records"], &mut materials)?;
        for (old, actual) in &aliases {
            if logical_path(old).is_some() && actual.as_str().is_some_and(|path| path.contains("/materials/")) {
                materials.insert(old.clone());
            }
        }
        let mut collected_dirs = HashSet::new();
        for logical in materials {
            let Some(relative) = logical_path(&logical) else { continue; };
            let mapping = aliases.get(&logical).or_else(|| aliases.get(relative)).and_then(Value::as_str);
            if let Some(mapping) = mapping {
                let actual = mapped(mapping)?;
                // The alias table also records handoff directories. Their file aliases are collected separately.
                match read(&project_dir, &actual, MAX_BYTES) {
                    Ok(data) => {
                        let mut old: Vec<_> = aliases.iter().filter(|(_, value)| **value == mapping)
                            .map(|(key, _)| key.clone()).collect();
                        if old.is_empty() { old.push(logical); }
                        collected.add(data, "material", None, None, old)?;
                    }
                    Err(error) if error.kind() == ErrorKind::PermissionDenied && subdir(&project_dir, &actual, false).is_ok() => {}
                    Err(error) => return Err(io_error(error)),
                }
            } else if let Some(directory) = handoff_dir(relative) {
                if project.is_some() && collected_dirs.insert(directory.to_owned()) {
                    collected.directory(&project_dir, root, directory)?;
                }
            } else if relative.starts_with(".astrion/compact_result/") {
                match read(&project_dir, relative, MAX_BYTES) {
                    Ok(data) => collected.add(data, "material", None, None, vec![relative.into(), root.join(relative).to_string_lossy().into_owned()])?,
                    Err(error) if error.kind() == ErrorKind::NotFound => {},
                    Err(error) => return Err(io_error(error)),
                }
            }
        }
        if project.is_none() {
            for handoff in raw["metadata"]["chat"]["handoffs"].as_object().into_iter().flat_map(|rows| rows.values()) {
                let Some(relative) = text(handoff, "materials") else { continue; };
                if !valid_handoff_root(relative) { return Err(bad("Invalid Chat handoff material directory")); }
                if aliases.get(relative).and_then(Value::as_str).is_some() { continue; }
                let workspace_id = text(handoff, "workspace_id").ok_or_else(|| bad("A Chat handoff workspace is unavailable"))?;
                if collected_dirs.insert(format!("{workspace_id}:{relative}")) {
                    let source = workspace(workspace_id)?;
                    let directory = open_canonical_dir(&source.path).map_err(io_error)?;
                    collected.directory(&directory, &source.path, relative)?;
                }
            }
        }
        verify_coverage(raw, &collected.resources, shared_media)?;
        Ok(collected)
    }

    fn directory(&mut self, root: &Dir, project: &Path, relative: &str) -> Result<(), RuntimeError> {
        let directory = subdir(root, relative, false).map_err(io_error)?;
        self.walk(&directory, project, relative, 0, &mut 0)
    }

    fn walk(&mut self, directory: &Dir, project: &Path, relative: &str, depth: usize, scanned: &mut usize) -> Result<(), RuntimeError> {
        if depth > 64 { return Err(limit()); }
        let mut entries = Vec::new();
        for entry in directory.entries().map_err(io_error)? {
            *scanned += 1;
            if *scanned > MAX_ASSETS { return Err(limit()); }
            let entry = entry.map_err(io_error)?;
            let name = entry.file_name().into_string().map_err(|_| bad("Invalid resource filename"))?;
            let _ = components(&name).map_err(io_error)?;
            let kind = entry.file_type().map_err(io_error)?;
            entries.push((name, kind));
        }
        entries.sort_unstable_by(|a, b| a.0.cmp(&b.0));
        for (name, kind) in entries {
            let child = format!("{relative}/{name}");
            if kind.is_dir() {
                self.walk(&directory.open_dir_nofollow(&name).map_err(io_error)?, project, &child, depth + 1, scanned)?;
            } else if kind.is_file() {
                let data = read_bytes(directory, &name, MAX_BYTES).map_err(io_error)?;
                self.add(data, "material", None, None, vec![child.clone(), project.join(child).to_string_lossy().into_owned()])?;
            } else {
                return Err(bad("Conversation resources cannot use symbolic links or special files"));
            }
        }
        Ok(())
    }
}

fn relative_path(root: &Path, text: &str) -> Result<String, RuntimeError> {
    let path = Path::new(text);
    let relative = if path.is_absolute() {
        path.strip_prefix(root).map_err(|_| RuntimeError { status: 404, message: format!("Image/video must be available in its media store: {text}") })?
    } else { path };
    let mut names = Vec::new();
    for component in relative.components() {
        match component {
            Component::Normal(name) => names.push(name.to_str().ok_or_else(|| bad("Invalid resource filename"))?),
            Component::CurDir => (),
            _ => return Err(bad("Invalid conversation resource path")),
        }
    }
    let result = names.join("/");
    let _ = components(&result).map_err(io_error)?;
    Ok(result)
}

fn mapped(path: &str) -> Result<String, RuntimeError> {
    if !path.starts_with(&format!("{NAMESPACE}/")) { return Err(bad("Invalid migrated resource mapping")); }
    let _ = components(path).map_err(io_error)?;
    Ok(path.into())
}

pub(crate) fn logical_path(text: &str) -> Option<&str> {
    for (start, _) in text.match_indices(".astrion/") {
        let piece = &text[start..];
        if ![".astrion/compact_result/", ".astrion/handoff/"].iter().any(|prefix| piece.starts_with(prefix)) { continue; }
        let stop = piece.find(|c: char| c.is_whitespace() || "`<>\"'[])(（），。；;".contains(c)).unwrap_or(piece.len());
        let path = piece[..stop].trim_end_matches(['.', ',', ':']);
        if path.split('/').nth(2).is_some_and(|name| !name.is_empty()) { return Some(path); }
    }
    None
}

fn handoff_dir(relative: &str) -> Option<&str> {
    let rest = relative.strip_prefix(".astrion/handoff/")?;
    let length = relative.len() - rest.len() + rest.find('/').unwrap_or(rest.len());
    Some(&relative[..length])
}

fn valid_handoff_root(path: &str) -> bool {
    path.strip_prefix(".astrion/handoff/").is_some_and(|name| !name.is_empty()
        && name.bytes().all(|byte| byte.is_ascii_alphanumeric() || b"_-".contains(&byte)))
}

fn scan(value: &Value, result: &mut BTreeSet<String>) -> Result<(), RuntimeError> {
    match value {
        Value::Array(values) => for value in values { scan(value, result)?; },
        Value::Object(values) => for value in values.values() { scan(value, result)?; },
        Value::String(value) => {
            let mut rest = value.as_str();
            while let Some(path) = logical_path(rest) {
                result.insert(path.into());
                if result.len() > MAX_ASSETS { return Err(limit()); }
                // Both slices have the same provenance, with `path` inside `rest`.
                let after = path.as_ptr() as usize - rest.as_ptr() as usize + path.len();
                rest = &rest[after..];
            }
        }
        _ => (),
    }
    Ok(())
}

pub(crate) fn verify_coverage(raw: &Value, resources: &[Resource], shared_media: bool) -> Result<(), RuntimeError> {
    let mut ids = HashSet::new();
    let mut paths = HashSet::new();
    let mut handoff_dirs = HashSet::new();
    let mut material_paths = HashSet::new();
    for row in resources {
        if media_kind(&row.kind) {
            if let Some(id) = &row.media_id { ids.insert((row.kind.as_str(), id.as_str())); }
            paths.extend(row.paths.iter().map(|path| (row.kind.as_str(), path.as_str())));
        } else {
            for path in &row.paths {
                if let Some(relative) = logical_path(path) {
                    let _ = components(relative).map_err(io_error)?;
                    material_paths.insert(relative);
                    if let Some(directory) = handoff_dir(relative).filter(|directory| *directory != relative) {
                        handoff_dirs.insert(directory);
                    }
                }
            }
        }
    }
    let mut required_materials = BTreeSet::new();
    scan(&raw["messages"], &mut required_materials)?;
    scan(&raw["metadata"]["deep_compression_records"], &mut required_materials)?;
    if required_materials.iter().any(|path| !material_paths.contains(path.as_str())
        && !handoff_dirs.contains(path.as_str()))
    {
        return Err(bad("A referenced summary or handoff material is missing from the archive"));
    }
    for message in raw["messages"].as_array().expect("validated messages") {
        for reference in values(message, "media_refs") {
            if let Some(kind) = text(reference, "kind").filter(|kind| media_kind(kind))
            {
                if shared_media {
                    for path in ["source_path", "path"].into_iter().filter_map(|key| text(reference, key)) {
                        paths.insert((kind, path));
                    }
                } else if !text(reference, "media_id").is_some_and(|id| ids.contains(&(kind, id))) {
                    return Err(bad("A referenced image/video is missing from the archive"));
                }
            }
        }
        for (field, kind) in LEGACY_MEDIA {
            if values(message, field).iter().filter_map(local_media).any(|path| !paths.contains(&(kind, path))) {
                return Err(bad("A legacy image/video path is missing from the archive"));
            }
        }
    }
    for handoff in raw["metadata"]["chat"]["handoffs"].as_object().into_iter().flat_map(|items| items.values()) {
        if let Some(relative) = text(handoff, "materials")
            && (!valid_handoff_root(relative) || !handoff_dirs.contains(relative))
        { return Err(bad("Chat handoff materials are missing from the archive")); }
    }
    Ok(())
}

fn limit() -> RuntimeError { bad("Conversation resources exceed the export limit") }
fn invalid_media() -> RuntimeError { bad("Invalid image/video identity") }
fn missing_media(id: &str) -> RuntimeError { RuntimeError { status: 404, message: format!("Referenced image/video is missing: {id}") } }
