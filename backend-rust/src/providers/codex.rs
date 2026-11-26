//! ChatGPT/Codex account registry shared by the provider catalog and login lifecycle.
//! Public projections never include token contents. Model refresh and OAuth
//! transport live in `codex_oauth.rs`.

use std::{
    fs,
    path::{Path, PathBuf},
    time::SystemTime,
};

use ring::digest::{SHA256, digest};
use serde_json::{Value, json};

pub(super) const LEGACY: &str = "openai-codex";
const DISPLAY_BASE: &str = "OpenAI（ChatGPT 订阅）";

pub(super) struct Account {
    pub id: String,
    pub hash: String,
    pub display_name: String,
    pub connected: bool,
    pub state: &'static str,
    pub credential_missing: bool,
    pub models_count: usize,
    pub models_fetched_at: Option<String>,
}

pub(super) fn accounts(data_dir: &Path) -> Vec<Account> {
    let mut index = load_index(data_dir);
    index.retain(|id, _| valid_connection_id(id));
    let legacy_path = data_dir.join("codex_auth.json");
    let accounts_dir = data_dir.join("codex_accounts");
    let mut resolved: Vec<(Account, bool, String)> = index
        .iter()
        .map(|(id, record)| {
            let account = public(id, record, &legacy_path, &accounts_dir, data_dir);
            let created = record["created_at"].as_str().unwrap_or_default().to_owned();
            let legacy = record["legacy"].as_bool().unwrap_or(false);
            (account, legacy, created)
        })
        .collect();
    // Legacy first, then creation time and connection ID.
    resolved.sort_by(|left, right| {
        (!left.1, left.2.as_str(), left.0.id.as_str()).cmp(&(
            !right.1,
            right.2.as_str(),
            right.0.id.as_str(),
        ))
    });
    resolved
        .into_iter()
        .map(|(account, _, _)| account)
        .collect()
}

/// Python `/api/codex/accounts` payload: public account records in legacy-first
/// order, each carrying the per-account model-cache summary. The legacy file is
/// not persisted back; this is the same in-memory migration/discovery as the
/// catalog projection.
pub(super) fn public_accounts(data_dir: &Path) -> (Vec<Value>, Option<String>) {
    let mut index = load_index(data_dir);
    index.retain(|id, _| valid_connection_id(id));
    let legacy_path = data_dir.join("codex_auth.json");
    let accounts_dir = data_dir.join("codex_accounts");
    let mut rows: Vec<(&str, &Value, bool, &str)> = index
        .iter()
        .map(|(id, record)| {
            (
                id.as_str(),
                record,
                record["legacy"].as_bool().unwrap_or(false),
                record["created_at"].as_str().unwrap_or_default(),
            )
        })
        .collect();
    rows.sort_by(|left, right| (!left.2, left.3, left.0).cmp(&(!right.2, right.3, right.0)));
    let primary = rows.first().map(|(id, ..)| (*id).to_owned());
    let accounts = rows
        .into_iter()
        .map(|(id, record, legacy, _)| {
            let path = credential_path(id, &legacy_path, &accounts_dir);
            let credential_missing = !path.exists();
            let connected = credential_connected(&path, credential_missing);
            let state = if connected {
                "ready"
            } else if credential_missing {
                "credential_missing"
            } else {
                "disconnected"
            };
            let hash = record["display_hash"].as_str().unwrap_or_default();
            json!({
                "id": id,
                "hash": hash,
                "display_name": display_name(hash),
                "created_at": record.get("created_at").cloned().unwrap_or(Value::Null),
                "last_login_at": record.get("last_login_at").cloned().unwrap_or(Value::Null),
                "is_legacy": legacy,
                "connected": connected,
                "state": state,
                "credential_missing": credential_missing,
                "is_primary": primary.as_deref() == Some(id),
                "models": if credential_missing {
                    json!({"model_count": 0, "total_models": 0})
                } else {
                    model_cache_summary(data_dir, id)
                },
            })
        })
        .collect();
    (accounts, primary)
}

pub(super) fn display_name(hash: &str) -> String {
    let hash = hash.trim();
    if hash.is_empty() {
        DISPLAY_BASE.to_owned()
    } else {
        format!("{DISPLAY_BASE}-{hash}")
    }
}

