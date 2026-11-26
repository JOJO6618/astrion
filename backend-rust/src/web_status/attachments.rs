//! Authenticated workspace uploads and legacy attachment URLs.
mod quarantine;

use std::{
    collections::VecDeque,
    io::{self, ErrorKind},
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};

use axum::{
    Extension, Json, Router,
    extract::{
        DefaultBodyLimit, Multipart, Path as RoutePath, State, multipart::MultipartRejection,
    },
    http::{HeaderMap, StatusCode, header},
    response::{IntoResponse, Response},
    routing::{get, post},
};
use serde_json::{Value, json};

use crate::{
    catalog::CatalogSource,
    conversation_transfer::{assets, sha256},
    knowledge::storage::{open_canonical_dir, publish},
    runtime::Runtime,
    tools::read_file::open_download,
    workspace_catalog::RegisteredWorkspace,
};

use super::file_response::{Payload, Representation};

const MAX_BYTES: usize = 50 * 1024 * 1024;
const UPLOADS: &str = ".astrion/user_upload";
const EXTENSIONS: &str = concat!(
    ".txt,.md,.rst,.py,.js,.ts,.json,.yml,.yaml,.ini,.cfg,.conf,.ipynb",
    ",.csv,.tsv,.log,.mdx,.env,.sh,.bat,.ps1,.sql,.html,.css,",
    ".svg,.png,.jpg,.jpeg,.gif,.bmp,.webp,.ico,.pdf,.pptx,.docx,",
    ".xlsx,.mp3,.wav,.flac,.mp4,.mov,.mkv,.avi,.webm,",
    ".zip,.tar,.tar.gz,.tar.bz2,.tgz,.tbz,.gz,.bz2,.xz,.7z,.com",
);

struct Attachments {
    runtime: Runtime,
    catalog: CatalogSource,
    data: PathBuf,
    logs: PathBuf,
    limits: Mutex<VecDeque<Instant>>,
    extensions: Vec<String>,
    scanner: quarantine::Scanner,
}

pub fn attachment_router(
    runtime: Runtime,
    catalog: CatalogSource,
    data: PathBuf,
    logs: PathBuf,
) -> Router {
    let extensions =
        std::env::var("UPLOAD_ALLOWED_EXTENSIONS").unwrap_or_else(|_| EXTENSIONS.into());
    let extensions = extensions
        .split(',')
        .map(|value| value.trim().to_ascii_lowercase())
        .filter(|value| !value.is_empty())
        .map(|mut value| {
            if !value.starts_with('.') {
                value.insert(0, '.');
            }
            value
        })
        .collect();
    Router::new()
        .route(
            "/api/upload",
            post(upload).layer(DefaultBodyLimit::max(MAX_BYTES + 64 * 1024)),
        )
        .route("/user_upload/{*filename}", get(user_upload))
        .route("/workspace/{*filename}", get(workspace_image))
        .with_state(Arc::new(Attachments {
            runtime,
            catalog,
            data,
            logs,
            limits: Mutex::default(),
            extensions,
            scanner: quarantine::Scanner::from_env(),
        }))
}

type App = Arc<Attachments>;
type Result<T> = std::result::Result<T, UploadError>;

struct UploadError {
    status: StatusCode,
    code: &'static str,
    message: &'static str,
}

impl UploadError {
    fn new(status: StatusCode, code: &'static str, message: &'static str) -> Self {
        Self {
            status,
            code,
            message,
        }
    }

    fn bad(code: &'static str, message: &'static str) -> Self {
        Self::new(StatusCode::BAD_REQUEST, code, message)
    }

    fn storage() -> Self {
        Self::new(
            StatusCode::INTERNAL_SERVER_ERROR,
            "upload_io_error",
            "文件保存失败",
        )
    }

    fn large() -> Self {
        Self::new(
            StatusCode::PAYLOAD_TOO_LARGE,
            "size_exceeded",
            "文件超过 50 MB 上传限制",
        )
    }
}

impl IntoResponse for UploadError {
    fn into_response(self) -> Response {
        (
            self.status,
            Json(json!({"success":false,"code":self.code,"error":self.message})),
        )
            .into_response()
    }
}

