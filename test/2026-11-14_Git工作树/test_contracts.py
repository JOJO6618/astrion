"""Conversation snapshots, idle-only routes and real read-before-edit path binding."""
import asyncio
import copy
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import test_worktree as fixtures
from flask import Flask
from core.main_terminal_parts.tools_definition.main import ToolsDefinitionMainMixin
from core.main_terminal_parts.tools_execution import MainTerminalToolsExecutionMixin
from core.main_terminal_parts.tools_read import MainTerminalToolsReadMixin
from core.tool_loading import build_snapshot
from modules.execution_scope import ExecutionScope, bind_execution_scope
from modules.git_worktree import definitions, prompt
from modules.git_worktree.service import Worktrees
from modules.terminal_ops.command import CommandMixin
from modules.terminal_manager import TerminalManager
from server.main_task_gate import try_acquire_main_task_gate, release_main_task_gate
from server.status import worktree as routes
from utils.conversation_manager import ConversationManager
from utils.context_manager.fs_access import files_for_owner, build_skills_catalog


class ToolsOwner(MainTerminalToolsReadMixin, MainTerminalToolsExecutionMixin):
    pass


class ContractTests(unittest.TestCase):
    setUp = fixtures.WorktreeTest.setUp
    git = fixtures.WorktreeTest.git
    make_owner = fixtures.WorktreeTest.make_owner
    create = fixtures.WorktreeTest.create

    def tool_owner(self):
        result = self.create()
        self.assertTrue(result['success'], result)
        owner = ToolsOwner()
        owner.__dict__.update(self.owner.__dict__)
        from modules.execution_plane import LocalExecutionBackend
        owner.execution_backend = LocalExecutionBackend(owner)
        owner.context_manager.project_path = self.root
        owner.context_manager._get_meta_flag = lambda key, default=None: owner.context_manager.conversation_metadata.get(key, default)
        owner.context_manager._set_meta_flag = lambda key, value: owner.context_manager.conversation_metadata.__setitem__(key, value)
        owner.context_manager.load_file = Mock()
        owner.data_dir = self.root / 'fixture-data'
        owner.get_execution_mode = lambda: 'sandbox'
        owner._is_plan_work_mode = lambda: False
        owner.custom_tools_enabled = False
        owner._check_skill_strict_prerequisite = lambda *args: None
        owner.disabled_tools = set()
        return owner

    def test_real_read_cache_and_guard_use_one_physical_file(self):
        owner = self.tool_owner()
        result = owner._handle_read_tool({'path': 'file.txt', 'type': 'read'})
        self.assertTrue(result['success'], result)
        absolute = str(Path(owner.execution_root) / 'file.txt')
        self.assertEqual(result['path'], absolute)
        owner.context_manager.load_file.assert_called_once_with(absolute, content='base\n')
        owner._mark_file_read_from_result('read_file', {'path': 'file.txt'}, result)
        self.assertTrue(owner._has_read_file_in_conversation('file.txt'))
        self.assertIsNone(owner._check_read_before_edit_prerequisite('edit_file', {'file_path': 'file.txt'}))
        asyncio.run(Worktrees(owner).change(None, exit=True))
        self.assertFalse(owner._has_read_file_in_conversation('file.txt'), 'a worktree read must not authorize overwriting the main checkout')

    def test_skill_and_project_memory_remain_bound_to_original_workspace(self):
        from modules.project_memory import topic_directory

        owner = self.tool_owner()
        skill = self.root / '.astrion/skills/fixture/SKILL.md'
        skill.parent.mkdir(parents=True)
        skill.write_text('---\nname: fixture\ndescription: fixture skill\n---\nworkspace skill')
        memory = topic_directory(str(self.root), str(owner.data_dir)) / 'fixture.md'
        memory.parent.mkdir(parents=True)
        memory.write_text('---\nname: fixture\ndescription: unique workspace note\n---\nworkspace memory')
        catalog = build_skills_catalog(files_for_owner(owner), scan_project_agents=False)
        self.assertEqual([item['id'] for item in catalog], ['fixture'])
        result = owner._handle_read_skill_tool({'skill_name': 'fixture'})
        self.assertTrue(result['success'], result)
        self.assertIn('workspace skill', result['content'])
        result = owner._handle_recall_project_memory('fixture')
        self.assertTrue(result['success'], result)
        self.assertIn('workspace memory', result['content'])
        result = owner._handle_search_project_memory(['unique'])
        self.assertEqual(result['count'], 1, result)
        result = owner._handle_update_project_memory('second', 'workspace note', 'new memory')
        self.assertTrue(result['success'], result)
        self.assertTrue((memory.parent / 'second.md').exists())
        self.assertFalse((Path(owner.execution_root) / '.astrion/memory/second.md').exists())

    def test_main_approved_scope_command_still_uses_worktree(self):
        result = self.create()
        directory = Path(result['state']['directory'])
        scope = ExecutionScope(executor_kind='main', actor_id='main', workspace_root=self.root,
                               access_level='sandbox_write', write_granted=True)
        with bind_execution_scope(scope):
            self.assertEqual(CommandMixin._resolve_work_path(self.owner.terminal_ops, None), directory)
            self.assertEqual(CommandMixin._resolve_work_path(self.owner.terminal_ops, 'child'), directory / 'child')

    def test_background_registration_snapshots_worktree_cwd_in_main_approval_scope(self):
        from modules.background_command_manager import BackgroundCommandManager
        self.assertTrue(self.create()['success'])
        directory = Path(self.owner.execution_root)
        ops = self.owner.terminal_ops
        ops.container_session = SimpleNamespace(mode='host')
        ops._validate_command = lambda command: (True, '')
        manager = BackgroundCommandManager(str(self.root))
        scope = ExecutionScope(executor_kind='main', actor_id='main', workspace_root=self.root,
                               access_level='sandbox_write', write_granted=True)
        for requested, expected in ((None, directory), ('child', directory / 'child'),
                                    (str(self.root), self.root)):
            with self.subTest(requested=requested), bind_execution_scope(scope), patch(
                    'modules.background_command_manager.threading.Thread') as thread:
                result = manager.create_background_command(terminal_ops=ops, command='fixture',
                    working_dir=requested, timeout=1, conversation_id='conv_fixture', wait_seconds=0.1)
                self.assertTrue(result['success'], result)
                self.assertEqual(thread.call_args.kwargs['kwargs']['work_path'], expected)
        ops.execution_root = self.root
        self.assertEqual(thread.call_args.kwargs['kwargs']['work_path'], self.root)

    def test_new_terminal_default_changes_without_touching_existing_processes(self):
        result = self.create()
        directory = Path(result['state']['directory'])
        manager = TerminalManager(project_path=str(self.root), container_session=SimpleNamespace(mode='host'))
        manager.execution_root = directory
        scope = ExecutionScope(executor_kind='main', actor_id='main', workspace_root=self.root,
                               access_level='sandbox_write', write_granted=True)
        terminal = SimpleNamespace(start=lambda: True, close=lambda: None, shell_command='fixture-shell', using_container=False, working_dir=str(directory))
        with patch('modules.terminal_manager.PersistentTerminal', return_value=terminal) as factory:
            with bind_execution_scope(scope):
                response = manager.open_terminal('new')
        self.assertTrue(response['success'], response)
        self.assertEqual(factory.call_args.kwargs['working_dir'], str(directory))
        manager.execution_root = self.root
        self.assertEqual(manager.terminals['new'].working_dir, str(directory))

    def test_sandbox_terminal_relative_directory_without_approval_scope(self):
        self.assertTrue(self.create()['success'])
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                manager = TerminalManager(project_path=str(self.root), container_session=SimpleNamespace(mode='host'))
                manager.host_execution_mode = 'sandbox'
                directory = Path(self.owner.execution_root) if enabled else self.root
                if enabled:
                    manager.execution_root = directory
                terminal = SimpleNamespace(start=lambda: True, close=lambda: None, shell_command='fixture-shell', using_container=False, working_dir=str(directory))
                with patch('modules.terminal_manager.PersistentTerminal', return_value=terminal) as factory:
                    with bind_execution_scope(None):
                        result = manager.open_terminal('relative', working_dir='.')
                        self.assertTrue(result['success'], result)
                        self.assertEqual(factory.call_args.kwargs['working_dir'], str(directory))
                        result = manager.open_terminal('absolute', working_dir=str(self.root))
                        self.assertFalse(result['success'], result)
                        result = manager.open_terminal('outside', working_dir='../../../../escape')
                        self.assertFalse(result['success'], result)

    def test_creation_requires_write_approval_but_enter_and_exit_are_state_only(self):
        owner = self.tool_owner()
        owner.get_permission_mode = lambda: 'approval'
        args = {'target': {'action': 'create', 'branch_name': 'x', 'start_ref': 'HEAD', 'include_uncommitted': False}}
        self.assertTrue(owner.evaluate_tool_permission('enter_worktree', args)['requires_approval'])
        owner.get_permission_mode = lambda: 'readonly'
        self.assertFalse(owner.evaluate_tool_permission('enter_worktree', args)['allowed'])
        self.assertTrue(owner.evaluate_tool_permission('exit_worktree', {})['allowed'])
        self.assertTrue(owner.evaluate_tool_permission('enter_worktree', {'target': {'action': 'enter'}})['allowed'])

    def test_dispatch_rejects_disabled_deferred_or_mixed_batch_switch(self):
        owner = self.tool_owner()
        owner.context_manager.conversation_metadata['git_worktree_enabled'] = False
        disabled = json.loads(asyncio.run(owner.handle_tool_call('exit_worktree', {})))
        self.assertFalse(disabled['success'])
        owner.context_manager.conversation_metadata['git_worktree_enabled'] = True
        owner.context_manager.conversation_metadata['tool_loading'] = build_snapshot(['exit_worktree'])
        deferred = json.loads(asyncio.run(owner.handle_tool_call('exit_worktree', {})))
        self.assertFalse(deferred['success'])
        owner.context_manager.conversation_metadata.pop('tool_loading')
        owner._worktree_tool_batch_size = 2
        mixed = json.loads(asyncio.run(owner.handle_tool_call('exit_worktree', {})))
        self.assertFalse(mixed['success'])
        self.assertIn('单独调用', mixed['error'])

    def test_tools_only_exist_for_enabled_conversation(self):
        owner = SimpleNamespace(context_manager=SimpleNamespace(conversation_metadata={}),
                                disabled_tools=set(), model_key=None, _apply_intent_to_tools=lambda tools: tools)
        for name in ('core', 'file', 'terminal', 'search_web', 'agent', 'context', 'misc', 'workflow', 'browser', 'custom', 'mcp'):
            setattr(owner, '_build_' + name + '_tools', lambda: [])
        # 其他内置工具（如定时任务）常驻；这里只断言工作树工具由开关控制。
        disabled_names = {item['function']['name'] for item in ToolsDefinitionMainMixin.define_tools(owner)}
        self.assertFalse({'enter_worktree', 'exit_worktree'} & disabled_names)
        owner.context_manager.conversation_metadata['git_worktree_enabled'] = True
        tools = ToolsDefinitionMainMixin.define_tools(owner)
        enabled_names = {item['function']['name'] for item in tools}
        self.assertLessEqual({'enter_worktree', 'exit_worktree'}, enabled_names)

    def test_creation_snapshot_changes_only_new_agent_documents(self):
        prefs = {'git_worktree_enabled': False}
        with patch('modules.personalization_manager.load_personalization_config', side_effect=lambda *args: dict(prefs)):
            manager = ConversationManager(str(self.root / 'data'), str(self.root))
            first = manager.create_conversation(project_path=str(self.root), initial_messages=[])
            first_bytes = manager._get_conversation_file_path(first).read_bytes()
            prefs['git_worktree_enabled'] = True
            second = manager.create_conversation(project_path=str(self.root), initial_messages=[])
            self.assertFalse(manager.read_conversation(first)['metadata']['git_worktree_enabled'])
            self.assertTrue(manager.read_conversation(second)['metadata']['git_worktree_enabled'])
            self.assertEqual(manager._get_conversation_file_path(first).read_bytes(), first_bytes)
            chat = ConversationManager(str(self.root / 'chat-data'), conversation_kind='chat')
            cid = chat.create_conversation(project_path=None, initial_messages=[])
            self.assertNotIn('git_worktree_enabled', chat.read_conversation(cid)['metadata'])
        self.assertEqual(prompt({}, str(self.root)), '')

    def test_route_rejects_busy_main_and_releases_its_token(self):
        app = Flask(__name__)
        workspace = SimpleNamespace(workspace_id='ws', project_path=self.root)
        callback = inspect.unwrap(routes.change_worktree)
        token = try_acquire_main_task_gate(self.owner)
        try:
            with app.test_request_context(json={'conversation_id': 'conv_fixture', 'action': 'exit'}):
                response, status = callback(self.owner, workspace, 'fixture')
                self.assertEqual(status, 409)
        finally:
            release_main_task_gate(self.owner, token)
        record = SimpleNamespace(username='fixture', conversation_id='conv_fixture', workspace_id='ws', status='pending')
        with patch.object(routes.task_manager, '_tasks', {'main': record}):
            with app.test_request_context(json={'conversation_id': 'conv_fixture', 'action': 'exit'}):
                _, status = callback(self.owner, workspace, 'fixture')
                self.assertEqual(status, 409)
        token = try_acquire_main_task_gate(self.owner)
        self.assertIsNotNone(token)
        release_main_task_gate(self.owner, token)

    def test_route_allows_background_children_and_requires_exact_identity(self):
        app = Flask(__name__)
        workspace = SimpleNamespace(workspace_id='ws', project_path=self.root)
        callback = inspect.unwrap(routes.change_worktree)
        self.owner.sub_agent_manager.has_running_tasks = lambda: True
        with patch.object(routes.task_manager, '_tasks', {}):
            with app.test_request_context(json={'conversation_id': 'conv_fixture', 'action': 'exit'}):
                response, status = callback(self.owner, workspace, 'fixture')
                self.assertEqual(status, 200, response.get_json())
            with app.test_request_context(json={'conversation_id': 'conv_other', 'action': 'exit'}):
                _, status = callback(self.owner, workspace, 'fixture')
                self.assertEqual(status, 400)

    def test_get_is_read_only_and_does_not_require_a_terminal(self):
        app = Flask(__name__)
        context = SimpleNamespace(manager_for=lambda cid: SimpleNamespace(read_conversation=lambda cid: {'metadata': {}}))
        workspace = SimpleNamespace(project_path=self.root)
        with patch('server.context.resources.get_user_resources', side_effect=AssertionError('must not allocate runtime')):
            with app.test_request_context('/?conversation_id=conv_old'):
                response = inspect.unwrap(routes.get_worktree)(context, workspace, 'fixture')
        self.assertEqual(response.get_json(), {'success': True, 'data': {'enabled': False}})


if __name__ == '__main__':
    unittest.main()
