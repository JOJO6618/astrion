use std::{
    collections::VecDeque,
    env,
    num::NonZeroU32,
    path::Path,
    sync::Mutex,
    time::{Duration, Instant},
};

use ring::pbkdf2;
use scrypt::{Params, scrypt};
use serde_json::Value;
use subtle::ConstantTimeEq;

use super::{AdminError, Result, store};

const VERIFY_LIMIT: usize = 5;
const VERIFY_WINDOW: Duration = Duration::from_secs(300);
const DEFAULT_TTL: u64 = 1800;

pub(super) struct Secondary {
    credential: Credential,
    attempts: Mutex<VecDeque<Instant>>,
    pub(super) ttl: Duration,
}

enum Credential {
    Unconfigured,
    Plain(String),
    Hash(String),
}

impl Secondary {
    pub(super) fn for_host(data: &Path) -> Self {
        let root = if data.ends_with("host/data") {
            data.parent().and_then(Path::parent).unwrap_or(data)
        } else {
            data
        };
        let settings = store::directory(root, false)
            .ok()
            .flatten()
            .and_then(|dir| store::read_object(&dir, "settings.json").ok().flatten())
            .unwrap_or_default();
        Self::from_configuration(&settings, |key| env::var(key).ok())
    }

    pub(super) fn from_configuration(
        settings: &Value,
        variable: impl Fn(&str) -> Option<String>,
    ) -> Self {
        let string = |value: Option<String>| {
            value
                .map(|value| value.trim().to_owned())
                .filter(|value| !value.is_empty())
        };
        let setting = store::nonempty(&settings["admin"]["secondary_password_hash"]);
        let value = |name: &str, mapped: Option<&str>| {
            let env_vars = &settings["env_vars"][name];
            let env_vars = env_vars
                .as_str()
                .map(str::to_owned)
                .or_else(|| env_vars.as_number().map(ToString::to_string));
            string(
                variable(name)
                    .or_else(|| mapped.map(str::to_owned))
                    .or(env_vars),
            )
        };
        let hash = value("AGENT_CFG_ADMIN_SECONDARY_PASSWORD_HASH", setting)
            .or_else(|| value("ADMIN_SECONDARY_PASSWORD_HASH", setting));
        let password = value("ADMIN_SECONDARY_PASSWORD", None);
        let credential = if let Some(hash) = hash {
            Credential::Hash(hash)
        } else if let Some(password) = password {
            Credential::Plain(password)
        } else {
            Credential::Unconfigured
        };
        let ttl = value("AGENT_ADMIN_SECONDARY_TTL_SECONDS", None)
            .and_then(|value| value.parse::<u64>().ok())
            .unwrap_or(DEFAULT_TTL);
        Self {
            credential,
            attempts: Mutex::default(),
            ttl: Duration::from_secs(ttl),
        }
    }

    pub(super) fn configured(&self) -> bool {
        !matches!(self.credential, Credential::Unconfigured)
    }

    pub(super) fn attempt(&self, now: Instant) -> Result<Option<u64>> {
        let mut attempts = self.attempts.lock().map_err(AdminError::lock)?;
        while attempts
            .front()
            .is_some_and(|time| now.duration_since(*time) >= VERIFY_WINDOW)
        {
            attempts.pop_front();
        }
        if attempts.len() >= VERIFY_LIMIT {
            let elapsed = now.duration_since(attempts[0]);
            return Ok(Some(
                VERIFY_WINDOW
                    .as_secs()
                    .saturating_sub(elapsed.as_secs())
                    .max(1),
            ));
        }
        attempts.push_back(now);
        Ok(None)
    }

    pub(super) fn verify(&self, password: &str) -> bool {
        match &self.credential {
            Credential::Unconfigured => false,
            Credential::Plain(expected) => {
                bool::from(password.as_bytes().ct_eq(expected.as_bytes()))
            }
            Credential::Hash(hash) => verify_werkzeug(hash, password.as_bytes()).unwrap_or(false),
        }
    }
}

fn verify_werkzeug(hash: &str, password: &[u8]) -> Option<bool> {
    let mut fields = hash.split('$');
    let (method, salt, expected) = (fields.next()?, fields.next()?.as_bytes(), fields.next()?);
    if fields.next().is_some() || salt.is_empty() {
        return None;
    }
    let expected: Vec<u8> = expected
        .as_bytes()
        .chunks(2)
        .map(|pair| {
            if pair.len() != 2 {
                return None;
            }
            let digit = |c| match c {
                b'0'..=b'9' => Some(c - b'0'),
                b'a'..=b'f' => Some(c - b'a' + 10),
                _ => None,
            };
            Some((digit(pair[0])? << 4) | digit(pair[1])?)
        })
        .collect::<Option<_>>()?;
    let mut params = method.split(':');
    match params.next()? {
        "scrypt" => {
            let n = params.next()?.parse::<u32>().ok()?;
            let r = params.next()?.parse::<u32>().ok()?;
            let p = params.next()?.parse::<u32>().ok()?;
            if params.next().is_some()
                || expected.len() != 64
                || !n.is_power_of_two()
                || u64::from(n) * u64::from(r) > 1_048_576
                || p > 16
                || u64::from(n) * u64::from(r) * u64::from(p) > 4_194_304
            {
                return None;
            }
            let params = Params::new(n.trailing_zeros() as u8, r, p, 64).ok()?;
            let mut digest = [0; 64];
            scrypt(password, salt, &params, &mut digest).ok()?;
            Some(bool::from(digest.as_slice().ct_eq(&expected)))
        }
        "pbkdf2" => {
            let (algorithm, length) = match params.next().unwrap_or("sha256") {
                "sha1" => (pbkdf2::PBKDF2_HMAC_SHA1, 20),
                "sha256" => (pbkdf2::PBKDF2_HMAC_SHA256, 32),
                "sha384" => (pbkdf2::PBKDF2_HMAC_SHA384, 48),
                "sha512" => (pbkdf2::PBKDF2_HMAC_SHA512, 64),
                _ => return None,
            };
            let iterations: NonZeroU32 = params.next().unwrap_or("1000000").parse().ok()?;
            if params.next().is_some() || expected.len() != length || iterations.get() > 10_000_000
            {
                return None;
            }
            Some(pbkdf2::verify(algorithm, iterations, salt, password, &expected).is_ok())
        }
        _ => None,
    }
}
