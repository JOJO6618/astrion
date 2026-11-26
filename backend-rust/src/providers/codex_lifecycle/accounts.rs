use std::{
    fs, io,
    path::{Path, PathBuf},
    sync::{Mutex, MutexGuard, PoisonError},
};

use serde_json::{Map, Value, json};
use uuid::Uuid;

use super::super::{codex, codex_oauth, store};

// Every lifecycle writer takes the shared account lock before this registry
// lock. The registry lock is never held during network I/O or an async wait.
pub(super) fn registry_lock() -> MutexGuard<'static, ()> {
    static REGISTRY: Mutex<()> = Mutex::new(());
    REGISTRY.lock().unwrap_or_else(PoisonError::into_inner)
}

type Index = Map<String, Value>;

#[derive(Clone, PartialEq, Eq)]
pub(super) struct AccountKey {
    pub id: String,
    identity: Value,
    created_at: Value,
}

impl AccountKey {
    fn from_record(id: &str, record: &Value) -> Self {
        Self {
            id: id.to_owned(),
            identity: record["identity_hash"].clone(),
            created_at: record["created_at"].clone(),
        }
    }

    pub fn matches(&self, index: &Index) -> bool {
        index.get(&self.id).is_some_and(|record| {
            record["identity_hash"] == self.identity && record["created_at"] == self.created_at
        })
    }
}

pub(super) fn resolve(index: &Index, requested: &str) -> Option<AccountKey> {
    let id = if requested.is_empty() {
        codex::primary_in_index(index)?
    } else {
        requested
    };
    index
        .get(id)
        .map(|record| AccountKey::from_record(id, record))
}

pub(super) fn status(data_dir: &Path, account: &AccountKey, index: &Index) -> Value {
    let id = &account.id;
    let hash = index[id]["display_hash"].as_str().unwrap_or_default();
    let mut payload = auth_status(&codex::credential_path_for(data_dir, id));
    payload["account_id"] = json!(id);
    payload["account_hash"] = json!(hash);
    payload["account_name"] = json!(codex::display_name(hash));
    payload["is_primary"] = json!(codex::primary_in_index(index) == Some(id));
    payload["models"] = codex::model_cache_summary(data_dir, id);
    payload
}

fn auth_status(path: &Path) -> Value {
    let data: Value = match fs::read(path) {
        Ok(bytes) => match serde_json::from_slice(&bytes) {
            Ok(data) => data,
            Err(_) => {
                return json!({"connected": false, "error": "读取 Codex 凭证失败: JSON 格式不正确。"});
            }
        },
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            return json!({"connected": false, "error": "未找到 Codex 凭证。请在个人空间连接 Codex 账号。"});
        }
        Err(_) => return json!({"connected": false, "error": "读取 Codex 凭证失败。"}),
    };
    let Some(tokens) = data["tokens"].as_object() else {
        return json!({"connected": false, "error": "Codex 凭证格式不正确（缺少 tokens）。"});
    };
    let access = tokens.get("access_token").and_then(nonempty);
    let refresh = tokens.get("refresh_token").and_then(nonempty);
    let expires_at = access.and_then(|token| {
        let claims = codex::jwt_payload(token);
        let value = &claims["exp"];
        value
            .as_i64()
            .or_else(|| value.as_str()?.trim().parse().ok())
    });
    let expires_in_seconds = expires_at.filter(|value| *value != 0).map(|value| {
        value
            .saturating_sub(time::OffsetDateTime::now_utc().unix_timestamp())
            .max(0)
    });
    json!({
        "connected": access.is_some() && refresh.is_some(),
        "auth_mode": data["auth_mode"],
        "expires_at": expires_at,
        "expires_in_seconds": expires_in_seconds,
        "last_refresh": data["last_refresh"],
    })
}

pub(super) struct Tokens {
    access: String,
    refresh: String,
    id_token: String,
    account_id: Option<String>,
    identity: String,
}

impl Tokens {
    pub fn parse(payload: &Value) -> Result<Self, &'static str> {
        let access = nonempty(&payload["access_token"]);
        let refresh = nonempty(&payload["refresh_token"]);
        let (Some(access), Some(refresh)) = (access, refresh) else {
            return Err("token 响应缺少 access_token/refresh_token");
        };
        let id_token = payload["id_token"].as_str().unwrap_or_default().to_owned();
        let claims = codex::jwt_payload(&id_token);
        let account_id = nonempty(&claims["https://api.openai.com/auth"]["chatgpt_account_id"])
            .map(str::to_owned);
        let identity = codex::identity_hash(&id_token, account_id.as_deref());
        Ok(Self {
            access: access.into(),
            refresh: refresh.into(),
            id_token,
            account_id,
            identity,
        })
    }
}

#[derive(Clone)]
pub(super) struct Registration {
    pub connection: AccountKey,
    pub hash: String,
    pub account_id: Option<String>,
}

pub(super) struct LoginTarget {
    pub id: String,
    existing: Option<AccountKey>,
}

impl LoginTarget {
    pub fn select(
        data_dir: &Path,
        index: &Index,
        tokens: &Tokens,
        reconnect: Option<&AccountKey>,
    ) -> Result<Self, String> {
        if let Some(account) = reconnect {
            if !account.matches(index) || !matches_identity(&index[&account.id], &tokens.identity) {
                return Err("reconnect_account_mismatch".into());
            }
            return Ok(Self {
                id: account.id.clone(),
                existing: Some(account.clone()),
            });
        }
        if let Some(account) = matching_account(index, &tokens.identity) {
            return Ok(Self {
                id: account.id.clone(),
                existing: Some(account),
            });
        }
        for _ in 0..32 {
            let uuid = Uuid::new_v4().simple().to_string();
            let id = format!("openai-codex-{}", &uuid[..8]);
            if !index.contains_key(&id) && !codex::credential_path_for(data_dir, &id).exists() {
                return Ok(Self { id, existing: None });
            }
        }
        Err("无法分配 Codex 账号连接 id".into())
    }

