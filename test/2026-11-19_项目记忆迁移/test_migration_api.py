from __future__ import annotations

from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from flask import Flask, session

from modules.project_memory import NOTICE_NAME
from modules.project_memory_migration import WorkspaceMemories
from server import project_memory_migration as migration
from server import state


class MigrationApiTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.app = Flask(__name__)
        self.app.secret_key = 'project-memory-test'
        self.app.register_blueprint(migration.project_memory_migration_bp)
        self.client = self.app.test_client()
        self.addCleanup(patch.stopall)
        patch('server.auth_helpers.is_logged_in', side_effect=lambda: bool(session.get('username'))).start()
        patch.object(migration, '_JOB', None).start()
        patch.object(migration, '_STARTUP_SHOWN', False).start()

    def login(self, role, host=False):
        with self.client.session_transaction() as browser_session:
            browser_session.update(username='test-user', role=role, host_mode=host)

    def create_legacy(self, name):
        legacy = self.root / name / 'project/.astrion/memory'
        legacy.mkdir(parents=True)
        (legacy / 'topic.md').write_text(f'memory in {name}')
        return legacy

    def test_host_startup_claims_once_and_settings_still_reopens(self):
        self.login('admin', host=True)
        first = self.create_legacy('one')
        self.create_legacy('two')
        patch.object(migration, 'DATA_DIR', str(self.root / 'runtime')).start()
        patch.object(migration, 'load_host_workspace_catalog', return_value={
            'workspaces': [{'workspace_id': name, 'label': name, 'path': str(self.root / name / 'project')}
                           for name in ('one', 'two')],
        }).start()
        url = '/api/project-memory-migration'
        self.assertEqual(self.client.post(url + '/startup').json['overview']['total'], 2)
        self.assertEqual(self.client.post(url + '/startup').json, {})
        overview = self.client.get(url).json['overview']
        self.assertEqual(len(overview['workspaces']), 2)
        self.assertNotEqual(overview['workspaces'][0]['target'], overview['workspaces'][1]['target'])
        self.assertTrue((first / 'topic.md').exists())

        response = self.client.post(url)
        self.assertEqual(response.status_code, 202)
        job_id = response.json['job']['id']
        for _ in range(150):
            job = self.client.get(url, query_string={'job': job_id}).json['job']
            if job['phase'] in {'done', 'failed'}:
                break
            time.sleep(0.01)
        self.assertEqual((job['phase'], job['copied'], job['verified'], job['migrated']), ('done', 2, 2, 2), job)
        self.assertEqual(self.client.get(url).json['overview']['total'], 0)
        self.assertEqual([p.name for p in first.iterdir()], [NOTICE_NAME])

    def test_docker_requires_admin_for_scan_and_start_and_includes_api_accounts(self):
        patch.object(migration, 'TERMINAL_SANDBOX_MODE', 'docker').start()
        url = '/api/project-memory-migration'
        self.assertEqual(self.client.get(url).status_code, 401)
        self.login('user')
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url).status_code, 403)
        self.login('admin')
        self.assertEqual(self.client.post(url + '/startup').status_code, 403)
        self.create_legacy('web/web-user/projects/default')
        self.create_legacy('api/api-user/workspaces/job')
        users = Mock()
        users.workspace_root = self.root / 'web'
        users.list_users.return_value = {'web-user': object()}
        users.list_user_workspaces.return_value = {'default': {'label': 'Default'}}
        api = Mock()
        api.workspace_root = self.root / 'api'
        api.list_users.return_value = {'api-user': object()}
        api.list_workspaces.return_value = {'job': {}}
        with patch.object(state, 'user_manager', users), patch.object(state, 'api_user_manager', api):
            overview = self.client.get(url).json['overview']
        self.assertEqual((overview['users'], len(overview['workspaces']), overview['total']), (2, 2, 2))
        users.list_user_workspaces.assert_called_once_with('web-user', prepare=False)
        api.list_workspaces.assert_called_once_with('api-user')
        api.ensure_workspace.assert_not_called()

    def test_active_docker_workspace_never_reaches_mount_cutover(self):
        from server.tasks import task_manager

        legacy = self.create_legacy('active')
        workspace = WorkspaceMemories('active', 'default', 'Default', legacy.parents[1], self.root / 'data',
                                     True, (legacy / 'topic.md',))
        verify, clean = Mock(), Mock()
        with patch.object(task_manager, 'list_tasks', return_value=[SimpleNamespace(status='running')]):
            with self.assertRaisesRegex(RuntimeError, '空闲后重试'):
                migration._with_idle_docker_workspace(workspace, verify, clean)
        verify.assert_not_called()
        clean.assert_not_called()
        self.assertTrue((legacy / 'topic.md').exists())
