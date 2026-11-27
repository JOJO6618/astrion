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
ARTIFACTS = ROOT / "_experiments/windows-shared-test-20261110"
# 可选第二个参数给输出目录加后缀，避免覆盖上一份包：
#   python package.py smoke r2  ->  output/Astrion-Windows-Shared-Test-20261110-r2
TAG = f"-{sys.argv[2]}" if len(sys.argv) > 2 else ""
PACKAGE = ROOT / f"output/Astrion-Windows-Shared-Test-20261110{TAG}"
EXE_NAME = "AstrionSharedTest.exe"
PRODUCTION_EXE = "astrion-desktop.exe"


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
    if len(sys.argv) < 2 or sys.argv[1] != "smoke":
        raise SystemExit("Tauri packaging is retired. Use the current desktop-electron build.")
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    smoke()