struct Incoming {
    name: String,
    bytes: Vec<u8>,
}

async fn upload(
    State(app): State<App>,
    workspace: Option<Extension<RegisteredWorkspace>>,
    multipart: std::result::Result<Multipart, MultipartRejection>,
) -> Result<Json<Value>> {
    let workspace = get_workspace(workspace)?;
    app.verify(&workspace)?;
    let policy =
        super::admin_policy(&app.data, &app.catalog.load()).map_err(|_| UploadError::storage())?;
    if policy["ui_blocks"]["block_upload"] == true {
        return Err(UploadError::new(
            StatusCode::FORBIDDEN,
            "upload_disabled",
            "管理员已禁用文件上传",
        ));
    }
    app.admit()?;
    let incoming = read_upload(multipart).await?;
    let lowercase = incoming.name.to_ascii_lowercase();
    if !app.extensions.is_empty()
        && !app
            .extensions
            .iter()
            .any(|suffix| lowercase.ends_with(suffix))
    {
        app.audit(json!({"accepted":false,"workspace_id":workspace.id,"name":incoming.name,"code":"extension_forbidden"}));
        return Err(UploadError::bad(
            "extension_forbidden",
            "不允许上传此文件类型",
        ));
    }
    let hash = sha256(&incoming.bytes);
    let size = incoming.bytes.len();
    let report = app
        .scanner
        .scan(&app.data, &workspace.path, &incoming.bytes)
        .await;
    if let Err(error) = &report {
        app.audit(
            json!({"accepted":false,"workspace_id":workspace.id,"name":incoming.name,
            "size":size,"sha256":hash,"code":error.code}),
        );
    }
    let report = report?;
    let app_copy = app.clone();
    let workspace_id = workspace.id.clone();
    let final_name = tokio::task::spawn_blocking(move || {
        app_copy.verify(&workspace)?;
        save_upload(&workspace.path, incoming)
    })
    .await
    .map_err(|_| UploadError::storage())??;
    let path = format!("{UPLOADS}/{final_name}");
    app.audit(
        json!({"accepted":true,"workspace_id":workspace_id,"target_relative":path,
        "sha256":hash,"size":size,"scan":report}),
    );
    Ok(Json(
        json!({"success":true,"path":path,"filename":final_name,"folder":UPLOADS,
        "scan":report,"sha256":hash,"size":size}),
    ))
}

impl Attachments {
    fn verify(&self, workspace: &RegisteredWorkspace) -> Result<()> {
        let bound = self
            .runtime
            .bound_workspace(&workspace.id)
            .map_err(|_| UploadError::storage())?;
        if bound.as_deref() != Some(workspace.path.as_path())
            || workspace.path.canonicalize().ok().as_ref() != Some(&workspace.path)
        {
            return Err(missing());
        }
        Ok(())
    }

    fn admit(&self) -> Result<()> {
        let mut seen = self.limits.lock().map_err(|_| UploadError::storage())?;
        let now = Instant::now();
        while seen
            .front()
            .is_some_and(|stamp| now.duration_since(*stamp) >= Duration::from_secs(300))
        {
            seen.pop_front();
        }
        if seen.len() >= 20 {
            return Err(UploadError::new(
                StatusCode::TOO_MANY_REQUESTS,
                "rate_limited",
                "上传过于频繁，请稍后重试",
            ));
        }
        seen.push_back(now);
        Ok(())
    }

    fn audit(&self, mut value: Value) {
        use cap_fs_ext::{FollowSymlinks, MetadataExt, OpenOptionsFollowExt};
        use std::io::Write;
        let result = (|| -> io::Result<()> {
            let path = self.logs.canonicalize()?;
            let root = open_canonical_dir(&path)?;
            let log = assets::subdir(&root, "upload_guard", true)?;
            let mut options = cap_std::fs::OpenOptions::new();
            options.append(true).create(true).follow(FollowSymlinks::No);
            #[cfg(unix)]
            {
                use cap_std::fs::OpenOptionsExt;
                options.mode(0o600).custom_flags(libc::O_NONBLOCK);
            }
            let mut file = log.open_with("host.log", &options)?;
            let stat = file.metadata()?;
            if !stat.is_file() || stat.nlink() != 1 {
                return Ok(());
            }
            value["timestamp"] = json!(jiff::Timestamp::now().to_string());
            let mut line = serde_json::to_vec(&value)?;
            line.push(b'\n');
            file.write_all(&line)
        })();
        let _ = result;
    }
}

