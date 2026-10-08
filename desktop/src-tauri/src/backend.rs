//! Python 后端子进程管理与主窗口创建。
//!
//! 开发期：探测系统 python（对齐 CLI gateway.ts 的探测顺序：
//!   项目 .venv → homebrew 3.13/3.12/3.11 → PATH python3），并要求 import yaml/flask 可用；
//! 生产期（TODO）：使用打包进来的 python-build-standalone 解释器（sidecar），
//!   保证目标机器无 Python 也能运行，且 skills 可 pip install 扩展依赖。

use std::io::{BufRead, BufReader};
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{AppHandle, Manager, WebviewUrl, WebviewWindowBuilder};

/// 后端启动就绪超时（首次启动需写日志/同步角色，给足余量）
const BACKEND_READY_TIMEOUT: Duration = Duration::from_secs(60);
/// 就绪轮询间隔
const READY_POLL_INTERVAL: Duration = Duration::from_millis(200);
/// 迁移进度窗口尺寸（逻辑像素）：只放标题 + 进度条 + 一行状态，别撑大
const MIGRATION_WINDOW_SIZE: (f64, f64) = (420.0, 168.0);
/// 迁移窗口创建后先等一小段再跑迁移：给 webview 进程留出加载进度页的时间，
/// 否则小数据量下窗口还没画出来就已经关掉了
const MIGRATION_PROGRESS_LEAD_IN: Duration = Duration::from_millis(500);
/// 主线程窗口创建等待上限（超时说明主线程不可用，直接当启动失败处理）
const MAIN_WINDOW_TIMEOUT: Duration = Duration::from_secs(60);
/// 迁移进度窗口 label（run_on_main_thread 里按它关闭）
const MIGRATION_WINDOW_LABEL: &str = "migrate";

#[derive(Default)]
pub struct BackendState {
    child: Mutex<Option<Child>>,
}

/// 入口：解析运行数据目录 → （必要时先迁移）→ spawn 后端 → 等就绪 → 建主窗口。
///
/// 迁移时机说明（与 Electron 壳的关键差异）：数据目录迁移必须发生在 **spawn 后端之前**。
/// 后端在跑的时候源目录持续被写入，上游那套「复制完再统计源目录做校验」会稳定误判
/// verification_failed（本机实测日志追加场景 10 次失败 9 次），而且放宽校验只会把
/// 「明确报错」换成「静默切到撕裂的副本」，更糟。停机迁移则精确且可校验。
pub fn start_backend_and_create_window(app: &AppHandle) -> Result<(), String> {
    // 控制桥（自动更新 + 迁移进度页）：失败不致命——应用照常运行，仅更新与进度页不可用。
    let bridge_port = match crate::bridge::start_bridge(app) {
        Ok(p) => Some(p),
        Err(err) => {
            eprintln!("[astrion-desktop] 控制桥启动失败（更新功能不可用）: {err}");
            None
        }
    };

    // 命令环境：Windows 上把注册表里进程缺失的 PATH 条目追加到末尾（只增不减）
    let shell_path = crate::shell_env::shell_path_additions();

    let pending = crate::rundata::read_settings().pending_migration.is_some();

    if pending && bridge_port.is_some() {
        // 进度窗口必须能画出东西，而 setup 是同步阻塞的（阻塞即冻结事件循环）——
        // 所以先同步建窗口再返回 setup，迁移与后端启动全交给后台线程。
        // 迁移期间窗口始终 ≥ 1（退出的前提是零窗口），不存在意外自杀风险。
        create_migration_window(app, bridge_port.expect("已判非空"))?;
        let handle = app.clone();
        std::thread::spawn(move || {
            std::thread::sleep(MIGRATION_PROGRESS_LEAD_IN);
            let data_root = crate::rundata::run_pending_migration();
            match spawn_backend_and_wait(&handle, bridge_port, shell_path, data_root) {
                Ok(port) => show_main_window(&handle, port),
                Err(err) => {
                    eprintln!("[astrion-desktop] 启动后端失败: {err}");
                    let log_path = std::env::temp_dir().join("astrion-desktop-startup.log");
                    let _ = std::fs::write(&log_path, format!("[astrion-desktop] 启动失败\n{err}\n"));
                    handle.exit(1);
                }
            }
        });
        return Ok(());
    }

    // 无待迁移（或桥不可用、进度页无法展示）：沿用同步路径
    let data_root = if pending {
        crate::rundata::run_pending_migration()
    } else {
        crate::rundata::resolve_data_root()
    };
    let port = spawn_backend_and_wait(app, bridge_port, shell_path, data_root)?;
    create_main_window(app, port).map_err(|e| format!("创建主窗口失败: {e}"))
}

