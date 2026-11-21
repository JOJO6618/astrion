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

    def test_linked_worktree_git_paths_can_be_queried_without_allocating_runtime(self):
        from modules.git_worktree.query import view, query_args
        self.assertTrue(self.create()['success'])
        directory = Path(self.owner.execution_root)
        (directory / 'file.txt').write_text('edited inside portable tree\n')
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
        # Host 绝对路径不需要换算；只有执行设备（WSL 盘符）路径才重写 gitdir。
        self.assertEqual(query_args(directory, ['status']), ['status'])
        # 换算后的管理目录如果跑出 .git/worktrees，必须拒绝而不是去读主仓库。
        with patch('modules.git_worktree.query._display_path',
                   return_value=directory.parent / 'outside'):
            with self.assertRaises(ValueError):
                query_args(directory, ['status'])

    def test_wsl_drive_paths_are_translated_for_host_reads(self):
        from pathlib import PureWindowsPath
        from modules.git_worktree.query import _display_path
        root = PureWindowsPath('C:/repo')
        self.assertEqual(_display_path('/mnt/c/repo/.git/worktrees/task', root),
                         Path('C:/repo/.git/worktrees/task'))
        self.assertEqual(_display_path('/home/user/repo', root), Path('/home/user/repo'))

    def test_unadapted_backend_does_not_read_controller_resources(self):
        self.owner.execution_backend = SimpleNamespace()
        with self.assertRaisesRegex(WorktreeError, '尚未适配'):
            Worktrees(self.owner)


if __name__ == '__main__':
    unittest.main()