fn credential_connected(path: &Path, missing: bool) -> bool {
    if missing {
        return false;
    }
    // Python `_public_record` falls back to `file_present` when auth status
    // raises (malformed/unreadable credential), and only reports disconnected
    // when a readable tokens object exists with an empty pair.
    match fs::read(path)
        .ok()
        .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok())
    {
        Some(data) if data.get("tokens").is_some_and(Value::is_object) => {
            let tokens = &data["tokens"];
            truthy(&tokens["access_token"]) && truthy(&tokens["refresh_token"])
        }
        _ => true,
    }
}

pub(super) fn model_cache_summary(data_dir: &Path, account_id: &str) -> Value {
    let cache = read_object(&models_path_for(data_dir, account_id));
    let models = cache["models"].as_array();
    let (source, fetched_at, total_models) = match models {
        Some(models) if !models.is_empty() => (
            cache.get("source").cloned().unwrap_or(Value::Null),
            cache.get("fetched_at").cloned().unwrap_or(Value::Null),
            models.len(),
        ),
        // Python `CodexModelsManager._load` returns this synthetic shape when
        // the account cache has no models and no CLI cache is available.
        _ => (json!("empty"), Value::Null, 0),
    };
    let mut visible = std::collections::BTreeSet::new();
    for model in models.into_iter().flatten() {
        let slug = model["slug"].as_str().map(str::trim).unwrap_or_default();
        if slug.is_empty()
            || model["visibility"].as_str() != Some("list")
            || !truthy(&model["supported_in_api"])
        {
            continue;
        }
        visible.insert(format!("{account_id}/{slug}"));
    }
    json!({
        "source": source,
        "fetched_at": fetched_at,
        "model_count": visible.len(),
        "total_models": total_models,
        "visible_models": visible.into_iter().collect::<Vec<_>>(),
    })
}

pub(crate) fn load_index(data_dir: &Path) -> serde_json::Map<String, Value> {
    hydrate_index(data_dir, read_index(data_dir).unwrap_or_default())
}

/// Lifecycle mutations must never replace an unreadable or malformed registry.
pub(super) fn lifecycle_index(data_dir: &Path) -> Result<serde_json::Map<String, Value>, String> {
    read_index(data_dir).map(|index| hydrate_index(data_dir, index))
}

fn hydrate_index(
    data_dir: &Path,
    mut index: serde_json::Map<String, Value>,
) -> serde_json::Map<String, Value> {
    index.retain(|id, _| valid_connection_id(id));
    migrate_legacy(&mut index, &data_dir.join("codex_auth.json"));
    discover(&mut index, &data_dir.join("codex_accounts"));
    index
}

/// Connection IDs become file names (`codex_accounts/<id>.json`), so only the
/// catalog's ASCII alphanumeric/`-`/`_` shape may be read from disk.
pub(super) fn valid_connection_id(value: &str) -> bool {
    !value.is_empty()
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
}

fn public(
    id: &str,
    record: &Value,
    legacy_path: &Path,
    accounts_dir: &Path,
    data_dir: &Path,
) -> Account {
    let hash = record["display_hash"]
        .as_str()
        .unwrap_or_default()
        .to_owned();
    let path = credential_path(id, legacy_path, accounts_dir);
    let credential_missing = !path.exists();
    let credential = read_object(&path);
    let tokens = &credential["tokens"];
    let connected =
        !credential_missing && truthy(&tokens["access_token"]) && truthy(&tokens["refresh_token"]);
    let state = if connected {
        "ready"
    } else if credential_missing {
        "credential_missing"
    } else {
        "disconnected"
    };
    // A registered account without usable credentials must not surface a stale
    // model count (Python only reads the cache when `connected`).
    let (models_count, models_fetched_at) = if connected {
        let cache = if id == LEGACY {
            data_dir.join("codex_models.json")
        } else {
            data_dir.join("codex_models").join(format!("{id}.json"))
        };
        let cache = read_object(&cache);
        let count = cache["models"]
            .as_array()
            .into_iter()
            .flatten()
            .filter(|model| {
                model["visibility"].as_str() == Some("list") && truthy(&model["supported_in_api"])
            })
            .count();
        (count, cache["fetched_at"].as_str().map(str::to_owned))
    } else {
        (0, None)
    };
    Account {
        id: id.to_owned(),
        display_name: if hash.is_empty() {
            DISPLAY_BASE.to_owned()
        } else {
            format!("{DISPLAY_BASE}-{hash}")
        },
        hash,
        connected,
        state,
        credential_missing,
        models_count,
        models_fetched_at,
    }
}

