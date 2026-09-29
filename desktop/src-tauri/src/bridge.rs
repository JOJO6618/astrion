//! 桌面控制桥：127.0.0.1 上的最小 HTTP 服务，供内嵌 Python 后端代理调用。
//!
//! 背景：主窗口加载的是 External URL（后端同源 serve 前端），Tauri 不给这类页面
//! 注入 JS API，前端无法直接调 Rust 的 updater 接口。因此壳侧开一个不依赖任何
//! HTTP 库的微型服务（与 backend.rs 手写 HTTP 的风格一致），只暴露三件事：
//!   GET  /version           应用版本（构建期 CARGO_PKG_VERSION）
//!   POST /update/install    触发 updater 检查 → 下载 → 安装 → 重启（无感更新）
//!   GET  /update/progress   查询更新进度（前端经后端代理轮询）
//!   POST /window/drag       开始拖拽移动窗口（顶部对话标签条的空白区域按下时调用）
//!   POST /window/control    窗口控制（无边框模式的自绘三大键）：
//!                           minimize / maximize-toggle / close
//!   GET  /window/state      窗口状态（当前仅 maximized，供自绘三大键切换图标）
//!   POST /chrome/dispatch   chrome 标签条 webview 的意图（激活/新建/关闭标签）→
//!                           eval 注入主 webview（External URL 页面拿不到 Tauri JS API，
//!                           但壳可以主动向页面执行 JS）
//!   GET  /rundata/info      运行数据目录设置（环境变量锁定/已配置/生效中/默认）
//!   POST /rundata/apply     切换数据目录（含可选迁移）——只写设置与待迁移标记，
//!                           真正的复制在下次启动、spawn 后端之前完成（见 rundata.rs）
//!   POST /rundata/restart   请求壳重启（迁移在重启后的启动链里执行）
//!   GET  /migration         迁移进度窗口页面（自包含 HTML，无外部资源）
//!   GET  /migration/progress 迁移进度 JSON（供上述页面轮询）
//! 注意：窗口句柄一律用 get_window("main")——tauri 的 get_webview_window 内部
//! 要求窗口所有 webview 与窗口同 label（is_webview_window），主窗口挂了 chrome
//! 子 webview 后该判定恒为 false，会静默返回 None（2026-09-28 三大键失效事故）。
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

