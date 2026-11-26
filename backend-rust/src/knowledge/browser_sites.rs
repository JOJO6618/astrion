use std::{
    collections::BTreeMap,
    io::{self, ErrorKind},
    sync::Mutex,
};

use cap_std::fs::Dir;
use serde_json::{Value, json};

use super::storage::{publish, read_bytes};

const FILE: &str = "browser_site_permissions.json";
static SITE_WRITES: Mutex<()> = Mutex::new(());

type Sites = BTreeMap<String, Value>;

pub(super) fn valid_site(site: &str) -> bool {
    if site.is_empty() || site.len() > 260 {
        return false;
    }
    if let Some(host) = site.strip_prefix('[') {
        let Some((host, port)) = host.split_once("]:") else {
            return false;
        };
        return valid_port(port)
            && !host.is_empty()
            && host
                .bytes()
                .all(|c| c.is_ascii_digit() || matches!(c, b'a'..=b'f' | b':' | b'.'));
    }
    let host = if let Some((host, port)) = site.split_once(':') {
        if !valid_port(port) {
            return false;
        }
        host
    } else {
        site
    };
    host.split('.').all(|label| {
        !label.is_empty()
            && label
                .bytes()
                .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'-')
    })
}

fn valid_port(port: &str) -> bool {
    (1..=5).contains(&port.len()) && port.bytes().all(|byte| byte.is_ascii_digit())
}

pub(super) fn list(data: &Dir) -> io::Result<Vec<Value>> {
    let _guard = SITE_WRITES
        .lock()
        .map_err(|_| io::Error::other("Browser site lock is unavailable"))?;
    Ok(rows(load(data)?))
}

pub(super) fn revoke(data: &Dir, site: &str) -> io::Result<(bool, Vec<Value>)> {
    let _guard = SITE_WRITES
        .lock()
        .map_err(|_| io::Error::other("Browser site lock is unavailable"))?;
    let mut sites = load(data)?;
    let removed = sites.remove(site).is_some();
    if removed {
        let replacement = serde_json::to_vec_pretty(&json!({"version": 1, "sites": &sites}))?;
        publish(data, FILE, &replacement, true)?;
    }
    Ok((removed, rows(sites)))
}

fn load(data: &Dir) -> io::Result<Sites> {
    let bytes = match read_bytes(data, FILE, 8 * 1024 * 1024) {
        Ok(bytes) => bytes,
        Err(error) if error.kind() == ErrorKind::NotFound => return Ok(Sites::new()),
        Err(error) => return Err(error),
    };
    let mut doc = serde_json::from_slice::<Value>(&bytes).unwrap_or_default();
    let sites = doc
        .get_mut("sites")
        .and_then(Value::as_object_mut)
        .map(std::mem::take);
    Ok(sites
        .into_iter()
        .flatten()
        .filter(|(site, info)| valid_site(site) && info.is_object())
        .collect())
}

fn rows(sites: Sites) -> Vec<Value> {
    sites
        .into_iter()
        .map(|(site, info)| json!({"site": site, "granted_at": info["granted_at"]}))
        .collect()
}