fn credential_path(id: &str, legacy: &Path, accounts_dir: &Path) -> PathBuf {
    if id == LEGACY {
        legacy.to_owned()
    } else {
        accounts_dir.join(format!("{id}.json"))
    }
}

/// Credential file for one account (legacy uses `codex_auth.json`).
pub(super) fn credential_path_for(data_dir: &Path, id: &str) -> PathBuf {
    credential_path(
        id,
        &data_dir.join("codex_auth.json"),
        &data_dir.join("codex_accounts"),
    )
}

/// Model cache for one account (legacy keeps the global `codex_models.json`).
pub(super) fn models_path_for(data_dir: &Path, id: &str) -> PathBuf {
    if id == LEGACY {
        data_dir.join("codex_models.json")
    } else {
        data_dir.join("codex_models").join(format!("{id}.json"))
    }
}

/// Resolve an explicit connection id, or the primary account (legacy first,
/// then earliest) when none is supplied. Unknown ids never fall back.
pub(super) fn resolve_account(data_dir: &Path, requested: &str) -> Option<Account> {
    let requested = requested.trim();
    let accounts = accounts(data_dir);
    if requested.is_empty() {
        return accounts.into_iter().next();
    }
    if !valid_connection_id(requested) {
        return None;
    }
    accounts.into_iter().find(|account| account.id == requested)
}

/// Primary account id without building the full catalog: one index read, plus
/// a one-time legacy/discovery fallback only while the index is empty. A
/// corrupt index is an error, never a silent switch to another account.
pub(super) fn primary_id(data_dir: &Path) -> Result<Option<String>, String> {
    let mut accounts = read_index(data_dir)?;
    accounts.retain(|id, _| valid_connection_id(id));
    if !accounts.contains_key(LEGACY) {
        // Presence of the legacy slot wins even while other accounts exist.
        // A corrupt legacy file must surface as a credential error, not as a
        // silent switch to a registered secondary account.
        match fs::metadata(data_dir.join("codex_auth.json")) {
            Ok(_) => return Ok(Some(LEGACY.into())),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => return Err(format!("credential_read_error: {error}")),
        }
    }
    if accounts.is_empty() {
        return discover_primary(data_dir);
    }
    Ok(primary_in_index(&accounts).map(str::to_owned))
}

fn read_index(data_dir: &Path) -> Result<serde_json::Map<String, Value>, String> {
    let index_path = data_dir.join("codex_accounts.json");
    let bytes = match fs::read(&index_path) {
        Ok(bytes) => Some(bytes),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => None,
        Err(error) => return Err(format!("account_registry_error: {error}")),
    };
    let accounts = match bytes {
        Some(bytes) => {
            let value: Value = serde_json::from_slice(&bytes)
                .map_err(|error| format!("account_registry_error: {error}"))?;
            let object = value
                .as_object()
                .ok_or_else(|| "account_registry_error: index is not an object".to_owned())?;
            match object.get("accounts") {
                None => serde_json::Map::new(),
                Some(Value::Object(accounts)) => accounts.clone(),
                Some(_) => {
                    return Err("account_registry_error: accounts is not an object".into());
                }
            }
        }
        None => serde_json::Map::new(),
    };
    Ok(accounts)
}

pub(super) fn primary_in_index(accounts: &serde_json::Map<String, Value>) -> Option<&str> {
    if accounts.contains_key(LEGACY) {
        return Some(LEGACY);
    }
    accounts
        .iter()
        .min_by(|(left_id, left), (right_id, right)| {
            (
                left["created_at"].as_str().unwrap_or_default(),
                left_id.as_str(),
            )
                .cmp(&(
                    right["created_at"].as_str().unwrap_or_default(),
                    right_id.as_str(),
                ))
        })
        .map(|(id, _)| id.as_str())
}