/// 迁移进度窗口：页面由控制桥自身 serve（此刻后端还没启动，没有别的宿主）。
/// 保留原生标题栏——迁移卡住时用户至少能关掉窗口退出（下次启动会重试迁移）。
fn create_migration_window(app: &AppHandle, bridge_port: u16) -> Result<(), String> {
    let url = format!("http://127.0.0.1:{bridge_port}/migration");
    WebviewWindowBuilder::new(
        app,
        MIGRATION_WINDOW_LABEL,
        WebviewUrl::External(url.parse().map_err(|e| format!("进度页地址无效: {e}"))?),
    )
    .title("Astrion")
    .inner_size(MIGRATION_WINDOW_SIZE.0, MIGRATION_WINDOW_SIZE.1)
    .resizable(false)
    .maximizable(false)
    .minimizable(false)
    .always_on_top(true)
    .center()
    .build()
    .map_err(|e| format!("创建迁移进度窗口失败: {e}"))?;
    Ok(())
}

/// 后台线程 → 主线程：建主窗口（先建后关，任意时刻至少有一个窗口存活）
fn show_main_window(app: &AppHandle, port: u16) {
    let handle = app.clone();
    let (tx, rx) = std::sync::mpsc::channel::<Result<(), String>>();
    let dispatched = app.run_on_main_thread(move || {
        let result = create_main_window(&handle, port).map_err(|e| e.to_string());
        if result.is_ok() {
            close_migration_window(&handle);
        }
        let _ = tx.send(result);
    });
    if let Err(err) = dispatched {
        eprintln!("[astrion-desktop] 主线程调度失败，主窗口未创建: {err}");
        app.exit(1);
        return;
    }
    match rx.recv_timeout(MAIN_WINDOW_TIMEOUT) {
        Ok(Ok(())) => {}
        Ok(Err(err)) => {
            eprintln!("[astrion-desktop] 创建主窗口失败: {err}");
            app.exit(1);
        }
        Err(_) => {
            eprintln!("[astrion-desktop] 创建主窗口超时");
            app.exit(1);
        }
    }
}

fn close_migration_window(app: &AppHandle) {
    if let Some(window) = app.get_webview_window(MIGRATION_WINDOW_LABEL) {
        let _ = window.close();
    }
}

/// 解析 Python 与后端源码目录：生产优先内嵌运行时，否则开发形态（系统 python + 源码树）。
fn resolve_runtime(app: &AppHandle) -> Result<(PathBuf, PathBuf), String> {
    // 生产优先：.app 内嵌的运行时（python-build-standalone + 预装依赖 + 源码）；
    // 不存在则回退开发模式：系统 python + 源码树。
    if let Some(pair) = resolve_embedded_runtime(app) {
        return Ok(pair);
    }
    let repo_root = resolve_repo_root()?;
    let python = detect_python(&repo_root)
        .ok_or_else(|| "未找到可用 Python（需要 import yaml/flask 可用）".to_string())?;
    Ok((python, repo_root))
}

/// 选端口 → spawn 后端 → 等就绪；返回后端端口。
fn spawn_backend_and_wait(
    app: &AppHandle,
    bridge_port: Option<u16>,
    shell_path: Option<Vec<String>>,
    data_root: PathBuf,
) -> Result<u16, String> {
    let port = pick_free_port()?;
    let (python, backend_dir) = resolve_runtime(app)?;
    let child = spawn_backend(
        &python,
        &backend_dir,
        port,
        bridge_port,
        &data_root,
        shell_path.as_deref(),
    )?;
    app.state::<BackendState>()
        .child
        .lock()
        .map_err(|_| "state lock poisoned")?
        .replace(child);

    wait_backend_ready(port)?;
    Ok(port)
}

