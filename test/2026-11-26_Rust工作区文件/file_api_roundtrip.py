"""Real Python Host vs Rust Web contract for workspace file routes.

The Python side is the production Host app (not a stub): ``server.app`` is
initialized with ``initialize_host`` on an isolated workspace, then served by a
real Werkzeug listener.  The Rust side is the real ``astrion-headless --web``.
Both are driven through the same HTTP browser/session and compared.

Covered:
  GET  /api/files
  GET  /api/focused
  GET  /api/project/files/search
  GET  /api/gui/files/entries
  GET/POST /api/gui/files/text
  GET  /api/gui/files/download

Run:
  ASTRION_HEADLESS_BIN=backend-rust/target-master/debug/astrion-headless \
  python3.11 -B test/2026-11-26_Rust工作区文件/file_api_roundtrip.py

Use ``--python-only`` while the Rust routes are still being implemented; that
mode prints the Python oracle without requiring a Rust binary.
"""
from __future__ import annotations

from contextlib import contextmanager
from http.cookiejar import CookieJar
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE.parent / '2026-11-24_Rust后端'))
from agent_process_roundtrip import rust  # noqa: E402
from instance_lock_e2e import fixture_environment  # noqa: E402

MISSING = object()
WRITE_TEXT = '写入后的内容\nline two\n'
DOWNLOAD_TEXT = b'download fixture bytes\n\x00\x01\x02\n'

PYTHON_SERVER = r'''
import os
import sys

sys.path.insert(0, os.environ['FIXTURE_REPO'])
from server.app import app
from server.host_startup import initialize_host

initialize_host(app, os.environ['HOST_PROJECT_PATH'])
from werkzeug.serving import make_server

httpd = make_server('127.0.0.1', 0, app, threaded=True)
print('READY=' + str(httpd.server_port), flush=True)
import threading
threading.Thread(target=lambda: (sys.stdin.read(), httpd.shutdown()), daemon=True).start()
httpd.serve_forever()
'''


def create_workspace(workspace: Path, outside: Path) -> None:
    shutil.rmtree(workspace, ignore_errors=True)
    workspace.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(outside, ignore_errors=True)
    outside.mkdir(parents=True, exist_ok=True)

    (workspace / 'alpha.txt').write_text('alpha\n', encoding='utf-8')
    (workspace / 'Beta.MD').write_text('# beta\n', encoding='utf-8')
    (workspace / 'zeta.json').write_text('{"fixture": true}\n', encoding='utf-8')
    (workspace / 'binary.bin').write_bytes(b'\x00\xff\xfe\x80binary')
    (workspace / 'no-extension').write_text('plain text without suffix\n', encoding='utf-8')
    (workspace / 'too-large.txt').write_text('x' * (2 * 1024 * 1024 + 1), encoding='utf-8')
    (workspace / 'exact-limit.txt').write_text('e' * (2 * 1024 * 1024), encoding='utf-8')
    (workspace / 'download.txt').write_bytes(DOWNLOAD_TEXT)

    docs = workspace / 'docs'
    (docs / 'nested').mkdir(parents=True)
    (docs / 'guide.md').write_text('指南第一版\nline2\n', encoding='utf-8')
    (docs / 'nested' / 'deep.txt').write_text('deep\n', encoding='utf-8')
    deep = docs / 'nested' / 'l2' / 'l3' / 'l4' / 'l5' / 'l6' / 'l7'
    deep.mkdir(parents=True)
    (deep / 'too-deep.txt').write_text('not visible\n', encoding='utf-8')

    hidden = workspace / '.hidden'
    hidden.mkdir()
    (hidden / 'secret.txt').write_text('hidden secret\n', encoding='utf-8')
    spaced = workspace / 'folder with space'
    spaced.mkdir()
    (spaced / '中文 文件.txt').write_text('中文内容\n', encoding='utf-8')

    outside_file = outside / 'outside.txt'
    outside_file.write_text('outside\n', encoding='utf-8')
    try:
        (workspace / 'escape-link').symlink_to(outside_file)
        (workspace / 'inside-link').symlink_to(docs / 'guide.md')
    except (OSError, NotImplementedError):
        pass


