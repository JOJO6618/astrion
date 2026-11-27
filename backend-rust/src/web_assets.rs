//! Host Web entry pages and assets rooted in a deployment's explicit `static` directory.

mod manifest;
pub mod personalization;
mod static_file;

use crate::web_auth::BrowserSession;
use axum::{
    Extension, Router,
    extract::{Path as RoutePath, State},
    http::{HeaderValue, Method, StatusCode, header},
    middleware::{self, Next},
    response::{IntoResponse, Response},
    routing::{MethodRouter, any, get},
};
use cap_fs_ext::{DirExt, FollowSymlinks, OpenOptionsFollowExt};
use cap_std::{
    ambient_authority,
    fs::{Dir, File, OpenOptions},
};
use manifest::ManifestCache;
use std::{
    io::{self, Read},
    path::{Path, PathBuf},
    sync::Arc,
};

/// The effective `block_realtime_terminal` setting, provided by the Host policy middleware.
#[derive(Clone, Copy)]
pub enum TerminalPolicy {
    Allowed,
    Blocked,
}

#[derive(Clone)]
pub struct WebAssets(Arc<AssetRoot>);

impl WebAssets {
    /// `static_root` is a trusted deployment directory containing the HTML and `dist/`.
    pub fn new(static_root: impl AsRef<Path>) -> io::Result<Self> {
        let root = Dir::open_ambient_dir(static_root, ambient_authority())?;
        Ok(Self(Arc::new(AssetRoot {
            root: Arc::new(root),
            manifest: Arc::default(),
            host_data: None,
        })))
    }

    /// Load localized server messages using the same user preferences as `/api/personalization`.
    pub fn with_host_data(mut self, data: impl AsRef<Path>) -> Self {
        Arc::make_mut(&mut self.0).host_data = Some(data.as_ref().to_owned());
        self
    }

    /// Layer `WebAuth::with_browser_session` around this router to supply verified identity.
    pub fn router(&self) -> Router {
        Router::new()
            .route("/", page(Page::Root))
            .route("/new", page(Page::Main))
            .route("/chat", page(Page::Main))
            .route("/chat/new", page(Page::Main))
            .route("/chat/{conversation_id}", any(conversation))
            .route("/{conversation_id}", any(conversation))
            .route("/settings", page(Page::Main))
            .route("/settings/{*section}", page(Page::Main))
            .route("/git", page(Page::Main))
            .route("/schedules", page(Page::Main))
            .route("/workflows", page(Page::Main))
            .route("/workflow/{*name}", page(Page::Main))
            .route("/terminal", page(Page::Terminal))
            .route("/admin/policy", page(Page::AdminPolicy))
            .route("/chrome", page(Page::Chrome))
            .route("/browser-window", page(Page::BrowserWindow))
            .route(
                "/static/{*filename}",
                get(static_file::serve).options(static_file::serve),
            )
            .layer(middleware::from_fn(security_headers))
            .with_state(self.0.clone())
    }

    /// Pass to `WebAuth::router_with_login` for session handling and signed-in redirects.
    pub fn login(&self) -> MethodRouter {
        get(|State(assets): State<Arc<AssetRoot>>| render(assets, "login.html"))
            .options(options)
            .layer(middleware::from_fn(security_headers))
            .with_state(self.0.clone())
    }
}

#[derive(Clone)]
struct AssetRoot {
    root: Arc<Dir>,
    manifest: Arc<ManifestCache>,
    host_data: Option<PathBuf>,
}

impl AssetRoot {
    fn file(&self, relative: &str) -> io::Result<File> {
        let mut components = relative.split('/');
        let name = components.next_back().expect("asset filename");
        let mut parent = None;
        // Each no-follow open pins the directory before resolving the next component.
        for component in components {
            let directory = parent.as_ref().unwrap_or(self.root.as_ref());
            parent = Some(directory.open_dir_nofollow(component).map_err(open_error)?);
        }
        let directory = parent.as_ref().unwrap_or(self.root.as_ref());
        let mut options = OpenOptions::new();
        options.read(true).follow(FollowSymlinks::No);
        #[cfg(unix)]
        {
            use cap_std::fs::OpenOptionsExt;
            options.custom_flags(libc::O_NONBLOCK);
        }
        let file = directory.open_with(name, &options).map_err(open_error)?;
        if !file.metadata()?.is_file() {
            return Err(io::ErrorKind::NotFound.into());
        }
        Ok(file)
    }
}

fn open_error(error: io::Error) -> io::Error {
    #[cfg(unix)]
    if error.raw_os_error() == Some(libc::ELOOP) {
        return io::ErrorKind::NotFound.into();
    }
    match error.kind() {
        io::ErrorKind::InvalidInput
        | io::ErrorKind::NotADirectory
        | io::ErrorKind::IsADirectory
        | io::ErrorKind::PermissionDenied => io::ErrorKind::NotFound.into(),
        _ => error,
    }
}

#[derive(Clone, Copy)]
enum Page {
    Root,
    Main,
    Terminal,
    AdminPolicy,
    Chrome,
    BrowserWindow,
}

fn page(page: Page) -> MethodRouter<Arc<AssetRoot>> {
    get(move |State(assets), session, policy| show(page, assets, session, policy)).options(options)
}