/// 迁移进度窗口页面（自包含，无外部资源；由桥自身 serve——此刻后端还没启动）。
/// 轮询 /migration/progress；完成后由壳关闭窗口。
const MIGRATION_PAGE: &str = r##"<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>正在迁移数据</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body {
    margin: 0; height: 100vh; display: flex; flex-direction: column;
    justify-content: center; gap: 14px; padding: 22px 24px;
    font: 13px/1.5 "Segoe UI", system-ui, sans-serif;
    background: #faf9f5; color: #1f1e1c; user-select: none;
  }
  .title { font-size: 14px; font-weight: 600; }
  .bar { height: 6px; border-radius: 3px; background: rgba(0,0,0,.08); overflow: hidden; }
  .fill { height: 100%; width: 0%; border-radius: 3px; background: #cc785c; transition: width .18s ease; }
  .fill.busy { width: 35%; animation: slide 1.1s ease-in-out infinite; }
  @keyframes slide { 0% { margin-left: -35%; } 100% { margin-left: 100%; } }
  .meta { display: flex; justify-content: space-between; gap: 12px; color: #6b675f; font-size: 12px; }
  .meta .detail { white-space: nowrap; }
  .err { color: #b3261e; }
  @media (prefers-color-scheme: dark) {
    body { background: #1c1b1a; color: #ece9e4; }
    .bar { background: rgba(255,255,255,.12); }
    .meta { color: #a8a29a; }
    .err { color: #f2b8b5; }
  }
</style>
</head>
<body>
  <div class="title" id="title">正在迁移运行数据…</div>
  <div class="bar"><div class="fill" id="fill"></div></div>
  <div class="meta"><span id="status"></span><span class="detail" id="detail"></span></div>
<script>
  // 壳级页面不走前端 i18n 引擎，按浏览器语言自选一份文案（zh 开头用中文，其余英文）
  var ZH = (navigator.language || '').toLowerCase().indexOf('zh') === 0;
  var T = ZH ? {
    moving: '正在迁移运行数据…',
    doneTitle: '数据迁移完成',
    failTitle: '数据迁移未完成',
    unit: ' 项',
    failPrefix: '迁移失败：',
    unknown: '未知错误',
    phases: {
      idle: '准备中', scanning: '正在统计文件…', copying: '正在复制数据…',
      verifying: '正在校验…', done: '迁移完成'
    },
    errors: {
      verification_failed: '复制后的校验未通过，已保留原目录',
      target_not_empty: '目标目录包含现有数据，已保留原目录',
      target_not_directory: '目标路径不是可用目录，已保留原目录',
      overlapping_directories: '新目录与当前目录互相包含',
      same_directory: '新旧目录相同',
      path_required: '数据目录路径无效'
    }
  } : {
    moving: 'Moving app data…',
    doneTitle: 'Data moved',
    failTitle: 'Data migration incomplete',
    unit: ' items',
    failPrefix: 'Migration failed: ',
    unknown: 'unknown error',
    phases: {
      idle: 'Preparing', scanning: 'Scanning files…', copying: 'Copying data…',
      verifying: 'Verifying…', done: 'Finished'
    },
    errors: {
      verification_failed: 'Copy verification failed; the original folder was kept',
      target_not_empty: 'The target folder already has data; the original folder was kept',
      target_not_directory: 'The target path is not a usable folder; the original folder was kept',
      overlapping_directories: 'The new folder overlaps the current one',
      same_directory: 'The new folder is the same as the current one',
      path_required: 'The data folder path is invalid'
    }
  };
  var title = document.getElementById('title');
  var fill = document.getElementById('fill');
  var status = document.getElementById('status');
  var detail = document.getElementById('detail');
  var stopped = false;

  title.textContent = T.moving;
  status.textContent = T.phases.idle;

  function render(p) {
    var total = p.bytes_total || 0;
    var done = p.bytes_done || 0;
    var pct = total > 0 ? Math.min(100, Math.round(done / total * 100)) : 0;
    if (p.phase === 'scanning' || (p.phase === 'copying' && total === 0)) {
      fill.classList.add('busy');
      fill.style.width = '';
    } else {
      fill.classList.remove('busy');
      fill.style.width = pct + '%';
    }
    status.textContent = T.phases[p.phase] || p.phase;
    detail.textContent = total > 0 && p.phase !== 'error'
      ? pct + '%  ·  ' + (p.files_done || 0) + '/' + (p.files_total || 0) + T.unit
      : '';
    if (p.phase === 'error') {
      stopped = true;
      title.textContent = T.failTitle;
      status.className = 'err';
      status.textContent = T.errors[p.error] || (T.failPrefix + (p.error || T.unknown));
    } else if (p.phase === 'done') {
      stopped = true;
      title.textContent = T.doneTitle;
      fill.classList.remove('busy');
      fill.style.width = '100%';
    }
  }

  function tick() {
    if (stopped) return;
    fetch('/migration/progress', { cache: 'no-store' })
      .then(function (r) { return r.json(); })
      .then(function (p) { render(p); setTimeout(tick, 200); })
      .catch(function () { setTimeout(tick, 400); });
  }
  tick();
</script>
</body>
</html>
"##;

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

    // POST 的 body：chrome/dispatch、window/control、rundata/apply 需要内容（JSON，独立上限
    // 64KB）；其余端点不需要 body，读完只为让 TCP 正常收尾（不读完就关连接可能触发 RST）
    let want_body = method == "POST"
        && (path == "/chrome/dispatch" || path == "/window/control" || path == "/rundata/apply");
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
        ("POST", "/window/drag") => match app.get_window("main") {
            Some(window) => match window.start_dragging() {
                Ok(()) => respond(&mut stream, 200, "{\"started\":true}"),
                Err(e) => {
                    let body = serde_json::json!({ "started": false, "error": e.to_string() }).to_string();
                    respond(&mut stream, 409, &body)
                }
            },
            None => respond(&mut stream, 409, "{\"started\":false,\"error\":\"window_missing\"}"),
        },
        ("GET", "/window/state") => match app.get_window("main") {
            Some(window) => {
                let body = serde_json::json!({
                    "maximized": window.is_maximized().unwrap_or(false),
                })
                .to_string();
                respond(&mut stream, 200, &body)
            }
            None => respond(&mut stream, 409, "{\"error\":\"window_missing\"}"),
        },
        ("POST", "/window/control") => {
            // Windows 无边框模式：自绘三大键的动作转发。close 走 window.close()
            // 触发 CloseRequested，main.rs 的既有处理会退出进程并收尸后端。
            let payload = serde_json::from_slice::<serde_json::Value>(&body)
                .unwrap_or(serde_json::Value::Null);
            let action = payload.get("action").and_then(|a| a.as_str()).unwrap_or("");
            match app.get_window("main") {
                Some(window) => {
                    let result = match action {
                        "minimize" => window.minimize(),
                        "maximize-toggle" => {
                            if window.is_maximized().unwrap_or(false) {
                                window.unmaximize()
                            } else {
                                window.maximize()
                            }
                        }
                        "close" => window.close(),
                        _ => {
                            return respond(
                                &mut stream,
                                409,
                                "{\"ok\":false,\"error\":\"invalid_action\"}",
                            );
                        }
                    };
                    match result {
                        Ok(()) => respond(&mut stream, 200, "{\"ok\":true}"),
                        Err(e) => {
                            let body = serde_json::json!({ "ok": false, "error": e.to_string() }).to_string();
                            respond(&mut stream, 409, &body)
                        }
                    }
                }
                None => respond(&mut stream, 409, "{\"ok\":false,\"error\":\"window_missing\"}"),
            }
        }
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
        ("GET", "/rundata/info") => {
            let body = crate::rundata::info_payload();
            respond(&mut stream, 200, &body)
        }
        ("POST", "/rundata/apply") => {
            let payload = serde_json::from_slice::<serde_json::Value>(&body)
                .unwrap_or(serde_json::Value::Null);
            let data_root = payload
                .get("data_root")
                .or_else(|| payload.get("dataRoot"))
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let migrate = payload
                .get("migrate")
                .and_then(|v| v.as_bool())
                .unwrap_or(false);
            match crate::rundata::schedule_data_root_change(data_root, migrate) {
                Ok(target) => {
                    let body = serde_json::json!({
                        "success": true,
                        "active_path": target.to_string_lossy(),
                        "restart_required": true,
                    })
                    .to_string();
                    respond(&mut stream, 200, &body)
                }
                Err(code) => {
                    let body =
                        serde_json::json!({ "success": false, "error": code }).to_string();
                    respond(&mut stream, 400, &body)
                }
            }
        }
        ("POST", "/rundata/restart") => {
            // 先把 202 写回去，再稍后重启——重启会杀掉后端，前端此时可能收不到响应，
            // 前端已有「连接先于响应断开视为已交给壳重启」的处理
            let result = respond(&mut stream, 202, "{\"success\":true,\"restarting\":true}");
            let handle = app.clone();
            std::thread::spawn(move || {
                std::thread::sleep(Duration::from_millis(300));
                handle.restart();
            });
            result
        }
        ("GET", "/migration") => respond_raw(
            &mut stream,
            200,
            "text/html; charset=utf-8",
            MIGRATION_PAGE,
        ),
        ("GET", "/migration/progress") => {
            let body = match crate::rundata::migration_progress().lock() {
                Ok(p) => serde_json::json!({
                    "active": p.active,
                    "phase": p.phase,
                    "files_done": p.files_done,
                    "files_total": p.files_total,
                    "bytes_done": p.bytes_done,
                    "bytes_total": p.bytes_total,
                    "error": p.error,
                })
                .to_string(),
                Err(_) => "{\"phase\":\"error\",\"error\":\"lock_poisoned\"}".to_string(),
            };
            respond(&mut stream, 200, &body)
        }
        _ => respond(&mut stream, 404, "{\"error\":\"not_found\"}"),
    }
}

fn respond(stream: &mut TcpStream, status: u16, body: &str) -> std::io::Result<()> {
    respond_raw(stream, status, "application/json; charset=utf-8", body)
}

fn respond_raw(
    stream: &mut TcpStream,
    status: u16,
    content_type: &str,
    body: &str,
) -> std::io::Result<()> {
    let reason = match status {
        200 => "OK",
        202 => "Accepted",
        404 => "Not Found",
        409 => "Conflict",
        431 => "Request Header Fields Too Large",
        _ => "OK",
    };
    let resp = format!(
        "HTTP/1.1 {status} {reason}\r\nContent-Type: {content_type}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
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