def reset_workspace(workspace: Path, outside: Path) -> None:
    create_workspace(workspace, outside)


class Browser:
    def __init__(self, base: str, *, login: bool = True):
        self.base = base
        self.cookies = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))
        self.csrf = ''
        if login:
            self.login()

    def request(self, method: str, path: str, *, body=MISSING, headers=None,
                status: int = 200, csrf: bool = True):
        sent = {'Origin': self.base, 'X-Astrion-Workspace-Id': 'default', **(headers or {})}
        data = None
        if body is not MISSING:
            if isinstance(body, (bytes, bytearray)):
                data = bytes(body)
            else:
                data = json.dumps(body, ensure_ascii=False).encode('utf-8')
                sent.setdefault('Content-Type', 'application/json')
            if csrf and self.csrf:
                sent['X-CSRF-Token'] = self.csrf
        request = Request(self.base + path, data=data, headers=sent, method=method)
        try:
            response = self.cookies.open(request, timeout=8)
        except HTTPError as error:
            response = error
        with response:
            payload = response.read()
            assert response.status == status, (method, path, response.status, status, payload[:2000])
            return payload, {key.lower(): value for key, value in response.headers.items()}

    def login(self) -> None:
        payload, _ = self.request('GET', '/api/csrf-token')
        self.csrf = json.loads(payload)['token']
        self.request('POST', '/host-login', body={})
        payload, _ = self.request('GET', '/api/csrf-token')
        self.csrf = json.loads(payload)['token']

    def json(self, method: str, path: str, *, body=MISSING, status: int = 200,
             csrf: bool = True, headers=None):
        payload, response_headers = self.request(method, path, body=body, status=status,
                                                 csrf=csrf, headers=headers)
        return json.loads(payload), response_headers


def query(base_path: str, **params) -> str:
    filtered = {key: value for key, value in params.items() if value is not None}
    return base_path + ('?' + urlencode(filtered) if filtered else '')


def entries_view(payload: dict) -> dict:
    data = payload['data']
    return {
        'success': payload['success'],
        'path': data['path'],
        'breadcrumb': data['breadcrumb'],
        'items': [
            {key: item[key] for key in ('name', 'path', 'type', 'size', 'extension', 'is_editable')}
            for item in data['items']
        ],
    }


def assert_outside_link(link: dict | None, workspace: Path, outside: Path) -> None:
    assert link is not None, 'outside symlink is missing from directory entries'
    expected_size = os.lstat(workspace / 'escape-link').st_size
    target_size = (outside / 'outside.txt').stat().st_size
    assert expected_size != target_size, (expected_size, target_size)
    assert link == {
        'name': 'escape-link',
        'path': 'escape-link',
        'type': 'file',
        'size': expected_size,
        'extension': '',
        'is_editable': False,
    }, link


def text_view(payload: dict, *, include_content: bool = False) -> dict:
    if 'data' in payload:
        result = {'success': payload['success'], 'data': {
            key: payload['data'][key] for key in ('path', 'size')
        }}
        assert parseable_iso(payload['data']['modified_at']), payload
        return result
    result = {'success': payload['success'], 'path': payload.get('path')}
    if include_content:
        result['content'] = payload.get('content')
    if 'modified_at' in payload:
        assert parseable_iso(payload['modified_at']), payload
        result['modified_at'] = '<iso>'
    return result


def parseable_iso(value: str) -> bool:
    try:
        from datetime import datetime
        datetime.fromisoformat(value)
        return True
    except (TypeError, ValueError):
        return False


def error_view(payload: dict) -> dict:
    """Keep the exact production error payload for strict comparison."""
    assert payload.get('success') is False, payload
    error = payload.get('error')
    assert isinstance(error, str) and error, payload
    return payload


