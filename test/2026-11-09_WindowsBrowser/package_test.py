"""Smoke-test an already built Windows Tauri validation package."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / '_experiments/windows-browser-20261109'
PACKAGE = ROOT / 'output/Astrion-Windows-Browser-Test-20261109-r3'


def visible_windows(pid):
    user = ctypes.WinDLL('user32', use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
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
    import psutil
    run_root = ARTIFACTS / ('packaged-smoke-' + str(time.time_ns()))
    workspace = run_root / 'workspace'
    workspace.mkdir(parents=True)
    env = os.environ.copy()
    for key in list(env):
        if key.startswith(('ASTRION_', 'AGENT_')):
            del env[key]
    env.update(ASTRION_DESKTOP_DATA_ROOT=str(run_root), ASTRION_DESKTOP_SETTINGS_FILE=str(run_root / 'rundata_settings.json'), ASTRION_DESKTOP_INITIAL_WORKSPACE=str(workspace), ASTRION_QUICK_SETTINGS_FILE=str(run_root / 'quick-entry-windows.json'))
    log = ARTIFACTS / 'package-smoke.log'
    report = {'data_root': str(run_root), 'package': str(PACKAGE), 'checks': {}}
    child_pids = set()
    with log.open('w', encoding='utf-8') as stream:
        process = subprocess.Popen([str(PACKAGE / 'AstrionBrowserTest.exe')], cwd=PACKAGE, env=env, stdout=stream, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 70
            port = None
            last_error = 'backend process port not discovered'
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor())
            def post(path, payload):
                csrf = json.loads(opener.open(f'http://127.0.0.1:{port}/api/csrf-token', timeout=5).read())['token']
                request = urllib.request.Request(f'http://127.0.0.1:{port}{path}', data=json.dumps(payload).encode('utf-8'), headers={'Content-Type': 'application/json', 'X-CSRF-Token': csrf}, method='POST')
                return json.loads(opener.open(request, timeout=10).read())
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError('Packaged shell exited before startup')
                for child in psutil.Process(process.pid).children(recursive=True):
                    child_pids.add(child.pid)
                    try:
                        args = child.cmdline()
                        if '--port' in args and 'server.app' in args:
                            assert str(PACKAGE / 'runtime/python').lower() in str(Path(child.exe()).parent).lower()
                            port = int(args[args.index('--port') + 1])
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
                if port:
                    try:
                        # Reproduce the desktop login UI's CSRF-protected loopback login.
                        assert post('/host-login', {}).get('success') is True
                        response = opener.open(f'http://127.0.0.1:{port}/api/health', timeout=2)
                        if response.status == 200:
                            break
                    except Exception as error:
                        last_error = str(error)
                time.sleep(0.2)
            else:
                raise RuntimeError('Packaged backend did not become healthy: ' + last_error)
            report['checks']['embeddedBackendHealthy'] = True
            report['checks']['dataRootIsolated'] = (run_root / 'host').is_dir()
            catalog = json.loads(opener.open(f'http://127.0.0.1:{port}/api/host/workspaces', timeout=10).read())
            source = Path(catalog['data']['source_path']).resolve()
            assert source.is_relative_to(run_root.resolve()), 'Workspace catalog is outside smoke isolation'
            created = post('/api/host/workspaces/create', {'path': str(workspace), 'label': 'Browser package smoke', 'set_default': True})
            assert created.get('success') is True
            assert post('/api/host/workspaces/select', {'workspace_id': created['data']['workspace']['workspace_id']}).get('success') is True
            report['checks']['workspaceCatalogIsolated'] = True
            page = opener.open(f'http://127.0.0.1:{port}/', timeout=10).read().decode('utf-8')
            report['checks']['entryHtml'] = 'html' in page.lower()
            tools = json.loads(opener.open(f'http://127.0.0.1:{port}/api/browser-tools', timeout=10).read())
            report['checks']['browserToolsSupported'] = tools.get('success') is True and tools.get('supported') is True
            import re
            assets = re.findall(r'(?:src|href)=[\"\']([^\"\']+\.(?:js|css)(?:\?[^\"\']*)?)[\"\']', page)
            for asset in assets:
                from urllib.parse import urljoin
                assert opener.open(urljoin(f'http://127.0.0.1:{port}/', asset), timeout=10).status == 200
            report['checks']['entryAssets'] = len(assets) > 0
            # An HWND can be visible while WebView2 creation is still inside build().
            window_deadline = time.monotonic() + 45
            window_state = {}
            while time.monotonic() < window_deadline:
                window_state = json.loads(opener.open(f'http://127.0.0.1:{port}/api/desktop/window/state', timeout=6).read())
                if window_state.get('success') is True:
                    break
                time.sleep(0.3)
            assert window_state.get('success') is True, window_state
            report['checks']['nativeWindowRegistered'] = True
            report['checks']['shellWebviewIsolated'] = (run_root / 'shell-webview2').is_dir()
            user, windows = visible_windows(process.pid)
            report['checks']['nativeWindowVisible'] = bool(windows)
            assert windows, 'Native window not visible'
            user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
            titles = []
            for handle in windows:
                title = ctypes.create_unicode_buffer(512)
                user.GetWindowTextW(handle, title, 512)
                titles.append(title.value)
            report['visible_window_titles'] = titles
            # Exercise the actual custom close button endpoint, not a fabricated OS event.
            close = post('/api/desktop/window/control', {'action': 'close'})
            assert close.get('success') is True, close
            report['checks']['closeButtonAccepted'] = True
            process.wait(timeout=20)
            time.sleep(1)
            remaining = [pid for pid in child_pids if psutil.pid_exists(pid)]
            # WebView2 can briefly drain child processes; the embedded Python must be gone.
            backends = []
            for pid in remaining:
                try:
                    item = psutil.Process(pid)
                    if 'server.app' in item.cmdline():
                        backends.append(pid)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            report['checks']['backendReapedOnClose'] = not backends
            assert all(report['checks'].values()), report
        except Exception as error:
            report['error'] = str(error)
            (ARTIFACTS / 'package-smoke-failure.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            print(json.dumps(report, indent=2))
            raise
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=15)
    (ARTIFACTS / 'package-smoke.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    shutil.copy2(ARTIFACTS / 'package-smoke.json', PACKAGE / 'verification/package-smoke.json')
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] != "smoke":
        raise SystemExit("Tauri packaging is retired. Use the current desktop-electron build.")
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    smoke()
