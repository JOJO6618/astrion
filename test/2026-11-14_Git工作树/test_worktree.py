"""Real temporary Git repositories; no model calls, real credentials or running server."""
import asyncio
import atexit
import copy
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
os.environ['ASTRION_IGNORE_DOTENV'] = '1'
RUNTIME = tempfile.TemporaryDirectory(prefix='worktree-runtime-', dir=Path(__file__).parent)
atexit.register(RUNTIME.cleanup)
os.environ.update(ASTRION_DATA_ROOT=RUNTIME.name, DATA_DIR=RUNTIME.name + '/data',
                  LOGS_DIR=RUNTIME.name + '/logs', USER_SPACE_DIR=RUNTIME.name + '/users',
                  API_USER_SPACE_DIR=RUNTIME.name + '/api', DEPLOY_CONFIG_DIR=RUNTIME.name + '/deploy',
                  TERMINAL_SANDBOX_MODE='host', WEB_SECRET_KEY='worktree-isolated-test-only')

from modules.file_manager import FileManager
from modules.execution_plane import LocalExecutionBackend
from modules.execution_plane.files import LocalFileSystem
from modules.git_worktree import enabled, definitions, prompt
from modules.git_worktree.service import Worktrees, WorktreeError
from modules.git_worktree.binding import restore_binding
from modules.terminal_ops.command import CommandMixin


class Store:
    def __init__(self, meta):
        self.document = {'metadata': copy.deepcopy(meta)}

    def mutate_conversation(self, cid, mutate):
        candidate = copy.deepcopy(self.document)
        result = mutate(candidate)
        self.document = candidate
        return candidate, result


class Backend:
    """Test transport: execute only fixture Git commands in the temporary checkout."""
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


class WorktreeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='astrion-worktree-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / 'repo with spaces'
        self.root.mkdir()
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        (self.root / 'file.txt').write_text('base\n')
        (self.root / 'remove.txt').write_text('remove\n')
        (self.root / 'binary.dat').write_bytes(b'\x00base\xff')
        (self.root / '.gitignore').write_text('.env\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'fixture')
        self.head = self.git('rev-parse', 'HEAD').strip()
        self.owner = self.make_owner()

    def git(self, *args, cwd=None):
        result = subprocess.run(['git', '-c', 'core.excludesFile=' + os.devnull, *args],
                                cwd=cwd or self.root, capture_output=True, check=True,
                                env={**os.environ, 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1'})
        return result.stdout.decode('utf-8', 'replace')

    def make_owner(self, enabled_flag=True):
        meta = {'git_worktree_enabled': enabled_flag, 'frozen_system_prompt': 'frozen initial system'}
        store = Store(meta)
        cm = SimpleNamespace(current_conversation_id='conv_fixture', conversation_metadata=meta,
                             _get_conversation_manager_for_id=lambda cid: store)
        files = FileManager(str(self.root), container_session=SimpleNamespace(mode='host'))
        files.set_host_execution_mode('direct')
        owner = SimpleNamespace(project_path=str(self.root), context_manager=cm, file_manager=files,
                                terminal_ops=SimpleNamespace(project_path=self.root),
                                terminal_manager=SimpleNamespace(), ocr_client=SimpleNamespace(),
                                get_permission_mode=lambda: 'unrestricted', get_work_mode=lambda: 'execute',
                                sub_agent_manager=SimpleNamespace(sync_worktree_binding=lambda state: None))
        transport = Backend(owner)
        owner.terminal_ops.run_command = transport.run_command
        owner.get_execution_mode = lambda: 'sandbox'
        owner.execution_backend = LocalExecutionBackend(owner)
        owner.store = store
        return owner

    def create(self, branch='astrion/task', **kwargs):
        target = {'action': 'create', 'branch_name': branch, 'start_ref': 'HEAD', 'include_uncommitted': False}
        target.update(kwargs)
        return asyncio.run(Worktrees(self.owner).change(target))

    def test_create_read_command_and_exit_reenter(self):
        result = self.create()
        self.assertTrue(result['success'], result)
        directory = Path(result['state']['directory'])
        self.assertTrue(directory.is_relative_to(self.root / '.astrion/worktrees'))
        self.assertEqual(self.owner.project_path, str(self.root))
        self.assertEqual(self.git('branch', '--show-current').strip(), 'main')
        (directory / 'file.txt').write_text('task\n')
        self.assertEqual(self.owner.file_manager.read_file('file.txt')['content'], 'task\n')
        self.assertEqual(CommandMixin._resolve_work_path(self.owner.terminal_ops, None), directory)
        self.assertEqual((self.root / 'file.txt').read_text(), 'base\n')
        result = asyncio.run(Worktrees(self.owner).change(None, exit=True, notify_user=True))
        self.assertTrue(result['success'], result)
        self.assertTrue(directory.exists())
        self.assertEqual(self.owner.execution_root, str(self.root))
        self.assertIn('git_worktree_notice', self.owner.store.document['metadata'])
        self.assertEqual(self.owner.store.document['metadata']['frozen_system_prompt'], 'frozen initial system')
        result = asyncio.run(Worktrees(self.owner).change({'action': 'enter', 'branch_name': 'astrion/task'}))
        self.assertTrue(result['success'], result)
        self.assertEqual((directory / 'file.txt').read_text(), 'task\n')

    def test_starting_branch_and_tag(self):
        self.git('tag', 'initial')
        (self.root / 'file.txt').write_text('later\n')
        self.git('commit', '-qam', 'later')
        result = self.create(start_ref='initial')
        self.assertTrue(result['success'], result)
        self.assertEqual((Path(result['state']['directory']) / 'file.txt').read_text(), 'base\n')
        self.assertEqual(result['state']['head'], self.head)

    def test_carry_staged_unstaged_binary_delete_and_new_file(self):
        (self.root / 'file.txt').write_text('staged\n')
        self.git('add', 'file.txt')
        (self.root / 'file.txt').write_text('unstaged\n')
        (self.root / 'binary.dat').write_bytes(b'\x00changed\xfe')
        (self.root / 'remove.txt').unlink()
        (self.root / 'new.dat').write_bytes(b'\x00new\xff')
        (self.root / '.env').write_text('synthetic fixture only')
        before = self.git('diff', '--binary', 'HEAD')
        result = self.create(include_uncommitted=True)
        self.assertTrue(result['success'], result)
        directory = Path(result['state']['directory'])
        self.assertEqual((directory / 'file.txt').read_text(), 'unstaged\n')
        self.assertEqual((directory / 'binary.dat').read_bytes(), b'\x00changed\xfe')
        self.assertFalse((directory / 'remove.txt').exists())
        self.assertEqual((directory / 'new.dat').read_bytes(), b'\x00new\xff')
        self.assertFalse((directory / '.env').exists())
        self.assertFalse((directory / '.astrion/worktrees').exists())
        self.assertEqual(before, self.git('diff', '--binary', 'HEAD'))
        self.assertEqual((self.root / 'new.dat').read_bytes(), b'\x00new\xff')

    def test_second_creation_reuses_managed_parent_directory(self):
        first = self.create('astrion/one')
        second = self.create('astrion/two')
        self.assertTrue(first['success'], first)
        self.assertTrue(second['success'], second)
        self.assertNotEqual(first['state']['directory'], second['state']['directory'])

    def test_conflict_keeps_source_binding_and_retains_worktree(self):
        self.git('checkout', '-qb', 'other')
        (self.root / 'file.txt').write_text('other branch\n')
        self.git('commit', '-qam', 'other')
        self.git('checkout', 'main')
        (self.root / 'file.txt').write_text('local source\n')
        result = self.create(start_ref='other', include_uncommitted=True)
        self.assertFalse(result['success'], result)
        self.assertTrue(Path(result['retained_directory']).exists())
        self.assertFalse(hasattr(self.owner, 'execution_root'))
        self.assertEqual((self.root / 'file.txt').read_text(), 'local source\n')
        self.assertNotIn('git_worktree', self.owner.store.document['metadata'])

    def test_required_parameters_and_existing_branch_not_reset(self):
        with self.assertRaises(WorktreeError):
            asyncio.run(Worktrees(self.owner).change({'action': 'create', 'branch_name': 'x', 'start_ref': 'HEAD'}))
        with self.assertRaises(WorktreeError):
            asyncio.run(Worktrees(self.owner).change({'action': 'create', 'branch_name': 'x', 'start_ref': 'HEAD', 'include_uncommitted': None}))
        result = self.create('main')
        self.assertFalse(result['success'])
        self.assertEqual(self.git('rev-parse', 'main').strip(), self.head)

    def test_disabled_and_readonly_fail_closed(self):
        self.owner.context_manager.conversation_metadata['git_worktree_enabled'] = False
        with self.assertRaises(WorktreeError):
            self.create()
        self.owner.context_manager.conversation_metadata['git_worktree_enabled'] = True
        self.owner.get_permission_mode = lambda: 'readonly'
        self.assertFalse(self.create()['success'])
        self.assertFalse((self.root / '.astrion/worktrees').exists())

    def test_symlink_and_escaped_binding_rejected(self):
        self.owner.context_manager.conversation_metadata['git_worktree'] = {'directory': str(self.root / '..')}
        with self.assertRaises(ValueError):
            restore_binding(self.owner)
        target = self.root / '.astrion'
        target.symlink_to(self.root.parent, target_is_directory=True)
        result = self.create()
        self.assertFalse(result['success'])

    def test_query_lists_managed_branch(self):
        result = self.create()
        self.assertTrue(result['success'], result)
        listing = asyncio.run(Worktrees(self.owner).list())
        self.assertEqual(len(listing['worktrees']), 2)
        self.assertIn('astrion/task', [tree['branch'] for tree in listing['worktrees']])


class SchemaTest(unittest.TestCase):
    def test_two_tools_and_required_creation_arguments(self):
        tools = definitions()
        self.assertEqual([tool['function']['name'] for tool in tools], ['enter_worktree', 'exit_worktree'])
        create = tools[0]['function']['parameters']['properties']['target']['anyOf'][0]
        self.assertIn('start_ref', create['required'])
        self.assertIn('include_uncommitted', create['required'])
        self.assertFalse(enabled({}))
        self.assertFalse(enabled({'git_worktree_enabled': 1}))
        self.assertEqual(prompt({}, '/workspace'), '')
        self.assertIn('不要重复询问', prompt({'git_worktree_enabled': True}, '/workspace'))


if __name__ == '__main__':
    unittest.main()
