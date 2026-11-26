use std::{fs, io, io::ErrorKind, path::Path};

use cap_std::fs::Dir;
use serde_json::Value;

use crate::knowledge::storage::{open_canonical_dir, publish, read_bytes};

pub(super) const MAX_FILE_BYTES: u64 = 2 * 1024 * 1024;

pub(super) fn directory(path: &Path, create: bool) -> io::Result<Option<Dir>> {
    if create {
        fs::create_dir_all(path)?;
    }
    match path.canonicalize() {
        Ok(path) => open_canonical_dir(&path).map(Some),
        Err(error) if !create && error.kind() == ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error),
    }
}

pub(super) fn read(dir: &Dir, name: &str) -> io::Result<Option<Vec<u8>>> {
    match read_bytes(dir, name, MAX_FILE_BYTES) {
        Ok(bytes) => Ok(Some(bytes)),
        Err(error) if error.kind() == ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error),
    }
}

pub(super) fn read_object(dir: &Dir, name: &str) -> io::Result<Option<Value>> {
    read(dir, name)?
        .map(|bytes| {
            let value: Value = serde_json::from_slice(&bytes)?;
            if !value.is_object() {
                return Err(io::Error::new(ErrorKind::InvalidData, "Expected an object"));
            }
            Ok(value)
        })
        .transpose()
}

pub(super) fn write(dir: &Dir, name: &str, content: &[u8]) -> io::Result<()> {
    if content.len() as u64 > MAX_FILE_BYTES {
        return Err(io::Error::new(ErrorKind::InvalidInput, "File too large"));
    }
    let exists = match dir.symlink_metadata(name) {
        Ok(metadata) if metadata.is_file() => true,
        Ok(_) => return Err(io::Error::new(ErrorKind::InvalidData, "Invalid file type")),
        Err(error) if error.kind() == ErrorKind::NotFound => false,
        Err(error) => return Err(error),
    };
    publish(dir, name, content, exists)
}

pub(super) fn remove(dir: &Dir, name: &str) -> io::Result<()> {
    match dir.remove_file(name) {
        Err(error) if error.kind() == ErrorKind::NotFound => Ok(()),
        result => result,
    }
}

pub(super) fn pretty(value: &Value) -> io::Result<Vec<u8>> {
    Ok(serde_json::to_vec_pretty(value)?)
}

pub(super) fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::Number(value) => value.as_f64() != Some(0.0),
        Value::String(value) => !value.is_empty(),
        Value::Array(value) => !value.is_empty(),
        Value::Object(value) => !value.is_empty(),
    }
}

pub(super) fn nonempty(value: &Value) -> Option<&str> {
    value.as_str().filter(|value| !value.is_empty())
}
