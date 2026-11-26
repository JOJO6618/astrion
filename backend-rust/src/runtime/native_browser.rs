use std::{
    io::{self, Read},
    path::{Component, Path, PathBuf},
    sync::LazyLock,
    time::Duration,
};

use reqwest::header::HeaderValue;
use serde_json::{Map, Value, json};
use uuid::Uuid;

use crate::{
    approval::{ApprovalType, PermissionMode, Status as ApprovalStatus, ToolRequest},
    knowledge::browser::{BrowserError, BrowserPending},
};

use super::{
    Runtime, RuntimeError,
    approval::{claim_process_approval, create_process_approval, task_scope},
    emit_user_message, host_policy, now,
    store::{Status, Store},
    writable_task,
};

static SCHEMAS: LazyLock<Vec<Value>> = LazyLock::new(|| {
    serde_json::from_str(include_str!(
        "../../../core/main_terminal_parts/tools_definition/browser_tools.json"
    ))
    .expect("desktop browser tool schemas")
});

const DISABLED: &str = "浏览器未开启。用户可在输入栏「+」菜单开启浏览器。";

#[derive(Clone)]
pub struct DesktopBrowser {
    client: reqwest::Client,
    port: u16,
    token: HeaderValue,
}

impl DesktopBrowser {
    pub fn from_parent() -> io::Result<Option<Self>> {
        if !cfg!(any(target_os = "macos", target_os = "windows"))
            || std::env::var_os("ASTRION_DESKTOP_VERSION").is_none()
        {
            return Ok(None);
        }
        let Some(pipe) = std::env::var_os("ASTRION_DESKTOP_BROWSER_TOKEN_FD") else {
            return Ok(None);
        };
        let port = std::env::var("ASTRION_DESKTOP_BRIDGE_PORT")
            .ok()
            .and_then(|raw| raw.parse::<u16>().ok())
            .filter(|&port| port != 0)
            .ok_or_else(|| io::Error::other("Missing Desktop browser bridge port"))?;
        let token = read_parent_pipe(&pipe)?;
        let client = reqwest::Client::builder()
            .no_proxy()
            .redirect(reqwest::redirect::Policy::none())
            .build()
            .map_err(io::Error::other)?;
        Ok(Some(Self {
            client,
            port,
            token,
        }))
    }

    async fn post(
        &self,
        operation: &str,
        payload: &Value,
        timeout: Duration,
    ) -> Result<Value, String> {
        let response = self
            .client
            .post(format!(
                "http://127.0.0.1:{}/browser/{operation}",
                self.port
            ))
            .header("x-astrion-browser-token", self.token.clone())
            .json(payload)
            .timeout(timeout)
            .send()
            .await
            .map_err(|_| "无法连接内置浏览器。")?;
        let response = response
            .error_for_status()
            .map_err(|_| "内置浏览器控制通道拒绝请求。")?;
        let mut response = response;
        let mut body = Vec::new();
        while let Some(chunk) = response
            .chunk()
            .await
            .map_err(|_| "无法读取内置浏览器响应。")?
        {
            if body.len() + chunk.len() > 40 * 1024 * 1024 {
                return Err("内置浏览器响应过大。".into());
            }
            body.extend_from_slice(&chunk);
        }
        let value: Value =
            serde_json::from_slice(&body).map_err(|_| "内置浏览器返回了无效结果。")?;
        if !value.is_object() {
            return Err("内置浏览器返回了无效结果。".into());
        }
        Ok(value)
    }
}

fn token_line(mut pipe: impl Read) -> io::Result<HeaderValue> {
    let mut token = [0; 65];
    pipe.read_exact(&mut token)?;
    if token[64] != b'\n' || !token[..64].iter().all(u8::is_ascii_hexdigit) {
        return Err(io::Error::other("Invalid Desktop browser credential pipe"));
    }
    let mut header = HeaderValue::from_bytes(&token[..64]).map_err(io::Error::other)?;
    header.set_sensitive(true);
    Ok(header)
}

