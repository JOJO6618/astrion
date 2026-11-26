use super::{
    App, Result, Scope, WebError, conversation_id,
    file_response::{Payload, Representation},
};
use crate::{legacy::media::BlobStore, tools::read_file::open_download, worktree::Worktree};
use axum::{
    Extension,
    extract::{Path as RoutePath, Query},
    http::{HeaderMap, StatusCode},
    response::Response,
};
use serde::Deserialize;
use serde_json::Value;
use std::{
    io::ErrorKind,
    path::{Path, PathBuf},
};

#[derive(Deserialize)]
pub(super) struct Location {
    #[serde(flatten)]
    scope: Scope,
    path: Option<String>,
}

fn metadata(app: &App, scope: &Scope, headers: &HeaderMap) -> Result<Value> {
    app.check_workspace(headers, scope, &Value::Null)?;
    match scope
        .conversation_id
        .as_deref()
        .map(str::trim)
        .filter(|id| !id.is_empty())
    {
        Some(id) => Ok(app
            .runtime
            .web_metadata(&app.workspace_id, &conversation_id(id)?)?),
        None => Ok(Value::Null),
    }
}

fn locate(app: &App, meta: &Value, raw: &str) -> Result<(PathBuf, PathBuf)> {
    let root = &app.project_path;
    if let Some(mapped) = meta["conversation_transfer"]["paths"][raw].as_str()
        && mapped.starts_with(".astrion/conversation_assets/")
        && crate::conversation_transfer::assets::components(mapped).is_ok()
    {
        return Ok((root.clone(), root.join(mapped)));
    }
    let execution = if meta["git_worktree_enabled"] == true && !meta["git_worktree"].is_null() {
        Worktree::new(root)
            .and_then(|tree| tree.execution_root(meta))
            .map_err(|_| WebError::invalid("工作树不可用"))?
    } else {
        root.clone()
    };
    let path = if let Some(relative) = raw.strip_prefix("/workspace/") {
        root.join(relative)
    } else if raw == "/workspace" {
        root.clone()
    } else {
        execution.join(raw)
    };
    let allowed = if path.starts_with(&execution) {
        execution
    } else {
        root.clone()
    };
    Ok((allowed, path))
}

pub(super) async fn attachment(
    Extension(app): Extension<App>,
    Query(location): Query<Location>,
    headers: HeaderMap,
) -> Result<Response> {
    file(app, location, headers, true).await
}

pub(super) async fn inline(
    Extension(app): Extension<App>,
    Query(location): Query<Location>,
    headers: HeaderMap,
) -> Result<Response> {
    file(app, location, headers, false).await
}

async fn file(
    app: App,
    location: Location,
    headers: HeaderMap,
    attachment: bool,
) -> Result<Response> {
    let (representation, headers) = tokio::task::spawn_blocking(move || -> Result<_> {
        let meta = metadata(&app, &location.scope, &headers)?;
        let raw = location
            .path
            .as_deref()
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .ok_or(WebError::invalid("缺少 path"))?;
        let (root, path) = locate(&app, &meta, raw)?;
        let file = open_download(
            &root,
            &app.data_dir,
            path.to_str().ok_or(WebError::invalid("无效路径"))?,
        )
        .map_err(|e| match e.kind() {
            ErrorKind::NotFound | ErrorKind::IsADirectory | ErrorKind::NotADirectory => {
                WebError(StatusCode::NOT_FOUND, "文件不存在")
            }
            _ => WebError::invalid("文件不可访问"),
        })?;
        let stat = file
            .metadata()
            .map_err(|_| WebError::invalid("无法读取文件信息"))?;
        let name = path
            .file_name()
            .unwrap_or_default()
            .to_string_lossy()
            .into_owned();
        let guessed = mime_guess::from_path(&name).first_raw();
        let mime = if attachment {
            guessed.unwrap_or("application/octet-stream")
        } else {
            preview_mime(&name, guessed)
        };
        let modified = stat.modified().ok();
        let tag = modified
            .and_then(|time| time.duration_since(std::time::UNIX_EPOCH).ok())
            .map(|elapsed| {
                use std::hash::{DefaultHasher, Hash, Hasher};
                let mut hash = DefaultHasher::new();
                path.hash(&mut hash);
                format!(
                    "\"{:x}-{:x}-{:x}\"",
                    hash.finish(),
                    stat.len(),
                    elapsed.as_nanos()
                )
            });
        Ok((
            Representation {
                payload: Payload::File(file),
                size: stat.len(),
                name,
                mime: mime.into(),
                attachment,
                modified,
                tag,
            },
            headers,
        ))
    })
    .await
    .map_err(|_| WebError::invalid("文件服务不可用"))??;
    representation.respond(&headers).await
}

