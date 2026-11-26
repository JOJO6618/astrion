use super::{
    App, Result, WebError,
    project_files::{browse_policy, extension},
    snapshot,
};
use crate::knowledge::storage::open_canonical_dir;
use axum::{
    Extension, Json,
    body::Body,
    extract::Query,
    http::{StatusCode, header},
    response::{IntoResponse, Response},
};
use cap_fs_ext::{FollowSymlinks, MetadataExt, OpenOptionsFollowExt};
use cap_std::fs::{Dir, File, OpenOptions};
use serde::Deserialize;
use serde_json::{Value, json};
use std::{
    ffi::OsStr,
    io::{Read, Write},
    path::{Path, PathBuf},
};
use tokio_util::io::ReaderStream;

const TEXT_LIMIT: usize = 2 * 1024 * 1024;
const EDITABLE: &[&str] = &[
    ".txt", ".md", ".py", ".js", ".ts", ".json", ".yaml", ".yml", ".html", ".css", ".scss",
    ".less", ".xml", ".csv", ".ini", ".cfg", ".toml", ".sh", ".bat", ".java", ".kt", ".go", ".rs",
    ".c", ".cpp", ".h", ".hpp", ".vue", ".svelte", ".php", ".rb", ".swift", ".dart", ".sql",
];
#[derive(Deserialize)]
pub(super) struct Location {
    path: Option<String>,
}
#[derive(Deserialize)]
pub(super) struct Edit {
    path: String,
    content: String,
}

fn invalid(_: impl std::fmt::Display) -> WebError {
    WebError::invalid("文件不存在或无法访问")
}
fn resolve(root: &Path, path: &str) -> Result<PathBuf> {
    let target = root.join(path.trim()).canonicalize().map_err(invalid)?;
    if !target.starts_with(root) {
        return Err(WebError::invalid("路径超出工作区"));
    }
    Ok(target)
}
fn parent(target: &Path) -> Result<(Dir, &OsStr)> {
    let dir =
        open_canonical_dir(target.parent().ok_or_else(|| invalid("parent"))?).map_err(invalid)?;
    Ok((dir, target.file_name().ok_or_else(|| invalid("name"))?))
}
fn open_file(target: &Path, write: bool) -> Result<File> {
    let (dir, name) = parent(target)?;
    let mut options = OpenOptions::new();
    options.read(!write).write(write).follow(FollowSymlinks::No);
    #[cfg(unix)]
    {
        use cap_std::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NONBLOCK);
    }
    let file = dir.open_with(name, &options).map_err(invalid)?;
    let metadata = file.metadata().map_err(invalid)?;
    if !metadata.is_file() || metadata.nlink() != 1 {
        return Err(WebError::invalid("目标不是普通文件"));
    }
    Ok(file)
}
fn local_time(metadata: &cap_std::fs::Metadata) -> Result<String> {
    let time = jiff::Timestamp::try_from(metadata.modified().map_err(invalid)?.into_std())
        .map_err(invalid)?;
    Ok(time
        .to_zoned(jiff::tz::TimeZone::system())
        .strftime("%Y-%m-%dT%H:%M:%S%.6f")
        .to_string())
}
fn editable(target: &Path, size: u64, ext: &str) -> bool {
    if size > TEXT_LIMIT as u64 {
        return false;
    }
    if !ext.is_empty() {
        return EDITABLE.contains(&ext);
    }
    let Ok(file) = open_file(target, false) else {
        return false;
    };
    let mut bytes = Vec::new();
    file.take(8192).read_to_end(&mut bytes).is_ok()
        && match std::str::from_utf8(&bytes) {
            Ok(_) => true,
            Err(e) => e.error_len().is_none() && size > 8192,
        }
}

pub(super) async fn entries(
    Extension(app): Extension<App>,
    Query(query): Query<Location>,
) -> Result<Json<Value>> {
    snapshot(move || {
        browse_policy(&app,false)?;
        let root=&app.project_path;
        let target=resolve(root,query.path.as_deref().unwrap_or(""))?;
        let dir=open_canonical_dir(&target).map_err(invalid)?;
        let relative=target.strip_prefix(root).map_err(invalid)?;
        let mut crumbs=vec![json!({"name":"根目录","path":""})];
        let mut path=PathBuf::new();
        for component in relative {path.push(component);crumbs.push(json!({"name":component.to_string_lossy(),"path":path.to_string_lossy().replace('\\',"/")}));}
        let mut items=Vec::new();
        for entry in dir.entries().map_err(invalid)? {
            let entry=entry.map_err(invalid)?;
            let name=entry.file_name().to_string_lossy().into_owned();
            let rel=relative.join(&name);
            let link_path = target.join(&name);
            let actual = resolve(root, &rel.to_string_lossy()).unwrap_or(link_path);
            let metadata = std::fs::symlink_metadata(&actual).map_err(invalid)?;
            let is_dir=metadata.is_dir();
            let ext=extension(&name);
            let modified=metadata.modified().map_err(invalid)?.duration_since(std::time::UNIX_EPOCH).map(|d|d.as_secs_f64()).unwrap_or(0.0);
            let row=json!({"name":name,"path":rel.to_string_lossy().replace('\\',"/"),"type":if is_dir{"directory"}else{"file"},"size":metadata.len(),"modified_at":modified,"extension":if is_dir{None}else{Some(&ext)},"is_editable":metadata.is_file() && editable(&actual, metadata.len(),&ext)});
            items.push((!is_dir,name.to_lowercase(),row));
        }
        items.sort_by(|a,b|(a.0,&a.1).cmp(&(b.0,&b.1)));
        Ok(json!({"success":true,"data":{"path":relative.to_string_lossy().replace('\\',"/"),"breadcrumb":crumbs,"items":items.into_iter().map(|row|row.2).collect::<Vec<_>>()}}))
    }).await
}

