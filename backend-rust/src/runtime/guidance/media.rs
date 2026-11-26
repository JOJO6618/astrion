use std::{collections::HashSet, io, path::Path};

use base64::{
    Engine,
    engine::general_purpose::{STANDARD, STANDARD_NO_PAD},
};
use cap_std::fs::Dir;
use ring::digest::{SHA256, digest};
use serde_json::{Value, json};
use time::{OffsetDateTime, format_description::well_known::Rfc3339};

use crate::{
    knowledge::storage::{ensure_dir, open_canonical_dir, publish, read_bytes},
    legacy::media::legacy_mime,
    tools::read_file::ReadFile,
};

use super::{RuntimeError, media_path};

const MAX_MEDIA_BYTES: u64 = 50 * 1024 * 1024;

pub(super) fn path_kind(path: &str) -> Option<&'static str> {
    if let Some((kind, _)) = legacy_mime(path) {
        return Some(kind);
    }
    Some(
        match Path::new(path)
            .extension()?
            .to_str()?
            .to_ascii_lowercase()
            .as_str()
        {
            "svg" | "ico" | "tif" | "tiff" | "heic" | "heif" => "image",
            "avi" | "mpeg" | "mpg" | "mkv" | "m4v" | "ogv" => "video",
            _ => return None,
        },
    )
}

pub(super) struct Writer<'a> {
    data: &'a Path,
    workspace: &'a Path,
    execution_root: &'a Path,
    aliases: Value,
    workspace_files: Option<ReadFile>,
    execution_files: Option<ReadFile>,
    storage: Option<Storage>,
}

impl<'a> Writer<'a> {
    pub(super) fn new(
        data: &'a Path,
        workspace: &'a Path,
        execution_root: &'a Path,
        document: &Value,
    ) -> Self {
        Self {
            data,
            workspace,
            execution_root,
            aliases: document["metadata"]["conversation_transfer"]["paths"].clone(),
            workspace_files: None,
            execution_files: None,
            storage: None,
        }
    }

    pub(super) fn store(
        &mut self,
        user: &mut Value,
        images: &[Value],
        videos: &[Value],
    ) -> Result<(), RuntimeError> {
        let mut refs = Vec::new();
        let mut seen = HashSet::new();
        for (kind, items) in [("image", images), ("video", videos)] {
            for item in items {
                let Some((bytes, mime)) = self.read(kind, item) else {
                    continue;
                };
                let hash: String = digest(&SHA256, &bytes)
                    .as_ref()
                    .iter()
                    .map(|byte| format!("{byte:02x}"))
                    .collect();
                let source = item["source_path"].as_str().or_else(|| {
                    let path = media_path(item);
                    (!path.is_empty()).then_some(path)
                });
                if !seen.insert((hash.clone(), source, kind)) {
                    continue;
                }
                if self.storage.is_none() {
                    self.storage = Some(Storage::open(self.data).map_err(RuntimeError::storage)?);
                }
                let storage = self.storage.as_mut().expect("media storage opened");
                let mut reference = storage
                    .put(&hash, &bytes, &mime, kind, source)
                    .map_err(RuntimeError::storage)?;
                if let Some(fps) = item.get("fps").filter(|value| !value.is_null()) {
                    reference["fps"] = fps.clone();
                }
                refs.push(reference);
            }
        }
        if !refs.is_empty() {
            user["media_refs"] = json!(refs);
            user["metadata"]["media_refs"] = json!(refs);
        }
        Ok(())
    }

    pub(super) fn publish_index(self, conversation: &str, messages: &[Value]) -> io::Result<()> {
        let Some(mut storage) = self.storage else {
            return Ok(());
        };
        for message in messages {
            if let (Some(id), Some(refs)) = (
                message["message_id"].as_str(),
                message["media_refs"].as_array(),
            ) {
                storage.link(conversation, id, refs);
            }
        }
        storage.publish_index()
    }

    fn read(&mut self, kind: &str, item: &Value) -> Option<(Vec<u8>, String)> {
        let mime = item["mime_type"]
            .as_str()
            .or_else(|| item["mimeType"].as_str());
        if kind == "image" {
            if let Some(url) = item["data_url"].as_str() {
                let (mime, payload) = url.strip_prefix("data:")?.split_once(";base64,")?;
                return Some((decode(payload)?, validated_mime(kind, Some(mime))?));
            }
            if let Some(payload) = item["data_base64"]
                .as_str()
                .or_else(|| item["data"].as_str())
            {
                return Some((
                    decode(payload)?,
                    validated_mime(kind, mime.or(Some("image/png")))?,
                ));
            }
        }
        let original = media_path(item);
        let original = if original.is_empty() && kind == "image" {
            item["url"].as_str()?
        } else {
            original
        };
        if original.is_empty() {
            return None;
        }
        let (path, root, files) = match self.aliases[original].as_str() {
            Some(alias)
                if alias.starts_with(".astrion/conversation_assets/")
                    && !alias.contains('\\')
                    && !alias.split('/').any(|part| part == "..") =>
            {
                (alias, self.workspace, &mut self.workspace_files)
            }
            Some(_) => return None,
            None if Path::new(original).starts_with(self.workspace)
                && !Path::new(original).starts_with(self.execution_root) =>
            {
                (original, self.workspace, &mut self.workspace_files)
            }
            None => (original, self.execution_root, &mut self.execution_files),
        };
        let mime = validated_mime(
            kind,
            mime.or_else(|| legacy_mime(path).map(|(_, mime)| mime)),
        )?;
        if files.is_none() {
            *files = ReadFile::open(root, self.data).ok();
        }
        let bytes = files.as_ref()?.read_bytes(path).ok()?;
        (!bytes.is_empty() && bytes.len() as u64 <= MAX_MEDIA_BYTES).then_some((bytes, mime))
    }
}

