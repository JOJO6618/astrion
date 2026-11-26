"""Cross-process writer lock acceptance using isolated Python and Rust backends.

Run from the repository with the Python backend dependencies and a built binary:
    python3.11 -B test/2026-11-24_Rust后端/instance_lock_e2e.py
The Python workers use the production entry points with Flask's listener replaced
by a stdin barrier. The Rust worker starts its actual loopback listener on port 0.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
LOCK_NAME = 'astrion-backend.lock'
CONTENTION = 'Another Astrion backend is using this data directory'
PYTHON_READY = 'INSTANCE_LOCK_TEST_READY'
RUST_READY = 'Astrion Rust server listening on'
PYTHON_ENTRIES = ('server.app', 'server.headless_app')
TIMEOUT = 12

PYTHON_WORKER = '''
import runpy
import sys

root, entry, workspace, *flags = sys.argv[1:]
sys.path.insert(0, root)
from flask import Flask
from server import host_startup

host_startup.start_background_jobs = lambda: None

def hold_until_parent_closes_stdin(self, *args, **kwargs):
    print('INSTANCE_LOCK_TEST_READY', flush=True)
    sys.stdin.buffer.read()

Flask.run = hold_until_parent_closes_stdin
sys.argv = [entry, '--path', workspace, *flags]
runpy.run_module(entry, run_name='__main__')
'''

PYTHON_IMPORT = '''
import sys
sys.path.insert(0, sys.argv[1])
from server.app import app
from server.headless_app import create_headless_app
assert list(app.url_map.iter_rules())
assert list(create_headless_app().url_map.iter_rules())
'''


def fixture_environment(directory: Path) -> dict[str, str]:
    for part in ('home', 'config', 'workspace'):
        (directory / part).mkdir(parents=True, exist_ok=True)
    (directory / 'config/custom_models.json').write_text('{"models": []}\n', encoding='utf-8')
    workspace_catalog = directory / 'config/host_workspaces.json'
    workspace_catalog.write_text(json.dumps({'default_workspace_id': 'default', 'workspaces': [
        {'workspace_id': 'default', 'label': 'Lock fixture', 'path': str(directory / 'workspace')},
    ]}), encoding='utf-8')
    env = {key: os.environ[key] for key in ('SystemRoot', 'WINDIR') if key in os.environ}
    env.update({
        'PATH': os.defpath,
        'HOME': str(directory / 'home'),
        'USERPROFILE': str(directory / 'home'),
        'ASTRION_IGNORE_DOTENV': '1',
        'ASTRION_DATA_ROOT': str(directory / 'astrion'),
        'DEPLOY_CONFIG_DIR': str(directory / 'config'),
        'HOST_WORKSPACES_FILE': str(workspace_catalog),
        'DEFAULT_PROJECT_PATH': str(directory / 'workspace'),
        'HOST_PROJECT_PATH': str(directory / 'workspace'),
        'DATA_DIR': str(directory / 'data'),
        'LOGS_DIR': str(directory / 'logs'),
        'TERMINAL_SANDBOX_MODE': 'host',
        'WEB_SECRET_KEY': 'isolated-instance-lock-test',
    })
    return env


def python_command(entry: str, environment: dict[str, str], *, debug: bool = False) -> list[str]:
    command = [sys.executable, '-BI', '-X', 'utf8', '-c', PYTHON_WORKER,
               str(ROOT), entry, environment['HOST_PROJECT_PATH']]
    if debug:
        command.append('--debug')
    return command


def rust_command(binary: Path, environment: dict[str, str]) -> list[str]:
    return [str(binary), '--port', '0', '--workspace', environment['HOST_PROJECT_PATH'],
            '--data-dir', environment['DATA_DIR'], '--endpoint', 'http://127.0.0.1:9/v1/chat/completions',
            '--model', 'instance-lock-test']


def run(command: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, env=env, cwd=env['HOST_PROJECT_PATH'], stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding='utf-8', timeout=TIMEOUT)


def assert_conflict(command: list[str], env: dict[str, str], language: str) -> None:
    result = run(command, env)
    assert result.returncode != 0 and CONTENTION in result.stdout, (
        f'{language} was expected to report an occupied data directory, got {result.returncode}\n{result.stdout}'
    )
    assert PYTHON_READY not in result.stdout and RUST_READY not in result.stdout, result.stdout


def assert_imports_unlocked(env: dict[str, str]) -> None:
    result = run([sys.executable, '-BI', '-X', 'utf8', '-c', PYTHON_IMPORT, str(ROOT)], env)
    assert result.returncode == 0, result.stdout


@contextmanager
def holder(command: list[str], env: dict[str, str], log_path: Path, ready: str):
    python = ready == PYTHON_READY
    with log_path.open('wb') as output:
        process = subprocess.Popen(command, env=env, cwd=env['HOST_PROJECT_PATH'],
                                   stdin=subprocess.PIPE if python else subprocess.DEVNULL,
                                   stdout=output, stderr=subprocess.STDOUT)
    try:
        deadline = time.monotonic() + TIMEOUT
        while time.monotonic() < deadline and process.poll() is None:
            if ready in log_path.read_text(encoding='utf-8'):
                yield
                return
            time.sleep(0.02)
        raise AssertionError(f'Process did not reach {ready} (exit={process.poll()})\n{log_path.read_text(encoding="utf-8")}')
    finally:
        if python:
            process.stdin.close()
        elif process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def test_python_entries(directory: Path, binary: Path | None) -> None:
    env = fixture_environment(directory)
    lock = Path(env['DATA_DIR']) / LOCK_NAME
    assert_imports_unlocked(env)
    assert not lock.exists(), 'Importing the apps or creating a Flask app must leave the backend lock absent'
    legacy_entry = [sys.executable, '-BI', '-X', 'utf8', str(ROOT / 'main.py')]
    for entry, other in (PYTHON_ENTRIES, PYTHON_ENTRIES[::-1]):
        with holder(python_command(entry, env), env, directory / 'holder.log', PYTHON_READY):
            assert lock.is_file(), 'The Python entry must claim the shared file before entering Flask.run'
            assert_conflict(python_command(other, env), env, other)
            assert_conflict(legacy_entry, env, 'main.py')
            assert not (Path(env['DATA_DIR']) / 'conversation_history.json').exists()
            assert_imports_unlocked(env)
            if binary is not None:
                assert_conflict(rust_command(binary, env), env, 'Rust')
                assert not (Path(env['DATA_DIR']) / 'runtime').exists()
    assert lock.is_file(), 'Stopping the writer must preserve the lock file'
    print('Python full/headless contention, lock-free app construction and post-exit reacquisition passed.')
    if binary is not None:
        print('Both Python entry points reject a concurrent real Rust binary.')


def test_debug_worker(directory: Path) -> None:
    env = fixture_environment(directory)
    lock = Path(env['DATA_DIR']) / LOCK_NAME
    for entry in PYTHON_ENTRIES:
        with holder(python_command(entry, env, debug=True), env, directory / 'supervisor.log', PYTHON_READY):
            if not lock.exists():
                assert not (Path(env['DATA_DIR']) / 'host_api_token').exists()
            worker = dict(env, WERKZEUG_RUN_MAIN='true')
            with holder(python_command(entry, worker, debug=True), worker, directory / 'worker.log', PYTHON_READY):
                assert lock.is_file()
                assert_conflict(python_command(entry, env), env, entry)
    print('Werkzeug debug supervisors leave the shared lock to their writer workers.')


def test_rust_holder(directory: Path, binary: Path) -> None:
    env = fixture_environment(directory)
    lock = Path(env['DATA_DIR']) / LOCK_NAME
    command = rust_command(binary, env)
    with holder(command, env, directory / 'rust-holder.log', RUST_READY):
        assert lock.is_file(), 'The Rust binary must create the same shared lock file'
        before = lock.stat()
        for entry in PYTHON_ENTRIES:
            assert_conflict(python_command(entry, env), env, entry)
        assert not (Path(env['LOGS_DIR']) / 'debug_stream.log').exists()
        assert_imports_unlocked(env)
    with holder(python_command('server.app', env), env, directory / 'python-after-rust.log', PYTHON_READY):
        assert os.path.samestat(before, lock.stat()), 'The new writer must reuse the original lock file'
        assert_conflict(command, env, 'Rust')
    with holder(command, env, directory / 'rust-restarted.log', RUST_READY):
        assert os.path.samestat(before, lock.stat()), 'The restarted Rust writer must reuse the original lock file'
    print('The real Rust binary rejects both Python entries and both backends can restart after process exit.')


def main() -> None:
    suffix = '.exe' if os.name == 'nt' else ''
    default_binary = os.environ.get('ASTRION_HEADLESS_BIN') or (ROOT / f'backend-rust/target/debug/astrion-headless{suffix}')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rust-bin', type=Path, default=Path(default_binary))
    parser.add_argument('--python-only', action='store_true', help='exercise Python startup and reloader wiring alone')
    parser.add_argument('--rust-contender-only', action='store_true', help='run Rust only as the refused contender when the execution environment cannot stop child processes')
    args = parser.parse_args()
    if args.python_only and args.rust_contender_only:
        parser.error('Choose at most one partial verification mode')
    binary = None if args.python_only else args.rust_bin.resolve()
    if binary is not None and not binary.is_file():
        parser.error(f'Build astrion-headless or pass --rust-bin. Executable unavailable: {binary}')
    with tempfile.TemporaryDirectory(prefix='instance_lock_run-', dir=HERE) as raw:
        scratch = Path(raw)
        test_python_entries(scratch / 'python', binary)
        test_debug_worker(scratch / 'reload')
        if binary is not None and not args.rust_contender_only:
            test_rust_holder(scratch / 'rust', binary)


if __name__ == '__main__':
    main()
