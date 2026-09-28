//! 桌面控制桥：127.0.0.1 上的最小 HTTP 服务，供内嵌 Python 后端代理调用。
//!
//! 背景：主窗口加载的是 External URL（后端同源 serve 前端），Tauri 不给这类页面
//! 注入 JS API，前端无法直接调 Rust 的 updater 接口。因此壳侧开一个不依赖任何
//! HTTP 库的微型服务（与 backend.rs 手写 HTTP 的风格一致），只暴露三件事：
//!   GET  /version           应用版本（构建期 CARGO_PKG_VERSION）
//!   POST /update/install    触发 updater 检查 → 下载 → 安装 → 重启（无感更新）
//!   GET  /update/progress   查询更新进度（前端经后端代理轮询）
//!   POST /window/drag       开始拖拽移动窗口（顶部对话标签条的空白区域按下时调用）
//!   POST /chrome/dispatch   chrome 标签条 webview 的意图（激活/新建/关闭标签）→
//!                           eval 注入主 webview（External URL 页面拿不到 Tauri JS API，
//!                           但壳可以主动向页面执行 JS）
//! 仅绑定 loopback，不鉴权：同机进程本就在同一威胁域内。

use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::{Mutex, OnceLock};
use std::time::Duration;

use tauri::{AppHandle, Manager};
use tauri_plugin_updater::UpdaterExt;

/// 应用版本（构建期写入；与 tauri.conf.json version 由发布脚本保持同步）
pub const APP_VERSION: &str = env!("CARGO_PKG_VERSION");

/// 请求头读取上限（防御性，正常请求几百字节）
const MAX_HEADER_BYTES: usize = 16 * 1024;
/// POST body 丢弃读取上限（本桥接口不需要 body，读完只为让 TCP 正常收尾）
const MAX_BODY_DRAIN: usize = 1024 * 1024;

#[derive(Clone, Copy, PartialEq)]
enum UpdateState {
    Idle,
    Checking,
    Downloading,
    Installing,
    Restarting,
    NoUpdate,
    Error,
}

impl UpdateState {
    fn as_str(self) -> &'static str {
        match self {
            UpdateState::Idle => "idle",
            UpdateState::Checking => "checking",
            UpdateState::Downloading => "downloading",
            UpdateState::Installing => "installing",
            UpdateState::Restarting => "restarting",
            UpdateState::NoUpdate => "no_update",
            UpdateState::Error => "error",
        }
    }
}

struct Progress {
    state: UpdateState,
    downloaded: u64,
    total: Option<u64>,
    error: Option<String>,
}

impl Default for Progress {
    fn default() -> Self {
        Self {
            state: UpdateState::Idle,
            downloaded: 0,
            total: None,
            error: None,
        }
    }
}

static PROGRESS: OnceLock<Mutex<Progress>> = OnceLock::new();
static INSTALL_RUNNING: OnceLock<Mutex<bool>> = OnceLock::new();

fn progress() -> &'static Mutex<Progress> {
    PROGRESS.get_or_init(|| Mutex::new(Progress::default()))
}

fn install_running() -> &'static Mutex<bool> {
    INSTALL_RUNNING.get_or_init(|| Mutex::new(false))
}

fn set_progress(state: UpdateState, error: Option<String>) {
    if let Ok(mut p) = progress().lock() {
        p.state = state;
        p.error = error;
        if state == UpdateState::Checking {
            p.downloaded = 0;
            p.total = None;
        }
    }
}

fn add_downloaded(chunk: u64, total: Option<u64>) {
    if let Ok(mut p) = progress().lock() {
        p.state = UpdateState::Downloading;
        p.downloaded = p.downloaded.saturating_add(chunk);
        if total.is_some() {
            p.total = total;
        }
    }
}

