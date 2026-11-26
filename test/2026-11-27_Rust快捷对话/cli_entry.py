"""Start the ordinary CLI wrapper in a real isolated terminal with the bundled sidecar."""
from __future__ import annotations

import fcntl
import json
import os
import pty
import select
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import termios
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]


def request(url, headers=None):
    with urlopen(Request(url, headers=headers or {}), timeout=1) as response:
        return json.load(response)


def run(app, root):
    resources = app / 'Contents/Resources/runtime/rust'
    backend = resources / 'astrion-headless'
    workspace = root / 'workspace'
    config = root / 'config'
    data = root / 'host/data'
    for directory in [workspace, config, data, root / 'home']:
        directory.mkdir(parents=True)
    registry = config / 'host_workspaces.json'
    registry.write_text(json.dumps({'default_workspace_id': 'default', 'workspaces': [
        {'workspace_id': 'default', 'label': 'CLI wrapper fixture', 'path': str(workspace)}
    ]}), encoding='utf-8')
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    environment = {key: os.environ[key] for key in ('PATH', 'SHELL') if key in os.environ}
    environment.update({
        'HOME': str(root / 'home'), 'TERM': 'xterm-256color',
        'ASTRION_IGNORE_DOTENV': '1', 'ASTRION_DATA_ROOT': str(root),
        'ASTRION_RUST_BACKEND_BIN': str(backend), 'ASTRION_RUST_RESOURCE_ROOT': str(resources),
        'ASTRION_API_PORT': str(port), 'HOST_WORKSPACES_FILE': str(registry),
        'DATA_DIR': str(data), 'DEPLOY_CONFIG_DIR': str(config), 'LOGS_DIR': str(root / 'host/logs'),
    })
    assert shutil.which('bun', path=environment['PATH'])
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 30, 100, 0, 0))
    child = subprocess.Popen([ROOT / 'cli/bin/astrion'], cwd=workspace, env=environment,
                             stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
    os.close(slave)
    output = bytearray()
    owned_pid = None
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if select.select([master], [], [], .05)[0]:
                try:
                    output.extend(os.read(master, 32_768))
                except OSError:
                    pass
            try:
                current = request(base + '/api/host-mode-enabled')
            except (URLError, TimeoutError):
                assert child.poll() is None, output[-2000:]
                continue
            pid = current['pid']
            cmd = subprocess.check_output(['/bin/ps', '-o', 'command=', '-p', str(pid)], text=True)
            assert str(backend) in cmd and str(data) in cmd, cmd
            owned_pid = pid
            assert current['backend'] == 'rust'
            token = (data / 'host_api_token').read_text().strip()
            workspaces = request(base + '/api/host/workspaces', {'Authorization': 'Bearer ' + token})
            assert 'CLI wrapper fixture' in json.dumps(workspaces), workspaces
            assert child.poll() is None and output, output[-2000:]
            print(f'Astrion CLI wrapper opened an isolated TUI and started packaged Rust PID {owned_pid}.')
            return
        raise AssertionError(output[-2000:])
    finally:
        if child.poll() is None:
            child.terminate()
        child.wait(timeout=5)
        if owned_pid:
            os.kill(owned_pid, signal.SIGTERM)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                try:
                    request(base + '/api/host-mode-enabled')
                except (URLError, TimeoutError):
                    break
                time.sleep(.05)
            else:
                raise AssertionError('The isolated Rust Gateway did not close its listener')
        os.close(master)


if __name__ == '__main__':
    app = Path(sys.argv[1]).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix='astrion-cli-wrapper-') as temp:
        run(app, Path(temp).resolve())
