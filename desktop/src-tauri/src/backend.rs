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

#[derive(Default)]
pub struct BackendState {
    child: Mutex<Option<Child>>,
}

/// 入口：选端口 → spawn 后端 → 等就绪 → 建主窗口。
pub fn start_backend_and_create_window(app: &AppHandle) -> Result<(), String> {
    let port = pick_free_port()?;
    // 生产优先：.app 内嵌的运行时（python-build-standalone + 预装依赖 + 源码）；
    // 不存在则回退开发模式：系统 python + 源码树。
    let (python, backend_dir) = match resolve_embedded_runtime(app) {
        Some(pair) => pair,
        None => {
            let repo_root = resolve_repo_root()?;
            let python = detect_python(&repo_root)
                .ok_or_else(|| "未找到可用 Python（需要 import yaml/flask 可用）".to_string())?;
            (python, repo_root)
        }
    };

    // 控制桥（自动更新）：失败不致命——应用照常运行，仅更新功能不可用。
    let bridge_port = match crate::bridge::start_bridge(app) {
        Ok(p) => Some(p),
        Err(err) => {
            eprintln!("[astrion-desktop] 控制桥启动失败（更新功能不可用）: {err}");
            None
        }
    };

    let child = spawn_backend(&python, &backend_dir, port, bridge_port)?;
    app.state::<BackendState>()
        .child
        .lock()
        .map_err(|_| "state lock poisoned")?
        .replace(child);

    wait_backend_ready(port)?;

    create_main_window(app, port).map_err(|e| format!("创建主窗口失败: {e}"))?;
    Ok(())
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
    // 桌面版数据根：固定独立目录，绝不与任何 server 实例（8091/8092 等）共享。
    // ASTRION_DESKTOP_DATA_ROOT 仅供测试/调试覆盖（从父环境读取，不受清洗影响）。
    let data_root = std::env::var_os("ASTRION_DESKTOP_DATA_ROOT")
        .map(PathBuf::from)
        .or_else(|| {
            std::env::var_os("HOME")
                .map(|h| PathBuf::from(h).join(".astrion").join("astrion-desktop"))
        })
        .or_else(|| {
            std::env::var_os("USERPROFILE")
                .map(|h| PathBuf::from(h).join(".astrion").join("astrion-desktop"))
        });
    if let Some(root) = data_root {
        cmd.env("ASTRION_DATA_ROOT", root);
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

fn create_main_window(app: &AppHandle, port: u16) -> tauri::Result<()> {
    let url = format!("http://127.0.0.1:{port}/");
    WebviewWindowBuilder::new(app, "main", WebviewUrl::External(url.parse().expect("valid url")))
        .title("Astrion")
        .inner_size(1280.0, 800.0)
        .min_inner_size(960.0, 600.0)
        // 桌面壳环境标记：页面在任意脚本执行前可读到（登录页据此自动免登录）。
        // 不用 withGlobalTauri——它对 External URL 页面不注入，且语义过重。
        .initialization_script("window.__ASTRION_DESKTOP__ = true;")
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
            let is_backend = matches!(nav_url.host_str(), Some("127.0.0.1") | Some("localhost"))
                && nav_url.port() == Some(port);
            if !is_backend {
                open_in_system_browser(nav_url.as_str());
            }
            is_backend
        })
        .build()?;
    Ok(())
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


