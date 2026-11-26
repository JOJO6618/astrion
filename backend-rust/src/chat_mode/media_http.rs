use std::{
    io::{Cursor, ErrorKind},
    path::Path,
};

use axum::{
    Json,
    extract::{Multipart, Path as RoutePath, State, multipart::MultipartRejection},
    http::header,
    response::{IntoResponse, Response},
};
use image::{ImageFormat, ImageReader};
use serde_json::{Value, json};

use super::{ChatError, ChatMode, routes::error, store::timestamp, tasks::now};
use crate::{
    knowledge::storage::{ensure_dir, open_canonical_dir, publish, read_bytes},
    legacy::media::BlobStore,
};

pub(super) const MAX_VIDEO: usize = 100 * 1024 * 1024;
const MAX_IMAGE: usize = 25 * 1024 * 1024;

type Kind = (&'static str, &'static str, &'static str);

fn inspect(bytes: &[u8]) -> Result<Kind, ChatError> {
    if bytes.is_empty() || bytes.len() > MAX_VIDEO {
        return Err(ChatError::bad_request("Media is empty or exceeds 100 MB"));
    }
    if let Ok(format) = image::guess_format(bytes) {
        let extension = match format {
            ImageFormat::Jpeg => "jpg",
            ImageFormat::Png => "png",
            ImageFormat::WebP => "webp",
            ImageFormat::Gif => "gif",
            ImageFormat::Bmp => "bmp",
            _ => return Err(ChatError::bad_request("Unsupported image format")),
        };
        if bytes.len() > MAX_IMAGE {
            return Err(ChatError::bad_request("Image exceeds 25 MB"));
        }
        let reader = || ImageReader::with_format(Cursor::new(bytes), format);
        let invalid = |_| ChatError::bad_request("Unsupported image format or dimensions");
        let (width, height) = reader().into_dimensions().map_err(invalid)?;
        if u64::from(width) * u64::from(height) > 100_000_000 {
            return Err(ChatError::bad_request(
                "Unsupported image format or dimensions",
            ));
        }
        reader().decode().map_err(invalid)?;
        return Ok(("image", format.to_mime_type(), extension));
    }
    if bytes.len() >= 16 && &bytes[4..8] == b"ftyp" {
        return match &bytes[8..12] {
            b"avif" | b"avis" | b"heic" | b"heix" | b"mif1" => {
                Err(ChatError::bad_request("Unsupported image format"))
            }
            b"qt  " => Ok(("video", "video/quicktime", "mov")),
            _ => Ok(("video", "video/mp4", "mp4")),
        };
    }
    if bytes.starts_with(b"\x1aE\xdf\xa3")
        && bytes[..bytes.len().min(4096)]
            .windows(4)
            .any(|word| word == b"webm")
    {
        return Ok(("video", "video/webm", "webm"));
    }
    Err(ChatError::bad_request(
        "Only supported images and videos can be sent in Chat",
    ))
}

fn save(root: &Path, bytes: &[u8], (kind, mime, ext): Kind) -> Result<Value, ChatError> {
    let _index = crate::legacy::media::index_lock()?;
    let conversations = open_canonical_dir(&root.join("conversations"))?;
    let store = ensure_dir(&conversations, "media_store")?;
    let blobs = ensure_dir(&store, "blobs")?;
    let sha = ring::digest::digest(&ring::digest::SHA256, bytes)
        .as_ref()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>();
    let bucket = ensure_dir(&blobs, &sha[..2])?;
    let file = format!("{sha}.{ext}");
    let id = format!("sha256:{sha}");
    let (mut index, existed): (Value, _) = match read_bytes(&store, "index.json", MAX_VIDEO as u64)
    {
        Ok(raw) => (serde_json::from_slice(&raw)?, true),
        Err(err) if err.kind() == ErrorKind::NotFound => {
            (json!({"version":1,"media":{},"message_map":{}}), false)
        }
        Err(err) => return Err(err.into()),
    };
    let entries = index
        .get_mut("media")
        .and_then(Value::as_object_mut)
        .ok_or_else(|| ChatError::storage("Invalid Chat media index"))?;
    let reference =
        json!({"media_id":id,"sha256":sha,"kind":kind,"mime_type":mime,"size":bytes.len()});
    let entry = entries
        .entry(id)
        .or_insert_with(|| json!({}))
        .as_object_mut()
        .ok_or_else(|| ChatError::storage("Invalid Chat media entry"))?;
    entry.extend(reference.as_object().unwrap().clone());
    let date = timestamp(now())?;
    entry.entry("created_at").or_insert_with(|| json!(&date));
    entry.insert("updated_at".into(), json!(&date));
    entry.insert(
        "blob_rel_path".into(),
        json!(format!("blobs/{}/{file}", &sha[..2])),
    );
    index["updated_at"] = json!(date);
    // Replacing the digest-named file also repairs a missing or truncated blob on retry.
    publish(&bucket, &file, bytes, true)?;
    publish(&store, "index.json", &serde_json::to_vec(&index)?, existed)?;
    Ok(reference)
}

async fn file_bytes(form: Result<Multipart, MultipartRejection>) -> Result<Vec<u8>, ChatError> {
    let mut form = form.map_err(|_| ChatError::bad_request("Select an image or video"))?;
    while let Some(mut field) = form
        .next_field()
        .await
        .map_err(|e| ChatError::bad_request(e.to_string()))?
    {
        if field.name() != Some("media") || field.file_name().is_none() {
            continue;
        }
        let mut buffer = Vec::new();
        while let Some(chunk) = field
            .chunk()
            .await
            .map_err(|e| ChatError::bad_request(e.to_string()))?
        {
            if chunk.len() > MAX_VIDEO - buffer.len() {
                return Err(ChatError::bad_request("Media exceeds 100 MB"));
            }
            buffer.extend_from_slice(&chunk);
        }
        return Ok(buffer);
    }
    Err(ChatError::bad_request("Select an image or video"))
}

pub(super) async fn upload(
    State(mode): State<ChatMode>,
    form: Result<Multipart, MultipartRejection>,
) -> Response {
    let bytes = match file_bytes(form).await {
        Ok(bytes) => bytes,
        Err(err) => return error(err).into_response(),
    };
    match tokio::task::spawn_blocking(move || {
        let kind = inspect(&bytes)?;
        mode.storage(|paths| {
            let reference = save(paths.root(), &bytes, kind)?;
            super::media_gc::collect_after_write(paths);
            Ok(reference)
        })
    })
    .await
    {
        Ok(Ok(data)) => Json(json!({"success":true,"data":data})).into_response(),
        Ok(Err(err)) => error(err).into_response(),
        Err(err) => error(ChatError::storage(err)).into_response(),
    }
}

pub(super) async fn download(
    State(mode): State<ChatMode>,
    RoutePath(id): RoutePath<String>,
) -> Response {
    let result = tokio::task::spawn_blocking(move || {
        let digest = id.strip_prefix("sha256:")?;
        if digest.len() != 64 || !digest.bytes().all(|byte| byte.is_ascii_hexdigit()) {
            return None;
        }
        let store = BlobStore::open(&mode.root)?;
        let entry = store.entry(&id)?;
        if !matches!(entry["kind"].as_str(), Some("image" | "video")) {
            return None;
        }
        let mime = entry["mime_type"].as_str()?;
        let disposition = format!("inline; filename=\"{id}\"");
        Some(
            (
                [
                    (header::CONTENT_TYPE, mime),
                    (header::CACHE_CONTROL, "private, no-store"),
                    (header::X_CONTENT_TYPE_OPTIONS, "nosniff"),
                    (header::CONTENT_DISPOSITION, &disposition),
                ],
                store.registered_bytes(&id)?,
            )
                .into_response(),
        )
    })
    .await;
    match result {
        Ok(Some(response)) => response,
        Ok(None) => error(ChatError::not_found("Chat media unavailable")).into_response(),
        Err(err) => error(ChatError::storage(err)).into_response(),
    }
}