#[cfg(target_os = "macos")]
fn read_parent_pipe(pipe: &std::ffi::OsStr) -> io::Result<HeaderValue> {
    use std::os::fd::FromRawFd;

    if pipe != "3" {
        return Err(io::Error::other(
            "Invalid Desktop browser credential descriptor",
        ));
    }
    let mut info = std::mem::MaybeUninit::<libc::stat>::uninit();
    // The shell exclusively assigns descriptor 3 for the one-shot credential.
    if unsafe { libc::fstat(3, info.as_mut_ptr()) } != 0 {
        return Err(io::Error::last_os_error());
    }
    let kind = unsafe { info.assume_init() }.st_mode & libc::S_IFMT;
    if !matches!(kind, libc::S_IFIFO | libc::S_IFSOCK) {
        return Err(io::Error::other(
            "Desktop browser credential requires a private pipe",
        ));
    }
    token_line(unsafe { std::fs::File::from_raw_fd(3) })
}

#[cfg(windows)]
fn read_parent_pipe(pipe: &std::ffi::OsStr) -> io::Result<HeaderValue> {
    if pipe != "0" {
        return Err(io::Error::other(
            "Invalid Desktop browser credential descriptor",
        ));
    }
    // The Windows guardian uses the first line of its inherited control pipe
    // for the credential and keeps the pipe available for graceful shutdown.
    token_line(io::stdin().lock())
}

#[cfg(not(any(target_os = "macos", windows)))]
fn read_parent_pipe(_: &std::ffi::OsStr) -> io::Result<HeaderValue> {
    Err(io::Error::other(
        "Desktop browser is unavailable on this platform",
    ))
}

struct PendingCall {
    bridge: DesktopBrowser,
    id: String,
    finished: bool,
}

impl Drop for PendingCall {
    fn drop(&mut self) {
        if self.finished {
            return;
        }
        if let Ok(handle) = tokio::runtime::Handle::try_current() {
            let bridge = self.bridge.clone();
            let id = self.id.clone();
            handle.spawn(async move {
                let _ = bridge
                    .post("cancel", &json!({"call_id":id}), Duration::from_secs(5))
                    .await;
            });
        }
    }
}

struct Context {
    bridge: DesktopBrowser,
    identity: Value,
    workspace: PathBuf,
    data: PathBuf,
    permission: PermissionMode,
    epoch: u64,
    preferences: Value,
}

impl Runtime {
    pub fn configure_desktop_browser(
        &self,
        browser: Option<DesktopBrowser>,
    ) -> Result<(), RuntimeError> {
        self.store()?.native_browser = browser;
        Ok(())
    }

    pub(crate) fn desktop_browser_supported(&self) -> bool {
        self.store()
            .is_ok_and(|store| store.native_browser.is_some())
    }

    pub(crate) fn change_browser_tools(
        &self,
        workspace: &str,
        conversation: &str,
        enabled: bool,
    ) -> Result<Option<BrowserPending>, BrowserError> {
        let store = self.store().map_err(|_| BrowserError::Unavailable)?;
        if store.native_browser.is_none() {
            return Err(BrowserError::Unsupported);
        }
        let (header, _) = store.header(workspace, conversation).map_err(|error| {
            if matches!(error.status, 400 | 404) {
                BrowserError::NotFound
            } else {
                BrowserError::Unavailable
            }
        })?;
        if header["metadata"]["conversation_kind"] == "chat" {
            return Err(BrowserError::Unsupported);
        }
        let active = store.tasks.values().any(|task| {
            task.workspace_id == workspace
                && task.conversation_id == conversation
                && task.status == Status::Running
        });
        let pending = active.then_some(if enabled {
            BrowserPending::On
        } else {
            BrowserPending::Off
        });
        store
            .update_agent_metadata(workspace, conversation, |metadata| {
                metadata["pending_browser_tools"] = json!(pending);
                if pending.is_none() {
                    metadata["browser_tools_enabled"] = json!(enabled);
                }
            })
            .map_err(|_| BrowserError::Unavailable)?;
        Ok(pending)
    }

