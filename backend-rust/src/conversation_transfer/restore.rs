use std::{
    collections::BTreeMap,
    io::{self, ErrorKind},
    path::Path,
};

use cap_fs_ext::DirExt;
use cap_std::fs::Dir;
use serde_json::{Map, Value, json};

use crate::{
    knowledge::storage::{open_canonical_dir, publish, read_bytes},
    runtime::RuntimeError,
};

use super::{
    MAX_BYTES,
    archive::{Manifest, Payloads, Resource},
    assets::{self, MEDIA_INDEX, NAMESPACE},
    bad, conflict, io_error, media_kind, sha256, values,
};

const OWNER: &str = ".transfer-owner";

pub(crate) struct Restore {
    pub paths: Value,
    /// Remove only a namespace created by this attempt if publication fails.
    created: Option<(Dir, String)>,
}

impl Restore {
    pub(crate) fn prepare(
        data: &Path,
        project: Option<&Path>,
        id: &str,
        key: &str,
        raw: &Value,
        manifest: &Manifest,
        files: &Payloads,
        date: &str,
    ) -> Result<Self, RuntimeError> {
        let mut plan = BTreeMap::<String, &Resource>::new();
        let mut aliases = Map::new();
        let prefix = format!("{NAMESPACE}/{id}");
        for row in &manifest.resources {
            if row.paths.is_empty() {
                continue;
            }
            let logical = (row.kind == "material")
                .then(|| row.paths.iter().find_map(|path| assets::logical_path(path)))
                .flatten();
            let tail = if let Some(logical) = logical {
                let _ = assets::components(logical).map_err(io_error)?;
                format!("materials/{logical}")
            } else {
                format!("{}{}", row.sha256, extension(row))
            };
            if plan
                .insert(tail.clone(), row)
                .is_some_and(|previous| previous.sha256 != row.sha256)
            {
                return Err(conflict("Conflicting conversation resources"));
            }
            let target = format!("{prefix}/{tail}");
            for old in &row.paths {
                if row.media_id.is_some() {
                    // Historical canonical media is selected by content ID.
                    // An original filename can also have been reused later.
                    aliases.insert(old.clone(), json!(target));
                } else {
                    alias(&mut aliases, old, &target)?;
                }
                if let Some(logical) =
                    logical.filter(|logical| logical.starts_with(".astrion/handoff/"))
                    && old.ends_with(logical)
                {
                    let mut old = old.as_str();
                    let mut target = target.as_str();
                    while let (Some((from, _)), Some((to, _))) =
                        (old.rsplit_once('/'), target.rsplit_once('/'))
                    {
                        if !from.contains(".astrion/handoff/") {
                            break;
                        }
                        alias(&mut aliases, from, to)?;
                        old = from;
                        target = to;
                    }
                }
            }
        }
        let mut result = Self {
            paths: Value::Object(aliases),
            created: None,
        };
        if !plan.is_empty() {
            let root = project.unwrap_or(data).canonicalize().map_err(io_error)?;
            let root = open_canonical_dir(&root).map_err(io_error)?;
            let parent = assets::subdir(&root, NAMESPACE, true).map_err(io_error)?;
            let created = match parent.create_dir(id) {
                Ok(()) => true,
                Err(error) if error.kind() == ErrorKind::AlreadyExists => false,
                Err(error) => return Err(io_error(error)),
            };
            if created {
                result.created = Some((parent.try_clone().map_err(io_error)?, id.into()));
            }
            let target = parent.open_dir_nofollow(id).map_err(io_error)?;
            let owner = sha256(key.as_bytes());
            if created {
                publish(&target, OWNER, owner.as_bytes(), false).map_err(io_error)?;
            } else if read_bytes(&target, OWNER, 64).ok().as_deref() != Some(owner.as_bytes()) {
                return Err(conflict("The conversation asset folder already exists"));
            }
            for (path, row) in plan {
                let bytes = payload(row, files)?;
                let (parent, name) = assets::parent(&target, &path, true).map_err(io_error)?;
                put_checked(&parent, name, bytes)?;
            }
        }
        save_media(data, raw, id, manifest, files, date)?;
        Ok(result)
    }

    pub(crate) fn commit(mut self) {
        self.created = None;
    }
}

impl Drop for Restore {
    fn drop(&mut self) {
        if let Some((parent, id)) = self.created.take() {
            let _ = parent.remove_dir_all(id);
        }
    }
}

