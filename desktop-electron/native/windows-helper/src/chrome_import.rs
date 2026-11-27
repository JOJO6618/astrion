mod exports;
mod history;
#[cfg(test)]
#[path = "../../../../test/2026-11-27_WindowsChromeRust/chrome_import.rs"]
mod tests;

use serde::Serialize;
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::io::{self, Read, Write};
use std::path::PathBuf;
use url::Url;

type ImportResult<T> = Result<T, &'static str>;
const MAX_PIPE_BYTES: u64 = 32 * 1024 * 1024;

#[derive(Default, Serialize)]
struct Stats {
    read: usize,
    ready: usize,
    skipped: usize,
    failed: usize,
    conflicts: usize,
    reasons: BTreeMap<&'static str, usize>,
}

impl Stats {
    fn issue(&mut self, reason: &'static str, failed: bool) {
        if failed {
            self.failed += 1;
        } else {
            self.skipped += 1;
        }
        *self.reasons.entry(reason).or_default() += 1;
    }
}

#[derive(Default, Serialize)]
struct Preview {
    stats: PreviewStats,
    passwords: Vec<Value>,
    cookies: Vec<Value>,
    history: Vec<Value>,
}

#[derive(Default, Serialize)]
struct PreviewStats {
    passwords: Stats,
    cookies: Stats,
    history: Stats,
}

fn origin(input: &str) -> ImportResult<String> {
    let url = Url::parse(input).map_err(|_| "invalid_record")?;
    if !matches!(url.scheme(), "http" | "https")
        || !url.has_host()
        || !url.username().is_empty()
        || url.password().is_some()
        || !input
            .split_once("://")
            .is_some_and(|(_, authority)| !authority.starts_with(['/', '\\']))
    {
        return Err("invalid_record");
    }
    Ok(url.origin().ascii_serialization())
}

fn chrome_root() -> ImportResult<PathBuf> {
    let local = std::env::var_os("LOCALAPPDATA").ok_or("chrome_read_failed")?;
    Ok(PathBuf::from(local)
        .join("Google")
        .join("Chrome")
        .join("User Data"))
}

fn dispatch(request: &Value) -> ImportResult<Value> {
    let value = &request["value"];
    match request["action"].as_str() {
        Some("sources") => Ok(json!({ "sources": history::sources(&chrome_root()?)? })),
        Some("preview-history") => {
            let source = value["source"].as_str().ok_or("invalid_source")?;
            Ok(json!(history::preview(&chrome_root()?, source)?))
        }
        Some("preview-file") => Ok(json!(exports::preview(value)?)),
        _ => Err("chrome_read_failed"),
    }
}

pub fn run() -> Result<(), String> {
    let mut request = Vec::new();
    io::stdin()
        .take(MAX_PIPE_BYTES + 1)
        .read_to_end(&mut request)
        .map_err(|_| "chrome_read_failed".to_owned())?;
    let result = if request.len() as u64 > MAX_PIPE_BYTES {
        Err("import_file_too_large")
    } else {
        serde_json::from_slice(&request)
            .map_err(|_| "chrome_read_failed")
            .and_then(|request| dispatch(&request))
    };
    let response = match result {
        Ok(value) => json!({ "ok": true, "value": value }),
        Err(error) => json!({ "ok": false, "error": error }),
    };
    let mut stdout = io::BufWriter::new(io::stdout().lock());
    serde_json::to_writer(&mut stdout, &response).map_err(|_| "chrome_read_failed".to_owned())?;
    stdout.flush().map_err(|_| "chrome_read_failed".to_owned())
}