    pub(crate) fn release_browser_tools(
        &self,
        workspace: &str,
        conversation: &str,
    ) -> Result<(), BrowserError> {
        let bridge = self
            .store()
            .map_err(|_| BrowserError::Unavailable)?
            .native_browser
            .clone()
            .ok_or(BrowserError::Unsupported)?;
        let handle =
            tokio::runtime::Handle::try_current().map_err(|_| BrowserError::Unavailable)?;
        let result = handle.block_on(bridge.post("release", &json!({
            "identity":{"username":"host","workspace_id":workspace,"conversation_id":conversation},
        }), Duration::from_secs(2))).map_err(|_| BrowserError::Unavailable)?;
        if result["success"] != true {
            return Err(BrowserError::Unavailable);
        }
        Ok(())
    }

    pub(super) fn browser_schemas(&self) -> Vec<Value> {
        if self.desktop_browser_supported() {
            SCHEMAS.clone()
        } else {
            Vec::new()
        }
    }

    pub(super) fn apply_browser_settings(&self, task_id: &str) -> Result<Option<Value>, String> {
        let mut store = self.store().map_err(|error| error.message)?;
        if store.native_browser.is_none() {
            return Ok(None);
        }
        let task = writable_task(&mut store, task_id)?;
        let workspace = task.workspace_id.clone();
        let conversation = task.conversation_id.clone();
        let (header, _) = store
            .header(&workspace, &conversation)
            .map_err(|error| error.message)?;
        let metadata = &header["metadata"];
        let mode = match metadata["pending_browser_tools"].as_str() {
            Some("on") => true,
            Some("off") => false,
            _ => return Ok(None),
        };
        store
            .update_agent_metadata(&workspace, &conversation, |metadata| {
                metadata["pending_browser_tools"] = Value::Null;
                metadata["browser_tools_enabled"] = json!(mode);
                metadata["browser_snapshot_epoch"] = json!(
                    metadata["browser_snapshot_epoch"]
                        .as_u64()
                        .unwrap_or(0)
                        .saturating_add(1)
                );
            })
            .map_err(|error| error.message)?;
        if mode == (metadata["browser_tools_enabled"] == true) {
            return Ok(None);
        }
        let content = if mode {
            "【浏览器】用户已为本对话开启内置浏览器工具。"
        } else {
            "【浏览器】用户已为本对话关闭内置浏览器工具。"
        };
        let metadata = json!({"source":"browser_change","message_source":"runtime_notice",
            "runtime_mode_notice":true,"visibility":"compact","starts_work":false,"kind":"browser_tools","mode":if mode {"on"} else {"off"}});
        store
            .append_messages(
                &workspace,
                &conversation,
                vec![json!({"role":"user","content":content,"metadata":metadata})],
                now(),
                None,
                None,
            )
            .map_err(|error| error.message)?;
        emit_user_message(writable_task(&mut store, task_id)?, content, metadata);
        Ok(Some(json!({"role":"user","content":content})))
    }