/// 退出时 kill 后端子进程（Drop 兜底，防止孤儿进程残留）。
pub fn shutdown_backend(app: &AppHandle) {
    if let Some(state) = app.try_state::<BackendState>() {
        if let Ok(mut guard) = state.child.lock() {
            if let Some(mut child) = guard.take() {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
    }
}

impl Drop for BackendState {
    fn drop(&mut self) {
        if let Ok(mut guard) = self.child.lock() {
            if let Some(mut child) = guard.take() {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
    }
}

fn pick_free_port() -> Result<u16, String> {
    let listener = TcpListener::bind("127.0.0.1:0").map_err(|e| format!("分配端口失败: {e}"))?;
    let port = listener.local_addr().map_err(|e| e.to_string())?.port();
    // 释放后由后端绑定，存在理论竞态（被其他进程抢占），本地场景可接受
    drop(listener);
    Ok(port)
}

/// 内嵌运行时解析（生产形态）：resource_dir 下的 runtime/python + runtime/backend。
/// 返回 (python 解释器, 后端源码目录)；不存在（开发形态）返回 None。
fn resolve_embedded_runtime(app: &AppHandle) -> Option<(PathBuf, PathBuf)> {
    let resource_dir = app.path().resource_dir().ok()?;
    let python = if cfg!(windows) {
        // 0.3.2 起后端进程名唯一化（astrion-backend.exe）：NSIS 安装/卸载钩子
        // 按进程名精确清理残留后端，杀 python.exe 会误伤用户系统 Python。
        // 旧包（或开发期未跑 prepare_backend）无此文件时回退 python.exe。
        let renamed = resource_dir.join("runtime/python/astrion-backend.exe");
        if renamed.exists() {
            renamed
        } else {
            resource_dir.join("runtime/python/python.exe")
        }
    } else {
        resource_dir.join("runtime/python/bin/python3.12")
    };
    let backend = resource_dir.join("runtime/backend");
    if python.exists() && backend.join("server/app.py").exists() {
        Some((python, backend))
    } else {
        None
    }
}

/// 项目根目录解析：
/// - 环境变量 ASTRION_DESKTOP_REPO 显式覆盖（调试用）；
/// - 开发期：CARGO_MANIFEST_DIR（desktop/src-tauri）向上两级即仓库根；
/// - 生产期：由 resolve_embedded_runtime 接管，不会走到这里。
fn resolve_repo_root() -> Result<PathBuf, String> {
    if let Ok(explicit) = std::env::var("ASTRION_DESKTOP_REPO") {
        let p = PathBuf::from(explicit);
        if p.join("server/app.py").exists() {
            return Ok(p);
        }
        return Err(format!("ASTRION_DESKTOP_REPO 指向的目录不含 server/app.py: {}", p.display()));
    }
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let root = manifest
        .parent()
        .and_then(|p| p.parent())
        .map(|p| p.to_path_buf())
        .ok_or_else(|| "无法推导仓库根目录".to_string())?;
    if !root.join("server/app.py").exists() {
        return Err(format!("推导的仓库根不含 server/app.py: {}", root.display()));
    }
    Ok(root)
}

/// python 候选解释器（按优先级）：要求 `import yaml, flask` 成功才接受。
fn detect_python(repo_root: &Path) -> Option<PathBuf> {
    python_candidates(repo_root)
        .into_iter()
        .find(|c| python_has_deps(c))
}

/// macOS / Linux 开发形态候选：项目 .venv → homebrew → PATH python3。
#[cfg(not(windows))]
fn python_candidates(repo_root: &Path) -> Vec<PathBuf> {
    let mut candidates: Vec<PathBuf> = Vec::new();
    candidates.push(repo_root.join(".venv/bin/python"));
    for v in ["python3.13", "python3.12", "python3.11", "python3"] {
        candidates.push(PathBuf::from(format!("/opt/homebrew/bin/{v}")));
    }
    if let Ok(path_var) = std::env::var("PATH") {
        for dir in path_var.split(':') {
            candidates.push(PathBuf::from(dir).join("python3"));
        }
    }
    candidates
}

/// Windows 开发形态候选：项目 .venv → py launcher（3.13→3.12→3.11→3）→ PATH python.exe。
/// 注意 WindowsApps 下的 python.exe 是商店占位符（运行会弹商店而非执行），
/// python_has_deps 的真实 import 检查会自动把它过滤掉。
#[cfg(windows)]
fn python_candidates(repo_root: &Path) -> Vec<PathBuf> {
    let mut candidates: Vec<PathBuf> = Vec::new();
    candidates.push(repo_root.join(".venv/Scripts/python.exe"));
    for v in ["3.13", "3.12", "3.11", "3"] {
        if let Some(p) = resolve_py_launcher(v) {
            candidates.push(p);
        }
    }
    if let Ok(path_var) = std::env::var("PATH") {
        for dir in path_var.split(';') {
            candidates.push(PathBuf::from(dir).join("python.exe"));
        }
    }
    candidates
}

/// 通过 py launcher 解析指定版本的真实解释器路径（py 是启动器不是解释器，
/// spawn 需要真实路径，故用 `-c "import sys; print(sys.executable)"` 解析）。
#[cfg(windows)]
fn resolve_py_launcher(version: &str) -> Option<PathBuf> {
    let output = Command::new("py")
        .args([format!("-{version}"), "-c".into(), "import sys; print(sys.executable)".into()])
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let path = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if path.is_empty() {
        None
    } else {
        Some(PathBuf::from(path))
    }
}

fn python_has_deps(python: &Path) -> bool {
    if !python.exists() {
        return false;
    }
    Command::new(python)
        .args(["-c", "import yaml, flask, httpx, openai"])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

/// 子进程环境清洗前缀（数据越权事故修复）：
/// 启动壳的 shell 可能是某个运行中实例（CLI/开发实例）的子进程，其环境携带
/// 他人的数据根/密钥/端口配置（ASTRION_DATA_ROOT、AGENT_CFG_* 等）。
/// 继承后桌面后端会直接跑在别的实例数据目录上（两实例共享读写，设置互相覆盖）。
/// 一律剥离；数据根由桌面壳在下面显式指定，确定性隔离。
const CHILD_ENV_SCRUB_PREFIXES: [&str; 2] = ["ASTRION_", "AGENT_"];

fn spawn_backend(
    python: &Path,
    backend_dir: &Path,
    port: u16,
    bridge_port: Option<u16>,
    data_root: &Path,
    shell_path: Option<&[String]>,
) -> Result<Child, String> {
    // --path 语义为「兜底默认工作区」，桌面首启由用户在引导流程中自行创建。
    // 生产形态下 backend_dir 在 .app 内（只读），兜底路径给用户主目录；
    // 开发形态给源码树的 project/（原行为）。
    // Windows 无 HOME 变量，走 USERPROFILE 兜底。
    let default_ws = std::env::var_os("HOME")
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("USERPROFILE").map(PathBuf::from))
        .unwrap_or_else(|| backend_dir.join("project"));
    let mut cmd = Command::new(python);
    cmd.current_dir(backend_dir)
        .args([
            "-m", "server.app",
            "--port", &port.to_string(),
            "--path", &default_ws.to_string_lossy(),
        ])
        // .app 内为只读目录：禁写 __pycache__（staging 已用内嵌解释器预编译，
        // 运行时直接读现成 pyc；开发形态下 python 会自行写缓存，无副作用）
        .env("PYTHONDONTWRITEBYTECODE", "1")
        // Windows 中文系统默认代码页 GBK：后端 print 含 emoji/Unicode 符号时
        // GBK 编码直接崩溃（UnicodeEncodeError）。PEP 540 UTF-8 模式根治：
        // stdio 全部 UTF-8，无视系统代码页。macOS/Linux 本就 UTF-8，无害。
        .env("PYTHONUTF8", "1");

    // 环境清洗：剥离全部 ASTRION_*/AGENT_* 继承变量（双向泄漏都防：
    // 既不读他人的数据根，也不把自己的配置透给后端）。
    for (key, _) in std::env::vars() {
        if CHILD_ENV_SCRUB_PREFIXES.iter().any(|p| key.starts_with(p)) {
            cmd.env_remove(&key);
        }
    }
    // 桌面版数据根：由壳侧统一解析（ASTRION_DESKTOP_DATA_ROOT > 设置文件 > 默认目录），
    // 固定独立目录，绝不与任何 server 实例（8091/8092 等）共享。
    // 显式设置在清洗之后（否则会被上面的 env_remove 抹掉）。
    cmd.env("ASTRION_DATA_ROOT", data_root);
    // Desktop Host is password-exempt and must remain loopback-only.
    cmd.env("ASTRION_IGNORE_DOTENV", "1");
    cmd.env("TERMINAL_SANDBOX_MODE", "host");
    cmd.env("WEB_SERVER_HOST", "127.0.0.1");

    // 命令环境：Windows 上没有「登录 shell」概念，环境变量的权威来源是注册表。
    // 从资源管理器/开始菜单启动的 GUI 进程只拿到系统变量，用户自己装的
    // node/python/git 等不在 PATH 里——智能体执行的命令会找不到它们。
    // 口径对齐 PowerShell 团队给 VS Code 的建议：读注册表，但【只做增量】——
    // 进程已有条目顺序原样保留，注册表里多出来的追加到末尾（整体替换会把
    // “用户专门带特定环境启动”的场景破坏掉）。
    if let Some(additions) = shell_path {
        let current = std::env::var("PATH").unwrap_or_default();
        cmd.env("PATH", crate::shell_env::merge_path_entries(&current, additions));
    }

    // 桌面应用身份与控制桥地址：后端据此判定「自己是桌面壳内嵌实例」并代理更新接口。
    // 显式设置在清洗之后（同 ASTRION_DATA_ROOT 模式），不受继承环境影响。
    cmd.env("ASTRION_DESKTOP_VERSION", crate::bridge::APP_VERSION);
    if let Some(bp) = bridge_port {
        cmd.env("ASTRION_DESKTOP_BRIDGE_PORT", bp.to_string());
    }

    // Windows 桌面壳是 GUI 子系统进程（release 下 windows_subsystem），
    // 若不加 CREATE_NO_WINDOW，spawn 控制台子进程（python.exe）会弹出黑框终端窗口。
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }

    let mut child = cmd
        // 后端 stdout/stderr 均接管转发（dev 终端可见；release 无控制台，
        // 后端日志走自身 logs/ 文件）
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("spawn 后端失败: {e}"))?;

    // Windows：后端挂入 KILL_ON_JOB_CLOSE 的 Job Object。壳任何方式退出
    // （正常关窗 / 崩溃 / 被 taskkill / updater 启动安装器后 process::exit 自杀）
    // 都会触发内核收尸，根治后端孤儿残留（锁安装目录文件导致覆盖安装失败、
    // 占用端口）。失败仅降级为无 job 保护，不阻断启动。
    #[cfg(windows)]
    attach_kill_on_close_job(&child);

    // 独立线程转发后端 stdout/stderr，避免管道打满阻塞后端
    if let Some(stdout) = child.stdout.take() {
        std::thread::spawn(move || {
            let reader = BufReader::new(stdout);
            for line in reader.lines().map_while(Result::ok) {
                println!("[astrion-backend] {line}");
            }
        });
    }
    if let Some(stderr) = child.stderr.take() {
        std::thread::spawn(move || {
            let reader = BufReader::new(stderr);
            for line in reader.lines().map_while(Result::ok) {
                eprintln!("[astrion-backend] {line}");
            }
        });
    }
    Ok(child)
}

/// 轮询无鉴权端点直到后端就绪。手写最小 HTTP/1.0 GET，避免引入 HTTP 客户端依赖。
fn wait_backend_ready(port: u16) -> Result<(), String> {
    let deadline = Instant::now() + BACKEND_READY_TIMEOUT;
    while Instant::now() < deadline {
        if http_get_ok(port, "/api/host-mode-enabled") {
            return Ok(());
        }
        std::thread::sleep(READY_POLL_INTERVAL);
    }
    Err(format!("后端 {BACKEND_READY_TIMEOUT:?} 内未就绪（127.0.0.1:{port}）"))
}

fn http_get_ok(port: u16, path: &str) -> bool {
    use std::io::{Read, Write};
    let Ok(mut stream) = std::net::TcpStream::connect(("127.0.0.1", port)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(800)));
    let req = format!("GET {path} HTTP/1.0\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n");
    if stream.write_all(req.as_bytes()).is_err() {
        return false;
    }
    let mut buf = String::new();
    if stream.read_to_string(&mut buf).is_err() {
        return false;
    }
    // 只关心状态行是否为 2xx
    buf.lines()
        .next()
        .map(|status| status.contains(" 200") || status.contains(" 204"))
        .unwrap_or(false)
}

/// 顶部 chrome 标签条高度（CSS px）。前端 _tab-strip.scss 的 46px 与主页面
/// 预留空间都对齐这个值；改动需三处同步。
pub const CHROME_STRIP_HEIGHT: f64 = 46.0;

/// 前端平台标记：壳注入 window.__ASTRION_PLATFORM__，chrome 标签条据此做平台
/// 差异化 UI——macOS 左侧预留红绿灯悬浮区；Windows 原生标题栏自带三大键，
/// 标签条左侧预留位改放「设置」入口按钮（见 ConversationTabStrip.vue）。
#[cfg(windows)]
const PLATFORM_MARKER: &str = "window.__ASTRION_PLATFORM__ = 'windows';";
#[cfg(target_os = "macos")]
const PLATFORM_MARKER: &str = "window.__ASTRION_PLATFORM__ = 'macos';";
#[cfg(all(unix, not(target_os = "macos")))]
const PLATFORM_MARKER: &str = "window.__ASTRION_PLATFORM__ = 'linux';";
#[cfg(windows)]
const QUICK_SETTINGS_SCRIPT: &str = include_str!("../../../static/quick-capture/windows-settings.js");
#[cfg(not(windows))]
const QUICK_SETTINGS_SCRIPT: &str = "";

fn create_main_window(app: &AppHandle, port: u16) -> tauri::Result<()> {
    let url = format!("http://127.0.0.1:{port}/");
    let chrome_url = format!("http://127.0.0.1:{port}/chrome");
    let mut builder = WebviewWindowBuilder::new(app, "main", WebviewUrl::External(url.parse().expect("valid url")))
        .title("Astrion")
        .inner_size(1280.0, 800.0)
        .min_inner_size(960.0, 600.0);
    // macOS 顶部浏览器式对话标签条：标题栏 Overlay（红绿灯悬浮在 chrome webview 之上），
    // 隐藏窗口标题文字。红绿灯位置不再走 traffic_light_position——tauri #14072：
    // unstable feature 下该 API 失效、灯组被钉在 (0,0)；改为窗口创建后用 objc2
    // 手动定位（见 position_traffic_lights，在 create 后与每次 Resized 时调用）。
    #[cfg(target_os = "macos")]
    {
        builder = builder
            .title_bar_style(tauri::TitleBarStyle::Overlay)
            .hidden_title(true);
    }
    // Windows：去掉原生标题栏——顶部 chrome 标签条即标题栏（浏览器式：左侧设置
    // 入口/新建/标签，右侧自绘最小化/最大化/关闭，经控制桥 /window/control 转发）。
    // shadow(true) 恢复 DWM 阴影 + Win11 圆角；tao 的 MARKER_UNDECORATED_SHADOW
    // 保留四边缩放与拖边吸附（详见 tao windows event_loop WM_NCHITTEST 处理）。
    #[cfg(windows)]
    {
        builder = builder.decorations(false).shadow(true);
    }
    let window = builder
        // 桌面壳环境标记：页面在任意脚本执行前可读到（登录页据此自动免登录）。
        // 不用 withGlobalTauri——它对 External URL 页面不注入，且语义过重。
        // 平台标记供 chrome 标签条做平台差异化 UI（Windows 左侧设置按钮）。
        .initialization_script(&format!(
            "window.__ASTRION_DESKTOP__ = true; {PLATFORM_MARKER} {QUICK_SETTINGS_SCRIPT}"
        ))
        // 恢复 HTML5 文件拖放：Tauri 2 默认 dragDropEnabled=true，壳会拦截系统拖放
        // 改发 tauri://drag-drop 事件、吃掉页面自身的 drop 事件；而 External URL
        // 页面没有 Tauri JS API，事件无人接收，拖文件进窗口直接失效。关掉壳的拖放
        // 处理，前端 drag.ts 的 HTML5 dragenter/dragover/drop 链路即恢复原样工作。
        .disable_drag_drop_handler()
        // 外链导航拦截：只放行后端同源导航（页面自身路由/刷新）；其余 http(s)
        // 一律拦下并转交系统默认浏览器。WebView 里 window.open 不可用、
        // <a href> 跳转会顶替应用页面（点模型输出的链接把 Astrion 变成目标站），
        // 统一在壳侧根治，不依赖每个前端入口自觉走 openExternal。
        .on_navigation(move |nav_url| {
            if nav_url.scheme() != "http" && nav_url.scheme() != "https" {
                return true; // about:blank / data: / blob: 等非页面跳转放行
            }
            // 回环地址一律放行：预览面板的 iframe（独立预览服务器端口 / 智能体起的
            // dev server 端口）就是回环非同源——拦截会把预览内容顶去系统浏览器，
            // 与「窗口内预览」语义冲突；聊天里的回环链接点击由前端 document 级
            // capture 拦截转预览面板（App.vue），到不了这里。
            if matches!(nav_url.host_str(), Some("127.0.0.1") | Some("localhost") | Some("::1")) {
                return true;
            }
            open_in_system_browser(nav_url.as_str());
            false
        })
        .build()?;

    // 顶部对话标签条 = 独立 chrome webview（加载后常驻，主页面导航/刷新/进设置页
    // 都不会让它重载或闪动）。主 webview 显式钉在 chrome 条下方：
    // tauri 默认的按比例 auto_resize 会把固定 46px 高度也缩放，所以两个 webview
    // 都关掉比例缩放，由 layout_webviews 在窗口缩放时手动布局。
    let main_webview = window.get_webview("main").expect("主 webview 与窗口同 label");
    let _ = main_webview.set_auto_resize(false);
    let chrome_builder = tauri::WebviewBuilder::new(
        "chrome",
        WebviewUrl::External(chrome_url.parse().expect("valid url")),
    )
    // chrome 页同样有桌面标记（标签 store 以 __ASTRION_DESKTOP__ 判定启用）；
    // __ASTRION_CHROME__ 供页面自检加载位置；平台标记同上。
    .initialization_script(&format!(
        "window.__ASTRION_DESKTOP__ = true; window.__ASTRION_CHROME__ = true; {PLATFORM_MARKER}"
    ))
    // 不抢键盘焦点（输入主权始终在主页面）
    .focused(false);
    // add_child 定义在 Window 上（unstable）；窗口与主 webview 同 label（main），
    // WebviewWindow 无 Deref 到 Webview，经 Manager::get_window 取宿主 Window。
    let host_window = window
        .get_window("main")
        .expect("主窗口与主 webview 同 label");
    host_window.add_child(
        chrome_builder,
        tauri::LogicalPosition::new(0.0, 0.0),
        tauri::LogicalSize::new(1280.0, CHROME_STRIP_HEIGHT),
    )?;
    layout_webviews(&host_window);
    #[cfg(windows)]
    crate::single_instance::main_window_ready(app);
    #[cfg(target_os = "macos")]
    position_traffic_lights(&host_window);
    #[cfg(target_os = "macos")]
    install_ctrl_return_send_interceptor(app);
    #[cfg(windows)]
    if let Err(error) = crate::quick::start(app, port, crate::rundata::resolve_data_root()) {
        let _ = std::fs::write(std::env::temp_dir().join("astrion-quick-startup.log"), error);
    }
    Ok(())
}

/// macOS Sequoia（15+）起 Ctrl+Return 是系统级「打开上下文菜单」快捷键：
/// 按键在 AppKit 层被消费、DOM keydown 收不到——输入栏 Ctrl+Enter 发送失效
/// 且弹出系统右键菜单。本地事件监视器在事件到达 WKWebView 前拦截该组合键：
/// 吞掉原生事件（系统菜单不再触发），并向主 webview 注入合成 ctrl+Enter
/// keydown——输入栏 ProseMirror 既有 handler 收到后走正常发送链路。
/// （页面内 preventDefault 拦不住系统快捷键，必须在原生层拦截。）
#[cfg(target_os = "macos")]
fn install_ctrl_return_send_interceptor(app: &AppHandle) {
    use block2::RcBlock;
    use objc2_app_kit::{NSEvent, NSEventMask, NSEventModifierFlags};
    use std::ptr::NonNull;

    let handle = app.clone();
    let block = RcBlock::new(move |event: NonNull<NSEvent>| -> *mut NSEvent {
        let should_swallow = unsafe {
            let ev = event.as_ref();
            let flags = ev.modifierFlags();
            let ctrl_only = flags.contains(NSEventModifierFlags::Control)
                && !flags.contains(NSEventModifierFlags::Command)
                && !flags.contains(NSEventModifierFlags::Option);
            // keyCode 36 = Return，76 = 小键盘 Enter
            ctrl_only && matches!(ev.keyCode(), 36 | 76)
        };
        if !should_swallow {
            return event.as_ptr();
        }
        if let Some(webview) = handle.get_webview("main") {
            // 焦点不在编辑器时派发到 activeElement 无害（无 handler 消费）
            let js = r#"(() => {
    const el = document.activeElement;
    if (!el || typeof el.dispatchEvent !== "function") return;
    el.dispatchEvent(new KeyboardEvent("keydown", {
        key: "Enter", code: "Enter",
        ctrlKey: true, bubbles: true, cancelable: true
    }));
})();"#;
            let _ = webview.eval(js);
        }
        std::ptr::null_mut()
    });
    let monitor = unsafe {
        NSEvent::addLocalMonitorForEventsMatchingMask_handler(NSEventMask::KeyDown, &block)
    };
    // 监视器需存活整个应用生命周期；进程退出时由系统回收
    std::mem::forget(monitor);
}