async fn conversation(
    State(assets): State<Arc<AssetRoot>>,
    RoutePath(id): RoutePath<String>,
    method: Method,
    session: Option<Extension<BrowserSession>>,
) -> Response {
    let id = id.strip_prefix("conv_").unwrap_or(&id);
    let valid = match id.strip_prefix("import_") {
        Some(hash) => hash.len() == 32 && hash.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)),
        None => id.len() == 19 && id.bytes().enumerate().all(|(index, byte)| match index {
            8 | 15 => byte == b'_',
            _ => byte.is_ascii_digit(),
        }),
    };
    if !valid {
        return StatusCode::NOT_FOUND.into_response();
    }
    match method {
        Method::GET | Method::HEAD => show(Page::Main, assets, session, None).await,
        Method::OPTIONS => options().await,
        _ => (
            StatusCode::METHOD_NOT_ALLOWED,
            [(header::ALLOW, "GET, HEAD, OPTIONS")],
        )
            .into_response(),
    }
}

async fn show(
    page: Page,
    assets: Arc<AssetRoot>,
    session: Option<Extension<BrowserSession>>,
    policy: Option<Extension<TerminalPolicy>>,
) -> Response {
    match page {
        Page::Chrome => return render(assets, "chrome.html").await,
        Page::BrowserWindow => return render(assets, "browser-window.html").await,
        _ if session.is_none() => return redirect("/login"),
        _ => (),
    }
    match page {
        Page::Root => redirect("/new"),
        Page::Terminal => match policy.map(|Extension(policy)| policy) {
            Some(TerminalPolicy::Allowed) => render(assets, "terminal.html").await,
            Some(TerminalPolicy::Blocked) => {
                read_asset(move || {
                    let english = assets
                        .host_data
                        .as_deref()
                        .is_some_and(personalization::english);
                    let message = if english {
                        "Realtime terminal has been disabled by the administrator"
                    } else {
                        "实时终端已被管理员禁用"
                    };
                    Ok(no_store((StatusCode::FORBIDDEN, message)))
                })
                .await
            }
            None => no_store((
                StatusCode::SERVICE_UNAVAILABLE,
                "Terminal policy is unavailable.",
            )),
        },
        // The verified Host Web session represents the Host administrator.
        Page::AdminPolicy => render(assets, "admin_policy/index.html").await,
        _ => render(assets, "index.html").await,
    }
}

async fn render(assets: Arc<AssetRoot>, name: &'static str) -> Response {
    read_asset(move || {
        let mut html = String::new();
        assets.file(name)?.read_to_string(&mut html)?;
        let html = assets.manifest.inject(&assets, &html)?;
        Ok(no_store((
            [(header::CONTENT_TYPE, "text/html; charset=utf-8")],
            html,
        )))
    })
    .await
}

async fn read_asset(read: impl FnOnce() -> io::Result<Response> + Send + 'static) -> Response {
    match tokio::task::spawn_blocking(read).await {
        Ok(Ok(response)) => response,
        Ok(Err(error)) if error.kind() == io::ErrorKind::NotFound => {
            StatusCode::NOT_FOUND.into_response()
        }
        Ok(Err(error)) => asset_failure(error),
        Err(error) => asset_failure(error),
    }
}

fn asset_failure(error: impl std::fmt::Display) -> Response {
    eprintln!("Host Web assets: {error}");
    no_store((
        StatusCode::INTERNAL_SERVER_ERROR,
        "Web assets are unavailable.",
    ))
}

fn no_store(response: impl IntoResponse) -> Response {
    let mut response = response.into_response();
    response
        .headers_mut()
        .insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
    response
}

fn redirect(location: &'static str) -> Response {
    no_store((StatusCode::FOUND, [(header::LOCATION, location)]))
}

async fn options() -> Response {
    ([(header::ALLOW, "GET, HEAD, OPTIONS")], ()).into_response()
}

async fn security_headers(request: axum::extract::Request, next: Next) -> Response {
    let mut response = next.run(request).await;
    let headers = response.headers_mut();
    headers.insert(
        header::X_FRAME_OPTIONS,
        HeaderValue::from_static("SAMEORIGIN"),
    );
    headers.insert(
        header::X_CONTENT_TYPE_OPTIONS,
        HeaderValue::from_static("nosniff"),
    );
    headers.insert(
        header::REFERRER_POLICY,
        HeaderValue::from_static("strict-origin-when-cross-origin"),
    );
    response
}

fn public_path(path: &str) -> bool {
    path.split('/').all(|part| {
        !part.is_empty()
            && !part.starts_with('.')
            && !part.ends_with(['.', ' '])
            && !part.contains(['\\', ':', '%'])
            && !part.chars().any(char::is_control)
            && !short_windows_name(part)
    })
}

fn short_windows_name(part: &str) -> bool {
    cfg!(windows)
        && part
            .split('.')
            .next()
            .and_then(|stem| stem.rsplit_once('~'))
            .is_some_and(|(_, digits)| {
                !digits.is_empty() && digits.bytes().all(|byte| byte.is_ascii_digit())
            })
}

#[cfg(test)]
#[path = "../../test/2026-11-24_Rust后端/assets_contract.rs"]
mod tests;