    pub fn commit(
        self,
        data_dir: &Path,
        mut index: Index,
        tokens: Tokens,
        reconnect: bool,
    ) -> Result<Registration, String> {
        let credential_path = codex::credential_path_for(data_dir, &self.id);
        let same_target = match &self.existing {
            Some(account) => account.matches(&index),
            None => {
                !index.contains_key(&self.id)
                    && !credential_path.exists()
                    && matching_account(&index, &tokens.identity).is_none()
            }
        };
        if !same_target {
            return Err(if reconnect {
                "reconnect_account_mismatch"
            } else {
                "account_changed"
            }
            .into());
        }
        let id = self.id;
        let now = store::now_iso();
        let hash = index
            .get(&id)
            .and_then(|record| nonempty(&record["display_hash"]))
            .map(str::to_owned)
            .unwrap_or_else(|| codex::assign_display_hash(&index, &tokens.identity, &id));
        if let Some(record) = index.get_mut(&id) {
            record["last_login_at"] = json!(now);
            record["display_hash"] = json!(hash);
        } else {
            index.insert(
                id.clone(),
                json!({
                    "identity_hash": tokens.identity,
                    "display_hash": hash,
                    "created_at": now,
                    "last_login_at": now,
                    "legacy": false,
                }),
            );
        }
        let connection = AccountKey::from_record(&id, &index[&id]);
        let previous = codex_oauth::read_object(&credential_path);
        let credential = json!({
            "auth_mode": nonempty(&previous["auth_mode"]).unwrap_or("chatgpt"),
            "OPENAI_API_KEY": previous["OPENAI_API_KEY"],
            "tokens": {
                "id_token": tokens.id_token,
                "access_token": tokens.access,
                "refresh_token": tokens.refresh,
                "account_id": tokens.account_id,
            },
            "last_refresh": now,
        });
        change_credential(data_dir, &credential_path, &index, Some(&credential))
            .map_err(|error| format!("写入凭证失败: {error}"))?;
        Ok(Registration {
            connection,
            hash,
            account_id: tokens.account_id,
        })
    }
}

fn matches_identity(record: &Value, identity: &str) -> bool {
    !identity.is_empty() && record["identity_hash"].as_str() == Some(identity)
}

fn matching_account(index: &Index, identity: &str) -> Option<AccountKey> {
    index
        .iter()
        .filter(|(_, record)| matches_identity(record, identity))
        .min_by_key(|(id, record)| {
            (
                id.as_str() != codex::LEGACY,
                record["created_at"].as_str().unwrap_or_default(),
                id.as_str(),
            )
        })
        .map(|(id, record)| AccountKey::from_record(id, record))
}

pub(super) fn logout(data_dir: &Path, account: &AccountKey, mut index: Index) -> io::Result<Value> {
    let id = &account.id;
    index.shift_remove(id);
    change_credential(
        data_dir,
        &codex::credential_path_for(data_dir, id),
        &index,
        None,
    )?;
    let models_cache_cleared = clear_models(data_dir, id).is_ok();
    Ok(json!({
        "success": true,
        "account_id": id,
        "id": id,
        "removed": true,
        "models_cache_cleared": models_cache_cleared,
        "primary_account_id": codex::primary_in_index(&index),
    }))
}

fn clear_models(data_dir: &Path, id: &str) -> io::Result<()> {
    let path = codex::models_path_for(data_dir, id);
    if id != codex::LEGACY {
        return remove_if_present(&path);
    }
    if let Value::Object(mut data) = codex_oauth::read_object(&path) {
        let before = data.len();
        for key in ["models", "fetched_at", "source", "etag"] {
            data.shift_remove(key);
        }
        if data.len() != before {
            codex_oauth::write_private_json_blocking(&path, &Value::Object(data))?;
        }
    }
    Ok(())
}

fn change_credential(
    data_dir: &Path,
    credential_path: &Path,
    index: &Index,
    credential: Option<&Value>,
) -> io::Result<()> {
    let parked = park_credential(credential_path)?;
    let result = (|| {
        if let Some(credential) = credential {
            codex_oauth::write_private_json_blocking(credential_path, credential)?;
        }
        codex_oauth::write_private_json_blocking(
            &data_dir.join("codex_accounts.json"),
            &json!({"version": 1, "accounts": index}),
        )
    })();
    if let Err(error) = result {
        if let Some(previous) = &parked {
            store::replace_file(previous, credential_path)?;
        } else if credential.is_some() {
            remove_if_present(credential_path)?;
        }
        return Err(error);
    }
    if let Some(previous) = &parked {
        remove_if_present(previous)?;
    }
    Ok(())
}

fn park_credential(path: &Path) -> io::Result<Option<PathBuf>> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if !metadata.is_file() => {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "credential path is not a regular file",
            ));
        }
        Ok(_) => {}
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error),
    }
    let parked = path.with_file_name(format!(".codex-credential-{}.tmp", Uuid::new_v4().simple()));
    fs::rename(path, &parked)?;
    Ok(Some(parked))
}

fn remove_if_present(path: &Path) -> io::Result<()> {
    match fs::remove_file(path) {
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
        result => result,
    }
}

pub(super) fn nonempty(value: &Value) -> Option<&str> {
    value.as_str().filter(|text| !text.is_empty())
}