/// macOS 红绿灯手动定位（tauri #14072 绕行：unstable 下 builder API 钉死 (0,0)）。
/// 实测纠偏法：直接量出「关闭按钮顶边到窗口顶」的当前距离，与目标值的差量
/// 整体平移 row view——不依赖 tao 容器高度 trick 的锚定行为（实测偏上），
/// 每次调用都从实测位置重算，天然冪等。间距取「上下相等、左距相同」：
/// spacing = (46 - 按钮实测高) / 2（SDK26=16 → 15；旧 SDK=14 → 16），x = y = spacing。
/// 创建后与每次 Resized 时调用（AppKit 在全屏/缩放时会重排灯组）。
#[cfg(target_os = "macos")]
pub fn position_traffic_lights(window: &tauri::Window) {
    use objc2_app_kit::{NSWindow, NSWindowButton};

    let Ok(ptr) = window.ns_window() else {
        return;
    };
    if ptr.is_null() {
        return;
    }
    unsafe {
        let ns_window = &*(ptr as *const NSWindow);
        let (Some(close), Some(miniaturize), Some(zoom)) = (
            ns_window.standardWindowButton(NSWindowButton::CloseButton),
            ns_window.standardWindowButton(NSWindowButton::MiniaturizeButton),
            ns_window.standardWindowButton(NSWindowButton::ZoomButton),
        ) else {
            return;
        };
        let close_rect = close.frame();
        let btn_h = close_rect.size.height;
        let spacing = ((CHROME_STRIP_HEIGHT - btn_h) / 2.0).max(0.0);
        let x: f64 = std::env::var("ASTRION_TRAFFIC_LIGHT_X")
            .ok()
            .and_then(|v| v.parse().ok())
            .unwrap_or(spacing);
        let y: f64 = std::env::var("ASTRION_TRAFFIC_LIGHT_Y")
            .ok()
            .and_then(|v| v.parse().ok())
            .unwrap_or(spacing);

        let Some(row) = close.superview() else {
            return;
        };
        let Some(container) = row.superview() else {
            return;
        };
        // 垂直纠偏：Cocoa 坐标原点在左下，按钮顶边到窗口顶的距离 =
        // 窗口高 - (容器.y + row.y + 按钮.y + 按钮高)。要增大顶距就减小 origin.y。
        let window_h = ns_window.frame().size.height;
        let current_top = window_h
            - (container.frame().origin.y + row.frame().origin.y + close_rect.origin.y + btn_h);
        let dy = y - current_top;
        if dy.abs() > 0.1 {
            let mut origin = row.frame().origin;
            origin.y -= dy;
            row.setFrameOrigin(origin);
        }
        // 水平：各按钮 x = x + i*间距（间距保持系统原值）
        let space_between = miniaturize.frame().origin.x - close_rect.origin.x;
        for (i, button) in [close, miniaturize, zoom].into_iter().enumerate() {
            let mut origin = button.frame().origin;
            origin.x = x + i as f64 * space_between;
            button.setFrameOrigin(origin);
        }
    }
}

