"""Missing roots retain an exit route; unsupported backends fail closed."""
import inspect
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_worktree as fixtures
from flask import Flask
from core.main_terminal_parts.context.system_prompt import _tool_catalog
from core.tool_loading import build_snapshot
from modules.git_worktree.service import Worktrees, WorktreeError
from server.status import git as routes


class RecoveryTests(unittest.TestCase):
    setUp = fixtures.WorktreeTest.setUp
    git = fixtures.WorktreeTest.git
    make_owner = fixtures.WorktreeTest.make_owner
    create = fixtures.WorktreeTest.create

    def test_missing_root_keeps_summary_recovery_and_diff_never_reads_main(self):
        self.assertTrue(self.create()['success'])
        metadata = self.owner.context_manager.conversation_metadata
        metadata['git_worktree']['directory'] += '-missing'
        workspace = SimpleNamespace(project_path=self.root)
        context = SimpleNamespace(manager_for=lambda cid: SimpleNamespace(
            read_conversation=lambda cid: {'metadata': metadata}))
        app = Flask(__name__)
        with app.test_request_context('/?conversation_id=conv_fixture'), patch.object(
                routes, '_run_project_git', side_effect=AssertionError('no fallback Git read')):
            response = inspect.unwrap(routes.get_project_git_summary)(context, workspace, 'fixture')
            data = response.get_json()['data']
            self.assertTrue(data['has_git'])
            self.assertTrue(data['binding_error'])
            self.assertEqual(data['project_path'], metadata['git_worktree']['directory'])
            self.assertEqual(data['branch'], metadata['git_worktree']['branch'])
            response, code = inspect.unwrap(routes.get_project_git_diff)(context, workspace, 'fixture')
            self.assertEqual(code, 400)
            self.assertFalse(response.get_json()['success'])

    def test_broken_git_record_keeps_recovery_menu(self):
        self.assertTrue(self.create()['success'])
        metadata = self.owner.context_manager.conversation_metadata
        (Path(self.owner.execution_root) / '.git').write_text('gitdir: /invalid/admin/path\n')
        context = SimpleNamespace(manager_for=lambda cid: SimpleNamespace(
            read_conversation=lambda cid: {'metadata': metadata}))
        with Flask(__name__).test_request_context('/?conversation_id=conv_fixture'):
            response = inspect.unwrap(routes.get_project_git_summary)(context, SimpleNamespace(project_path=self.root), 'fixture')
        self.assertTrue(response.get_json()['data']['has_git'])
        self.assertTrue(response.get_json()['data']['binding_error'])
        self.assertEqual(response.get_json()['data']['project_path'], self.owner.execution_root)

    def test_disabled_tool_catalog_never_advertises_worktree_tools(self):
        metadata = self.owner.context_manager.conversation_metadata
        metadata['tool_loading'] = build_snapshot(['enter_worktree', 'exit_worktree'])
        self.owner.load_prompt = lambda name: '{tool_catalog}'
        metadata['git_worktree_enabled'] = False
        self.assertEqual(_tool_catalog(self.owner), '')
        metadata['git_worktree_enabled'] = True
        catalog = _tool_catalog(self.owner)
        self.assertIn('enter_worktree', catalog)
        self.assertIn('exit_worktree', catalog)

    def test_execution_device_git_paths_can_be_queried_without_allocating_runtime(self):
        from modules.git_worktree.query import view, query_args
        self.assertTrue(self.create()['success'])
        directory = Path(self.owner.execution_root)
        pointer = (directory / '.git').read_text().strip().removeprefix('gitdir: ')
        admin = Path(pointer)
        (directory / '.git').write_text('gitdir: /workspace/' + str(admin.relative_to(self.root)) + '\n')
        (admin / 'gitdir').write_text('/workspace/' + str(directory.relative_to(self.root)) + '/.git\n')
        (directory / 'file.txt').write_text('edited inside portable tree\n')
        with patch('config.TERMINAL_SANDBOX_MODE', 'docker'):
            ok, branch = routes._run_project_git(directory, ['branch', '--show-current'])
            self.assertTrue(ok)
            self.assertEqual(branch, 'astrion/task')
            ok, diff = routes._run_project_git_raw(directory, ['diff', 'HEAD', '--'])
            self.assertTrue(ok)
            self.assertIn('+edited inside portable tree', diff)
            listing = view(SimpleNamespace(project_path=self.root), self.owner.context_manager.conversation_metadata)
            tree = next(item for item in listing['worktrees'] if item['directory'] == str(directory))
            self.assertFalse(tree['unavailable'])
            self.assertTrue(tree['current'])
            (directory / '.git').write_text('gitdir: /workspace/.git/worktrees/../../outside\n')
            with self.assertRaises(ValueError):
                query_args(directory, ['status'])

    def test_custom_container_mount_is_shared_by_creation_and_queries(self):
        from modules.git_worktree.query import _display_path
        self.owner.container_session = SimpleNamespace(mode='docker')
        with patch('config.TERMINAL_SANDBOX_MODE', 'docker'), patch('config.TERMINAL_SANDBOX_MOUNT_PATH', '/app'):
            service = Worktrees(self.owner)
            path = self.root / '.git/worktrees/task'
            self.assertEqual(service.command_path(path), '/app/.git/worktrees/task')
            self.assertEqual(service.host_path('/app/.git/worktrees/task'), path)
            self.assertEqual(_display_path('/app/.git/worktrees/task', self.root), path)

    def test_unadapted_backend_does_not_read_controller_resources(self):
        self.owner.execution_backend = SimpleNamespace()
        with self.assertRaisesRegex(WorktreeError, '尚未适配'):
            Worktrees(self.owner)


if __name__ == '__main__':
    unittest.main()