    pub(super) async fn execute_browser_tool(
        &self,
        task_id: &str,
        call_id: &str,
        name: &str,
        arguments: Value,
        batch: &[Value],
    ) -> Result<Value, String> {
        let Some(op) = name.strip_prefix("browser_") else {
            return Err("Invalid browser tool".into());
        };
        let context = match self.browser_context(task_id) {
            Ok(context) => context,
            Err(failure) => return Ok(failure),
        };
        let mut args = arguments.as_object().cloned().unwrap_or_default();
        args.shift_remove("intent");
        let mut file_urls = Vec::new();
        if matches!(op, "navigate" | "tabs")
            && let Some(target) = args.get("url").and_then(Value::as_str)
            && target
                .trim_start()
                .to_ascii_lowercase()
                .starts_with("file:")
        {
            match self.browser_file_url(task_id, target) {
                Ok(url) => {
                    file_urls.push(url.clone());
                    args.insert("url".into(), json!(url));
                }
                Err(error) => return Ok(failure("path_not_allowed", &error)),
            }
        }
        let preflight = if op != "downloads"
            && (op != "tabs" || args.get("action").is_some_and(|action| action == "select"))
        {
            let tab = args.get("tab_id").cloned();
            let query = tab.map_or_else(|| json!({}), |id| json!({"tab_id":id}));
            match context
                .bridge
                .post(
                    "call",
                    &json!({"call_id":call_nonce(),"op":"site",
                "identity":context.identity,"args":query}),
                    Duration::from_secs(5),
                )
                .await
            {
                Ok(result) => result,
                Err(error) => return Ok(failure("browser_unavailable", &error)),
            }
        } else {
            Value::Null
        };
        if let Some(url) = preflight["url"]
            .as_str()
            .filter(|url| url.to_ascii_lowercase().starts_with("file:"))
        {
            match self.browser_file_url(task_id, url) {
                Ok(url) => file_urls.push(url),
                Err(error) => return Ok(failure("path_not_allowed", &error)),
            }
        }
        let action = action(op, &args, &preflight);
        let always = matches!(op, "evaluate" | "fill_saved_login");
        if action && context.permission == PermissionMode::ReadOnly {
            return Ok(failure(
                "readonly_denied",
                "只读和计划模式仅允许导航、快照等浏览器读取操作。",
            ));
        }
        let mut sites = crate::knowledge::browser::authorized_sites(&context.data)
            .map_err(|error| error.to_string())?;
        let site = preflight["site"].as_str().filter(|site| !site.is_empty());
        let mut authorized_once = None;
        if action && context.permission != PermissionMode::Unrestricted {
            let Some(site) = site else {
                return Ok(failure(
                    "site_not_approved",
                    "无法确认当前标签的站点，未执行操作。",
                ));
            };
            if always || !sites.iter().any(|allowed| allowed == site) {
                let request = ToolRequest {
                    tool_call_id: call_id.into(),
                    tool_name: name.into(),
                    arguments: args.clone(),
                    preview: json!({"browser_url":preflight["url"]})
                        .as_object()
                        .unwrap()
                        .clone(),
                    approval_type: ApprovalType::BrowserAction,
                    browser_site: Some(site.into()),
                };
                let approval = {
                    let mut store = self.store().map_err(|error| error.message)?;
                    create_process_approval(&mut store, task_id, &request)?
                };
                let approval = self.resolve_tool_approval(approval, batch).await?;
                if approval.status != ApprovalStatus::Approved {
                    return Ok(failure("approval_rejected", "浏览器操作未获批准。"));
                }
                {
                    let mut store = self.store().map_err(|error| error.message)?;
                    claim_process_approval(&mut store, task_id, &request, &approval, batch)?;
                }
                if !always && approval.remember_site == Some(true) {
                    crate::knowledge::browser::remember_site(&context.data, site)
                        .map_err(|error| error.to_string())?;
                }
                authorized_once = Some(site.to_owned());
            }
        }
        let readable_paths = if matches!(op, "file_upload" | "drop") {
            match self.browser_paths(task_id, args.get("paths")) {
                Ok(paths) => {
                    args.insert("paths".into(), json!(paths));
                    paths
                }
                Err(error) => return Ok(failure("path_not_allowed", &error)),
            }
        } else {
            Vec::new()
        };
        // The renderer may close or reassign a tab during approval. Electron
        // also checks the live tab and the exact approved site immediately before acting.
        if let Err(failure) = self.browser_context(task_id) {
            return Ok(failure);
        }
        if always {
            sites.clear();
        }
        if let Some(site) = authorized_once {
            sites.push(site);
        }
        let call_id = call_nonce();
        let mut pending = PendingCall {
            bridge: context.bridge.clone(),
            id: call_id.clone(),
            finished: false,
        };
        let mut payload = json!({
            "call_id":call_id,"op":op,"identity":context.identity,"args":args,"epoch":context.epoch,
            "file_urls":file_urls,"readable_paths":readable_paths,"reveal":context.preferences["browser_auto_open"] != false,
        });
        if context.permission == PermissionMode::Unrestricted {
            payload["any_site"] = json!(true);
        } else {
            payload["allowed_sites"] = json!(if context.permission == PermissionMode::ReadOnly {
                Vec::new()
            } else {
                sites
            });
        }
        let can_start_download = action
            || matches!(op, "navigate" | "handle_dialog" | "hover" | "scroll")
            || op == "tabs" && payload["args"]["action"] == "new";
        if can_start_download {
            let Some(directory) = self.browser_download_directory(task_id, &context) else {
                return Ok(failure("path_not_allowed", "内置浏览器的下载目录不可用。"));
            };
            payload["download_dir"] = json!(directory);
        }
        let mut result = match context
            .bridge
            .post("call", &payload, Duration::from_secs(150))
            .await
        {
            Ok(result) => result,
            Err(error) => return Ok(failure("browser_unavailable", &error)),
        };
        pending.finished = true;
        let image = result["image_base64"].take();
        result
            .as_object_mut()
            .expect("browser result")
            .shift_remove("image_base64");
        if let Some(image) = image.as_str().filter(|_| result["success"] != false) {
            match super::native_browser_files::screenshot(&context.workspace, image) {
                Ok(path) => result["screenshot_path"] = json!(path),
                Err(error) => return Ok(failure("screenshot_save_failed", &error)),
            }
        }
        result["op"] = json!(op);
        Ok(result)
    }

