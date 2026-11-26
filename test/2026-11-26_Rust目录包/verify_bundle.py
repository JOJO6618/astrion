#!/usr/bin/env python3.11
"""正常 macOS Rust 目录包（Astrion.app）的隔离预演与启动验证。

只读校验 + 可选的隔离启动；不添加/修改产品代码，不发布、不替换已安装应用。

用法：
  # 1) 没有产物时，用现有二进制 + 静态资源做“选择逻辑”预演（不编译、不启动进程）
  python3.11 -B verify_bundle.py --logic-only

  # 2) 有 Astrion.app 后做静态校验
  python3.11 -B verify_bundle.py --app "desktop-electron/dist-rust/mac-arm64/Astrion.app"

  # 3) 静态校验 + 隔离启动 + PID/端口/业务端点验证，然后尝试让应用自己退出
  python3.11 -B verify_bundle.py --app "..." --launch

启动隔离约定（正常形态，不是实验形态）：
  ASTRION_DESKTOP_DATA_ROOT        <tmp>/data    → 后端数据根，避开 ~/.astrion/astrion-desktop
  ASTRION_ELECTRON_USER_DATA       <tmp>/profile → Electron userData，单实例锁按它隔离
  ASTRION_DESKTOP_INITIAL_WORKSPACE<tmp>/ws      → 后端初始工作区（必须存在的目录）
  不设 ASTRION_DESKTOP_BACKEND —— 这一步要验证“包内只有 Rust 时默认就用包内 Rust”；
  也不设 ASTRION_RUST_EXPERIMENT —— 走正常形态（控制桥、更新策略、快捷窗都在）。
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import hashlib
import json
import os
import plistlib
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
DEFAULT_APP = REPO / "desktop-electron/dist-rust/mac-arm64/Astrion.app"
FALLBACK_BIN = REPO / "backend-rust/target-master/debug/astrion-headless"

class Libproc:
    def __init__(self) -> None:
        self.lib = ctypes.CDLL(ctypes.util.find_library("proc") or "/usr/lib/libproc.dylib")
        self.lib.proc_pidpath.restype = ctypes.c_int

    def path(self, pid: int) -> str:
        buf = ctypes.create_string_buffer(4096)
        if self.lib.proc_pidpath(pid, buf, ctypes.sizeof(buf)) <= 0:
            return ""
        return buf.value.decode("utf-8", "replace")


def parent_pid(pid: int) -> int | None:
    result = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True)
    value = result.stdout.strip()
    return int(value) if result.returncode == 0 and value.isdecimal() else None


def node(script: str, *args: str) -> dict:
    out = subprocess.run(
        ["node", "--input-type=module", "-e", script, *args],
        cwd=str(REPO), capture_output=True, text=True, timeout=60,
    )
    if out.returncode != 0:
        raise RuntimeError(f"node 断言失败：{out.stderr.strip()[:1500]}")
    return json.loads(out.stdout.strip().splitlines()[-1])


def fixture_resources(binary: Path, static_root: Path, with_python: bool, root: Path) -> Path:
    resources = root / "Contents/Resources"
    rust = resources / "runtime/rust"
    rust.mkdir(parents=True)
    (rust / "astrion-headless").symlink_to(binary)
    (rust / "static").symlink_to(static_root)
    (rust / "config").mkdir()
    (rust / "config/providers_catalog.json").symlink_to(REPO / "config/providers_catalog.json")
    if with_python:
        (resources / "runtime/python").mkdir()
    return resources


LOGIC = """
import {{ resolveDesktopBackend, rustDesktopLaunch, productionRustPaths }} from {module!r};
const resourcesPath = {resources!r};
const out = {{}};
const attempt = (name, env) => {{
  try {{
    const runtime = resolveDesktopBackend({{ resourcesPath, env, platform: 'darwin' }});
    out[name] = runtime === null ? {{ backend: 'python' }} : {{
      backend: 'rust',
      experimental: runtime.experimental,
      binary: runtime.binary,
      staticRoot: runtime.staticRoot,
      resourceRoot: runtime.resourceRoot,
    }};
  }} catch (error) {{ out[name] = {{ error: error.message }}; }}
}};
attempt('default', {{}});
attempt('explicit_python', {{ ASTRION_DESKTOP_BACKEND: 'python' }});
attempt('experiment_without_dirs', {{ ASTRION_RUST_EXPERIMENT: '1' }});
const rustenv = {{
  ASTRION_RUST_EXPERIMENT: '1',
  ASTRION_DESKTOP_DATA_ROOT: {exp_root!r} + '/data',
  ASTRION_ELECTRON_USER_DATA: {exp_root!r} + '/profile',
  ASTRION_DESKTOP_INITIAL_WORKSPACE: {exp_root!r} + '/ws',
}};
attempt('experiment_with_dirs', rustenv);
const runtime = resolveDesktopBackend({{ resourcesPath, env: {{}}, platform: 'darwin' }});
if (runtime && !runtime.experimental) {{
  const paths = productionRustPaths({data_root!r}, {{ ASTRION_DESKTOP_INITIAL_WORKSPACE: {workspace!r} }});
  const launch = rustDesktopLaunch({{ ...runtime, ...paths }}, {{ port: 12345, version: '0.0.0', identity: {{ build_id: 'probe', platform: 'darwin', arch: 'arm64' }}, env: {{}} }});
  out.launch = {{ command: launch.command, args: launch.args, cwd: launch.cwd, env: {{
    ASTRION_DATA_ROOT: launch.env.ASTRION_DATA_ROOT,
    ASTRION_RESOURCE_ROOT: launch.env.ASTRION_RESOURCE_ROOT,
    DEPLOY_CONFIG_DIR: launch.env.DEPLOY_CONFIG_DIR,
    HOST_WORKSPACES_FILE: launch.env.HOST_WORKSPACES_FILE,
    ASTRION_DESKTOP_PLATFORM: launch.env.ASTRION_DESKTOP_PLATFORM,
    ASTRION_DESKTOP_ARCH: launch.env.ASTRION_DESKTOP_ARCH,
  }} }};
}}
console.log(JSON.stringify(out));
"""


def logic_only(binary: Path) -> int:
    with tempfile.TemporaryDirectory(prefix="astrion-rustdir-logic-") as tmp:
        tmp_path = Path(tmp)
        resources = fixture_resources(binary, REPO / "static", with_python=False, root=tmp_path)
        exp_root = tmp_path / "experiment"
        for name in ("data", "profile", "ws"):
            (exp_root / name).mkdir(parents=True)
        result = node(LOGIC.format(
            module=str(REPO / "desktop-electron/src/rust-backend.js"),
            resources=str(resources), exp_root=str(exp_root),
            data_root=str(tmp_path / "data-root"), workspace=str(tmp_path / "ws"),
        ))
        default = result["default"]
        checks = {
            "只有 Rust 时默认 rust": default.get("backend") == "rust" and default.get("experimental") is False,
            "显式 python 被拒绝": "error" in result["explicit_python"],
            "实验形态要求专用目录": "error" in result["experiment_without_dirs"],
            "实验形态给出目录后成立": result["experiment_with_dirs"].get("experimental") is True,
            "--web 启动参数完整": "--web" in (result.get("launch") or {}).get("args", []),
        }
        py_resources = fixture_resources(binary, REPO / "static", with_python=True, root=tmp_path / "py")
        py_result = node(LOGIC.format(
            module=str(REPO / "desktop-electron/src/rust-backend.js"),
            resources=str(py_resources), exp_root=str(exp_root),
            data_root=str(tmp_path / "data-root"), workspace=str(tmp_path / "ws"),
        ))
        checks["带 Python 运行时默认仍是 python"] = py_result["default"].get("backend") == "python"
        print(json.dumps({"checks": checks, "default": default, "launch": result.get("launch")}, ensure_ascii=False, indent=2))
        return 0 if all(checks.values()) else 1


def bundle_checks(app: Path) -> dict:
    resources = app / "Contents/Resources"
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    runtimes = sorted(p.name for p in (resources / "runtime").iterdir())
    rust = resources / "runtime/rust"
    binary = rust / "astrion-headless"
    checks = {
        "runtime 只有 rust": runtimes == ["rust"],
        "没有内嵌 Python": not (resources / "runtime/python").exists() and not (resources / "runtime/backend").exists(),
        "后端二进制可执行": binary.is_file() and bool(binary.stat().st_mode & 0o111),
        "前端 manifest 存在": (rust / "static/dist/.vite/manifest.json").is_file(),
        "entry-styles 存在": (rust / "static/dist/.vite/entry-styles.json").is_file(),
        "资源种子存在": (rust / "config/providers_catalog.json").is_file(),
        "包含 Quick Chat 原生组件与更新策略文案": all(
            (resources / name).is_file()
            for name in (
                "quick-entry-listener", "quick-entry-capture",
                "update-policy-locales/zh-CN.json", "update-policy-locales/en-US.json",
            )
        ),
        "appId 正确": info.get("CFBundleIdentifier") == "com.astrion.desktop",
    }
    version = info.get("CFBundleShortVersionString")
    pkg = node("""
