from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[2]


class HostEntrypointsTest(unittest.TestCase):
    def run_isolated(self, source: str, **overrides: str):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'runtime'
            env = {
                **os.environ,
                'ASTRION_IGNORE_DOTENV': '1',
                'ASTRION_DATA_ROOT': str(root),
                'DATA_DIR': str(root / 'host' / 'data'),
                'LOGS_DIR': str(root / 'host' / 'logs'),
                'DEPLOY_CONFIG_DIR': str(root / 'config'),
                'HOST_WORKSPACES_FILE': str(root / 'config' / 'catalog.json'),
                'WEB_SECRET_KEY': 'synthetic-host-entrypoint-test',
                'ASTRION_DESKTOP_VERSION': '',
                'TERMINAL_SANDBOX_MODE': 'host',
                'LINUX_SAFETY': '0',
                **overrides,
            }
            result = subprocess.run(
                [sys.executable, '-B', '-c', source], cwd=REPO, env=env,
                capture_output=True, text=True, timeout=60,
            )
            return result, root.exists()

    def test_legacy_modes_stop_before_runtime_initialization(self):
        for settings in ({'TERMINAL_SANDBOX_MODE': 'docker'},
                         {'TERMINAL_SANDBOX_MODE': 'web'}, {'LINUX_SAFETY': '1'}):
            with self.subTest(settings=settings):
                result, initialized = self.run_isolated('import config', **settings)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('matching Docker/Web release', result.stderr)
                self.assertFalse(initialized)

    def test_full_and_headless_use_host_only(self):
        for shape in ('full', 'headless'):
            with self.subTest(shape=shape):
                result, _ = self.run_isolated(self.smoke_source(shape))
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @staticmethod
    def smoke_source(shape: str) -> str:
        return f'shape = {shape!r}\n' + r'''
import os
from pathlib import Path
import sys

from modules.host_workspace_manager import create_host_workspace
from server.context.resources import get_user_resources
from server.context.identity import RuntimeIdentity
from server.gateway_auth import host_api_token_path
from server.host_startup import initialize_host

root = Path(os.environ['ASTRION_DATA_ROOT'])
project = root / 'workspace'
entry = create_host_workspace(str(project), set_default=True)['workspace']
workspace_id = entry['workspace_id']
if shape == 'headless':
    from server.headless_app import create_headless_app
    app = create_headless_app()
    assert 'server.app_legacy' not in sys.modules
else:
    from server.app import app
initialize_host(app, str(project), thinking_mode=True)
assert app.config['DEFAULT_RUN_MODE'] == 'thinking'
assert app.config['DEFAULT_THINKING_MODE'] is True
assert 'modules.user_container_manager' not in sys.modules
assert 'modules.api_user_manager' not in sys.modules
assert 'server.api_auth' not in sys.modules

client = app.test_client()
models = client.get('/api/v1/models')
assert models.status_code == 200, models.get_data(as_text=True)
assert isinstance(models.json['items'], list)
for path in ('/api/projects', '/api/container-status', '/api/v1/workspaces',
             '/api/admin/api-users', '/api/app/version', '/api/app/apk/latest'):
    assert client.get(path).status_code == 404, path

assert client.get('/api/runtime/sessions').status_code == 401
bearer = {'Authorization': 'Bearer ' + host_api_token_path().read_text().strip(),
          'X-Astrion-Workspace-Id': workspace_id}
sessions = client.get('/api/runtime/sessions', headers=bearer)
assert sessions.status_code == 200, sessions.get_data(as_text=True)
created = client.post('/api/runtime/sessions', json={'run_mode': 'fast'}, headers=bearer)
assert created.status_code == 200, created.get_data(as_text=True)
conversation_id = created.json['conversation_id']
assert client.get(f'/api/runtime/sessions/{conversation_id}/history', headers=bearer).status_code == 200

owner = RuntimeIdentity(host_mode=True, host_workspace_id=workspace_id, role='admin')
terminal, workspace = get_user_resources('host', workspace_id, identity=owner)
backend = terminal.execution_backend
assert backend.get_environment()['execution_target'] == 'host'
assert get_user_resources('host', workspace_id, identity=owner)[0] is terminal
assert workspace.project_path == project.resolve()
assert 'modules.user_container_manager' not in sys.modules

if shape == 'full':
    browser = app.test_client()
    csrf = browser.get('/api/csrf-token').json['token']
    login = browser.post('/host-login', json={}, headers={'X-CSRF-Token': csrf})
    assert login.status_code == 200, login.get_data(as_text=True)
    assert browser.get('/api/session-status').json['session']['logged_in'] is True
    assert browser.get('/api/host/workspaces').status_code == 200
    attachment = workspace.uploads_dir / 'history.txt'
    attachment.write_text('saved attachment', encoding='utf-8')
    path = '/user_upload/history.txt'
    assert browser.get(path, query_string={'workspace_id': workspace_id}).data == b'saved attachment'
    assert browser.get(path, query_string={'workspace_id': 'missing-workspace'}).status_code == 404
    assert len([rule for rule in app.url_map.iter_rules() if rule.rule == '/user_upload/<path:filename>']) == 1
    assert browser.post('/login', json={}).status_code == 405
    assert browser.get('/register').status_code == 404
    csrf = browser.get('/api/csrf-token').json['token']
    assert browser.post('/logout', headers={'X-CSRF-Token': csrf}).status_code == 200
    assert browser.get('/api/session-status').json['session']['logged_in'] is False
    assert client.get('/api/runtime/sessions', headers=bearer).status_code == 200
'''
