//! The Web history endpoint reads committed messages even while a task is streaming.

use std::{collections::HashSet, path::Path};

use axum::{
    Extension, Json, Router,
    extract::{Path as RoutePath, RawQuery, State},
    http::StatusCode,
    routing::get,
};
use serde_json::{Value, json};

use crate::{workspace_catalog::RegisteredWorkspace, worktree::Worktree};

use super::{Runtime, RuntimeError, goals::verify_conversation_root, verify_workspace_binding};

type Reply = (StatusCode, Json<Value>);

/// Mount in the authenticated Web router after workspace selection.
pub fn router(runtime: Runtime) -> Router {
    Router::new()
        .route("/api/conversations/{id}/messages", get(messages))
        .with_state(runtime)
}

async fn messages(
    State(runtime): State<Runtime>,
    Extension(workspace): Extension<RegisteredWorkspace>,
    RoutePath(id): RoutePath<String>,
    RawQuery(query): RawQuery,
    preview: Option<Extension<crate::preview::Preview>>,
    headers: axum::http::HeaderMap,
) -> Reply {
    let scope = workspace.clone();
    let result = tokio::task::spawn_blocking(move || {
        let arguments = serde_urlencoded::from_str::<Vec<(String, String)>>(
            query.as_deref().unwrap_or_default(),
        )
        .unwrap_or_default();
        let limit = arguments
            .iter()
            .find(|(name, _)| name == "limit")
            .and_then(|(_, value)| parse_limit(value));
        read(&runtime, &workspace, &id, limit)
    })
    .await;
    match result {
        Ok(Ok((status, Json(mut payload)))) => {
            if status == StatusCode::OK && let Some(Extension(preview)) = preview
                && let Some(id) = payload["data"]["conversation_id"].as_str() {
                let (base, token) = preview.runtime_for(&headers, &scope, id).await;
                payload["data"]["preview_base"] = json!(base);
                payload["data"]["preview_token"] = json!(token);
            }
            (status, Json(payload))
        }
        Ok(Err(error)) if matches!(error.status, 400 | 404) => not_found(),
        Ok(Err(error)) => failed(error),
        Err(_) => failed(RuntimeError::new(500, "Runtime operation interrupted")),
    }
}

fn read(
    runtime: &Runtime,
    workspace: &RegisteredWorkspace,
    requested: &str,
    limit: Option<(bool, usize)>,
) -> Result<Reply, RuntimeError> {
    let normalized = requested.trim();
    let id = if normalized.starts_with("conv_") {
        normalized.to_owned()
    } else {
        format!("conv_{normalized}")
    };
    let document = {
        let store = runtime.store()?;
        verify_workspace_binding(&store, &workspace.id, Some(&workspace.path))?;
        let document = store.read(&workspace.id, &id)?;
        verify_conversation_root(&workspace.path, document.raw())
            .map_err(|_| RuntimeError::session_not_found())?;
        document
    };
    // Python's workspace preflight resolves a normalized ID, but its history lookup
    // takes the literal route parameter. Only the stored ID can return messages.
    if requested != id
        || document
            .raw()
            .get("token_statistics")
            .is_some_and(|stats| !stats.is_object())
    {
        let (status, Json(mut response)) = not_found();
        response["message"] = json!(format!("对话 {requested} 不存在"));
        return Ok((status, Json(response)));
    }
    let metadata = &document.raw()["metadata"];
    let execution_root =
        if metadata["git_worktree_enabled"] == true && !metadata["git_worktree"].is_null() {
            Worktree::new(&workspace.path)
                .and_then(|worktree| worktree.execution_root(metadata))
                .ok()
        } else {
            Some(workspace.path.clone())
        };
    let exists = |path: &str| {
        execution_root
            .as_deref()
            .is_some_and(|root| existing_file(&workspace.path, root, path))
    };
    let edited = &metadata["edited_files"];
    let edited_files = project_edited_files(edited, &exists);
    let preview_targets = project_preview_targets(&metadata["preview_targets"], &|path| {
        // The legacy endpoint only has a file validator when edited_files is a list.
        edited.is_array() && exists(path)
    });

    // Reuse the stored display history and its media references and message IDs.
    // Bootstrap adds the current task projection separately, at its event cursor.
    let mut messages = document.public_messages();
    let total_count = messages.len();
    if let Some((negative, count)) = limit {
        let first = if negative {
            count.min(total_count)
        } else {
            total_count.saturating_sub(count)
        };
        messages.drain(..first);
    }
    Ok((
        StatusCode::OK,
        Json(json!({"success": true, "data": {
            "conversation_id": id, "messages": messages, "total_count": total_count,
            "edited_files": edited_files, "preview_targets": preview_targets,
            "preview_base": null, "preview_token": null,
        }})),
    ))
}

fn not_found() -> Reply {
    (
        StatusCode::NOT_FOUND,
        Json(json!({"success": false, "error": "Conversation not found"})),
    )
}

fn failed(error: RuntimeError) -> Reply {
    if error.status==409 && error.message==crate::conversation_db::MIGRATION_REQUIRED{return (StatusCode::CONFLICT,Json(crate::conversation_db::migration_required_payload()));}
    (
        StatusCode::from_u16(error.status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR),
        Json(
            json!({"success": false, "error": error.message, "message": "获取对话消息时发生异常"}),
        ),
    )
}

