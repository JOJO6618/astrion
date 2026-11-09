"""Build and verify a portable Windows validation app without publishing or installing."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[2]
TEST = Path(__file__).resolve().parent
ARTIFACTS = ROOT / '_experiments/windows-browser-20261109'
PACKAGE = ROOT / 'output/Astrion-Windows-Browser-Test-20261109-r3'
CARGO = Path.home() / '.cargo/bin/cargo.exe'
RUNTIME = ROOT / 'desktop/src-tauri/runtime'


def run(command, name, cwd=ROOT):
    env = os.environ.copy()
    env['PATH'] = str(CARGO.parent) + os.pathsep + env.get('PATH', '')
    log = ARTIFACTS / name
    with log.open('w', encoding='utf-8') as stream:
        result = subprocess.run([str(item) for item in command], cwd=cwd, env=env, stdout=stream, stderr=subprocess.STDOUT)
    print(log.read_text(encoding='utf-8', errors='replace')[-5000:])
    if result.returncode:
        raise SystemExit(result.returncode)


def build():
    run([sys.executable, ROOT / 'desktop/scripts/prepare_backend.py'], 'package-stage.log')
    compile_native()


def build_inputs():
    paths = list((ROOT / 'desktop/src-tauri/src').rglob('*.rs'))
    paths += list((ROOT / 'desktop/src-tauri/src/browser').glob('*.js'))
    paths += list((ROOT / 'desktop/src-tauri/src/browser').glob('*.py'))
    paths += list((ROOT / 'desktop/browser-migration-extension').glob('*.*'))
    paths += [ROOT / 'desktop-electron/src/browser/passwords/page-preload.cjs']
    paths += list((ROOT / 'desktop-electron/src/browser/ai').rglob('*.js'))
    paths += [ROOT / 'modules/native_browser/executor.py']
    paths += [ROOT / 'desktop/src-tauri/Cargo.toml', ROOT / 'desktop/src-tauri/Cargo.lock', TEST / 'windows-test.conf.json', ROOT / 'static/dist/.vite/manifest.json']
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(paths)}


def compile_native():
    inputs = build_inputs()
    run(['node', ROOT / 'desktop/node_modules/@tauri-apps/cli/tauri.js', 'build', '--debug', '--no-bundle', '--features', 'browser-test', '--config', TEST / 'windows-test.conf.json'], 'package-build.log', ROOT / 'desktop')
    executable = ROOT / 'desktop/src-tauri/target/debug/astrion-desktop.exe'
    assert inputs == build_inputs(), 'Build inputs changed during compilation'
    receipt = {'inputs': inputs, 'exe_sha256': hashlib.sha256(executable.read_bytes()).hexdigest(), 'completed_at': time.time()}
    (ARTIFACTS / 'package-build-success.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')


def assemble():
    if PACKAGE.exists():
        raise RuntimeError('Refusing to overwrite an existing portable package')
    executable = ROOT / 'desktop/src-tauri/target/debug/astrion-desktop.exe'
    receipt = json.loads((ARTIFACTS / 'package-build-success.json').read_text(encoding='utf-8'))
    assert receipt['inputs'] == build_inputs(), 'Source changed since successful package build'
    assert receipt['exe_sha256'] == hashlib.sha256(executable.read_bytes()).hexdigest(), 'Executable does not match successful build'
    PACKAGE.mkdir(parents=True)
    shutil.copy2(executable, PACKAGE / 'AstrionBrowserTest.exe')
    for name in ('python', 'backend'):
        shutil.copytree(RUNTIME / name, PACKAGE / 'runtime' / name)
    for directory in (PACKAGE / 'runtime/backend',):
        forbidden = [path for path in directory.rglob('*') if path.is_file() and path.name in ('.env', 'secrets.json', 'providers.json', 'host_api_token', 'passwords.dpapi')]
        if forbidden:
            raise RuntimeError('Runtime staging contains private configuration')
    launcher = '@echo off\r\nsetlocal\r\ncd /d "%~dp0"\r\nset "ASTRION_DESKTOP_DATA_ROOT=%~dp0validation-profile"\r\nset "ASTRION_DESKTOP_SETTINGS_FILE=%~dp0validation-profile\\rundata_settings.json"\r\nset "ASTRION_DESKTOP_INITIAL_WORKSPACE=%~dp0validation-profile\\workspace"\r\nset "ASTRION_DESKTOP_REPO="\r\nif not exist "%ASTRION_DESKTOP_INITIAL_WORKSPACE%" mkdir "%ASTRION_DESKTOP_INITIAL_WORKSPACE%"\r\nstart "" "%~dp0AstrionBrowserTest.exe"\r\nendlocal\r\n'
    launcher = launcher.replace('set "ASTRION_DESKTOP_REPO="', 'set "ASTRION_QUICK_SETTINGS_FILE=%~dp0validation-profile\\quick-entry-windows.json"\r\nset "ASTRION_DESKTOP_REPO="')
    launcher = launcher.replace('%~dp0validation-profile', '%~dp0..\\Astrion-Windows-Browser-Test-20261109\\validation-profile')
    (PACKAGE / 'Start-Browser-Test.cmd').write_bytes(launcher.encode('ascii'))
    (PACKAGE / '启动说明.txt').write_text('r3 包含 AI file:// 导航与快照修改。\n双击 Start-Browser-Test.cmd 启动，复用同级第一次测试包内的 validation-profile。\n启动前请退出旧测试版，避免多个版本同时写入同一份资料。\n本次仅编译和打包，未运行回归测试或应用启动验证。\n', encoding='utf-8')
    shutil.copytree(ROOT / 'desktop/browser-migration-extension', PACKAGE / 'chrome-cookie-migration')
    evidence = PACKAGE / 'verification'
    evidence.mkdir()
    shutil.copy2(ARTIFACTS / 'package-build-success.json', evidence / 'build.json')
    notices = PACKAGE / 'third-party'
    notices.mkdir()
    for name in ('PLAYWRIGHT_LICENSE', 'PLAYWRIGHT_NOTICE'):
        shutil.copy2(ROOT / 'desktop-electron/src/browser/ai/vendor' / name, notices / name)
    manifest = json.loads((PACKAGE / 'runtime/backend/static/dist/.vite/manifest.json').read_text(encoding='utf-8'))
    missing = [entry['file'] for entry in manifest.values() if not (PACKAGE / 'runtime/backend/static/dist' / entry['file']).is_file()]
    if missing:
        raise RuntimeError('Missing packaged frontend resources')
    assert (PACKAGE / 'runtime/backend/modules/native_browser/executor.py').read_bytes() == (ROOT / 'modules/native_browser/executor.py').read_bytes(), 'Packaged browser executor is stale'
    result = {'identifier': 'com.astrion.desktop.browser-test-20261109-r3', 'validation': 'not_run_for_this_build', 'executable_sha256': hashlib.sha256((PACKAGE / 'AstrionBrowserTest.exe').read_bytes()).hexdigest(), 'manifest_entries': len(manifest), 'frontend_manifest_sha256': hashlib.sha256((PACKAGE / 'runtime/backend/static/dist/.vite/manifest.json').read_bytes()).hexdigest(), 'private_files_found': 0}
    (evidence / 'package.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))


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


def archive():
    target = PACKAGE.with_suffix('.zip')
    if target.exists():
        raise RuntimeError('Refusing to overwrite an existing archive')
    assert not (PACKAGE / 'validation-profile').exists(), 'Do not distribute any test user data'
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=5) as output:
        for path in PACKAGE.rglob('*'):
            if path.is_file():
                output.write(path, Path(PACKAGE.name) / path.relative_to(PACKAGE))
    with zipfile.ZipFile(target) as archive:
        assert archive.testzip() is None, 'Archive CRC failure'
        names = archive.namelist()
        assert not any('/validation-profile/' in name for name in names)
        assert any(name.endswith('/AstrionBrowserTest.exe') for name in names)
        assert any(name.endswith('/runtime/python/astrion-backend.exe') for name in names)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix('.zip.sha256.txt').write_text(digest + '  ' + target.name + '\n', encoding='ascii')
    print(json.dumps({'file': str(target), 'bytes': target.stat().st_size, 'sha256': digest}, indent=2))


if __name__ == '__main__':
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    {'build': build, 'compile': compile_native, 'assemble': assemble, 'smoke': smoke, 'archive': archive}[sys.argv[1]]()