def exercise(base: str, browser: Browser, workspace: Path, outside: Path) -> dict:
    result = {}
    result['files'] = browser.json('GET', '/api/files')[0]
    result['focused'] = browser.json('GET', '/api/focused')[0]

    for index, params in enumerate((
        {'q': ''},
        {'q': 'guide'},
        {'q': 'docs/'},
        {'q': 'txt'},
        {'q': '中文'},
        {'q': 'DEEP'},
        {'q': 'too-deep'},
        {'q': 'nomatch'},
        {'q': 'a', 'limit': 1},
        {'q': 'a', 'limit': 999},
    )):
        result[f'search_{index}'] = browser.json('GET', query('/api/project/files/search', **params))[0]

    root = entries_view(browser.json('GET', '/api/gui/files/entries')[0])
    docs = entries_view(browser.json('GET', query('/api/gui/files/entries', path='docs'))[0])
    nested = entries_view(browser.json('GET', query('/api/gui/files/entries', path='docs/nested'))[0])
    absolute_inside = entries_view(browser.json(
        'GET', query('/api/gui/files/entries', path=str(workspace / 'docs')))[0])
    result['entries'] = {'root': root, 'docs': docs, 'nested': nested,
                         'absolute_inside': absolute_inside}
    outside_link = next((item for item in root['items'] if item['name'] == 'escape-link'), None)

    result['entries_errors'] = {}
    for name, path in {
        'traversal': '../outside',
        'absolute_outside': str(outside),
        'missing': 'missing-dir',
        'file_not_dir': 'alpha.txt',
        'symlink': 'escape-link',
    }.items():
        payload, _ = browser.json('GET', query('/api/gui/files/entries', path=path), status=400)
        result['entries_errors'][name] = error_view(payload)

    text = browser.json('GET', query('/api/gui/files/text', path='docs/guide.md'))[0]
    result['text_read'] = text_view(text, include_content=True)
    result['text_symlink_inside'] = text_view(
        browser.json('GET', query('/api/gui/files/text', path='inside-link'))[0],
        include_content=True,
    )
    result['text_absolute_inside'] = text_view(
        browser.json('GET', query('/api/gui/files/text', path=str(workspace / 'docs/guide.md')))[0],
        include_content=True,
    )
    result['text_errors'] = {}
    for name, path in {
        'missing': 'missing.txt',
        'directory': 'docs',
        'non_utf8': 'binary.bin',
        'too_large': 'too-large.txt',
        'traversal': '../outside.txt',
        'absolute_outside': str(outside / 'outside.txt'),
        'symlink': 'escape-link',
    }.items():
        payload, _ = browser.json('GET', query('/api/gui/files/text', path=path), status=400)
        result['text_errors'][name] = error_view(payload)
    result['text_missing_path'] = error_view(
        browser.json('GET', '/api/gui/files/text', status=400)[0])

    written, _ = browser.json('POST', '/api/gui/files/text',
                              body={'path': 'docs/guide.md', 'content': WRITE_TEXT})
    result['text_write'] = text_view(written)
    result['text_read_after_write'] = text_view(
        browser.json('GET', query('/api/gui/files/text', path='docs/guide.md'))[0],
        include_content=True,
    )
    result['text_write_errors'] = {}
    for name, body in {
        'missing_path': {'content': 'x'},
        'missing_content': {'path': 'docs/guide.md'},
        'missing_file': {'path': 'missing.txt', 'content': 'x'},
        'directory': {'path': 'docs', 'content': 'x'},
        'traversal': {'path': '../outside.txt', 'content': 'x'},
        'symlink': {'path': 'escape-link', 'content': 'x'},
        'too_large': {'path': 'docs/guide.md', 'content': 'x' * (2 * 1024 * 1024 + 1)},
    }.items():
        payload, _ = browser.json('POST', '/api/gui/files/text', body=body, status=400)
        result['text_write_errors'][name] = error_view(payload)

    download, headers = browser.request('GET', query('/api/gui/files/download', path='download.txt'))
    result['download'] = {
        'body': base64.b64encode(download).decode('ascii'),
        'attachment': 'attachment' in headers.get('content-disposition', '').lower(),
        'filename': 'download.txt' in headers.get('content-disposition', ''),
    }
    inside_download, _ = browser.request('GET', query('/api/gui/files/download', path='inside-link'))
    result['download_inside_link'] = base64.b64encode(inside_download).decode('ascii')
    result['download_errors'] = {}
    for name, path, status in (
        ('missing_path', None, 400),
        ('missing_file', 'missing.txt', 400),
        ('directory', 'docs', 410),
        ('traversal', '../outside.txt', 400),
        ('absolute_outside', str(outside / 'outside.txt'), 400),
        ('symlink', 'escape-link', 400),
    ):
        route = '/api/gui/files/download' if path is None else query('/api/gui/files/download', path=path)
        payload, _ = browser.json('GET', route, status=status)
        result['download_errors'][name] = {'status': status, 'body': error_view(payload)}

    result['auth'] = {}
    for name, path in (('entries', '/api/gui/files/entries'),
                       ('files', '/api/files'),
                       ('focused', '/api/focused'),
                       ('search', '/api/project/files/search?q=x'),
                       ('text', '/api/gui/files/text?path=alpha.txt'),
                       ('download', '/api/gui/files/download?path=download.txt')):
        Browser(base, login=False).request('GET', path, status=401)
        result['auth'][name] = 401
    # Session without CSRF must not be able to write.
    payload, _ = browser.json('POST', '/api/gui/files/text',
                              body={'path': 'docs/guide.md', 'content': 'forbidden'},
                              csrf=False, status=403)
    result['csrf'] = error_view(payload)
    # The rejected write must not have changed the file.
    result['text_after_csrf'] = browser.json(
        'GET', query('/api/gui/files/text', path='docs/guide.md'))[0]['content']

    return result, outside_link


