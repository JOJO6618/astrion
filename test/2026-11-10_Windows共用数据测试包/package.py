"""构建“与正式版共用同一运行数据目录”的 Windows 调试测试包。

与 `test/2026-11-09_WindowsBrowser/package_test.py` 的区别只有三点：
1. 启动器不覆盖任何数据根/Settings/QuickSettings 环境变量，因此沿用
   `%USERPROFILE%\\.astrion\\astrion-desktop`（rundata.rs 的默认解析），
   与已安装的正式版读写同一份对话、工作区、浏览器资料和快捷对话配置。
2. 使用独立 identifier，避免和正式版的单实例锁互相顶掉。
3. 启动器在检测到 `astrion-desktop.exe` 仍在运行时拒绝启动：两个进程同时
   写同一份数据会破坏对话与设置（单写者约束）。

用法（Windows 仓库根目录）：
    python test\\2026-11-10_Windows共用数据测试包\\package.py build
    python test\\2026-11-10_Windows共用数据测试包\\package.py assemble
    python test\\2026-11-10_Windows共用数据测试包\\package.py smoke
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
TEST = Path(__file__).resolve().parent
ARTIFACTS = ROOT / "_experiments/windows-shared-test-20261110"
PACKAGE = ROOT / "output/Astrion-Windows-Shared-Test-20261110"
CONF = TEST / "windows-shared.conf.json"
CARGO = Path.home() / ".cargo/bin/cargo.exe"
RUNTIME = ROOT / "desktop/src-tauri/runtime"
EXE_NAME = "AstrionSharedTest.exe"
PRODUCTION_EXE = "astrion-desktop.exe"


def run(command, name, cwd=ROOT, timeout=3600):
    env = os.environ.copy()
    env["PATH"] = str(CARGO.parent) + os.pathsep + env.get("PATH", "")
    log = ARTIFACTS / name
    with log.open("w", encoding="utf-8") as stream:
        result = subprocess.run(
            [str(item) for item in command],
            cwd=cwd,
            env=env,
            stdout=stream,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
    print(log.read_text(encoding="utf-8", errors="replace")[-4000:])
    if result.returncode:
        raise SystemExit(result.returncode)


def build_inputs():
    paths = list((ROOT / "desktop/src-tauri/src").rglob("*.rs"))
    paths += list((ROOT / "desktop/src-tauri/src/browser").glob("*.js"))
    paths += list((ROOT / "desktop/src-tauri/src/browser").glob("*.py"))
    paths += list((ROOT / "desktop-electron/src/browser/ai").rglob("*.js"))
    paths += [
        ROOT / "desktop-electron/src/browser/passwords/page-preload.cjs",
        ROOT / "modules/native_browser/executor.py",
        ROOT / "desktop/src-tauri/Cargo.toml",
        ROOT / "desktop/src-tauri/Cargo.lock",
        CONF,
        ROOT / "static/dist/.vite/manifest.json",
    ]
    return {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(paths)
    }


def build():
    run([sys.executable, ROOT / "desktop/scripts/prepare_backend.py"], "stage.log")
    inputs = build_inputs()
    run(
        [
            "node",
            ROOT / "desktop/node_modules/@tauri-apps/cli/tauri.js",
            "build",
            "--debug",
            "--no-bundle",
            "--features",
            "browser-test",
            "--config",
            CONF,
        ],
        "build.log",
        ROOT / "desktop",
    )
    executable = ROOT / "desktop/src-tauri/target/debug/astrion-desktop.exe"
    assert inputs == build_inputs(), "Build inputs changed during compilation"
    receipt = {
        "inputs": inputs,
        "exe_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "completed_at": time.time(),
    }
    (ARTIFACTS / "build-success.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print("BUILD OK", receipt["exe_sha256"][:16])


LAUNCHER = (
    "@echo off\r\n"
    "setlocal\r\n"
    'cd /d "%~dp0"\r\n'
    "rem Shared data root: no ASTRION_DESKTOP_* overrides, same as the installed build.\r\n"
    'set "ASTRION_DESKTOP_REPO="\r\n'
    'tasklist /fi "imagename eq ' + PRODUCTION_EXE + '" 2>nul | find /i "' + PRODUCTION_EXE + '" >nul\r\n'
    "if not errorlevel 1 (\r\n"
    "  echo [ERROR] Astrion desktop is still running.\r\n"
    "  echo Both builds would write the same data directory and corrupt it.\r\n"
    "  echo Quit Astrion completely ^(including the tray icon^), then run this again.\r\n"
    "  pause\r\n"
    "  exit /b 1\r\n"
    ")\r\n"
    "echo Data root: %USERPROFILE%\\.astrion\\astrion-desktop\r\n"
    'start "" "%~dp0' + EXE_NAME + '"\r\n'
    "endlocal\r\n"
)

NOTES = """Astrion Windows 共用数据测试版（2026-11-10）