/// Discovery fallback used only when no index entry exists. It mirrors the
/// migration/self-healing pass of the full catalog: legacy file first, then the
/// earliest credential file with a non-empty token.
fn discover_primary(data_dir: &Path) -> Result<Option<String>, String> {
    let legacy = data_dir.join("codex_auth.json");
    match fs::metadata(&legacy) {
        // Presence is authoritative: a corrupt legacy file must be reported
        // by the credential read, never treated as "no account".
        Ok(_) => return Ok(Some(LEGACY.into())),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => return Err(format!("credential_read_error: {error}")),
    }
    let mut candidates: Vec<(SystemTime, String)> = Vec::new();
    let entries = match fs::read_dir(data_dir.join("codex_accounts")) {
        Ok(entries) => entries,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(format!("credential_read_error: {error}")),
    };
    for entry in entries.filter_map(Result::ok) {
        let path = entry.path();
        if path.extension().is_none_or(|extension| extension != "json") {
            continue;
        }
        let Some(id) = path.file_stem().and_then(|stem| stem.to_str()) else {
            continue;
        };
        if !valid_connection_id(id) || !credential_has_tokens(&path) {
            continue;
        }
        let created = fs::metadata(&path)
            .and_then(|metadata| metadata.modified())
            .unwrap_or(SystemTime::UNIX_EPOCH);
        candidates.push((created, id.to_owned()));
    }
    candidates.sort();
    Ok(candidates.into_iter().next().map(|(_, id)| id))
}

fn credential_has_tokens(path: &Path) -> bool {
    let Ok(bytes) = fs::read(path) else {
        return false;
    };
    let Ok(data) = serde_json::from_slice::<Value>(&bytes) else {
        return false;
    };
    let tokens = &data["tokens"];
    truthy(&tokens["access_token"]) || truthy(&tokens["refresh_token"])
}

fn migrate_legacy(index: &mut serde_json::Map<String, Value>, path: &Path) {
    if index.contains_key(LEGACY) {
        return;
    }
    let data = read_object(path);
    let tokens = &data["tokens"];
    if !truthy(&tokens["access_token"]) && !truthy(&tokens["refresh_token"]) {
        return;
    }
    let material = identity(
        tokens["id_token"].as_str().unwrap_or_default(),
        tokens["account_id"].as_str(),
    );
    let identity_hash = sha256(&material);
    let display_hash = assign_display_hash(index, &identity_hash, LEGACY);
    let created = mtime_iso(path);
    index.insert(
        LEGACY.into(),
        serde_json::json!({
            "identity_hash": identity_hash,
            "display_hash": display_hash,
            "created_at": created,
            "last_login_at": data["last_refresh"].as_str().filter(|value| !value.is_empty())
                .map(str::to_owned).unwrap_or(created),
            "legacy": true,
        }),
    );
}

fn discover(index: &mut serde_json::Map<String, Value>, directory: &Path) {
    let Ok(entries) = fs::read_dir(directory) else {
        return;
    };
    let mut paths: Vec<PathBuf> = entries
        .filter_map(Result::ok)
        .map(|entry| entry.path())
        .filter(|path| {
            path.extension()
                .is_some_and(|extension| extension == "json")
        })
        .collect();
    paths.sort();
    for path in paths {
        let Some(id) = path.file_stem().and_then(|stem| stem.to_str()) else {
            continue;
        };
        if !valid_connection_id(id) || index.contains_key(id) {
            continue;
        }
        let data = read_object(&path);
        let tokens = &data["tokens"];
        if !truthy(&tokens["access_token"]) && !truthy(&tokens["refresh_token"]) {
            continue;
        }
        let material = identity(
            tokens["id_token"].as_str().unwrap_or_default(),
            tokens["account_id"].as_str(),
        );
        let identity_hash = sha256(&material);
        let display_hash = assign_display_hash(index, &identity_hash, id);
        let created = mtime_iso(&path);
        index.insert(
            id.into(),
            serde_json::json!({
                "identity_hash": identity_hash,
                "display_hash": display_hash,
                "created_at": created,
                "last_login_at": data["last_refresh"].as_str().filter(|value| !value.is_empty())
                    .map(str::to_owned).unwrap_or(created),
                "legacy": false,
            }),
        );
    }
}