@contextmanager
def python_web(env: dict[str, str], log_path: Path):
    work_env = dict(env)
    work_env['FIXTURE_REPO'] = str(REPO)
    with log_path.open('w', encoding='utf-8') as log:
        process = subprocess.Popen(
            [sys.executable, '-B', __file__, 'python-server'],
            env=work_env, cwd=str(REPO), stdout=log, stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE, text=True,
        )
        try:
            deadline = time.monotonic() + 25
            port = None
            while time.monotonic() < deadline:
                text = log_path.read_text(encoding='utf-8')
                for line in text.splitlines():
                    if line.startswith('READY='):
                        port = int(line.removeprefix('READY='))
                        break
                if port is not None:
                    break
                if process.poll() is not None:
                    raise AssertionError('Python Host exited early:\n' + text)
                time.sleep(0.03)
            if port is None:
                raise AssertionError('Python Host did not become ready:\n' + log_path.read_text(encoding='utf-8'))
            yield f'http://127.0.0.1:{port}'
        finally:
            process.stdin.close()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def rust_binary() -> Path:
    configured = os.environ.get('ASTRION_HEADLESS_BIN')
    candidates = [Path(configured)] if configured else []
    candidates += [REPO / 'backend-rust/target-master/debug/astrion-headless',
                   REPO / 'backend-rust/target/debug/astrion-headless']
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise AssertionError('Build astrion-headless or set ASTRION_HEADLESS_BIN')


def assert_python_contract(result: dict, workspace: Path) -> None:
    files = result['files']
    assert set(files) == {
        'path', 'files', 'folders', 'total_files', 'total_size', 'tree',
        'unavailable', 'message',
    }, files
    assert files['path'] == str(workspace.resolve()), files
    assert files['files'] == [] and files['folders'] == [] and files['tree'] == {}, files
    assert files['total_files'] == 0 and files['total_size'] == 0, files
    assert files['unavailable'] is True and files['message'], files
    assert result['focused'] == {}, result['focused']

    root_search = result['search_0']
    names = [item['name'] for item in root_search['data']['items']]
    assert root_search['success'] is True, root_search
    assert names[:4] == ['.astrion', '.hidden', 'docs', 'folder with space'], names
    assert root_search['data']['total'] == 14, root_search
    assert result['entries']['root']['path'] == '', result['entries']['root']
    assert result['entries']['root']['breadcrumb'] == [{'name': '根目录', 'path': ''}], result['entries']['root']
    assert result['text_read']['content'] == '指南第一版\nline2\n', result['text_read']
    assert result['download']['body'] == base64.b64encode(DOWNLOAD_TEXT).decode('ascii')