    fn browser_context(&self, task_id: &str) -> Result<Context, Value> {
        let storage = |error: &str| failure("browser_unavailable", error);
        let store = self.store().map_err(|error| storage(&error.message))?;
        let bridge = store
            .native_browser
            .clone()
            .ok_or_else(|| storage("内置浏览器控制通道不可用。"))?;
        let (scope, settings) = task_scope(&store, task_id).map_err(|error| storage(&error))?;
        let (permission, _) = settings
            .process_modes()
            .ok_or_else(|| storage("浏览器任务权限不可用。"))?;
        let (header, _) = store
            .header(scope.principal().workspace_id(), scope.conversation_id())
            .map_err(|error| storage(&error.message))?;
        let metadata = &header["metadata"];
        if metadata["browser_tools_enabled"] != true || metadata["pending_browser_tools"] == "off" {
            return Err(failure("browser_disabled", DISABLED));
        }
        Ok(Context {
            bridge,
            permission,
            workspace: scope.workspace_root().to_owned(),
            data: store.data_dir.clone(),
            identity: json!({"username":scope.principal().username(),"workspace_id":scope.principal().workspace_id(),
                "conversation_id":scope.conversation_id()}),
            epoch: metadata["browser_snapshot_epoch"].as_u64().unwrap_or(0),
            preferences: super::store::read_json(&store.data_dir.join("personalization.json"))
                .map_err(|error| storage(&error.message))?
                .unwrap_or_default(),
        })
    }

    pub(super) fn browser_screenshot(&self, task_id: &str, path: &Path) -> Option<Value> {
        let context = self.browser_context(task_id).ok()?;
        let url = super::native_browser_files::screenshot_url(&context.workspace, path).ok()?;
        Some(json!({"type":"image_url","image_url":{"url":url}}))
    }

    fn browser_file_url(&self, task_id: &str, value: &str) -> Result<String, String> {
        let url = reqwest::Url::parse(value.trim()).map_err(|_| "无效的本地文件地址。")?;
        if url.scheme() != "file"
            || !url.host_str().is_none_or(|host| host == "localhost")
            || url.query().is_some()
        {
            return Err("只允许当前授权范围内的本机文件。".into());
        }
        let path = url.to_file_path().map_err(|_| "无效的本地文件地址。")?;
        let store = self.store().map_err(|error| error.message)?;
        let path = authorized_file(&store, task_id, &path)?;
        let mut destination =
            reqwest::Url::from_file_path(path).map_err(|_| "无效的本地文件地址。")?;
        destination.set_fragment(url.fragment());
        Ok(destination.to_string())
    }

