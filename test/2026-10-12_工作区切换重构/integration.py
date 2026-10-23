"""Real storage/routes with temporary runtime roots; never starts a server."""
from pathlib import Path
import inspect
import json
import os
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
_TEMP = tempfile.TemporaryDirectory(dir=Path(__file__).parent, prefix='isolated-')
RUNTIME = Path(_TEMP.name)
os.environ.update(ASTRION_IGNORE_DOTENV='1', ASTRION_DATA_ROOT=str(RUNTIME),
                  DATA_DIR=str(RUNTIME / 'data'), LOGS_DIR=str(RUNTIME / 'logs'),
                  DEPLOY_CONFIG_DIR=str(RUNTIME / 'deploy'), TERMINAL_SANDBOX_MODE='host',
                  USER_SPACE_DIR=str(RUNTIME / 'users'), API_USER_SPACE_DIR=str(RUNTIME / 'api'))

from flask import Flask
from server.context.data import WorkspaceData, _STORES
from server.context.identity import RuntimeIdentity
from server.context.status_view import workspace_status
from server.conversation_bootstrap import bootstrap_conversation
from server.chat.permission import get_permission_mode, get_execution_mode, get_network_permission, get_work_mode
from server.conversation import get_input_draft, get_conversation_token_statistics, get_conversation_tokens
from utils.conversation_manager import ConversationManager
from server import state


class DataIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.project = RUNTIME / 'project'; self.project.mkdir(exist_ok=True)
        self.app = Flask('workspace-switch-test')
        self.identity = RuntimeIdentity(host_mode=True, role='admin')
        self.workspace = SimpleNamespace(username='host', workspace_id='ws1', project_path=self.project,
            data_dir=RUNTIME / 'data', label='test')
        self.context = WorkspaceData('host', self.workspace, self.identity)
        _STORES.clear()
        self.mapping = patch.object(ConversationManager, '_load_host_workspaces', return_value=[
            {'workspace_id': 'ws1', 'path': str(self.project)}])
        self.mapping.start(); self.addCleanup(self.mapping.stop)
        self.manager = self.context.manager()
        self.manager.conversations_dir.mkdir(parents=True, exist_ok=True)
        self.cid = 'conv_synthetic'
        payload = {'id': self.cid, 'title': 'synthetic', 'messages': [], 'todo_list': None,
            'metadata': {'work_mode': 'plan', 'permission_mode': 'unrestricted', 'execution_mode': 'direct'},
            'token_statistics': {'total_input_tokens': 100, 'current_context_tokens': 73}}
        self.manager._get_conversation_file_path(self.cid).write_text(json.dumps(payload), encoding='utf-8')
        self.terminals = patch.dict(state.user_terminals, {}, clear=True)
        self.terminals.start(); self.addCleanup(self.terminals.stop)
        self.runtime = patch('server.context.resources.WebTerminal', side_effect=AssertionError('read allocated a runtime'))
        self.runtime.start(); self.addCleanup(self.runtime.stop)
        self.container = patch('server.context.resources.state.container_manager', ensure_container=SimpleNamespace())
        self.container.start(); self.addCleanup(self.container.stop)
        self.skills = patch('server.context.resources.sync_workspace_skills', side_effect=AssertionError('read copied skills'))
        self.skills.start(); self.addCleanup(self.skills.stop)
        self.rates = patch('modules.exchange_rates.get_rate', return_value=None)
        self.rates.start(); self.addCleanup(self.rates.stop)

    def invoke(self, function, *, cid=False):
        raw = inspect.unwrap(function)
        with self.app.test_request_context('/?conversation_id=' + self.cid):
            return raw(**({'conversation_id': self.cid} if cid else {}), data_context=self.context,
                       workspace=self.workspace, username='host')

    def test_read_only_manager_does_not_create_or_repair_storage(self):
        fresh = RUNTIME / 'missing-store'
        manager = ConversationManager(str(fresh), str(self.project), read_only=True)
        self.assertEqual(manager.get_conversation_list()['conversations'], [])
        self.assertFalse(fresh.exists())
        self.manager.index_file.write_text('broken-index', encoding='utf-8')
        listing = self.manager.get_conversation_list()
        self.assertEqual(listing['conversations'][0]['id'], self.cid)
        self.assertEqual(self.manager.index_file.read_text(), 'broken-index')
        self.assertFalse(list(self.manager.conversations_dir.glob('*.bak*')))

    def test_bootstrap_modes_and_stats_do_not_allocate_execution_resources(self):
        with patch('server.chat.preview.is_preview_enabled', return_value=False), \
             patch('server.conversation_bootstrap.is_preview_enabled', return_value=False):
            data = self.invoke(bootstrap_conversation, cid=True).get_json()['data']
        self.assertEqual(data['conversation_id'], self.cid)
        self.assertEqual(data['meta']['permission_mode'], 'readonly')
        self.assertEqual(data['meta']['execution_mode'], 'sandbox')
        self.assertEqual(data['current_context_tokens'], 73)
        self.assertEqual(data['meta']['pending_runtime_modes']['permission_mode'], None)
        for function in (get_permission_mode, get_execution_mode, get_network_permission, get_work_mode):
            self.assertTrue(self.invoke(function).get_json()['success'])
        self.assertTrue(self.invoke(get_input_draft).get_json()['success'])
        self.assertTrue(self.invoke(get_conversation_token_statistics, cid=True).get_json()['success'])
        self.assertEqual(self.invoke(get_conversation_tokens, cid=True).get_json()['data']['total_tokens'], 73)
        self.assertEqual(state.user_terminals, {})

    def test_single_store_index_includes_all_conversations_without_runtime(self):
        manager = self.context.manager()
        path = manager._get_conversation_file_path('conv_synthetic_child_parent')
        path.write_text(json.dumps({'id': 'conv_synthetic_child_parent', 'messages': [],
            'metadata': {}}), encoding='utf-8')
        rebuilt = manager._rebuild_index_from_files()
        self.assertEqual(set(rebuilt), {self.cid, 'conv_synthetic_child_parent'})
        self.assertTrue(all('multi_agent_mode' not in item for item in rebuilt.values()))
        self.assertEqual(state.user_terminals, {})

    def test_cold_idle_status_reads_without_memory_file_creation(self):
        before = set(RUNTIME.rglob('*'))
        status, terminal = workspace_status(self.context)
        self.assertIsNone(terminal)
        self.assertEqual(status['conversation']['current_id'], None)
        self.assertEqual(status['memory'], {'main': 0, 'task': 0})
        self.assertEqual(set(RUNTIME.rglob('*')), before)

    def test_wrong_workspace_returns_404_without_runtime_creation(self):
        other = RUNTIME / 'other-project'; other.mkdir(exist_ok=True)
        with patch.object(ConversationManager, '_load_host_workspaces', return_value=[
            {'workspace_id': 'ws2', 'path': str(other)}]):
            foreign = WorkspaceData('host', SimpleNamespace(username='host', workspace_id='ws2',
                project_path=other, data_dir=RUNTIME / 'data'), self.identity)
            with self.app.test_request_context('/'):
                response, code = inspect.unwrap(bootstrap_conversation)(self.cid, foreign, foreign.workspace, 'host')
        self.assertEqual(code, 404)
        self.assertFalse(response.get_json()['success'])
        self.assertEqual(state.user_terminals, {})


if __name__ == '__main__':
    try:
        unittest.main()
    finally:
        _TEMP.cleanup()