/// Stable short hash: at least six hex digits; only new accounts are lengthened
/// when prefixes collide, existing display hashes never change.
pub(super) fn assign_display_hash(
    index: &serde_json::Map<String, Value>,
    identity_hash: &str,
    account_id: &str,
) -> String {
    let used: Vec<&str> = index
        .iter()
        .filter(|(id, _)| id.as_str() != account_id)
        .filter_map(|(_, record)| record["display_hash"].as_str())
        .filter(|hash| !hash.is_empty())
        .collect();
    let fallback = sha256(&format!("account:{account_id}"));
    let source = if identity_hash.is_empty() {
        fallback.as_str()
    } else {
        identity_hash
    };
    for length in [6, 8, 10, 12, 16, 24, 32, source.len()] {
        let length = length.min(source.len());
        let candidate = &source[..length];
        if !used.contains(&candidate) {
            return candidate.to_owned();
        }
    }
    source.to_owned()
}

pub(super) fn identity_hash(id_token: &str, account_id: Option<&str>) -> String {
    sha256(&identity(id_token, account_id))
}

fn identity(id_token: &str, account_id: Option<&str>) -> String {
    let claims = jwt_payload(id_token);
    if let Some(id) = claims["https://api.openai.com/auth"]["chatgpt_account_id"].as_str()
        && !id.is_empty()
    {
        return format!("account:{id}");
    }
    if let Some(id) = account_id.filter(|id| !id.is_empty()) {
        return format!("account:{id}");
    }
    if let Some(sub) = claims["sub"].as_str().filter(|sub| !sub.is_empty()) {
        return format!("sub:{sub}");
    }
    if let Some(email) = claims["email"]
        .as_str()
        .map(str::trim)
        .filter(|email| !email.is_empty())
    {
        return format!("email:{}", email.to_ascii_lowercase());
    }
    String::new()
}

pub(super) fn jwt_payload(token: &str) -> Value {
    let Some(payload) = token.split('.').nth(1) else {
        return Value::Null;
    };
    decode_base64url(payload)
        .and_then(|bytes| serde_json::from_slice(&bytes).ok())
        .unwrap_or(Value::Null)
}

fn decode_base64url(text: &str) -> Option<Vec<u8>> {
    let mut output = Vec::with_capacity(text.len() * 3 / 4);
    let mut buffer = 0u32;
    let mut bits = 0u32;
    for byte in text.bytes() {
        let value = match byte {
            b'A'..=b'Z' => byte - b'A',
            b'a'..=b'z' => byte - b'a' + 26,
            b'0'..=b'9' => byte - b'0' + 52,
            b'-' | b'+' => 62,
            b'_' | b'/' => 63,
            b'=' => break,
            _ => return None,
        };
        buffer = (buffer << 6) | u32::from(value);
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            output.push((buffer >> bits) as u8);
            buffer &= (1 << bits) - 1;
        }
    }
    Some(output)
}

fn sha256(text: &str) -> String {
    if text.is_empty() {
        return String::new();
    }
    let digest = digest(&SHA256, text.as_bytes());
    digest
        .as_ref()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

fn mtime_iso(path: &Path) -> String {
    let modified = fs::metadata(path)
        .and_then(|metadata| metadata.modified())
        .unwrap_or(SystemTime::UNIX_EPOCH);
    let time = time::OffsetDateTime::from(modified);
    format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}.{:06}Z",
        time.year(),
        u8::from(time.month()),
        time.day(),
        time.hour(),
        time.minute(),
        time.second(),
        time.microsecond()
    )
}

fn read_object(path: &Path) -> Value {
    fs::read(path)
        .ok()
        .and_then(|bytes| serde_json::from_slice(&bytes).ok())
        .unwrap_or(Value::Null)
}

fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::Number(number) => number.as_f64() != Some(0.0),
        Value::String(value) => !value.is_empty(),
        Value::Array(value) => !value.is_empty(),
        Value::Object(value) => !value.is_empty(),
    }
}