fn validated_mime(kind: &str, mime: Option<&str>) -> Option<String> {
    let mime = mime?.trim().to_ascii_lowercase();
    let (top, subtype) = mime.split_once('/')?;
    (top == kind
        && !subtype.is_empty()
        && subtype
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'+' | b'.' | b'-')))
    .then_some(mime)
}

fn decode(payload: &str) -> Option<Vec<u8>> {
    let data = STANDARD
        .decode(payload)
        .or_else(|_| STANDARD_NO_PAD.decode(payload))
        .ok()?;
    (!data.is_empty() && data.len() as u64 <= MAX_MEDIA_BYTES).then_some(data)
}

struct Storage {
    root: Dir,
    blobs: Dir,
    index: Value,
    exists: bool,
    timestamp: String,
}

impl Storage {
    fn open(data: &Path) -> io::Result<Self> {
        let data = open_canonical_dir(data)?;
        let conversations = ensure_dir(&data, "conversations")?;
        let root = ensure_dir(&conversations, "media_store")?;
        let blobs = ensure_dir(&root, "blobs")?;
        let (index, exists) = match read_bytes(&root, "index.json", MAX_MEDIA_BYTES) {
            Ok(data) => (serde_json::from_slice::<Value>(&data)?, true),
            Err(error) if error.kind() == io::ErrorKind::NotFound => {
                (json!({"version":1,"media":{},"message_map":{}}), false)
            }
            Err(error) => return Err(error),
        };
        if !index.is_object() || !index["media"].is_object() || !index["message_map"].is_object() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "Invalid media index",
            ));
        }
        let timestamp = OffsetDateTime::now_utc()
            .format(&Rfc3339)
            .map_err(io::Error::other)?;
        Ok(Self {
            root,
            blobs,
            index,
            exists,
            timestamp,
        })
    }

    fn put(
        &mut self,
        hash: &str,
        bytes: &[u8],
        mime: &str,
        kind: &str,
        source: Option<&str>,
    ) -> io::Result<Value> {
        let extension = match mime {
            "image/jpeg" => "jpg",
            "image/svg+xml" => "svg",
            "image/tiff" => "tiff",
            "video/quicktime" => "mov",
            "video/x-msvideo" => "avi",
            mime => mime
                .split('/')
                .nth(1)
                .filter(|ext| ext.bytes().all(|byte| byte.is_ascii_alphanumeric()))
                .unwrap_or("bin"),
        };
        let bucket = &hash[..2];
        let filename = format!("{hash}.{extension}");
        let relative = format!("blobs/{bucket}/{filename}");
        let directory = ensure_dir(&self.blobs, bucket)?;
        if let Err(error) = publish(&directory, &filename, bytes, false) {
            if error.kind() != io::ErrorKind::AlreadyExists
                || read_bytes(&directory, &filename, MAX_MEDIA_BYTES)? != bytes
            {
                return Err(error);
            }
        }
        let media_id = format!("sha256:{hash}");
        let created = self.index["media"][&media_id]["created_at"]
            .as_str()
            .unwrap_or(&self.timestamp);
        self.index["media"][&media_id] = json!({
            "media_id":media_id,"sha256":hash,"kind":kind,"mime_type":mime,"size":bytes.len(),
            "blob_rel_path":relative,"created_at":created,"updated_at":self.timestamp,
        });
        Ok(json!({
            "media_id":media_id,"sha256":hash,"kind":kind,"mime_type":mime,"size":bytes.len(),
            "source_path":source,"store_path":format!("media_store/{relative}"),
        }))
    }

    fn link(&mut self, conversation: &str, message: &str, refs: &[Value]) {
        let media_ids: Vec<_> = refs
            .iter()
            .map(|reference| &reference["media_id"])
            .collect();
        let conversation = &mut self.index["message_map"][conversation];
        if !conversation.is_object() {
            *conversation = json!({});
        }
        conversation[message] = json!({"media_ids":media_ids,"updated_at":self.timestamp});
    }

    fn publish_index(mut self) -> io::Result<()> {
        self.index["updated_at"] = json!(self.timestamp);
        publish(
            &self.root,
            "index.json",
            &serde_json::to_vec_pretty(&self.index)?,
            self.exists,
        )
    }
}