fn alias(aliases: &mut Map<String, Value>, key: &str, path: &str) -> Result<(), RuntimeError> {
    if aliases.get(key).is_some_and(|previous| previous != path) {
        return Err(conflict("Conflicting conversation resource aliases"));
    }
    aliases.insert(key.to_owned(), json!(path));
    Ok(())
}

fn payload<'a>(row: &Resource, files: &'a Payloads) -> Result<&'a [u8], RuntimeError> {
    files
        .get(&row.file)
        .filter(|bytes| bytes.len() as u64 == row.size)
        .map(Vec::as_slice)
        .ok_or_else(|| bad("Conversation resource is unavailable"))
}

fn extension(row: &Resource) -> String {
    row.paths
        .first()
        .and_then(|path| Path::new(path).extension())
        .and_then(|part| part.to_str())
        .filter(|part| {
            (1..=12).contains(&part.len()) && part.bytes().all(|b| b.is_ascii_alphanumeric())
        })
        .or_else(|| {
            row.mime_type
                .as_deref()
                .and_then(mime_guess::get_mime_extensions_str)
                .and_then(|items| items.first().copied())
        })
        .map_or_else(|| ".bin".into(), |value| format!(".{value}"))
}

fn put_checked(parent: &Dir, name: &str, bytes: &[u8]) -> Result<(), RuntimeError> {
    match publish(parent, name, bytes, false) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == ErrorKind::AlreadyExists => {
            if read_bytes(parent, name, MAX_BYTES).map_err(io_error)? != bytes {
                return Err(conflict(
                    "The conversation resource already exists with different data",
                ));
            }
            Ok(())
        }
        Err(error) => Err(io_error(error)),
    }
}

fn save_media(
    data: &Path,
    raw: &Value,
    id: &str,
    manifest: &Manifest,
    files: &Payloads,
    date: &str,
) -> Result<(), RuntimeError> {
    let _index = crate::legacy::media::index_lock().map_err(RuntimeError::storage)?;
    let mut links = Map::new();
    for message in raw["messages"]
        .as_array()
        .ok_or_else(|| bad("Invalid conversation messages"))?
    {
        let Some(message_id) = message["message_id"].as_str() else {
            continue;
        };
        let media: Vec<_> = values(message, "media_refs")
            .iter()
            .filter_map(|reference| reference["media_id"].as_str())
            .collect();
        if !media.is_empty() {
            links.insert(
                message_id.into(),
                json!({"media_ids":media,"updated_at":date}),
            );
        }
    }
    if links.is_empty() && !manifest.resources.iter().any(|row| media_kind(&row.kind)) {
        return Ok(());
    }
    let data = data.canonicalize().map_err(io_error)?;
    let data = open_canonical_dir(&data).map_err(io_error)?;
    let media = assets::subdir(&data, MEDIA_INDEX, true).map_err(io_error)?;
    let current = assets::read_media_index(&data).map_err(io_error)?;
    let exists = current.is_some();
    let mut index = current.unwrap_or_else(|| json!({"version":1,"media":{},"message_map":{}}));
    for row in manifest
        .resources
        .iter()
        .filter(|row| media_kind(&row.kind))
    {
        let media_id = format!("sha256:{}", row.sha256);
        let existing = &index["media"][&media_id];
        let path = if existing.is_null() {
            format!(
                "blobs/{}/{}{}",
                &row.sha256[..2],
                row.sha256,
                extension(row)
            )
        } else {
            assets::blob_path(existing, &row.sha256)
                .ok_or_else(|| bad("Invalid media store record"))?
                .to_owned()
        };
        let (directory, filename) = assets::parent(&media, &path, true).map_err(io_error)?;
        put_checked(&directory, filename, payload(row, files)?)?;
        if existing.is_null() {
            index["media"][media_id] = json!({
                "media_id":media_id,"sha256":row.sha256,"kind":row.kind,"mime_type":row.mime_type,
                "size":row.size,"created_at":date,"updated_at":date,"blob_rel_path":path,
            });
        }
    }
    if !links.is_empty() {
        index["message_map"][id] = Value::Object(links);
    }
    index["updated_at"] = json!(date);
    publish(
        &media,
        "index.json",
        &serde_json::to_vec(&index)
            .map_err(io::Error::from)
            .map_err(io_error)?,
        exists,
    )
    .map_err(io_error)
}