/// 启动桥：绑定 127.0.0.1:0（由系统分配空闲端口），后台线程 accept。
/// 返回实际监听端口（经环境变量传给后端）。
pub fn start_bridge(app: &AppHandle) -> Result<u16, String> {
    let listener = TcpListener::bind("127.0.0.1:0").map_err(|e| format!("控制桥绑定失败: {e}"))?;
    let port = listener
        .local_addr()
        .map_err(|e| format!("控制桥取端口失败: {e}"))?
        .port();
    let app_handle = app.clone();
    std::thread::spawn(move || {
        for stream in listener.incoming().map_while(Result::ok) {
            let h = app_handle.clone();
            std::thread::spawn(move || {
                let _ = handle_conn(stream, h);
            });
        }
    });
    Ok(port)
}

fn handle_conn(mut stream: TcpStream, app: AppHandle) -> std::io::Result<()> {
    let _ = stream.set_read_timeout(Some(Duration::from_secs(5)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(5)));

    // 读请求头（\r\n\r\n 为止，带上限）
    let mut buf: Vec<u8> = Vec::with_capacity(2048);
    let mut chunk = [0u8; 4096];
    let header_end = loop {
        if let Some(pos) = find_subslice(&buf, b"\r\n\r\n") {
            break pos + 4;
        }
        if buf.len() >= MAX_HEADER_BYTES {
            return respond(&mut stream, 431, "{\"error\":\"header_too_large\"}");
        }
        let n = stream.read(&mut chunk)?;
        if n == 0 {
            return Ok(()); // 对端关闭，无请求
        }
        buf.extend_from_slice(&chunk[..n]);
    };

    let head = String::from_utf8_lossy(&buf[..header_end]).to_string();
    let mut lines = head.lines();
    let request_line = lines.next().unwrap_or("");
    let mut parts = request_line.split_whitespace();
    let method = parts.next().unwrap_or("");
    let path = parts.next().unwrap_or("");

    // POST 的 body：chrome/dispatch 需要内容（JSON 意图，独立上限 64KB）；
    // 其余端点不需要 body，读完只为让 TCP 正常收尾（不读完就关连接可能触发 RST）
    let want_body = method == "POST" && path == "/chrome/dispatch";
    let content_length: usize = lines
        .filter_map(|l| l.split_once(':'))
        .find(|(k, _)| k.trim().eq_ignore_ascii_case("content-length"))
        .and_then(|(_, v)| v.trim().parse().ok())
        .unwrap_or(0)
        .min(MAX_BODY_DRAIN);
    let mut body: Vec<u8> = if want_body {
        buf[header_end..].to_vec()
    } else {
        Vec::new()
    };
    let mut body_have = buf.len().saturating_sub(header_end);
    while body_have < content_length {
        let n = stream.read(&mut chunk)?;
        if n == 0 {
            break;
        }
        if want_body {
            body.extend_from_slice(&chunk[..n]);
        }
        body_have += n;
    }

    match (method, path) {
        ("GET", "/version") => {
            let body = serde_json::json!({ "version": APP_VERSION }).to_string();
            respond(&mut stream, 200, &body)
        }
        ("POST", "/update/install") => match try_start_install(&app) {
            Ok(()) => respond(&mut stream, 202, "{\"started\":true}"),
            Err(e) => {
                let body = serde_json::json!({ "started": false, "error": e }).to_string();
                respond(&mut stream, 409, &body)
            }
        },
        ("GET", "/update/progress") => {
            let body = match progress().lock() {
                Ok(p) => serde_json::json!({
                    "state": p.state.as_str(),
                    "downloaded": p.downloaded,
                    "total": p.total,
                    "error": p.error,
                })
                .to_string(),
                Err(_) => "{\"state\":\"error\",\"error\":\"lock_poisoned\"}".to_string(),
            };
            respond(&mut stream, 200, &body)
        }
        ("POST", "/window/drag") => match app.get_webview_window("main") {
            Some(window) => match window.start_dragging() {
                Ok(()) => respond(&mut stream, 200, "{\"started\":true}"),
                Err(e) => {
                    let body = serde_json::json!({ "started": false, "error": e.to_string() }).to_string();
                    respond(&mut stream, 409, &body)
                }
            },
            None => respond(&mut stream, 409, "{\"started\":false,\"error\":\"window_missing\"}"),
        },
        ("POST", "/chrome/dispatch") => {
            // body 是 JSON Value，重序列化后就是合法 JS 字面量，无注入面
            let payload = serde_json::from_slice::<serde_json::Value>(&body)
                .unwrap_or(serde_json::Value::Null);
            let js = format!(
                "window.__astrionChromeDispatch && window.__astrionChromeDispatch({payload})"
            );
            match app.get_webview("main") {
                Some(webview) => match webview.eval(&js) {
                    Ok(()) => respond(&mut stream, 200, "{\"dispatched\":true}"),
                    Err(e) => {
                        let body = serde_json::json!({ "dispatched": false, "error": e.to_string() }).to_string();
                        respond(&mut stream, 409, &body)
                    }
                },
                None => respond(&mut stream, 409, "{\"dispatched\":false,\"error\":\"webview_missing\"}"),
            }
        }
        _ => respond(&mut stream, 404, "{\"error\":\"not_found\"}"),
    }
}