    fn browser_paths(&self, task_id: &str, paths: Option<&Value>) -> Result<Vec<PathBuf>, String> {
        let Some(paths) = paths else {
            return Ok(Vec::new());
        };
        let rows = paths
            .as_array()
            .filter(|rows| rows.len() <= 20)
            .ok_or("paths 必须是不超过 20 项的文件路径数组。")?;
        let store = self.store().map_err(|error| error.message)?;
        rows.iter()
            .map(|row| {
                let path = row
                    .as_str()
                    .map(str::trim)
                    .filter(|path| !path.is_empty())
                    .ok_or("paths 必须是不超过 20 项的文件路径数组。")?;
                authorized_file(&store, task_id, Path::new(path))
            })
            .collect()
    }

    fn browser_download_directory(&self, task_id: &str, context: &Context) -> Option<PathBuf> {
        let settings = context.preferences["browser_download_dir"]
            .as_str()
            .map(str::trim)
            .filter(|path| !path.is_empty())
            .unwrap_or(".astrion/browser_downloads");
        let configured = settings
            .strip_prefix("~/")
            .and_then(|tail| std::env::home_dir().map(|home| home.join(tail)))
            .unwrap_or_else(|| context.workspace.join(settings));
        if configured
            .components()
            .any(|part| matches!(part, Component::ParentDir))
        {
            return None;
        }
        let ancestor = configured.ancestors().find(|path| path.is_dir())?;
        let canonical = ancestor
            .canonicalize()
            .ok()?
            .join(configured.strip_prefix(ancestor).ok()?);
        let store = self.store().ok()?;
        let (_, _) = task_scope(&store, task_id).ok()?;
        if let Some(deploy) = store.process_resources.deployment() {
            let policy = host_policy::load_paths(deploy).ok()?;
            if host_policy::forbidden_path(&canonical, &policy, &context.data, deploy).ok()? {
                return None;
            }
        }
        if !Path::new(settings).is_absolute()
            && !settings.starts_with("~/")
            && !canonical.starts_with(&context.workspace)
        {
            return None;
        }
        Some(canonical)
    }
}

fn authorized_file(store: &Store, task_id: &str, path: &Path) -> Result<PathBuf, String> {
    let (scope, _) = task_scope(store, task_id)?;
    let spelling = path.to_str().ok_or("无效的本机文件路径。")?;
    let path = match spelling.strip_prefix("/workspace/") {
        Some(relative) => scope.workspace_root().join(relative),
        None => scope.workspace_root().join(path),
    };
    if path
        .components()
        .any(|part| matches!(part, Component::ParentDir))
    {
        return Err("本机文件不在当前对话的授权范围内。".into());
    }
    let canonical = path.canonicalize().map_err(|_| "无法打开本机文件。")?;
    let path_str = canonical.to_str().ok_or("无效的本机文件路径。")?;
    let external = host_policy::native_path_root(store, task_id, path_str, false)?;
    if !(canonical.is_file()
        && (canonical.starts_with(scope.workspace_root()) || external.is_some()))
    {
        return Err("本机文件不在当前对话的授权范围内。".into());
    }
    Ok(canonical)
}

fn action(op: &str, args: &Map<String, Value>, preflight: &Value) -> bool {
    match op {
        "handle_dialog" => {
            args.get("accept") == Some(&Value::Bool(true)) && preflight["dialog"]["type"] != "alert"
        }
        "click" | "type" | "fill_form" | "select_option" | "press_key" | "drag" | "drop"
        | "file_upload" | "evaluate" | "fill_saved_login" => true,
        _ => false,
    }
}

fn call_nonce() -> String {
    format!("browser_{}", Uuid::new_v4().simple())
}

fn failure(code: &str, message: &str) -> Value {
    json!({"success":false,"code":code,"error":message})
}
