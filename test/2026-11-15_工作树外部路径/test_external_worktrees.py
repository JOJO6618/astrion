"""工作区外 / detached Git 工作树的进入、授权与临时放行；真实临时 Git，无模型调用。"""
import asyncio
import atexit
import copy
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
os.environ['ASTRION_IGNORE_DOTENV'] = '1'
RUNTIME = tempfile.TemporaryDirectory(prefix='worktree-external-runtime-', dir=Path(__file__).parent)
atexit.register(RUNTIME.cleanup)
os.environ.update(ASTRION_DATA_ROOT=RUNTIME.name, DATA_DIR=RUNTIME.name + '/data',
                  LOGS_DIR=RUNTIME.name + '/logs', USER_SPACE_DIR=RUNTIME.name + '/users',
                  API_USER_SPACE_DIR=RUNTIME.name + '/api', DEPLOY_CONFIG_DIR=RUNTIME.name + '/deploy',
                  TERMINAL_SANDBOX_MODE='host', WEB_SECRET_KEY='worktree-external-test-only')

from modules.execution_plane import LocalExecutionBackend
from modules.execution_plane.files import LocalFileSystem
from modules.file_manager import FileManager
from modules.git_worktree import current_state
from modules.git_worktree.binding import checked_root, restore_binding
from modules.git_worktree.grants import bind_extra_roots, external_roots
from modules.git_worktree.query import view
from modules.git_worktree.service import Worktrees, WorktreeError


class Store:
    def __init__(self, meta):
        self.document = {'metadata': copy.deepcopy(meta)}

    def mutate_conversation(self, cid, mutate):
        candidate = copy.deepcopy(self.document)
        result = mutate(candidate)
        self.document = candidate
        return candidate, result


class Backend:
    execution_mode = 'sandbox'

    def __init__(self, owner):
        self.owner = owner
        self.files = LocalFileSystem(owner.file_manager)

    async def run_command(self, command, *, working_dir, **kwargs):
        env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
        env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1')
        result = subprocess.run(command, cwd=working_dir, shell=True, capture_output=True, env=env)
        return {'success': result.returncode == 0, 'return_code': result.returncode,
                'output': (result.stdout + result.stderr).decode('utf-8', 'replace'),
                'stdout': result.stdout.decode('utf-8', 'replace'),
                'stderr': result.stderr.decode('utf-8', 'replace')}


class ExternalWorktreeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='astrion-worktree-external-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.root = self.base / 'repo'
        self.root.mkdir()
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        (self.root / 'file.txt').write_text('base\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'fixture')
        self.owner = self.make_owner()

    def git(self, *args, cwd=None):
        result = subprocess.run(['git', '-c', 'core.excludesFile=' + os.devnull, *args],
                                cwd=cwd or self.root, capture_output=True, check=True,
                                env={**os.environ, 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1'})
        return result.stdout.decode('utf-8', 'replace')

    def make_owner(self):
        meta = {'git_worktree_enabled': True, 'frozen_system_prompt': 'frozen initial system'}
        store = Store(meta)
        cm = SimpleNamespace(current_conversation_id='conv_fixture', conversation_metadata=meta,
                             _get_conversation_manager_for_id=lambda cid: store)
        files = FileManager(str(self.root), container_session=SimpleNamespace(mode='host'))
        # 与真实使用一致：沙箱模式下文件工具受可读授权限制，路径校验不能依赖它。
        files.set_host_execution_mode('sandbox')
        children = SimpleNamespace(tasks={}, _state_lock=threading.Lock(),
                                   sync_worktree_binding=lambda state: None)
        owner = SimpleNamespace(project_path=str(self.root), context_manager=cm, file_manager=files,
                                terminal_ops=SimpleNamespace(project_path=self.root),
                                terminal_manager=SimpleNamespace(), ocr_client=SimpleNamespace(),
                                get_permission_mode=lambda: 'unrestricted', get_work_mode=lambda: 'execute',
                                sub_agent_manager=children)
        transport = Backend(owner)
        owner.terminal_ops.run_command = transport.run_command
        owner.get_execution_mode = lambda: 'sandbox'
        owner.execution_backend = LocalExecutionBackend(owner)
        owner.store = store
        return owner

    # ---- fixtures -------------------------------------------------
    def sibling_worktree(self, branch='feature/sibling', folder='sibling'):
        directory = self.base / folder
        self.git('worktree', 'add', '-b', branch, str(directory), 'HEAD')
        return directory

    def detached_worktree(self, folder='.claude/worktrees/detached-one'):
        directory = self.root / folder
        directory.parent.mkdir(parents=True, exist_ok=True)
        self.git('worktree', 'add', '--detach', str(directory), 'HEAD')
        return directory

    def enter(self, target, *, origin='model'):
        return asyncio.run(Worktrees(self.owner).change(target, origin=origin))

    def authorize(self, *paths):
        return patch('modules.host_sandbox_policy.get_macos_writable_paths',
                     lambda workspace_path=None: [str(p) for p in paths])

    # ---- enter by branch ------------------------------------------
    def test_model_enter_external_requires_writable_authorization(self):
        self.sibling_worktree()
        result = self.enter({'action': 'enter', 'branch_name': 'feature/sibling'}, origin='model')
        self.assertFalse(result['success'], result)
        self.assertIn('工作区外', result['error'])

    def test_model_enter_external_allowed_with_authorization(self):
        directory = self.sibling_worktree()
        result = self.enter({'action': 'enter', 'branch_name': 'feature/sibling'})
        self.assertFalse(result['success'], result)
        with self.authorize(self.base):
            result = self.enter({'action': 'enter', 'branch_name': 'feature/sibling'})
        self.assertTrue(result['success'], result)
        self.assertEqual(result['state']['directory'], str(directory))
        self.assertEqual(result['state']['branch'], 'feature/sibling')
        self.assertFalse(result['state']['grant']['added'])
        self.assertEqual(str(checked_root(self.owner, result['state'])), str(directory))

    def test_model_enter_external_allowed_in_direct_mode(self):
        directory = self.sibling_worktree()
        self.owner.get_execution_mode = lambda: 'direct'
        result = self.enter({'action': 'enter', 'branch_name': 'feature/sibling'})
        self.assertTrue(result['success'], result)
        self.assertEqual(result['state']['directory'], str(directory))

    def test_inside_workspace_worktree_needs_no_authorization(self):
        directory = self.root / '.claude/worktrees/inside'
        directory.parent.mkdir(parents=True, exist_ok=True)
        self.git('worktree', 'add', '-b', 'feature/inside', str(directory), 'HEAD')
        result = self.enter({'action': 'enter', 'branch_name': 'feature/inside'})
        self.assertTrue(result['success'], result)
        self.assertEqual(result['state']['directory'], str(directory))
        self.assertNotIn('grant', result['state'])

    # ---- enter by directory ---------------------------------------
    def test_enter_detached_by_directory(self):
        directory = self.detached_worktree()
        result = self.enter({'action': 'enter', 'directory': str(directory)})
        self.assertTrue(result['success'], result)
        self.assertEqual(result['state']['branch'], '')
        self.assertTrue(result['state']['head'])
        self.assertEqual(str(checked_root(self.owner, result['state'])), str(directory))
        trees = view(SimpleNamespace(project_path=str(self.root)),
                     self.owner.store.document['metadata'])['worktrees']
        entry = next(tree for tree in trees if tree['directory'] == str(directory))
        self.assertTrue(entry['detached'])
        self.assertEqual(entry['branch'], '')

    def test_enter_detached_by_branch_name_is_impossible(self):
        self.detached_worktree()
        result = self.enter({'action': 'enter', 'branch_name': 'worktrees/detached-one'})
        self.assertFalse(result['success'], result)

    def test_directory_must_be_a_worktree_of_this_repo(self):
        outside = self.base / 'plain-dir'
        outside.mkdir()
        result = self.enter({'action': 'enter', 'directory': str(outside)})
        self.assertFalse(result['success'], result)
        self.assertIn('不是 Git 工作树', result['error'])
        clone = self.base / 'clone'
        subprocess.run(['git', 'clone', '-q', str(self.root), str(clone)], check=True,
                       env={**os.environ, 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1'})
        result = self.enter({'action': 'enter', 'directory': str(clone)})
        self.assertFalse(result['success'], result)
        self.assertIn('其它 Git 仓库', result['error'])

    def test_directory_cannot_be_main_checkout_or_symlink(self):
        result = self.enter({'action': 'enter', 'directory': str(self.root)})
        self.assertFalse(result['success'], result)
        directory = self.detached_worktree()
        link = self.base / 'linked'
        link.symlink_to(directory)
        result = self.enter({'action': 'enter', 'directory': str(link)})
        self.assertFalse(result['success'], result)
        self.assertIn('符号链接', result['error'])

    # ---- 参数互斥 --------------------------------------------------
    def test_target_keys_are_mutually_exclusive(self):
        self.sibling_worktree()
        for target in (
            {'action': 'enter', 'branch_name': 'feature/sibling', 'directory': str(self.base / 'sibling')},
            {'action': 'enter'},
            {'action': 'enter', 'branch_name': ''},
            {'action': 'enter', 'directory': 'relative/path'},
        ):
            with self.assertRaises(WorktreeError):
                Worktrees.validate_target(target)

    # ---- 临时放行与退出 --------------------------------------------
    def test_user_enter_records_temporary_grant_and_exit_clears_it(self):
        directory = self.sibling_worktree()
        result = self.enter({'action': 'enter', 'branch_name': 'feature/sibling'}, origin='user')
        self.assertTrue(result['success'], result)
        self.assertTrue(result['state']['grant']['added'])
        bound = current_state(self.owner.store.document['metadata'])
        self.assertEqual(bound['grant']['path'], str(directory))
        restore_binding(self.owner)
        self.assertEqual(self.owner.execution_root, str(directory))
        result = self.enter(None, origin='user') if False else asyncio.run(
            Worktrees(self.owner).change(None, exit=True, origin='user'))
        self.assertTrue(result['success'], result)
        self.assertEqual(self.owner.execution_root, str(self.root))
        self.assertNotIn('grant', current_state(self.owner.store.document['metadata']))

    def test_exit_blocked_while_sub_agent_runs(self):
        self.sibling_worktree()
        result = self.enter({'action': 'enter', 'branch_name': 'feature/sibling'}, origin='user')
        self.assertTrue(result['success'], result)
        self.owner.sub_agent_manager.tasks['t1'] = {'conversation_id': 'conv_fixture', 'status': 'running'}
        result = asyncio.run(Worktrees(self.owner).change(None, exit=True, origin='user'))
        self.assertFalse(result['success'], result)
        self.assertIn('子智能体', result['error'])
        self.owner.sub_agent_manager.tasks['t1']['status'] = 'idle'
        result = asyncio.run(Worktrees(self.owner).change(None, exit=True, origin='user'))
        self.assertTrue(result['success'], result)

    def test_ungranted_external_binding_is_refused(self):
        directory = self.sibling_worktree()
        with self.assertRaises(ValueError):
            checked_root(self.owner, {'directory': str(directory)})

    # ---- 沙箱授权合并 ----------------------------------------------
    def test_extra_roots_merge_into_sandbox_policy(self):
        from modules.host_sandbox_policy import get_macos_readable_paths, get_macos_writable_paths
        directory = self.sibling_worktree()
        before = get_macos_writable_paths(str(self.root))
        self.assertNotIn(str(directory), before)
        with bind_extra_roots(external_roots(directory, self.root)):
            self.assertIn(str(directory), get_macos_writable_paths(str(self.root)))
            self.assertIn(str(directory), get_macos_readable_paths(str(self.root)))
        self.assertNotIn(str(directory), get_macos_writable_paths(str(self.root)))

    def test_external_roots_ignore_paths_inside_workspace(self):
        self.assertEqual(external_roots(self.root / '.astrion/worktrees/x', self.root), ())


if __name__ == '__main__':
    unittest.main(verbosity=2)