/// 双 webview 布局：chrome 条钉顶部（全宽 × 固定 46px），主 webview 占剩余区域。
/// 窗口创建后与每次 Resized 时调用（main.rs 的 on_window_event）。
pub fn layout_webviews(window: &tauri::Window) {
    // Manager::get_webview is application-wide: never size main/chrome from a
    // quick-chat or screenshot-overlay window's resize event.
    if window.label() != "main" {
        return;
    }
    let Ok(size) = window.inner_size() else {
        return;
    };
    let scale = window.scale_factor().unwrap_or(1.0);
    let w = size.width as f64 / scale;
    let h = size.height as f64 / scale;
    if let Some(chrome) = window.get_webview("chrome") {
        let _ = chrome.set_bounds(tauri::Rect {
            position: tauri::LogicalPosition::new(0.0, 0.0).into(),
            size: tauri::LogicalSize::new(w, CHROME_STRIP_HEIGHT).into(),
        });
    }
    if let Some(main) = window.get_webview("main") {
        let _ = main.set_bounds(tauri::Rect {
            position: tauri::LogicalPosition::new(0.0, CHROME_STRIP_HEIGHT).into(),
            size: tauri::LogicalSize::new(w, (h - CHROME_STRIP_HEIGHT).max(0.0)).into(),
        });
    }
}