async fn read_upload(form: std::result::Result<Multipart, MultipartRejection>) -> Result<Incoming> {
    let mut multipart = form.map_err(|_| UploadError::bad("missing_file", "请选择要上传的文件"))?;
    let mut filename = None;
    let mut content = None;
    while let Some(mut field) = multipart.next_field().await.map_err(multipart_error)? {
        match field.name() {
            Some("filename") if filename.is_none() => {
                let mut bytes = Vec::new();
                while let Some(chunk) = field.chunk().await.map_err(multipart_error)? {
                    if chunk.len() > 4096_usize.saturating_sub(bytes.len()) {
                        return Err(UploadError::bad("invalid_filename", "无效的文件名"));
                    }
                    bytes.extend_from_slice(&chunk);
                }
                filename = Some(
                    String::from_utf8(bytes)
                        .map_err(|_| UploadError::bad("invalid_filename", "无效的文件名"))?,
                );
            }
            Some("file") if content.is_none() => {
                let name = field.file_name().unwrap_or_default().to_owned();
                let mime = field.content_type().map(str::to_owned);
                let mut bytes = Vec::new();
                while let Some(chunk) = field.chunk().await.map_err(multipart_error)? {
                    if chunk.len() > MAX_BYTES.saturating_sub(bytes.len()) {
                        return Err(UploadError::large());
                    }
                    bytes.extend_from_slice(&chunk);
                }
                content = Some((name, mime, bytes));
            }
            Some("filename" | "file") => {
                return Err(UploadError::bad("invalid_upload", "上传请求含重复字段"));
            }
            _ => (),
        }
    }
    let (fallback, mime, bytes) =
        content.ok_or_else(|| UploadError::bad("missing_file", "请选择要上传的文件"))?;
    let explicit = filename
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .unwrap_or(&fallback);
    let name = if explicit.trim().is_empty() {
        mime.as_deref()
            .and_then(mime_guess::get_mime_extensions_str)
            .and_then(|items| items.first())
            .map(|ext| format!("upload_{}.{ext}", jiff::Timestamp::now().as_second()))
            .ok_or_else(|| UploadError::bad("invalid_filename", "无效的文件名"))?
    } else {
        sanitize(explicit)
    };
    if name.is_empty() || assets::components(&name).is_err() {
        return Err(UploadError::bad("invalid_filename", "无效的文件名"));
    }
    Ok(Incoming { name, bytes })
}

fn multipart_error(error: axum::extract::multipart::MultipartError) -> UploadError {
    if error.status() == StatusCode::PAYLOAD_TOO_LARGE {
        UploadError::large()
    } else {
        UploadError::bad("invalid_upload", "上传请求不完整")
    }
}

fn sanitize(name: &str) -> String {
    let leaf = name.trim().rsplit(['/', '\\']).next().unwrap_or_default();
    leaf.chars()
        .filter(|value| *value != '\0')
        .map(|value| {
            if value.is_control() || "<>:\"|?*".contains(value) {
                '_'
            } else {
                value
            }
        })
        .collect::<String>()
        .trim_matches(['.', ' '])
        .to_owned()
}