fn preview_mime(name: &str, guessed: Option<&'static str>) -> &'static str {
    match guessed {
        Some("text/html" | "image/svg+xml" | "application/xhtml+xml") => "application/octet-stream",
        Some(mime)
            if mime.starts_with("text/")
                || mime.starts_with("image/")
                || mime.starts_with("audio/")
                || mime.starts_with("video/")
                || matches!(
                    mime,
                    "application/pdf"
                        | "application/json"
                        | "application/javascript"
                        | "application/xml"
                ) =>
        {
            mime
        }
        None if Path::new(name)
            .extension()
            .and_then(|e| e.to_str())
            .is_some_and(|ext| {
                matches!(
                    ext.to_ascii_lowercase().as_str(),
                    "md" | "markdown"
                        | "json5"
                        | "yaml"
                        | "yml"
                        | "ts"
                        | "jsx"
                        | "tsx"
                        | "vue"
                        | "py"
                        | "rb"
                        | "go"
                        | "rs"
                        | "java"
                        | "c"
                        | "h"
                        | "cpp"
                        | "hpp"
                        | "cs"
                        | "php"
                        | "swift"
                        | "kt"
                        | "sh"
                        | "bash"
                        | "zsh"
                        | "ps1"
                        | "bat"
                        | "cmd"
                        | "scss"
                        | "sass"
                        | "less"
                        | "toml"
                        | "ini"
                        | "cfg"
                        | "conf"
                        | "log"
                        | "sql"
                )
            }) =>
        {
            "text/plain; charset=utf-8"
        }
        _ => "application/octet-stream",
    }
}

pub(super) async fn media(
    Extension(app): Extension<App>,
    RoutePath(id): RoutePath<String>,
    Query(scope): Query<Scope>,
    headers: HeaderMap,
) -> Result<Response> {
    let (representation, headers) = tokio::task::spawn_blocking(move || -> Result<_> {
        metadata(&app, &scope, &headers)?;
        let missing = || WebError(StatusCode::NOT_FOUND, "媒体不存在");
        let store = BlobStore::open(&app.data_dir).ok_or_else(missing)?;
        let entry = store.entry(id.trim()).ok_or_else(missing)?;
        let bytes = store.registered_bytes(id.trim()).ok_or_else(missing)?;
        let mime = entry["mime_type"].as_str().unwrap_or("").trim();
        let kind = mime
            .split(';')
            .next()
            .unwrap_or("")
            .trim()
            .to_ascii_lowercase();
        let attachment = matches!(
            kind.as_str(),
            "text/html" | "image/svg+xml" | "application/xhtml+xml"
        );
        let mime =
            if attachment || mime.is_empty() || mime.parse::<axum::http::HeaderValue>().is_err() {
                "application/octet-stream"
            } else {
                mime
            };
        let name = entry["blob_rel_path"]
            .as_str()
            .and_then(|s| Path::new(s).file_name())
            .and_then(|s| s.to_str())
            .map(str::to_owned)
            .unwrap_or_else(|| id.replace(':', "_"));
        Ok((
            Representation {
                size: bytes.len() as u64,
                payload: Payload::Bytes(bytes.into()),
                name,
                mime: mime.into(),
                attachment,
                modified: None,
                tag: None,
            },
            headers,
        ))
    })
    .await
    .map_err(|_| WebError::invalid("媒体服务不可用"))??;
    representation.respond(&headers).await
}
