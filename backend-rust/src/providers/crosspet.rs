//! Minimal read-only bridge for CrossPet subscription quota.
//!
//! CrossPet never sees OAuth tokens. It receives an opaque identity (connection
//! id + credential-file version) and asks this module to perform one usage
//! request. The request shares the same process-wide per-account lock as model
//! refresh, so an expired credential is rotated once and then reused.
//!
//! The usage endpoint is fixed in production; only tests inject a loopback URL.
//! The identity returned after the network call must replace the caller's copy:
//! if the account was deleted, its primary status changed, or its credential
//! file was replaced while the request was in flight, this returns an error and
//! CrossPet keeps its previous data stale instead of attributing it to a new
//! account.

use std::{
    fs,
    path::Path,
    time::{Duration, UNIX_EPOCH},
};

use serde_json::Value;

use super::{
    codex,
    codex_oauth::{self, CodexConfig},
};

const USAGE_TIMEOUT: Duration = Duration::from_secs(20);

#[derive(Clone, Copy, PartialEq, Eq)]
struct CredentialVersion {
    modified_ns: i128,
    size: u64,
}

/// Opaque account identity safe to hand to CrossPet.
#[derive(Clone, PartialEq, Eq)]
pub struct CrosspetIdentity {
    account_id: String,
    version: CredentialVersion,
    /// SHA-256 of `tokens.account_id` (the remote ChatGPT subscription).
    /// `None` when the credential carries no stable remote identity.
    subscription_digest: Option<String>,
}

/// Primary account: legacy first, otherwise the earliest registered account.
/// A registered primary without a credential file returns `Ok(None)` and never
/// falls back to a secondary account. An existing but corrupt/unreadable
/// credential is an error so CrossPet keeps stale data.
pub(super) fn account(config: &CodexConfig) -> Result<Option<CrosspetIdentity>, String> {
    // One index read only. The catalog builder would read every account
    // credential and model cache on each CrossPet poll.
    let Some(primary) = codex::primary_id(config.data_dir())? else {
        return Ok(None);
    };
    let path = codex::credential_path_for(config.data_dir(), &primary);
    match fs::metadata(&path) {
        Ok(_) => {}
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(format!("credential_read_error: {error}")),
    }
    let bytes = fs::read(&path).map_err(|error| format!("credential_read_error: {error}"))?;
    let credential: Value =
        serde_json::from_slice(&bytes).map_err(|error| format!("credential_invalid: {error}"))?;
    if !credential.is_object() {
        return Err("credential_invalid".into());
    }
    let Some(tokens) = credential.get("tokens").filter(|tokens| tokens.is_object()) else {
        return Err("credential_invalid: tokens is not an object".into());
    };
    if !has_token(&tokens["access_token"]) || !has_token(&tokens["refresh_token"]) {
        // Both tokens are required for a connected account. One token alone is
        // disconnected: unavailable, but never fall back to a secondary account.
        return Ok(None);
    }
    Ok(Some(CrosspetIdentity {
        account_id: primary,
        version: version_of(&path)?,
        subscription_digest: subscription_digest(tokens),
    }))
}

fn subscription_digest(tokens: &Value) -> Option<String> {
    use ring::digest::{SHA256, digest};
    let subscription = tokens["account_id"]
        .as_str()
        .map(str::trim)
        .filter(|id| !id.is_empty())?;
    let digest = digest(&SHA256, subscription.as_bytes());
    let mut output = String::with_capacity(64);
    for byte in digest.as_ref() {
        use std::fmt::Write;
        let _ = write!(output, "{byte:02x}");
    }
    Some(output)
}

fn has_token(value: &Value) -> bool {
    value.as_str().is_some_and(|token| !token.trim().is_empty())
}