fn save_upload(root: &Path, incoming: Incoming) -> Result<String> {
    let root = open_canonical_dir(root).map_err(|_| UploadError::storage())?;
    let directory = assets::subdir(&root, UPLOADS, true).map_err(|_| UploadError::storage())?;
    let (stem, suffix) = incoming
        .name
        .rsplit_once('.')
        .map_or((incoming.name.as_str(), String::new()), |(stem, suffix)| {
            (stem, format!(".{suffix}"))
        });
    if suffix.len() >= 240 {
        return Err(UploadError::bad("invalid_filename", "无效的文件名"));
    }
    for sequence in 0..10_000_u32 {
        let suffix = if sequence == 0 {
            suffix.clone()
        } else {
            format!("_{sequence}{suffix}")
        };
        let max_stem = 255_usize.saturating_sub(suffix.len());
        let end = stem
            .char_indices()
            .map(|(index, _)| index)
            .chain([stem.len()])
            .take_while(|index| *index <= max_stem)
            .last()
            .unwrap_or_default();
        let name = format!("{}{suffix}", &stem[..end]);
        match publish(&directory, &name, &incoming.bytes, false) {
            Ok(()) => return Ok(name),
            Err(error) if error.kind() == ErrorKind::AlreadyExists => (),
            Err(_) => return Err(UploadError::storage()),
        }
    }
    Err(UploadError::storage())
}

async fn user_upload(
    State(app): State<App>,
    workspace: Option<Extension<RegisteredWorkspace>>,
    RoutePath(filename): RoutePath<String>,
    headers: HeaderMap,
) -> Result<Response> {
    serve(app, get_workspace(workspace)?, filename, headers, false).await
}

async fn workspace_image(
    State(app): State<App>,
    workspace: Option<Extension<RegisteredWorkspace>>,
    RoutePath(filename): RoutePath<String>,
    headers: HeaderMap,
) -> Result<Response> {
    serve(app, get_workspace(workspace)?, filename, headers, true).await
}

async fn serve(
    app: App,
    workspace: RegisteredWorkspace,
    filename: String,
    headers: HeaderMap,
    images_only: bool,
) -> Result<Response> {
    let file = tokio::task::spawn_blocking(move || {
        app.verify(&workspace)?;
        if assets::components(&filename).is_err() {
            return Err(UploadError::new(
                StatusCode::FORBIDDEN,
                "invalid_attachment_path",
                "无权访问该文件",
            ));
        }
        let mime = mime_guess::from_path(&filename)
            .first_raw()
            .unwrap_or("application/octet-stream");
        let path = if images_only {
            filename.clone()
        } else {
            format!("{UPLOADS}/{filename}")
        };
        let file = open_download(&workspace.path, &app.data, &path).map_err(|error| {
            if error.kind() == ErrorKind::PermissionDenied {
                UploadError::new(
                    StatusCode::FORBIDDEN,
                    "invalid_attachment_path",
                    "无权访问该文件",
                )
            } else {
                missing()
            }
        })?;
        if images_only && !mime.starts_with("image/") {
            return Err(UploadError::new(
                StatusCode::UNSUPPORTED_MEDIA_TYPE,
                "unsupported_image",
                "该路径仅用于访问图片",
            ));
        }
        let stat = file.metadata().map_err(|_| missing())?;
        let name = filename.rsplit('/').next().unwrap_or_default().to_owned();
        let attachment = !images_only
            && matches!(
                mime,
                "text/html" | "application/xhtml+xml" | "application/javascript"
            );
        let mime = if attachment {
            "application/octet-stream"
        } else {
            mime
        };
        Ok(Representation {
            payload: Payload::File(file),
            size: stat.len(),
            name,
            mime: mime.into(),
            attachment,
            modified: stat.modified().ok(),
            tag: None,
        })
    })
    .await
    .map_err(|_| UploadError::storage())??;
    let mut response = file.respond(&headers).await.map_err(|_| missing())?;
    response.headers_mut().insert(
        header::CACHE_CONTROL,
        axum::http::HeaderValue::from_static("private, no-cache"),
    );
    Ok(response)
}

fn get_workspace(workspace: Option<Extension<RegisteredWorkspace>>) -> Result<RegisteredWorkspace> {
    workspace
        .map(|Extension(workspace)| workspace)
        .ok_or_else(missing)
}

fn missing() -> UploadError {
    UploadError::new(
        StatusCode::NOT_FOUND,
        "attachment_missing",
        "附件或工作区不存在",
    )
}
