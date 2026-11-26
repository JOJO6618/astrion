"""Exercise the actual Rust Host Web administration listener with isolated data."""

import argparse
from contextlib import contextmanager
from hashlib import pbkdf2_hmac
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, HTTPCookieProcessor, ProxyHandler, Request, build_opener

HERE = Path(__file__).resolve().parent
PASSWORD = 'synthetic-admin-http-password'
POLICY = '/api/admin/policy'
EFFECTIVE = '/api/effective-policy'
STATUS = '/api/admin/secondary/status'
VERIFY = '/api/admin/secondary/verify'
TOOLS = '/api/admin/custom-tools'
FILE = TOOLS + '/file'


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Browser:
    def __init__(self, origin):
        self.origin = origin
        self.csrf = None
        self.opener = build_opener(ProxyHandler({}), NoRedirect(), HTTPCookieProcessor(CookieJar()))

    def request(self, method, path, expected=200, *, payload=None, csrf=True, origin=None, accept_json=True):
        headers = {'Origin': origin or self.origin}
        if payload is not None:
            headers['Content-Type'] = 'application/json'
        if csrf and self.csrf:
            headers['X-CSRF-Token'] = self.csrf
        data = json.dumps(payload).encode() if payload is not None else None
        request = Request(self.origin + path, data=data, headers=headers, method=method)
        try:
            response = self.opener.open(request, timeout=8)
        except HTTPError as error:
            response = error
        with response:
            body = response.read()
            assert response.status == expected, (method, path, response.status, expected, body)
            if accept_json and body:
                return json.loads(body)
            return response.headers, body

    def login(self):
        self.csrf = self.request('GET', '/api/csrf-token')['token']
        assert self.request('POST', '/host-login', payload={'password': ''})['success']
        self.csrf = self.request('GET', '/api/csrf-token')['token']

    def verify(self):
        assert self.request('POST', VERIFY, payload={'password': PASSWORD}, csrf=False)['success']