pub(super) async fn text(
    Extension(app): Extension<App>,
    Query(query): Query<Location>,
) -> Result<Json<Value>> {
    snapshot(move || {
        let path = query
            .path
            .filter(|s| !s.is_empty())
            .ok_or_else(|| WebError::invalid("缺少 path"))?;
        let file = open_file(&resolve(&app.project_path, &path)?, false)?;
        let meta = file.metadata().map_err(invalid)?;
        if meta.len() > TEXT_LIMIT as u64 {
            return Err(WebError::invalid("文件超过 2 MiB"));
        }
        let mut bytes = Vec::new();
        file.take(TEXT_LIMIT as u64 + 1)
            .read_to_end(&mut bytes)
            .map_err(invalid)?;
        if bytes.len() > TEXT_LIMIT {
            return Err(WebError::invalid("文件超过 2 MiB"));
        }
        let content = String::from_utf8(bytes)
            .map_err(|_| WebError::invalid("文件不是 UTF-8 文本"))?
            .replace("\r\n", "\n")
            .replace('\r', "\n");
        Ok(json!({"success":true,"path":path,"content":content,"modified_at":local_time(&meta)?}))
    })
    .await
}

pub(super) async fn save(
    Extension(app): Extension<App>,
    payload: std::result::Result<Json<Edit>, axum::extract::rejection::JsonRejection>,
) -> Result<Json<Value>> {
    let Json(edit) = payload.map_err(|_| WebError::invalid("缺少 path 或 content"))?;
    snapshot(move || {
        if edit.content.len()>TEXT_LIMIT {return Err(WebError::invalid("文件超过 2 MiB"))}
        let target=resolve(&app.project_path,&edit.path)?;
        let checked=open_file(&target,true)?;
        let original=checked.metadata().map_err(invalid)?;
        let (dir,name)=parent(&target)?;
        let tmp=format!(".astrion-edit-{}",uuid::Uuid::new_v4());
        let mut options=OpenOptions::new();options.write(true).create_new(true).follow(FollowSymlinks::No);
        #[cfg(unix)] {use cap_std::fs::OpenOptionsExt;options.mode(0o600);}
        let mut file=dir.open_with(&tmp,&options).map_err(invalid)?;
        let written=(|| {
            file.write_all(edit.content.as_bytes()).map_err(invalid)?;
            file.set_permissions(original.permissions()).map_err(invalid)?;
            file.sync_all().map_err(invalid)?;
            let meta=file.metadata().map_err(invalid)?;
            drop(file);
            let current=dir.symlink_metadata(name).map_err(invalid)?;
            if current.dev()!=original.dev() || current.ino()!=original.ino(){return Err(WebError(StatusCode::CONFLICT,"文件已改变"))}
            drop(checked);
            dir.rename(&tmp,&dir,name).map_err(invalid)?;
            Ok(json!({"success":true,"data":{"path":target.strip_prefix(&app.project_path).map_err(invalid)?.to_string_lossy().replace('\\',"/"),"size":meta.len().to_string(),"modified_at":local_time(&meta)?}}))
        })();
        if written.is_err(){let _=dir.remove_file(&tmp);}
        written
    }).await
}

pub(super) async fn download(
    Extension(app): Extension<App>,
    Query(query): Query<Location>,
) -> Result<Response> {
    let (file, name) = tokio::task::spawn_blocking(move || {
        let path = query
            .path
            .filter(|s| !s.is_empty())
            .ok_or_else(|| WebError::invalid("缺少 path"))?;
        let target = resolve(&app.project_path, &path)?;
        if target.is_dir() {
            return Err(WebError(StatusCode::GONE, "文件夹打包下载功能已下线"));
        }
        Ok((
            open_file(&target, false)?.into_std(),
            target
                .file_name()
                .unwrap_or_default()
                .to_string_lossy()
                .into_owned(),
        ))
    })
    .await
    .map_err(invalid)??;
    let encoded = percent_encoding::utf8_percent_encode(&name, percent_encoding::NON_ALPHANUMERIC);
    let ascii: String = name
        .chars()
        .filter(|c| c.is_ascii() && !c.is_ascii_control() && !matches!(c, '"' | '\\'))
        .collect();
    let disposition = format!("attachment; filename=\"{ascii}\"; filename*=UTF-8''{encoded}");
    let mime = mime_guess::from_path(&name)
        .first_or_octet_stream()
        .to_string();
    let len = file.metadata().map_err(invalid)?.len().to_string();
    Ok((
        [
            (header::CONTENT_TYPE, mime),
            (header::CONTENT_DISPOSITION, disposition),
            (header::CONTENT_LENGTH, len),
        ],
        Body::from_stream(ReaderStream::new(tokio::fs::File::from_std(file))),
    )
        .into_response())
}
