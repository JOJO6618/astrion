from __future__ import annotations

from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from flask import Flask, session

from modules.project_memory import NOTICE_NAME
from server import project_memory_migration as migration


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

    def catalog(self, *names):
        patch.object(migration, 'DATA_DIR', str(self.root / 'runtime')).start()
        patch.object(migration, 'load_host_workspace_catalog', return_value={
            'workspaces': [{'workspace_id': name, 'label': name, 'path': str(self.root / name / 'project')}
                           for name in names],
        }).start()

    def test_host_startup_claims_once_and_settings_still_reopens(self):
        self.login('admin', host=True)
        first = self.create_legacy('one')
        self.create_legacy('two')
        self.catalog('one', 'two')
        url = '/api/project-memory-migration'
        self.assertEqual(self.client.post(url + '/startup').json['overview']['total'], 2)
        self.assertEqual(self.client.post(url + '/startup').json, {})
        overview = self.client.get(url).json['overview']
        self.assertEqual(overview['mode'], 'host')
        self.assertEqual(overview['users'], 1)
        self.assertEqual(overview['data_root'], str(self.root / 'runtime/memory'))
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

    def test_non_host_identity_is_rejected_without_scanning(self):
        url = '/api/project-memory-migration'
        self.assertEqual(self.client.get(url).status_code, 401)
        self.login('user')
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url).status_code, 403)
        self.login('admin')
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url).status_code, 403)
        self.assertEqual(self.client.post(url + '/startup').status_code, 403)

    def test_empty_catalog_returns_empty_overview_and_no_job(self):
        self.login('user', host=True)
        self.catalog()
        url = '/api/project-memory-migration'
        self.assertEqual(self.client.post(url + '/startup').json, {})
        self.assertEqual(self.client.post(url).json['overview']['total'], 0)
        self.assertIsNone(migration._JOB)


if __name__ == '__main__':
    unittest.main()