class Instance:
    def __init__(self, root):
        self.root = root.resolve()
        self.data_root = self.root / 'runtime'
        self.data = self.data_root / 'host/data'
        self.deploy = self.data_root / 'config'
        self.resources = self.root / 'resources'
        self.project = self.root / 'project'
        for location in (self.data, self.deploy, self.resources / 'static', self.project, self.root / 'tmp'):
            location.mkdir(parents=True)
        digest = pbkdf2_hmac('sha256', PASSWORD.encode(), b'fixture-admin', 500).hex()
        write_json(self.data_root / 'settings.json', {
            'admin': {'secondary_password_hash': f'pbkdf2:sha256:500$fixture-admin${digest}'},
        })
        write_json(self.deploy / 'host_workspaces.json', {
            'default_workspace_id': 'admin_fixture', 'workspaces': [
                {'workspace_id': 'admin_fixture', 'path': str(self.project), 'label': 'Fixture'},
            ],
        })
        write_json(self.deploy / 'custom_models.json', [
            {'model_name': 'admin_fixture_model', 'model_id': 'fixture-model',
             'url': 'http://127.0.0.1:1/v1', 'apikey': 'synthetic-key'},
        ])
        (self.resources / 'static/terminal.html').write_text('<html>Fixture terminal</html>')
        manifest = self.resources / 'static/dist/.vite/manifest.json'
        manifest.parent.mkdir(parents=True)
        write_json(manifest, {})

    @contextmanager
    def serve(self, binary, *, web):
        keys = ('PATH', 'HOME', 'USERPROFILE', 'SystemRoot', 'WINDIR')
        env = {key: os.environ[key] for key in keys if key in os.environ}
        env.update(
            ASTRION_IGNORE_DOTENV='1', ASTRION_DESKTOP_STOP_STDIN='1',
            ASTRION_RESOURCE_ROOT=str(self.resources), ASTRION_DATA_ROOT=str(self.data_root),
            DEPLOY_CONFIG_DIR=str(self.deploy), CROSSPET_STATE_DIR=str(self.root / 'crosspet'),
            TMPDIR=str(self.root / 'tmp'), TEMP=str(self.root / 'tmp'), TMP=str(self.root / 'tmp'),
        )
        args = [str(binary), '--port', '0', '--workspace', str(self.project), '--data-dir', str(self.data)]
        if web:
            args += ['--web', '--static-root', str(self.resources / 'static')]
        log = self.root / 'backend.log'
        with log.open('wb') as output:
            process = subprocess.Popen(args, cwd=self.root, env=env, stdin=subprocess.PIPE,
                                       stdout=output, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                trace = log.read_text()
                assert process.poll() is None, trace
                address = re.search(r'Astrion Rust server listening on (127\.0\.0\.1:\d+)', trace)
                if address:
                    break
                time.sleep(0.04)
            else:
                raise AssertionError(trace)
            yield 'http://' + address.group(1)
        finally:
            process.stdin.close()
            try:
                result = process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
                raise
            assert result == 0, log.read_text()


def write_json(path, value):
    path.write_text(json.dumps(value), encoding='utf-8')


def check(binary, fixture):
    with fixture.serve(binary, web=True) as origin:
        primary, second = Browser(origin), Browser(origin)
        primary.request('OPTIONS', POLICY, accept_json=False)
        primary.request('GET', EFFECTIVE, 401)
        headers, _ = primary.request('POST', VERIFY, 302, payload={'password': PASSWORD}, csrf=False, accept_json=False)
        assert headers['Location'] == '/login'
        primary.login()
        second.login()
        assert primary.request('GET', STATUS)['configured']
        assert not primary.request('GET', STATUS)['verified']
        assert primary.request('GET', POLICY, 403)['code'] == 'secondary_required'
        assert primary.request('GET', EFFECTIVE)['success']
        primary.request('GET', '/terminal', accept_json=False)
        primary.request('POST', VERIFY, 403, payload={'password': PASSWORD}, csrf=False, origin='https://outside.invalid')
        primary.verify()
        assert primary.request('GET', STATUS)['verified']
        assert not second.request('GET', STATUS)['verified']
        assert 'admin_fixture_model' in primary.request('GET', POLICY)['defaults']['models']
        update = {'target_type': 'global', 'target_value': 'global', 'config': {
            'ui_blocks': {'block_realtime_terminal': True}, 'disabled_models': ['admin_fixture_model'],
        }}
        primary.request('POST', POLICY, 403, payload=update, csrf=False)
        primary.request('POST', POLICY, payload=update)
        effective = second.request('GET', EFFECTIVE)['data']
        assert effective['ui_blocks']['block_realtime_terminal'] is True
        assert effective['disabled_models'] == ['admin_fixture_model']
        second.request('GET', '/terminal', 403, accept_json=False)
        primary.request('POST', TOOLS, payload={'id': 'fixture-admin-tool', 'execution_code': "print('fixture')\n"})
        saved = fixture.data / 'custom_tools/fixture-admin-tool/execution.py'
        assert saved.read_text() == "print('fixture')\n"
        assert second.request('GET', EFFECTIVE)['data']['categories']['custom']['tools'] == ['fixture-admin-tool']
        primary.request('POST', FILE, payload={'id': 'fixture-admin-tool', 'name': 'execution.py', 'content': "print('edited')\n"})
        assert primary.request('GET', TOOLS)['data'][0]['execution_code'] == "print('fixture')\n"
        primary.request('POST', TOOLS + '/reload')
        headers, content = primary.request('GET', FILE + '?id=fixture-admin-tool&name=execution.py', accept_json=False)
        assert headers['Content-Type'].startswith('text/plain') and content == b"print('edited')\n"
        primary.request('GET', FILE + '?id=fixture-admin-tool&name=..%2F..%2Fadmin_policy.json', 400)
    with fixture.serve(binary, web=True) as origin:
        browser = Browser(origin)
        browser.login()
        assert not browser.request('GET', STATUS)['verified']
        browser.request('GET', '/terminal', 403, accept_json=False)
        browser.verify()
        assert browser.request('GET', TOOLS)['data'][0]['execution_code'] == "print('edited')\n"
        browser.request('DELETE', TOOLS + '?id=fixture-admin-tool', 403, csrf=False)
        browser.request('DELETE', TOOLS + '?id=fixture-admin-tool')
        assert not saved.exists()
        assert browser.request('GET', TOOLS)['data'] == []
    with fixture.serve(binary, web=False) as origin:
        browser = Browser(origin)
        for path in (EFFECTIVE, POLICY, STATUS, VERIFY, TOOLS, FILE, TOOLS + '/reload'):
            browser.request('GET', path, 404, accept_json=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('binary', type=Path)
    binary = parser.parse_args().binary.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix='admin-http-', dir=HERE) as temporary:
        check(binary, Instance(Path(temporary)))
    print('PASS: Host Web administrator API, cookie-bound verification, CSRF, terminal policy, tool persistence and headless exclusion')


if __name__ == '__main__':
    main()
