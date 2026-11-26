use std::{collections::{HashMap, HashSet}, io::{Cursor, Read, Write}};

use serde::{Deserialize, Serialize};
use serde_json::Value;
use zip::{CompressionMethod, ZipArchive, ZipWriter, write::SimpleFileOptions};

use crate::runtime::RuntimeError;

use super::{MAX_ASSETS, MAX_BYTES, MAX_DOCUMENT, MAX_UPLOAD, bad, document, is_digest, kind_name, sha256};

const FORMAT: &str = "astrion-conversation";
const MAX_MANIFEST: u64 = 4 * 1024 * 1024;

#[derive(Debug, Serialize, Deserialize)]
pub(crate) struct Manifest {
    format: String,
    version: u32,
    pub kind: String,
    pub document_sha256: String,
    pub source: Value,
    pub resources: Vec<Resource>,
    // This flag describes an internal migration only. A ZIP cannot set it.
    #[serde(skip)]
    pub shared_media: bool,
}

impl Manifest {
    pub fn new(kind: &str, encoded: &[u8], source: Value, resources: Vec<Resource>, shared_media: bool) -> Self {
        Self { format: FORMAT.into(), version: 1, kind: kind.into(), document_sha256: sha256(encoded), source, resources, shared_media }
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub(crate) struct Resource {
    pub kind: String,
    pub sha256: String,
    pub file: String,
    pub size: u64,
    pub mime_type: Option<String>,
    pub media_id: Option<String>,
    pub paths: Vec<String>,
}

pub(crate) type Payloads = HashMap<String, Vec<u8>>;

pub(crate) struct Archive {
    pub document: Value,
    pub manifest: Manifest,
    pub files: Payloads,
}

pub(crate) fn write(encoded: &[u8], manifest: &Manifest, files: &Payloads) -> Result<Vec<u8>, RuntimeError> {
    let manifest_data = serde_json::to_vec(manifest).map_err(|_| invalid_json())?;
    if manifest_data.len() as u64 > MAX_MANIFEST {
        return Err(bad("Conversation manifest is too large"));
    }
    let mut archive = ZipWriter::new(Cursor::new(Vec::new()));
    let options = SimpleFileOptions::default().compression_method(CompressionMethod::Deflated).unix_permissions(0o600);
    for (name, data) in [("manifest.json", manifest_data.as_slice()), ("conversation.json", encoded)]
        .into_iter().chain(files.iter().map(|(name, bytes)| (name.as_str(), bytes.as_slice())))
    {
        archive.start_file(name, options).map_err(zip_error)?;
        archive.write_all(data).map_err(super::io_error)?;
    }
    let bytes = archive.finish().map_err(zip_error)?.into_inner();
    if bytes.len() > MAX_UPLOAD { return Err(bad("Conversation archive is too large")); }
    Ok(bytes)
}

pub(crate) fn read(bytes: &[u8]) -> Result<Archive, RuntimeError> {
    if bytes.len() > MAX_UPLOAD { return Err(bad("Conversation file exceeds the import size limit")); }
    // The zip reader indexes by name and collapses duplicates. Inspect the raw
    // central directory before it can build that index or allocate from a count.
    let (directory, count) = inspect_directory(bytes)?;
    let mut archive = ZipArchive::new(Cursor::new(bytes)).map_err(zip_error)?;
    if archive.len() != count || archive.central_directory_start() != directory as u64 {
        return Err(invalid_entry());
    }
    let mut sizes = HashMap::with_capacity(count);
    let mut total = 0u64;
    for index in 0..archive.len() {
        let entry = archive.by_index_raw(index).map_err(zip_error)?;
        let name = entry.name();
        let allowed = matches!(name, "manifest.json" | "conversation.json") || name.strip_prefix("assets/").is_some_and(is_digest);
        if !allowed || entry.is_dir() || entry.is_symlink() || entry.encrypted() || entry.name_raw() != name.as_bytes() {
            return Err(invalid_entry());
        }
        total = total.saturating_add(entry.size());
        sizes.insert(name.to_owned(), entry.size());
    }
    if total > MAX_BYTES + MAX_DOCUMENT { return Err(bad("Conversation archive is too large")); }
    let mut extract = |name: &str, limit: u64| -> Result<Vec<u8>, RuntimeError> {
        let size = sizes.get(name).copied().ok_or_else(|| bad("Conversation archive is incomplete"))?;
        if size > limit { return Err(bad("Conversation archive entry is too large")); }
        let entry = archive.by_name(name).map_err(zip_error)?;
        let mut data = Vec::new();
        entry.take(limit + 1).read_to_end(&mut data).map_err(|_| invalid_entry())?;
        if data.len() as u64 != size { return Err(invalid_entry()); }
        Ok(data)
    };
    let manifest_bytes = extract("manifest.json", MAX_MANIFEST)?;
    let encoded = extract("conversation.json", MAX_DOCUMENT)?;
    let manifest: Manifest = serde_json::from_slice(&manifest_bytes).map_err(|_| invalid_json())?;
    if manifest.format != FORMAT || manifest.version != 1 {
        return Err(bad("Unsupported Astrion conversation file version"));
    }
    let document: Value = serde_json::from_slice(&encoded).map_err(|_| invalid_json())?;
    let kind = document::validate(&document)?;
    if manifest.kind != kind_name(kind) || manifest.document_sha256 != sha256(&encoded) {
        return Err(bad("Conversation document checksum failed"));
    }
    if !manifest.source.is_object() || manifest.resources.len() > MAX_ASSETS {
        return Err(bad("Invalid conversation manifest"));
    }
    let mut files = HashMap::new();
    let mut asset_total = 0;
    for row in &manifest.resources {
        validate_resource(row)?;
        if !files.contains_key(&row.file) {
            let remaining = MAX_BYTES - asset_total;
            if row.size > remaining || sizes.get(&row.file).copied() != Some(row.size) {
                return Err(bad("Conversation resource checksum failed"));
            }
            let data = extract(&row.file, row.size)?;
            if sha256(&data) != row.sha256 { return Err(bad("Conversation resource checksum failed")); }
            asset_total += row.size;
            files.insert(row.file.clone(), data);
        }
        if files[&row.file].len() as u64 != row.size { return Err(bad("Conversation resource checksum failed")); }
    }
    if sizes.len() != files.len() + 2 { return Err(bad("Conversation archive contains unreferenced entries")); }
    super::assets::verify_coverage(&document, &manifest.resources, false)?;
    Ok(Archive { document, manifest, files })
}

pub(crate) fn validate_resource(row: &Resource) -> Result<(), RuntimeError> {
    if !is_digest(&row.sha256) || row.file != format!("assets/{}", row.sha256)
        || row.paths.iter().any(|path| path.chars().count() > 8192 || path.contains('\0'))
        || row.media_id.as_ref().is_some_and(|id| id != &format!("sha256:{}", row.sha256))
    {
        return Err(bad("Invalid resource reference"));
    }
    match row.kind.as_str() {
        "image" | "video" if row.mime_type.as_deref().is_some_and(|mime| mime.starts_with(&format!("{}/", row.kind))) => Ok(()),
        "material" if row.media_id.is_none() => Ok(()),
        _ => Err(bad("Invalid conversation resource type")),
    }
}

fn invalid_json() -> RuntimeError { bad("Conversation JSON is invalid") }
fn invalid_entry() -> RuntimeError { bad("Invalid conversation archive entry") }
fn zip_error(_: zip::result::ZipError) -> RuntimeError { bad("This is not an Astrion conversation file") }

fn inspect_directory(bytes: &[u8]) -> Result<(usize, usize), RuntimeError> {
    fn integer(bytes: &[u8], start: usize, width: usize) -> Option<usize> {
        let mut raw = [0; 8];
        raw[..width].copy_from_slice(bytes.get(start..start.checked_add(width)?)?);
        usize::try_from(u64::from_le_bytes(raw)).ok()
    }
    let invalid = || bad("This is not an Astrion conversation file");
    let lower = bytes.len().saturating_sub(22 + u16::MAX as usize);
    let end = (lower..=bytes.len().checked_sub(22).ok_or_else(invalid)?).rev().find(|&start| {
        bytes.get(start..start + 4) == Some(b"PK\x05\x06")
            && integer(bytes, start + 20, 2) == Some(bytes.len() - start - 22)
    }).ok_or_else(invalid)?;
    if integer(bytes, end + 4, 4) != Some(0) { return Err(invalid_entry()); }
    let count = integer(bytes, end + 10, 2).ok_or_else(invalid)?;
    let size = integer(bytes, end + 12, 4).ok_or_else(invalid)?;
    let offset = integer(bytes, end + 16, 4).ok_or_else(invalid)?;
    let (count, start, stop) = if count == u16::MAX as usize || size == u32::MAX as usize || offset == u32::MAX as usize {
        let locator = end.checked_sub(20).ok_or_else(invalid)?;
        if bytes.get(locator..locator + 4) != Some(b"PK\x06\x07") || integer(bytes, locator + 16, 4) != Some(1) {
            return Err(invalid_entry());
        }
        let trailer = integer(bytes, locator + 8, 8).filter(|start| *start <= locator.saturating_sub(56)).ok_or_else(invalid_entry)?;
        if bytes.get(trailer..trailer + 4) != Some(b"PK\x06\x06") || integer(bytes, trailer + 16, 8) != Some(0) {
            return Err(invalid_entry());
        }
        let count = integer(bytes, trailer + 32, 8).ok_or_else(invalid_entry)?;
        let size = integer(bytes, trailer + 40, 8).ok_or_else(invalid_entry)?;
        let start = integer(bytes, trailer + 48, 8).ok_or_else(invalid_entry)?;
        if integer(bytes, trailer + 24, 8) != Some(count) || start.checked_add(size) != Some(trailer) { return Err(invalid_entry()); }
        (count, start, trailer)
    } else {
        if integer(bytes, end + 8, 2) != Some(count) || offset.checked_add(size) != Some(end) { return Err(invalid_entry()); }
        (count, offset, end)
    };
    if !(2..=MAX_ASSETS + 2).contains(&count) { return Err(bad("Conversation archive is incomplete or too large")); }
    let mut cursor = start;
    let mut names = HashSet::with_capacity(count);
    for _ in 0..count {
        if cursor.checked_add(46).is_none_or(|position| position > stop) || bytes.get(cursor..cursor + 4) != Some(b"PK\x01\x02") {
            return Err(invalid_entry());
        }
        let filename = integer(bytes, cursor + 28, 2).ok_or_else(invalid_entry)?;
        let extra = integer(bytes, cursor + 30, 2).ok_or_else(invalid_entry)?;
        let comment = integer(bytes, cursor + 32, 2).ok_or_else(invalid_entry)?;
        let entry_end = cursor + 46 + filename + extra + comment;
        if entry_end > stop || !names.insert(&bytes[cursor + 46..cursor + 46 + filename]) { return Err(invalid_entry()); }
        let local = integer(bytes, cursor + 42, 4).ok_or_else(invalid_entry)?;
        if local != u32::MAX as usize {
            if local >= start || bytes.get(local..local + 4) != Some(b"PK\x03\x04")
                || integer(bytes, local + 6, 2).is_none_or(|flags| flags & 1 != 0)
                || integer(bytes, local + 26, 2) != Some(filename)
                || bytes.get(local + 30..local + 30 + filename) != bytes.get(cursor + 46..cursor + 46 + filename)
            { return Err(invalid_entry()); }
        }
        cursor = entry_end;
    }
    if cursor != stop { return Err(invalid_entry()); }
    Ok((start, count))
}