fn respond(stream: &mut TcpStream, status: u16, body: &str) -> std::io::Result<()> {
    let reason = match status {
        200 => "OK",
        202 => "Accepted",
        404 => "Not Found",
        409 => "Conflict",
        431 => "Request Header Fields Too Large",
        _ => "OK",
    };
    let resp = format!(
        "HTTP/1.1 {status} {reason}\r\nContent-Type: application/json; charset=utf-8\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
        body.len()
    );
    stream.write_all(resp.as_bytes())?;
    stream.flush()
}

fn find_subslice(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    haystack.windows(needle.len()).position(|w| w == needle)
}

/// 占用安装互斥位后派生异步更新任务；已在进行中返回 Err("already_running")。
fn try_start_install(app: &AppHandle) -> Result<(), String> {
    {
        let mut running = install_running()
            .lock()
            .map_err(|_| "lock_poisoned".to_string())?;
        if *running {
            return Err("already_running".to_string());
        }
        *running = true;
    }
    set_progress(UpdateState::Checking, None);
    let app_handle = app.clone();
    tauri::async_runtime::spawn(async move {
        let result = run_update(app_handle).await;
        if let Err(err) = result {
            set_progress(UpdateState::Error, Some(err));
        }
        // 成功路径会 app.restart() 不返回；走到这里说明失败或无需更新，释放互斥位
        if let Ok(mut running) = install_running().lock() {
            *running = false;
        }
    });
    Ok(())
}

async fn run_update(app: AppHandle) -> Result<(), String> {
    // Windows 的 install_inner 在启动 NSIS 安装器后立即 std::process::exit(0)
    // 自杀壳进程——不跑 Drop 与 RunEvent::Exit，正常收尸链路（shutdown_backend）
    // 被绕过，后端孤儿锁死安装目录文件会导致 NSIS 报 "Error opening file for
    // writing"。on_before_exit 是 updater 官方预留钩子，恰在安装器启动前、
    // process::exit 前执行：此处先杀死后端并等待其退出，再放安装器进场。
    // （非 Windows 平台该钩子不被调用，mac 更新走原地替换无此问题。）
    let app_for_hook = app.clone();
    let update = app
        .updater_builder()
        .on_before_exit(move || {
            crate::backend::shutdown_backend(&app_for_hook);
        })
        .build()
        .map_err(|e| format!("updater 初始化失败: {e}"))?
        .check()
        .await
        .map_err(|e| format!("检查更新失败: {e}"))?;

    let Some(update) = update else {
        set_progress(UpdateState::NoUpdate, None);
        return Ok(());
    };

    update
        .download_and_install(
            |chunk_len, content_len| {
                add_downloaded(chunk_len as u64, content_len);
            },
            || {
                set_progress(UpdateState::Installing, None);
            },
        )
        .await
        .map_err(|e| format!("下载/安装失败: {e}"))?;

    // macOS：新 .app 已原地替换；Windows：NSIS passive 已覆盖安装。
    // 重启进入新版本（restart 不返回，进程被替换）。
    set_progress(UpdateState::Restarting, None);
    app.restart();
}