/// Fetch the subscription usage JSON for `identity`. The outer error means the
/// identity became invalid before/while selecting the account. Once a token
/// refresh has rotated the credential (or the request was attempted), the
/// current identity is returned even when the HTTP call fails: the inner error
/// lets CrossPet distinguish "this request rotated the credential" from
/// "another account/credential replaced it" without retrying blindly.
pub(super) async fn usage(
    config: &CodexConfig,
    identity: &CrosspetIdentity,
) -> Result<(CrosspetIdentity, Result<Value, String>), String> {
    let lock = codex_oauth::account_lock(config.data_dir(), &identity.account_id);
    let _guard = lock.lock().await;

    // A concurrent refresh may already have rotated the credential since the
    // caller's snapshot; reuse the current file and return its new identity
    // instead of treating that as an account switch. Account id/subscription
    // and post-request stability are the safety checks.
    let current = primary_identity(config, Some(&identity.account_id))?;
    ensure_same_subscription(identity, &current)?;
    let auth_path = codex::credential_path_for(config.data_dir(), &identity.account_id);
    let mut credential = codex_oauth::read_object(&auth_path);
    let access = codex_oauth::usable_access_token(config, &auth_path, &mut credential).await?;
    let expected = primary_identity(config, Some(&identity.account_id))?;
    ensure_same_subscription(identity, &expected)?;

    let client = codex_oauth::oauth_client(config).await?;
    let mut headers = codex_oauth::request_headers(&credential, &access)?;
    // Usage uses the same OAuth identity but not the Codex models beta header.
    headers.remove("OpenAI-Beta");
    let response = client
        .get(config.usage_url())
        .timeout(USAGE_TIMEOUT)
        .headers(headers)
        .send()
        .await;

    match response {
        Err(error) => {
            // Headers never arrived, but the account may still have changed.
            let after = stable_identity(config, &expected)?;
            Ok((after, Err(format!("request_error: {error}"))))
        }
        Ok(response) if response.status() != reqwest::StatusCode::OK => {
            let after = stable_identity(config, &expected)?;
            Ok((after, Err(format!("http_{}", response.status().as_u16()))))
        }
        Ok(response) => {
            let parsed = response.json::<Value>().await;
            // Re-check only after the full body is read: a slow body gives a
            // concurrent login/logout time to replace the account mid-stream.
            let after = stable_identity(config, &expected)?;
            match parsed {
                Ok(usage) => Ok((after, Ok(usage))),
                Err(error) => Ok((after, Err(format!("bad_json: {error}")))),
            }
        }
    }
}

fn stable_identity(
    config: &CodexConfig,
    expected: &CrosspetIdentity,
) -> Result<CrosspetIdentity, String> {
    let after = primary_identity(config, Some(&expected.account_id))?;
    ensure_same_subscription(expected, &after)?;
    if after.version != expected.version {
        return Err("credential_changed".into());
    }
    Ok(after)
}

fn ensure_same_subscription(
    previous: &CrosspetIdentity,
    current: &CrosspetIdentity,
) -> Result<(), String> {
    if previous.subscription_digest != current.subscription_digest {
        return Err("account_changed".into());
    }
    if previous.subscription_digest.is_none() && previous.version != current.version {
        // No remote identity to anchor the request: any rewrite of the file is
        // an account switch for attribution purposes.
        return Err("account_changed".into());
    }
    Ok(())
}

fn primary_identity(
    config: &CodexConfig,
    expected: Option<&str>,
) -> Result<CrosspetIdentity, String> {
    let Some(identity) = account(config)? else {
        return Err("account_changed".into());
    };
    if expected.is_some_and(|id| id != identity.account_id) {
        return Err("account_changed".into());
    }
    Ok(identity)
}

fn version_of(path: &Path) -> Result<CredentialVersion, String> {
    let metadata = fs::metadata(path).map_err(|error| format!("credential_read_error: {error}"))?;
    let modified = metadata
        .modified()
        .map_err(|error| format!("credential_read_error: {error}"))?;
    let modified_ns = modified
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_nanos() as i128)
        .unwrap_or_default();
    Ok(CredentialVersion {
        modified_ns,
        size: metadata.len(),
    })
}
