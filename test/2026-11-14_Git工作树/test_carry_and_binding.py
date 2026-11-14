"""Regression evidence for staging, cleanup, failure recovery and output boundaries."""
import asyncio
import base64
from pathlib import Path
import shutil
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_worktree as fixtures
from modules.git_worktree.binding import restore_binding, persist_binding
from modules.git_worktree.commands import git_command
from modules.git_worktree.query import view
from modules.git_worktree.service import Worktrees, WorktreeError
from modules.terminal_ops import TerminalOperator


class CarryAndBindingTests(unittest.TestCase):
    setUp = fixtures.WorktreeTest.setUp
    git = fixtures.WorktreeTest.git
    make_owner = fixtures.WorktreeTest.make_owner
    create = fixtures.WorktreeTest.create

    def test_staged_and_unstaged_layers_and_executable_are_copied(self):
        (self.root / 'file.txt').write_text('staged\n')
        self.git('add', 'file.txt')
        (self.root / 'file.txt').write_text('unstaged\n')
        script = self.root / 'new.sh'
        script.write_text('#!/bin/sh\necho fixture\n')
        script.chmod(0o755)
        source_staged = self.git('diff', '--cached', '--binary')
        source_unstaged = self.git('diff', '--binary')
        result = self.create(include_uncommitted=True)
        self.assertTrue(result['success'], result)
        directory = Path(result['state']['directory'])
        self.assertEqual(self.git('diff', '--cached', '--binary', cwd=directory), source_staged)
        self.assertEqual(self.git('diff', '--binary', cwd=directory), source_unstaged)
        self.assertEqual((directory / 'new.sh').stat().st_mode & 0o777, 0o755)
        self.assertEqual(list((self.root / '.astrion/worktrees').glob('.carry-*')), [])
        self.assertEqual(self.git('diff', '--cached', '--binary'), source_staged)
        self.assertEqual(self.git('diff', '--binary'), source_unstaged)

    def test_concurrent_untracked_destination_is_not_silently_accepted(self):
        import shlex
        (self.root / 'race.txt').write_text('original source\n')
        original = self.owner.terminal_ops.run_command
        async def raced(command, **kwargs):
            if command.startswith('cp '):
                copied = shlex.split(command.split(' && ', 1)[0])
                Path(copied[-1]).write_text('concurrent destination\n')
            return await original(command, **kwargs)
        self.owner.terminal_ops.run_command = raced
        result = self.create(include_uncommitted=True)
        self.assertFalse(result['success'], result)
        self.assertIn('retained_directory', result)
        self.assertNotIn('git_worktree', self.owner.store.document['metadata'])
        self.assertEqual((self.root / 'race.txt').read_text(), 'original source\n')
        self.assertEqual((Path(result['retained_directory']) / 'race.txt').read_text(), 'concurrent destination\n')

    def test_carry_to_another_base_when_patch_is_compatible(self):
        self.git('checkout', '-qb', 'other')
        (self.root / 'other.txt').write_text('other base\n')
        self.git('add', 'other.txt')
        self.git('commit', '-qm', 'other base')
        self.git('checkout', 'main')
        (self.root / 'file.txt').write_text('local edit\n')
        result = self.create(start_ref='other', include_uncommitted=True)
        self.assertTrue(result['success'], result)
        directory = Path(result['state']['directory'])
        self.assertEqual((directory / 'file.txt').read_text(), 'local edit\n')
        self.assertTrue((directory / 'other.txt').is_file())
        self.assertTrue(self.git('diff', cwd=directory))
        self.assertFalse(self.git('diff', '--cached', cwd=directory))

    def test_long_untracked_list_avoids_terminal_output_limit(self):
        # Enough filename bytes to exceed the command-output limit; file payloads
        # remain tiny. Simulate truncation only for ordinary captured stdout.
        original = self.owner.execution_backend.run_command
        async def bounded(command, **kwargs):
            result = await original(command, **kwargs)
            if len(result['stdout']) > 200:
                result.update(stdout=result['stdout'][-200:], stdout_truncated=True)
            return result
        self.owner.execution_backend.run_command = bounded
        for index in range(20):
            (self.root / (f'untracked-{index:02d}-' + 'x' * 30)).write_text(str(index))
        result = self.create(include_uncommitted=True)
        self.assertTrue(result['success'], result)
        directory = Path(result['state']['directory'])
        self.assertEqual(len(list(directory.glob('untracked-*'))), 20)

    def test_missing_tree_remains_visible_and_can_exit_without_execution_fallback(self):
        result = self.create()
        directory = Path(result['state']['directory'])
        shutil.rmtree(directory)
        restore_binding(self.owner, strict=False)
        self.assertEqual(self.owner.execution_root, str(directory))
        self.assertTrue(self.owner.execution_binding_error)
        self.assertFalse(self.owner.file_manager.write_file('accidental.txt', 'bad')['success'])
        listing = view(SimpleNamespace(project_path=self.root), self.owner.context_manager.conversation_metadata)
        self.assertTrue(listing['unavailable'])
        self.assertEqual(listing['directory'], str(directory))
        result = asyncio.run(Worktrees(self.owner).change(None, exit=True))
        self.assertTrue(result['success'], result)
        self.assertEqual(self.owner.execution_root, str(self.root))
        self.assertFalse(self.owner.execution_binding_error)
        self.assertFalse((self.root / 'accidental.txt').exists())

    def test_invalid_binding_never_commits_metadata(self):
        with self.assertRaises(ValueError):
            persist_binding(self.owner, {'directory': str(self.root / 'missing'), 'branch': 'bad'})
        self.assertNotIn('git_worktree', self.owner.store.document['metadata'])

    def test_child_sync_failure_retains_retry_marker_and_parent_binding(self):
        self.owner.sub_agent_manager.sync_worktree_binding = lambda state: (_ for _ in ()).throw(OSError('fixture storage failure'))
        result = self.create()
        self.assertTrue(result['success'], result)
        self.assertIn('sync_warning', result['state'])
        self.assertIn('git_worktree_children_pending', self.owner.store.document['metadata'])
        calls = []
        self.owner.sub_agent_manager.sync_worktree_binding = calls.append
        restore_binding(self.owner)
        self.assertEqual(len(calls), 1)
        self.assertNotIn('git_worktree_children_pending', self.owner.store.document['metadata'])

    def test_reenter_same_tree_does_not_duplicate_revision_or_notice(self):
        first = self.create()
        second = asyncio.run(Worktrees(self.owner).change({'action': 'enter', 'branch_name': 'astrion/task'}, notify_user=True))
        self.assertEqual(first['state']['revision'], second['state']['revision'])
        self.assertNotIn('git_worktree_notice', self.owner.store.document['metadata'])

    def test_git_ref_shell_metacharacters_are_literal(self):
        result = self.create(branch='feature/safe&echo-owned')
        self.assertTrue(result['success'], result)
        self.assertEqual(self.git('show-ref', '--verify', 'refs/heads/feature/safe&echo-owned').split()[0], self.head)

    def test_windows_shell_only_receives_base64_and_native_argv(self):
        ref = "feature/a&b%PATH%'^quoted"
        command = git_command(['check-ref-format', '--branch', ref], windows_direct=True)
        self.assertNotIn(ref, command)
        script = base64.b64decode(command.split()[-1]).decode('utf-16le')
        self.assertIn('UseShellExecute=$false', script)
        self.assertIn(ref.replace("'", "''"), script)
        self.assertIn('ReadToEndAsync', script)

    def test_real_terminal_operator_separates_stdout_and_stderr(self):
        terminal = TerminalOperator(str(self.root), container_session=SimpleNamespace(mode='host'))
        terminal.set_host_execution_mode('direct')
        result = asyncio.run(terminal._run_command_subprocess('printf result; printf warning >&2', self.root, 5))
        self.assertTrue(result['success'], result)
        self.assertEqual(result['stdout'], 'result')
        self.assertEqual(result['stderr'], 'warning')
        self.assertIn('warning', result['output'])
        self.assertFalse(result['stdout_truncated'])


if __name__ == '__main__':
    unittest.main()
