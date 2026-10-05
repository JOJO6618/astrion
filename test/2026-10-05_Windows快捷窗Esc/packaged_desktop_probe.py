"""Exercise the release shell, bundled backend, settings bridge and close lifecycle."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request

import psutil

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'cache/windows-051-desktop-probe'
OUT.mkdir(parents=True, exist_ok=True)
DATA = OUT / 'data'
SETTINGS = OUT / 'quick-settings.json'
SETTINGS.write_text(json.dumps({'enabled': True, 'modifier': 'alt', 'workspace': '', 'model': ''}), encoding='utf-8')
ENV = os.environ.copy()
ENV.update(ASTRION_DESKTOP_DATA_ROOT=str(DATA), ASTRION_DESKTOP_SETTINGS_FILE=str(OUT / 'rundata-settings.json'), ASTRION_QUICK_SETTINGS_FILE=str(SETTINGS))
ENV.pop('ASTRION_DESKTOP_REPO', None)
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def request(port: int, path: str, body=None):
    encoded = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f'http://127.0.0.1:{port}{path}', data=encoded, headers={'Content-Type': 'application/json'})
    with OPENER.open(req, timeout=5) as response:
        return json.load(response)


def main():
    report = {'version': '0.5.1', 'isolated_data': str(DATA)}
    with (OUT / 'process.log').open('wb') as log:
        process = subprocess.Popen([str(ROOT / 'desktop/src-tauri/target/release/astrion-desktop.exe')], cwd=OUT, env=ENV, stdout=log, stderr=log)
        backend_pids = []
        try:
            shell = psutil.Process(process.pid)
            deadline = time.monotonic() + 70
            bridge = None
            while time.monotonic() < deadline and process.poll() is None:
                for conn in shell.net_connections(kind='tcp'):
                    if conn.status != psutil.CONN_LISTEN:
                        continue
                    try:
                        if request(conn.laddr.port, '/version').get('version') == '0.5.1':
                            bridge = conn.laddr.port
                            break
                    except Exception:
                        pass
                if bridge:
                    try:
                        info = request(bridge, '/quick/info')
                        if info.get('ok'):
                            report['quick_config'] = info['data']
                            break
                    except Exception:
                        pass
                time.sleep(0.25)
            else:
                raise RuntimeError('Release shell did not initialize its backend and quick controller.')
            print('release shell and quick controller ready', flush=True)
            children = shell.children(recursive=True)
            backend = [p for p in children if p.name().lower() == 'astrion-backend.exe']
            assert len(backend) == 1, 'Expected one bundled backend process.'
            backend_pids = [p.pid for p in backend]
            report['bundled_backend'] = True
            report['backend_count'] = len(backend)
            report['permissions'] = request(bridge, '/quick/permissions')
            assert report['permissions']['data']['screenPermission'] == 'granted'
            token = DATA / 'host/data/host_api_token'
            assert token.is_file(), 'Bundled server did not create a host token in isolated data.'
            request(bridge, '/window/control', {'action': 'close'})
            time.sleep(1)
            assert process.poll() is None, 'Closing the main window exited despite enabled Quick Chat.'
            report['enabled_main_close_keeps_process'] = True
            configured = request(bridge, '/quick/configure', {'enabled': False})
            assert configured['data']['enabled'] is False
            request(bridge, '/window/control', {'action': 'close'})
            process.wait(timeout=12)
            deadline = time.monotonic() + 5
            while any(psutil.pid_exists(pid) for pid in backend_pids) and time.monotonic() < deadline:
                time.sleep(0.1)
            report['exit_code'] = process.returncode
            report['backend_cleaned_up'] = not any(psutil.pid_exists(pid) for pid in backend_pids)
            assert report['backend_cleaned_up'], 'Bundled backend survived shell exit.'
            (OUT / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            print(json.dumps(report, indent=2), flush=True)
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)


if __name__ == '__main__':
    main()