/// 用系统默认浏览器打开外部 URL（导航拦截的转交目标）。
/// 不引 tauri-plugin-shell：三个平台各一行系统命令即可。
/// Windows 用 explorer.exe 而非 cmd /c start——后者是控制台子系统，
/// 从 GUI 壳 spawn 会闪黑框；explorer.exe 是 GUI 程序，无此问题。
fn open_in_system_browser(url: &str) {
    #[cfg(windows)]
    {
        let _ = Command::new("explorer.exe").arg(url).spawn();
    }
    #[cfg(target_os = "macos")]
    {
        let _ = Command::new("open").arg(url).spawn();
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        let _ = Command::new("xdg-open").arg(url).spawn();
    }
}

/// Windows：创建 Job Object 并挂入后端子进程，设 KILL_ON_JOB_CLOSE。
///
/// job 句柄故意泄漏不 CloseHandle：句柄生命周期 == 壳进程生命周期，壳死亡时
/// 内核回收句柄、触发 kill-on-close 杀光 job 内进程（含后端 spawn 的孙进程）。
/// 这正是兜底收尸语义，与 shutdown_backend 的正常链路互补而非冲突。
#[cfg(windows)]
fn attach_kill_on_close_job(child: &Child) {
    use std::os::windows::io::AsRawHandle;
    use windows_sys::Win32::Foundation::CloseHandle;
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
        SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };

    unsafe {
        let job = CreateJobObjectW(std::ptr::null_mut(), std::ptr::null());
        if job.is_null() {
            eprintln!(
                "[astrion-desktop] 创建 Job Object 失败（后端无崩溃收尸保护）: {}",
                std::io::Error::last_os_error()
            );
            return;
        }

        let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        if SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            &info as *const _ as *const std::ffi::c_void,
            std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        ) == 0
        {
            eprintln!(
                "[astrion-desktop] 配置 Job Object 失败: {}",
                std::io::Error::last_os_error()
            );
            let _ = CloseHandle(job);
            return;
        }

        if AssignProcessToJobObject(job, child.as_raw_handle() as _) == 0 {
            eprintln!(
                "[astrion-desktop] 后端挂入 Job Object 失败: {}",
                std::io::Error::last_os_error()
            );
            let _ = CloseHandle(job);
        }
        // 成功路径不 CloseHandle(job)：泄漏至进程退出，由内核完成收尸
    }
}


