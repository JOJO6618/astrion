//! Opt-in file diagnostics for the isolated Escape reproduction.
use std::io::Write;
use serde_json::{json, Value};

pub fn record(event: &str, data: Value) {
    let Some(path) = std::env::var_os("ASTRION_QUICK_DIAGNOSTICS") else { return; };
    let at = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_millis();
    if let Ok(mut file) = std::fs::OpenOptions::new().create(true).append(true).open(path) {
        let _ = writeln!(file, "{}", json!({"at":at,"event":event,"data":data}));
    }
}