import { createRequire } from 'node:module';
const require = createRequire(process.cwd() + '/desktop-electron/package.json');
const manifest = require('@electron/asar').extractFile(process.argv[1], 'package.json');
console.log(JSON.stringify(JSON.parse(manifest.toString('utf8'))));
""", str(resources / "app.asar"))
    checks["包内构建编号存在"] = bool(pkg.get("astrionBuildId"))
    checks["包内版本一致"] = version == pkg.get("version")
    digest = None
    if binary.is_file():
        with binary.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {
        "app": str(app),
        "version": version,
        "buildId": pkg.get("astrionBuildId"),
        "runtimes": runtimes,
        "binary": str(binary),
        "binaryBytes": binary.stat().st_size if binary.is_file() else None,
        "binarySha256": digest,
        "checks": checks,
    }


def wait_http(url: str, timeout: float) -> dict | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception:
            time.sleep(0.1)
    return None


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def launch_isolated(app: Path) -> int:
    lib = Libproc()
    tmp = Path(tempfile.mkdtemp(prefix="astrion-rustdir-run-"))
    data = tmp / "data"
    profile = tmp / "profile"
    workspace = tmp / "ws"
    for directory in (data, profile, workspace):
        directory.mkdir()
    workspace_id = "bundle-acceptance"
    (data / "config").mkdir()
    (data / "config/host_workspaces.json").write_text(json.dumps({
        "default_workspace_id": workspace_id,
        "workspaces": [{"workspace_id": workspace_id, "label": "Bundle acceptance", "path": str(workspace)}],
    }))
    debug_port = free_port()
    env = {
        **os.environ,
        "ASTRION_DESKTOP_DATA_ROOT": str(data),
        "ASTRION_ELECTRON_USER_DATA": str(profile),
        "ASTRION_DESKTOP_INITIAL_WORKSPACE": str(workspace),
        "DATA_DIR": str(data / "host/data"),
        "LOGS_DIR": str(data / "host/logs"),
        "DEPLOY_CONFIG_DIR": str(data / "config"),
        "HOST_WORKSPACES_FILE": str(data / "config/host_workspaces.json"),
        "ASTRION_QUICK_SETTINGS_FILE": str(profile / "quick-entry.json"),
        "SHELL": "/bin/sh",
        "ELECTRON_ENABLE_LOGGING": "1",
    }
    for name in (
        "ASTRION_DESKTOP_BACKEND", "ASTRION_RUST_EXPERIMENT", "ASTRION_RUST_BACKEND_BIN",
        "ASTRION_RUST_RESOURCE_ROOT", "ASTRION_RUST_STATIC_ROOT", "ASTRION_RUST_MODEL_ENDPOINT",
        "ASTRION_RUST_MODEL", "ASTRION_MODEL_API_KEY", "ELECTRON_RUN_AS_NODE",
    ):
        env.pop(name, None)
    executable = app / "Contents/MacOS/Astrion"
    log_path = tmp / "app.log"
    log = log_path.open("wb")
    started = time.time()
    proc = subprocess.Popen([str(executable), f"--inspect={debug_port}"], env=env, stdout=log, stderr=subprocess.STDOUT)
    report = {"app": str(app), "tmp": str(tmp), "electronPid": proc.pid, "debugPort": debug_port}
    pid = None
    owned_backend = False
    try:
        port_file = profile / "backend-port.json"
        port = None
        deadline = time.time() + 60
        while time.time() < deadline and proc.poll() is None:
            if port_file.exists():
                try:
                    port = int(json.loads(port_file.read_text())["port"])
                    break
                except Exception:
                    pass
            time.sleep(0.1)
        report["port"] = port
        packaged_binary = app / "Contents/Resources/runtime/rust/astrion-headless"
        if port:
            status = wait_http(f"http://127.0.0.1:{port}/api/host-mode-enabled", 30)
            report["hostMode"] = status
            if status and type(status.get("pid")) is int and status["pid"] > 0:
                pid = status["pid"]
                report["backendPath"] = lib.path(pid)
                report["backendParentPid"] = parent_pid(pid)
                owned_backend = report["backendPath"] == str(packaged_binary) and report["backendParentPid"] == proc.pid
            token_file = data / "host/data/host_api_token"
            token = token_file.read_text().strip() if token_file.exists() else ""
            report["tokenPresent"] = bool(token)
            if token and owned_backend:
                request = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/runtime/sessions",
                    headers={"Authorization": f"Bearer {token}", "X-Astrion-Workspace-Id": workspace_id},
                )
                try:
                    with urllib.request.urlopen(request, timeout=10) as response:
                        payload = json.loads(response.read().decode("utf-8"))
                    report["sessions"] = {
                        "status": response.status,
                        "success": payload.get("success"),
                        "total": payload.get("total"),
                        "keys": sorted(payload.keys()),
                    }
                except Exception as exc:
                    report["sessions"] = {"error": f"{type(exc).__name__}: {exc}"}
        report["checks"] = {
            "报告 PID 是 Electron 启动的包内 Rust 子进程": owned_backend,
            "host-mode 报告 rust": bool(report.get("hostMode") and report["hostMode"].get("backend") == "rust"),
            "会话列表 200": (report.get("sessions") or {}).get("status") == 200,
        }
        deadline = time.time() + 20
        quick_ready = False
        while proc.poll() is None and time.time() < deadline:
            if "[astrion-quick]" in log_path.read_text(encoding="utf-8", errors="replace"):
                quick_ready = True
                break
            time.sleep(0.1)
        report["checks"]["桌面壳完成 Quick Chat 初始化"] = quick_ready
    finally:
        # 只连接本次应用日志公布的调试端点，让壳正常退出并关闭其后端。
        quit_ok = proc.poll() is None and try_quit(log_path, debug_port)
        if proc.poll() is None:
            try:
                proc.wait(timeout=12)
            except subprocess.TimeoutExpired:
                if owned_backend and pid and parent_pid(pid) == proc.pid:
                    try:
                        os.kill(pid, 15)
                    except ProcessLookupError:
                        pass
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
        log.close()
    report["checks"]["应用经正常退出"] = quit_ok and proc.returncode == 0
    stopped = not pid or lib.path(pid) != str(packaged_binary)
    report["checks"]["包内后端同时退出"] = owned_backend and stopped
    report["elapsedMs"] = round((time.time() - started) * 1000)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if all(report["checks"].values()) else 1


QUIT_JS = """
const socket = new WebSocket(process.argv[1]);
socket.addEventListener('open', () => {
  socket.send(JSON.stringify({ id: 1, method: 'Runtime.evaluate', params: {
    expression: "setTimeout(() => process.getBuiltinModule('node:module').createRequire(process.resourcesPath + '/app.asar/package.json')('electron').app.quit(), 50)",
    returnByValue: false
  }}));
});
socket.addEventListener('message', (event) => {
  const reply = JSON.parse(event.data);
  if (reply.id !== 1) return;
  socket.close();
  process.exit(reply.error || reply.result?.exceptionDetails ? 1 : 0);
});
socket.addEventListener('error', () => process.exit(1));
"""


def try_quit(log: Path, debug_port: int) -> bool:
    match = re.search(
        rf"Debugger listening on (ws://127\.0\.0\.1:{debug_port}/[\da-f-]+)",
        log.read_text(encoding="utf-8", errors="replace"),
    )
    if not match:
        return False
    try:
        return subprocess.run(["node", "-e", QUIT_JS, match[1]], timeout=5).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", default=str(DEFAULT_APP))
    parser.add_argument("--logic-only", action="store_true")
    parser.add_argument("--launch", action="store_true")
    parser.add_argument("--binary", default=str(FALLBACK_BIN))
    args = parser.parse_args()

    if args.logic_only:
        return logic_only(Path(args.binary))

    app = Path(args.app).resolve()
    if not app.is_dir():
        print(json.dumps({"error": f"未找到产物：{app}；先用 npm --prefix desktop-electron run build:rust:app"}, ensure_ascii=False))
        return 2
    report = bundle_checks(app)
    bad = [name for name, ok in report["checks"].items() if not ok]
    report["ok"] = not bad
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if bad:
        return 1
    if args.launch:
        return launch_isolated(app)
    return 0


if __name__ == "__main__":
    sys.exit(main())
