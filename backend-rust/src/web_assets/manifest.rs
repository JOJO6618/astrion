use super::{AssetRoot, public_path};
use cap_fs_ext::MetadataExt;
use serde_json::{Map, Value};
use std::{
    collections::{HashMap, hash_map::Entry},
    io::{self, Read},
    sync::{Arc, Mutex},
    time::SystemTime,
};

const STATIC: &str = "/static/";
const DIST: &str = "/static/dist/";
const MANIFEST: &str = "dist/.vite/manifest.json";
const STYLES: &str = "dist/.vite/entry-styles.json";

#[derive(Default)]
pub(super) struct ManifestCache(Mutex<Option<CachedManifest>>);

type Stamp = [Option<FileStamp>; 2];
type MapByReference = HashMap<String, String>;

#[derive(PartialEq, Eq)]
struct FileStamp {
    modified: SystemTime,
    length: u64,
    device: u64,
    inode: u64,
}

struct CachedManifest {
    stamp: Stamp,
    mapping: Arc<MapByReference>,
}

impl ManifestCache {
    pub(super) fn inject(&self, assets: &AssetRoot, html: &str) -> io::Result<String> {
        let mapping = self.get(assets)?;
        let mut result = String::with_capacity(html.len());
        let mut end = 0;
        for (start, _) in html.match_indices(STATIC) {
            let part = &html[start..];
            let length = part
                .bytes()
                .take_while(|byte| asset_url_byte(*byte))
                .count();
            let reference = &part[..length];
            if start < end || !is_script_or_css(reference) {
                continue;
            }
            let target = mapping
                .get(reference)
                .map(String::as_str)
                .unwrap_or(reference);
            if target.starts_with(DIST)
                || matches!(target, "/static/style.css" | "/static/browser-window.css")
            {
                let file = target.strip_prefix(STATIC).expect("static asset prefix");
                if !public_path(file) || assets.file(file).is_err() {
                    return Err(invalid(format!(
                        "missing build asset {target} (referenced as {reference})"
                    )));
                }
            }
            result.push_str(&html[end..start]);
            result.push_str(target);
            end = start + length;
        }
        result.push_str(&html[end..]);
        Ok(result)
    }

    fn get(&self, assets: &AssetRoot) -> io::Result<Arc<MapByReference>> {
        let mut current = self
            .0
            .lock()
            .map_err(|_| io::Error::other("manifest cache lock is poisoned"))?;
        let stamp = read_stamp(assets)?;
        if let Some(current) = current.as_ref().filter(|cached| cached.stamp == stamp) {
            return Ok(current.mapping.clone());
        }
        let mapping = Arc::new(build_map(assets)?);
        if stamp != read_stamp(assets)? {
            return Err(invalid("Vite manifest changed while loading"));
        }
        *current = Some(CachedManifest {
            stamp,
            mapping: mapping.clone(),
        });
        Ok(mapping)
    }
}

fn invalid(reason: impl Into<String>) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, reason.into())
}

fn read_stamp(assets: &AssetRoot) -> io::Result<Stamp> {
    fn stat(assets: &AssetRoot, name: &str) -> io::Result<Option<FileStamp>> {
        match assets.file(name).and_then(|file| file.metadata()) {
            Ok(meta) => Ok(Some(FileStamp {
                modified: meta.modified()?.into_std(),
                length: meta.len(),
                device: meta.dev(),
                inode: meta.ino(),
            })),
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(None),
            Err(error) => Err(error),
        }
    }
    Ok([stat(assets, MANIFEST)?, stat(assets, STYLES)?])
}

fn read_json(assets: &AssetRoot, name: &str) -> io::Result<Option<Map<String, Value>>> {
    let mut file = match assets.file(name) {
        Ok(file) => file,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error),
    };
    let mut data = Vec::new();
    file.read_to_end(&mut data)
        .map_err(|error| invalid(format!("could not load {name}: {error}")))?;
    serde_json::from_slice(&data)
        .map(Some)
        .map_err(|error| invalid(format!("invalid JSON in {name}: {error}")))
}

fn build_map(assets: &AssetRoot) -> io::Result<MapByReference> {
    let manifest = read_json(assets, MANIFEST)?.ok_or_else(|| {
        invalid(format!(
            "missing {MANIFEST}; build the web frontend before serving entry pages"
        ))
    })?;
    let mut mapping = MapByReference::new();
    for chunk in manifest.values() {
        let Some(file) = chunk
            .get("file")
            .and_then(Value::as_str)
            .filter(|file| !file.is_empty())
        else {
            continue;
        };
        if chunk.get("isEntry").and_then(Value::as_bool) == Some(true)
            && let Some(name) = chunk
                .get("name")
                .and_then(Value::as_str)
                .filter(|name| !name.is_empty())
        {
            if !public_path(name) || name.contains('/') || !is_script(file) {
                return Err(invalid("invalid Vite entry filename"));
            }
            insert_mapping(&mut mapping, format!("{DIST}assets/{name}.js"), file)?;
        }
        if let Some(styles) = chunk.get("css").and_then(Value::as_array) {
            for css in styles.iter().filter_map(Value::as_str) {
                if !css.ends_with(".css") || !manifest_path(css) {
                    return Err(invalid("invalid Vite stylesheet filename"));
                }
                if let Some(fixed) = strip_content_hash(css) {
                    insert_mapping(&mut mapping, format!("{DIST}{fixed}"), css)?;
                }
            }
        }
    }
    if let Some(assemblies) =
        read_json(assets, STYLES)?.and_then(|mut styles| styles.remove("assemblies"))
    {
        let assemblies = assemblies
            .as_object()
            .ok_or_else(|| invalid("invalid Vite stylesheet assembly map"))?;
        for (reference, target) in assemblies {
            let name = reference
                .strip_prefix(STATIC)
                .filter(|name| name.ends_with(".css") && manifest_path(name));
            let target = target.as_str().filter(|path| path.ends_with(".css"));
            match (name, target) {
                (Some(_), Some(target)) => insert_mapping(&mut mapping, reference.clone(), target)?,
                _ => return Err(invalid("invalid Vite stylesheet assembly filename")),
            }
        }
    }
    Ok(mapping)
}

fn insert_mapping(mapping: &mut MapByReference, reference: String, target: &str) -> io::Result<()> {
    if !manifest_path(target) {
        return Err(invalid(format!("invalid build asset path for {reference}")));
    }
    let target = format!("{DIST}{target}");
    match mapping.entry(reference) {
        Entry::Vacant(entry) => {
            entry.insert(target);
        }
        Entry::Occupied(entry) if entry.get() != &target => {
            return Err(invalid(format!(
                "conflicting Vite asset mappings for {}",
                entry.key()
            )));
        }
        _ => (),
    }
    Ok(())
}

fn manifest_path(path: &str) -> bool {
    public_path(path) && path.bytes().all(asset_url_byte)
}

fn asset_url_byte(byte: u8) -> bool {
    byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.' | b'/')
}

fn is_script_or_css(path: &str) -> bool {
    is_script(path) || path.ends_with(".css")
}

fn is_script(path: &str) -> bool {
    path.ends_with(".js") || path.ends_with(".mjs")
}

fn strip_content_hash(path: &str) -> Option<String> {
    let (stem, extension) = path.rsplit_once('.')?;
    let (base, suffix) = stem.split_at_checked(stem.len().checked_sub(9)?)?;
    let hash = suffix.strip_prefix('-')?;
    if base.rsplit('/').next()?.is_empty()
        || !hash
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
    {
        return None;
    }
    Some(format!("{base}.{extension}"))
}