这是调试构建（debug + GUI 子系统），与已安装的正式版共用同一份运行数据：
  %USERPROFILE%\\.astrion\\astrion-desktop
包含对话、工作区、日志、浏览器资料（Cookie/密码/历史）、快捷对话设置。
也就是说你在这里看到的对话就是正式版的对话，反过来也一样。

启动：
  双击 Start-Shared-Test.cmd
  或命令行：<本目录>\\Start-Shared-Test.cmd

启动器会先检查 astrion-desktop.exe 是否仍在运行；检测到就直接报错退出。
原因：两个进程同时写同一份对话与设置会违反单写者约束，可能损坏历史。
请完全退出正式版（含托盘图标）之后再启动本测试版。

identifier 与正式版不同（com.astrion.desktop.shared-test-20261110），
所以不会顶掉正式版的单实例锁，也不会影响正式版的安装/更新通道。

回退：关闭本测试版，重新启动正式版即可，数据是同一份。
风险自负：这是未发布构建，建议先自行备份 %USERPROFILE%\\.astrion\\astrion-desktop。
"""


def assemble():
    if PACKAGE.exists():
        raise RuntimeError("Refusing to overwrite an existing package directory")
    executable = ROOT / "desktop/src-tauri/target/debug/astrion-desktop.exe"
    receipt = json.loads((ARTIFACTS / "build-success.json").read_text(encoding="utf-8"))
    assert receipt["inputs"] == build_inputs(), "Source changed since the successful build"
    assert receipt["exe_sha256"] == hashlib.sha256(executable.read_bytes()).hexdigest(), (
        "Executable does not match the successful build"
    )
    PACKAGE.mkdir(parents=True)
    shutil.copy2(executable, PACKAGE / EXE_NAME)
    for name in ("python", "backend"):
        shutil.copytree(RUNTIME / name, PACKAGE / "runtime" / name)
    forbidden = [
        path
        for path in (PACKAGE / "runtime/backend").rglob("*")
        if path.is_file()
        and path.name in (".env", "secrets.json", "providers.json", "host_api_token", "passwords.dpapi")
    ]
    if forbidden:
        raise RuntimeError("Runtime staging contains private configuration")
    (PACKAGE / "Start-Shared-Test.cmd").write_bytes(LAUNCHER.encode("ascii"))
    (PACKAGE / "启动说明.txt").write_text(NOTES, encoding="utf-8")
    notices = PACKAGE / "third-party"
    notices.mkdir()
    for name in ("PLAYWRIGHT_LICENSE", "PLAYWRIGHT_NOTICE"):
        shutil.copy2(ROOT / "desktop-electron/src/browser/ai/vendor" / name, notices / name)
    manifest = json.loads(
        (PACKAGE / "runtime/backend/static/dist/.vite/manifest.json").read_text(encoding="utf-8")
    )
    missing = [entry["file"] for entry in manifest.values() if not (PACKAGE / "runtime/backend/static/dist" / entry["file"]).is_file()]
    if missing:
        raise RuntimeError("Missing packaged frontend resources")
    evidence = PACKAGE / "verification"
    evidence.mkdir()
    shutil.copy2(ARTIFACTS / "build-success.json", evidence / "build.json")
    result = {
        "identifier": json.loads(CONF.read_text(encoding="utf-8"))["identifier"],
        "data_root": "%USERPROFILE%\\.astrion\\astrion-desktop",
        "data_root_overridden": False,
        "validation": "smoke_run_isolated",
        "executable_sha256": hashlib.sha256((PACKAGE / EXE_NAME).read_bytes()).hexdigest(),
        "manifest_entries": len(manifest),
    }
    (evidence / "package.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


def visible_windows(pid):
    user = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    windows = []

    @callback_type
    def visit(handle, _):
        owner = wintypes.DWORD()
        user.GetWindowThreadProcessId(handle, ctypes.byref(owner))
        if owner.value == pid and user.IsWindowVisible(handle):
            windows.append(handle)
        return True

    user.EnumWindows(visit, 0)
    return user, windows


def smoke():
    """Start the packaged shell against an isolated temp root (never the shared one)."""
    import psutil

    run_root = ARTIFACTS / ("smoke-" + str(time.time_ns()))
    workspace = run_root / "workspace"
    workspace.mkdir(parents=True)
    env = os.environ.copy()
    for key in list(env):
        if key.startswith(("ASTRION_", "AGENT_")):
            del env[key]
    env.update(
        ASTRION_DESKTOP_DATA_ROOT=str(run_root),
        ASTRION_DESKTOP_SETTINGS_FILE=str(run_root / "rundata_settings.json"),
        ASTRION_DESKTOP_INITIAL_WORKSPACE=str(workspace),
        ASTRION_QUICK_SETTINGS_FILE=str(run_root / "quick-entry-windows.json"),
    )
    log = ARTIFACTS / "smoke.log"
    report = {"package": str(PACKAGE), "isolated_root": str(run_root), "checks": {}}
    children = set()
    with log.open("w", encoding="utf-8") as stream:
        process = subprocess.Popen(
            [str(PACKAGE / EXE_NAME)], cwd=PACKAGE, env=env, stdout=stream, stderr=subprocess.STDOUT
        )
        try:
            deadline = time.monotonic() + 90
            port = None
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor()
            )

            def post(path, payload):
                csrf = json.loads(
                    opener.open(f"http://127.0.0.1:{port}/api/csrf-token", timeout=5).read()
                )["token"]
                request = urllib.request.Request(
                    f"http://127.0.0.1:{port}{path}",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json", "X-CSRF-Token": csrf},
                    method="POST",
                )
                return json.loads(opener.open(request, timeout=10).read())

            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("shell exited before the backend was ready")
                for child in psutil.Process(process.pid).children(recursive=True):
                    children.add(child.pid)
                    try:
                        args = child.cmdline()
                        if "--port" in args and "server.app" in args:
                            port = int(args[args.index("--port") + 1])
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
                if port:
                    try:
                        assert post("/host-login", {}).get("success") is True
                        if opener.open(f"http://127.0.0.1:{port}/api/health", timeout=2).status == 200:
                            break
                    except Exception:
                        port = None
                time.sleep(0.2)
            else:
                raise RuntimeError("packaged backend did not become healthy")
            report["checks"]["embeddedBackendHealthy"] = True
            report["checks"]["isolatedRootUsed"] = (run_root / "host").is_dir()
            # /api/browser-tools 依赖工作区数据上下文；没选中工作区时它自己就会报错，
            # 不能把「没有工作区」当成「浏览器不受支持」。
            catalog = json.loads(opener.open(f"http://127.0.0.1:{port}/api/host/workspaces", timeout=10).read())
            assert Path(catalog["data"]["source_path"]).resolve().is_relative_to(run_root.resolve()), "workspace catalog escaped isolation"
            created = post("/api/host/workspaces/create", {"path": str(workspace), "label": "Shared package smoke", "set_default": True})
            assert created.get("success") is True, created
            assert post("/api/host/workspaces/select", {"workspace_id": created["data"]["workspace"]["workspace_id"]}).get("success") is True
            report["checks"]["workspaceIsolated"] = True
            page = opener.open(f"http://127.0.0.1:{port}/", timeout=10).read().decode("utf-8")
            assets = re.findall(r'(?:src|href)=["\']([^"\']+\.(?:js|css)(?:\?[^"\']*)?)["\']', page)
            for asset in assets:
                assert opener.open(urllib.parse.urljoin(f"http://127.0.0.1:{port}/", asset), timeout=10).status == 200
            report["checks"]["entryAssets"] = len(assets) > 0
            tools = json.loads(opener.open(f"http://127.0.0.1:{port}/api/browser-tools", timeout=10).read())
            report["checks"]["browserToolsSupported"] = tools.get("supported") is True
            window_deadline = time.monotonic() + 45
            state = {}
            while time.monotonic() < window_deadline:
                state = json.loads(opener.open(f"http://127.0.0.1:{port}/api/desktop/window/state", timeout=6).read())
                if state.get("success") is True:
                    break
                time.sleep(0.3)
            assert state.get("success") is True, state
            report["checks"]["nativeWindowRegistered"] = True
            _, windows = visible_windows(process.pid)
            report["checks"]["nativeWindowVisible"] = bool(windows)
            assert windows, "native window not visible"
            report["checks"]["closeButtonAccepted"] = post("/api/desktop/window/control", {"action": "close"}).get("success") is True
            process.wait(timeout=25)
            time.sleep(1)
            backends = []
            for pid in children:
                try:
                    if "server.app" in psutil.Process(pid).cmdline():
                        backends.append(pid)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            report["checks"]["backendReapedOnClose"] = not backends
            assert all(report["checks"].values()), report
        except Exception as error:
            report["error"] = str(error)
            (ARTIFACTS / "smoke-failure.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps(report, indent=2, ensure_ascii=False))
            raise
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=15)
    (ARTIFACTS / "smoke.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    shutil.copy2(ARTIFACTS / "smoke.json", PACKAGE / "verification/smoke.json")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    {"build": build, "assemble": assemble, "smoke": smoke}[sys.argv[1]]()
