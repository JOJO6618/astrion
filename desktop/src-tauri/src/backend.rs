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

    let child = spawn_backend(&python, &backend_dir, port)?;
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
        resource_dir.join("runtime/python/python.exe")
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
    for c in candidates {
        if python_has_deps(&c) {
            return Some(c);
        }
    }
    None
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

fn spawn_backend(python: &Path, backend_dir: &Path, port: u16) -> Result<Child, String> {
    // --path 语义为「兜底默认工作区」，桌面首启由用户在引导流程中自行创建。
    // 生产形态下 backend_dir 在 .app 内（只读），兜底路径给用户主目录；
    // 开发形态给源码树的 project/（原行为）。
    let default_ws = std::env::var_os("HOME")
        .map(PathBuf::from)
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
        .env("PYTHONDONTWRITEBYTECODE", "1");

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

    let mut child = cmd
        // 后端日志直通壳 stderr（tauri dev 控制台可见）；生产期可改接文件
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .map_err(|e| format!("spawn 后端失败: {e}"))?;

    // 独立线程转发后端 stdout，避免管道打满阻塞后端
    if let Some(stdout) = child.stdout.take() {
        std::thread::spawn(move || {
            let reader = BufReader::new(stdout);
            for line in reader.lines().map_while(Result::ok) {
                println!("[astrion-backend] {line}");
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
        .build()?;
    Ok(())
}