// Werkzeug's integer conversion uses the first value, then Python slices at -limit.
// Saturate to the platform's largest possible message count before slicing.
fn parse_limit(value: &str) -> Option<(bool, usize)> {
    let value = value.trim();
    let digits = value.strip_prefix(['-', '+']).unwrap_or(value);
    let mut count = 0usize;
    let mut digits_read = 0;
    for group in digits.split('_') {
        if group.is_empty() {
            return None;
        }
        for digit in group.bytes() {
            if !digit.is_ascii_digit() {
                return None;
            }
            count = count
                .saturating_mul(10)
                .saturating_add((digit - b'0') as usize);
            digits_read += 1;
        }
    }
    (count != 0 && digits_read <= 4300).then_some((value.starts_with('-'), count))
}

fn existing_file(workspace: &Path, execution_root: &Path, path: &str) -> bool {
    let path = if let Some(relative) = path.strip_prefix("/workspace/") {
        workspace.join(relative)
    } else {
        execution_root.join(path)
    };
    path.canonicalize().is_ok_and(|path| {
        (path.starts_with(workspace) || path.starts_with(execution_root)) && path.is_file()
    })
}

pub(crate) fn project_edited_files(value: &Value, exists: &impl Fn(&str) -> bool) -> Vec<Value> {
    entries(value)
        .filter_map(|entry| {
            let path = entry["path"].as_str()?.trim();
            (!path.is_empty() && exists(path)).then(|| {
                json!({
                    "path": path,
                    "op": entry.get("op").filter(|value| truthy(value)).unwrap_or(&json!("edit")),
                    "ts": entry.get("ts").filter(|value| truthy(value)).unwrap_or(&json!("")),
                })
            })
        })
        .collect()
}

pub(crate) fn project_preview_targets(value: &Value, exists: &impl Fn(&str) -> bool) -> Vec<Value> {
    let mut seen = HashSet::new();
    entries(value)
        .filter_map(|item| {
            if item["type"] == "server" {
                let (url, origin, label) = normalize_server(item["url"].as_str()?)?;
                if !seen.insert(("server", url.clone())) {
                    return None;
                }
                let mut item = item.clone();
                item["url"] = json!(url);
                item["origin"] = json!(origin);
                item["label"] = json!(label);
                return Some(item);
            }
            let path = item["path"].as_str()?;
            if path.is_empty() || path.ends_with(':') || !seen.insert(("file", path.to_owned())) {
                return None;
            }
            if item["type"] == "file" && (path.trim().is_empty() || !exists(path.trim())) {
                return None;
            }
            Some(item.clone())
        })
        .collect()
}

fn normalize_server(raw: &str) -> Option<(String, String, String)> {
    let lower = raw.to_ascii_lowercase();
    // Find the same first loopback URL prefix as the Python history normalizer.
    // Keep the explicit port, casing and query because they also identify targets.
    let (start, host_start, host) = lower.match_indices("http").find_map(|(start, _)| {
        let after_scheme = lower[start..]
            .strip_prefix("http://")
            .or_else(|| lower[start..].strip_prefix("https://"))?;
        let host = ["localhost", "127.0.0.1", "0.0.0.0", "[::1]"]
            .into_iter()
            .find(|host| after_scheme.starts_with(host))?;
        Some((start, lower.len() - after_scheme.len(), host))
    })?;
    let host_end = host_start + host.len();
    let rest = &raw[host_end..];
    let port = rest
        .strip_prefix(':')
        .map(|tail| tail.bytes().take(5).take_while(u8::is_ascii_digit).count())
        .filter(|count| *count > 0)
        .map(|count| &rest[1..=count]);
    let rest = &rest[port.map_or(0, |port| port.len() + 1)..];
    let path = rest.strip_prefix('/').map_or("", |tail| {
        let count = tail
            .bytes()
            .take_while(|byte| byte.is_ascii_alphanumeric() || b"-._~%/@?#=&+".contains(byte))
            .count();
        &rest[..=count]
    });
    let path = path
        .trim_end_matches(['.', ',', ';', ':', '!', '?'])
        .trim_end_matches('/');
    if (port.is_none() && path.is_empty()) || path.to_ascii_lowercase().ends_with("/favicon.ico") {
        return None;
    }
    let mut origin = if host == "0.0.0.0" {
        format!("{}127.0.0.1", &raw[start..host_start])
    } else {
        raw[start..host_end].to_owned()
    };
    if let Some(port) = port {
        origin.push(':');
        origin.push_str(port);
    }
    let label_path = if path.len() > 24 {
        format!("{}…", &path[..23])
    } else {
        path.to_owned()
    };
    Some((
        format!("{origin}{path}"),
        origin,
        format!(":{}{label_path}", port.unwrap_or("80")),
    ))
}

fn entries(value: &Value) -> impl Iterator<Item = &Value> {
    value
        .as_array()
        .into_iter()
        .flatten()
        .filter(|item| item.is_object())
}

fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::Number(value) => value.as_f64() != Some(0.0),
        Value::String(value) => !value.is_empty(),
        Value::Array(value) => !value.is_empty(),
        Value::Object(value) => !value.is_empty(),
    }
}
