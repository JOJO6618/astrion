"""First-message worktree selection and canonical tool/UI artifact paths."""
import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import test_worktree as fixtures
import test_contracts as contracts
from modules.execution_plane import LocalExecutionBackend
from modules.git_worktree.binding import restore_binding
from modules.git_worktree.creation import prepare
from modules.git_worktree import prompt
from modules.terminal_ops import TerminalOperator
from server.context import new_conversation
from server.conversation_bootstrap import _existing_file
from modules.file_manager import FileManager
from utils.conversation_manager import ConversationManager


class CreationTests(unittest.TestCase):
    setUp = fixtures.WorktreeTest.setUp
    git = fixtures.WorktreeTest.git
    make_owner = fixtures.WorktreeTest.make_owner
    create = fixtures.WorktreeTest.create

    def owner_for_creation(self):
        owner = self.owner
        owner.container_session = SimpleNamespace(mode='host')
        owner.terminal_ops = TerminalOperator(str(self.root), container_session=owner.container_session)
        owner.terminal_ops.set_host_execution_mode('direct')
        owner.user_role = 'admin'
        owner._is_host_mode = lambda: True
        owner.context_manager.conversation_manager = ConversationManager(str(self.root / 'fixture-data'), str(self.root))
        owner.get_execution_mode = lambda: 'direct'
        owner.get_network_permission = lambda: 'restricted'
        return owner

    def settings(self):
        return dict(model_key='fixture', run_mode='fast', thinking_mode=False, reasoning_effort=None,
            work_mode='execute', permission_mode='unrestricted', execution_mode='direct', network_permission='restricted')

    def target(self):
        return dict(action='create', branch_name='draft/task', start_ref='HEAD', include_uncommitted=False)

    def test_preparing_new_page_does_not_change_the_workspace_runtime(self):
        owner = self.owner_for_creation()
        before = copy.deepcopy(owner.context_manager.conversation_metadata)
        result = prepare(owner, self.settings(), self.target())
        self.assertTrue(Path(result['directory']).is_dir())
        self.assertFalse(hasattr(owner, 'execution_root'))
        self.assertEqual(owner.context_manager.conversation_metadata, before)
        self.assertFalse(hasattr(owner.file_manager, 'execution_root'))
        self.assertEqual(list(owner.context_manager.conversation_manager.conversations_dir.glob('conv_*.json')), [])

    def test_first_conversation_snapshot_contains_actual_tree_before_prompt(self):
        owner = self.owner_for_creation()
        settings = {**self.settings(), 'git_worktree_target': self.target()}
        workspace = SimpleNamespace(project_path=self.root, data_dir=self.root / 'fixture-data')
        prefs = dict(git_worktree_enabled=True, versioning_enabled_by_default=False)
        with patch.object(new_conversation, 'get_registered_model_keys', return_value=['fixture']), \
             patch.object(new_conversation, 'get_model_profile', return_value={'supports_thinking': False}), \
             patch.object(new_conversation, 'load_personalization_config', return_value=prefs):
            result = new_conversation.create_from_settings(owner, workspace, settings)
        self.assertTrue(result['success'], result)
        metadata = owner.context_manager.conversation_manager.read_conversation(result['conversation_id'])['metadata']
        self.assertTrue(metadata['git_worktree_enabled'])
        self.assertEqual(metadata['git_worktree']['branch'], 'draft/task')
        self.assertIn(metadata['git_worktree']['directory'], prompt(metadata, str(self.root)))
        self.assertIsNone(metadata.get('frozen_system_prompt'))
        self.assertEqual(owner.context_manager.current_conversation_id, 'conv_fixture')

    def test_disabled_new_conversation_rejects_draft_target_before_creating_tree(self):
        owner = self.owner_for_creation()
        settings = {**self.settings(), 'git_worktree_target': self.target()}
        with patch.object(new_conversation, 'get_registered_model_keys', return_value=['fixture']), \
             patch.object(new_conversation, 'get_model_profile', return_value={'supports_thinking': False}), \
             patch.object(new_conversation, 'load_personalization_config', return_value={'git_worktree_enabled': False}):
            with self.assertRaisesRegex(ValueError, '未启用'):
                new_conversation.create_from_settings(owner, SimpleNamespace(project_path=self.root, data_dir=self.root), settings)
        self.assertFalse((self.root / '.astrion/worktrees').exists())

    def test_new_tree_files_record_workspace_paths_but_return_absolute_model_paths(self):
        owner = contracts.ContractTests.tool_owner(self)
        owner.execution_backend = LocalExecutionBackend(owner)
        owner._track_shallow_versioning = lambda path: None
        owner._update_edit_summary = lambda *args: None
        recorded = []
        owner._record_edited_file = lambda path, op: recorded.append(path)
        result = json.loads(asyncio.run(owner.handle_tool_call('write_file', {'file_path': 'new.html', 'content': '<head></head>fixture'})))
        self.assertTrue(result['success'], result)
        physical = Path(owner.execution_root) / 'new.html'
        self.assertEqual(result['path'], str(physical))
        self.assertEqual(recorded, [str(physical.relative_to(self.root))])
        # Bootstrap and the independent preview server use workspace-relative
        # artifact paths, so returning to main does not hide these tree files.
        files = FileManager(str(self.root), container_session=SimpleNamespace(mode='host'))
        self.assertTrue(_existing_file(files, recorded[0]))
        owner.terminal_ops.run_command = fixtures.Backend(owner).run_command
        asyncio.run(__import__('modules.git_worktree.service', fromlist=['Worktrees']).Worktrees(owner).change(None, exit=True))
        self.assertTrue(_existing_file(files, recorded[0]))


if __name__ == '__main__':
    unittest.main()
