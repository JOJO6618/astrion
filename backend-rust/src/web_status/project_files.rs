use super::{App, Result, WebError, admin_policy, snapshot};
use crate::knowledge::storage::open_canonical_dir;
use axum::{Extension, Json, extract::Query, http::StatusCode};
use cap_fs_ext::DirExt;
use cap_std::fs::Dir;
use serde::Deserialize;
use serde_json::{Value, json};
use std::{io, path::Path};

pub(super) fn browse_policy(app: &App, tree: bool) -> Result<()> {
    let policy = admin_policy(&app.data_dir, &app.catalog.load()).map_err(|_| {
        WebError(
            StatusCode::SERVICE_UNAVAILABLE,
            "Host administrator policy is unavailable",
        )
    })?;
    let blocks = &policy["ui_blocks"];
    if blocks["block_file_manager"] == true || (tree && blocks["collapse_workspace"] == true) {
        return Err(WebError(
            StatusCode::FORBIDDEN,
            if tree {
                "文件浏览已被管理员禁用"
            } else {
                "文件管理已被管理员禁用"
            },
        ));
    }
    Ok(())
}

pub(super) async fn tree(Extension(app): Extension<App>) -> Result<Json<Value>> {
    snapshot(move || {
        browse_policy(&app, true)?;
        Ok(json!({"path":app.project_path,"files":[],"folders":[],"total_files":0,"total_size":0,"tree":{},"unavailable":true,"message":"宿主机模式下文件树不可用"}))
    }).await
}

#[derive(Deserialize, Default)]
pub(super) struct Search {
    q: Option<String>,
    limit: Option<i64>,
}

fn score(path: &str, q: &str) -> Option<u8> {
    if path.starts_with(q) {
        return Some(0);
    }
    let mut parts = path.split('/');
    if q.contains('/')
        && q.split('/')
            .all(|term| parts.next().is_some_and(|part| part.contains(term)))
    {
        return Some(1);
    }
    let name = path.rsplit('/').next()?;
    if name.starts_with(q) {
        Some(2)
    } else if name.contains(q) {
        Some(3)
    } else if path.contains(q) {
        Some(4)
    } else {
        None
    }
}

struct Match {
    rank: u8,
    length: usize,
    key: String,
    row: Value,
}
fn scan(dir: &Dir, prefix: &str, depth: usize, q: &str, found: &mut Vec<Match>) -> io::Result<()> {
    for entry in dir.entries()? {
        let entry = entry?;
        let name = entry.file_name().to_string_lossy().into_owned();
        let path = if prefix.is_empty() {
            name.clone()
        } else {
            format!("{prefix}/{name}")
        };
        let ty = entry.file_type()?;
        // Search lists link names but never descends through a directory link.
        let is_dir =
            ty.is_dir() || (ty.is_symlink() && dir.metadata(&name).is_ok_and(|m| m.is_dir()));
        let key = path.to_lowercase();
        let rank = if q.is_empty() {
            Some(u8::from(!is_dir))
        } else {
            score(&key, q)
        };
        if let Some(rank) = rank {
            let mut row =
                json!({"name":name,"path":path,"type":if is_dir {"directory"}else{"file"}});
            if !is_dir {
                row["extension"] = json!(extension(&name));
            }
            found.push(Match {
                rank,
                length: if q.is_empty() {
                    0
                } else {
                    path.chars().count()
                },
                key,
                row,
            });
        }
        if !q.is_empty() && depth < 6 && ty.is_dir() {
            if let Ok(child) = dir.open_dir_nofollow(&name) {
                scan(&child, &path, depth + 1, q, found)?;
            }
        }
    }
    Ok(())
}

pub(super) fn extension(name: &str) -> String {
    Path::new(name)
        .extension()
        .and_then(|ext| ext.to_str())
        .filter(|ext| !ext.is_empty())
        .map_or_else(String::new, |ext| format!(".{}", ext.to_lowercase()))
}

pub(super) async fn search(
    Extension(app): Extension<App>,
    Query(query): Query<Search>,
) -> Result<Json<Value>> {
    snapshot(move || {
        browse_policy(&app,true)?;
        let q = query.q.as_deref().unwrap_or("").trim().to_lowercase();
        let dir = open_canonical_dir(&app.project_path).map_err(|_| WebError::invalid("项目目录不可用"))?;
        let mut found = Vec::new();
        scan(&dir,"",0,&q,&mut found).map_err(|_| WebError(StatusCode::INTERNAL_SERVER_ERROR,"无法扫描项目目录"))?;
        found.sort_by(|a,b| (a.rank,a.length,&a.key).cmp(&(b.rank,b.length,&b.key)));
        let limit = if q.is_empty(){50}else{query.limit.unwrap_or(50).clamp(10,100) as usize};
        Ok(json!({"success":true,"data":{"total":found.len(),"items":found.into_iter().take(limit).map(|m|m.row).collect::<Vec<_>>()}}))
    }).await
}
