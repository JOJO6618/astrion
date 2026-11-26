//! Explicit conversation transfer. The normal legacy readers never invoke this module.

pub(crate) mod archive;
pub(crate) mod assets;
pub(crate) mod document;
pub(crate) mod restore;

use std::io;

use ring::digest::{SHA256, digest};
use serde_json::Value;

use crate::runtime::RuntimeError;

pub(crate) const MAX_ASSETS: usize = 4096;
pub(crate) const MAX_BYTES: u64 = 256 * 1024 * 1024;
pub(crate) const MAX_DOCUMENT: u64 = 32 * 1024 * 1024;
pub(crate) const MAX_UPLOAD: usize = (MAX_BYTES + MAX_DOCUMENT) as usize;

pub(crate) fn sha256(bytes: &[u8]) -> String {
    digest(&SHA256, bytes).as_ref().iter().map(|byte| format!("{byte:02x}")).collect()
}

pub(crate) fn is_digest(value: &str) -> bool {
    value.len() == 64 && value.bytes().all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

pub(crate) fn bad(message: impl Into<String>) -> RuntimeError {
    RuntimeError { status: 400, message: message.into() }
}

pub(crate) fn conflict(message: impl Into<String>) -> RuntimeError {
    RuntimeError { status: 409, message: message.into() }
}

pub(crate) fn io_error(error: io::Error) -> RuntimeError {
    let status = match error.kind() {
        io::ErrorKind::NotFound => 404,
        io::ErrorKind::InvalidInput | io::ErrorKind::InvalidData | io::ErrorKind::PermissionDenied => 400,
        _ => 500,
    };
    RuntimeError { status, message: error.to_string() }
}

pub(crate) fn encode(value: &Value) -> Result<Vec<u8>, RuntimeError> {
    let bytes = serde_json::to_vec(value).map_err(|_| bad("Invalid conversation JSON"))?;
    if bytes.len() as u64 > MAX_DOCUMENT {
        return Err(bad("Conversation document exceeds the 32 MiB limit"));
    }
    Ok(bytes)
}

pub(crate) fn values<'a>(message: &'a Value, key: &str) -> &'a [Value] {
    message[key].as_array().filter(|values| !values.is_empty())
        .or_else(|| message["metadata"][key].as_array()).map(Vec::as_slice).unwrap_or_default()
}

pub(crate) const LEGACY_MEDIA: [(&str, &str); 4] = [
    ("images", "image"), ("image_files", "image"), ("videos", "video"), ("video_files", "video"),
];

pub(crate) fn local_media(value: &Value) -> Option<&str> {
    value.as_str().or_else(|| value["path"].as_str()).filter(|path| {
        !path.is_empty() && !["http://", "https://", "data:"].iter().any(|prefix| path.starts_with(prefix))
    })
}

pub(crate) fn media_kind(value: &str) -> bool {
    matches!(value, "image" | "video")
}

pub(crate) fn kind_name(kind: crate::legacy::LegacyKind) -> &'static str {
    match kind { crate::legacy::LegacyKind::Agent => "agent", crate::legacy::LegacyKind::Chat => "chat" }
}