def differences(left, right, path='$') -> list[str]:
    if type(left) is not type(right):
        return [f'{path}: {left!r} != {right!r}']
    if isinstance(left, dict):
        result = []
        for key in sorted(set(left) | set(right)):
            if key not in left:
                result.append(f'{path}.{key}: missing in Python')
            elif key not in right:
                result.append(f'{path}.{key}: missing in Rust')
            else:
                result.extend(differences(left[key], right[key], f'{path}.{key}'))
        return result
    if isinstance(left, list):
        result = []
        if len(left) != len(right):
            result.append(f'{path}: length {len(left)} != {len(right)}')
        for index, (a, b) in enumerate(zip(left, right)):
            result.extend(differences(a, b, f'{path}[{index}]'))
        return result
    return [] if left == right else [f'{path}: {left!r} != {right!r}']


def run() -> None:
    python_only = '--python-only' in sys.argv
    binary = None if python_only else rust_binary()
    with tempfile.TemporaryDirectory(prefix='workspace-files-', dir=HERE) as directory:
        root = Path(directory)
        env = fixture_environment(root)
        env['ASTRION_RESOURCE_ROOT'] = str(REPO)
        workspace = Path(env['HOST_PROJECT_PATH'])
        outside = root / 'outside'
        create_workspace(workspace, outside)
        data_dir = Path(env['DATA_DIR'])
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / 'personalization.json').write_text(
            json.dumps({'enabled_skills': []}), encoding='utf-8')

        with python_web(env, root / 'python.log') as base:
            browser = Browser(base)
            # Warm up the production terminal, then remove its synced .astrion
            # directory so this contract only covers the synthetic workspace.
            browser.json('GET', '/api/files')
            shutil.rmtree(workspace / '.astrion', ignore_errors=True)
            python_result = exercise(base, browser, workspace, outside)
        assert_python_contract(python_result, workspace)
        if '--dump' in sys.argv:
            print(json.dumps(python_result, ensure_ascii=False, indent=2))
        print('Python Host file oracle collected.')

        # The production terminal creates .astrion on every request; keep that
        # one internal directory identical for the Rust run, then reset fixture files.
        preserved_astrion = root / 'astrion-preserved'
        source_astrion = workspace / '.astrion'
        if source_astrion.is_dir():
            shutil.copytree(source_astrion, preserved_astrion)
        reset_workspace(workspace, outside)
        if preserved_astrion.is_dir():
            shutil.copytree(preserved_astrion, workspace / '.astrion')
        if not python_only:
            with rust(env, binary, 'http://127.0.0.1:9/v1/chat/completions',
                      root / 'rust-file.log', web=True) as base:
                rust_result = exercise(base, Browser(base), workspace, outside)
            diff = differences(rust_result, python_result)

            # Python must read what Rust wrote, and the rejected CSRF write must not stick.
            with python_web(env, root / 'python-after-rust.log') as base:
                browser = Browser(base)
                current = browser.json('GET', query('/api/gui/files/text', path='docs/guide.md'))[0]
                assert current['content'] == WRITE_TEXT, current
                found = browser.json('GET', query('/api/project/files/search', q='guide'))[0]
                assert found['data']['items'][0]['path'] == 'docs/guide.md', found
                downloaded, _ = browser.request(
                    'GET', query('/api/gui/files/download', path='download.txt'))
                assert downloaded == DOWNLOAD_TEXT
            print('Python Host reads Rust Web writes; CSRF write was rejected.')
            assert not diff, 'Python/Rust file contract mismatch:\n' + '\n'.join(diff[:80])
            print('Python Host and Rust Web workspace file contracts match.')
        else:
            print('Rust comparison skipped with --python-only.')


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'python-server':
        exec(compile(PYTHON_SERVER, '<python-server>', 'exec'))
    else:
        run()
