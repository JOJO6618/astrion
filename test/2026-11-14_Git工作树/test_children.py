"""Safe-batch and idle synchronization without real model calls."""
import asyncio
from dataclasses import replace
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import test_worktree  # establishes isolated runtime before application imports
from modules.execution_scope import ExecutionScope, bind_execution_scope
from modules.git_worktree.children import apply_pending, queue_binding
from modules.scoped_execution_policy import fixed_write_root, fixed_execution_root
from modules.sub_agent.runtime import SubAgentRuntimeMixin
from modules.sub_agent.task import SubAgentTask


class ChildBindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.work = self.root / 'child'
        self.work.mkdir()
        self.tree = self.root / '.astrion/worktrees/task'
        self.tree.mkdir(parents=True)
        self.state = {'directory': str(self.tree), 'branch': 'task', 'revision': 2}
        self.manager = SimpleNamespace(_state_lock=threading.RLock(), _save_state=Mock(),
            terminal=SimpleNamespace(context_manager=SimpleNamespace(current_conversation_id='parent')))
        self.scope = ExecutionScope(executor_kind='sub_agent', actor_id='child',
            workspace_root=self.root, working_dir=self.work, access_level='working_dir_write')

    def instance(self):
        class Instance(SubAgentRuntimeMixin):
            pass
        child = Instance()
        child.manager = self.manager
        child.task_record = {'conversation_id': 'parent', 'status': 'idle', 'pending_worktree_binding': dict(self.state)}
        child.execution_scope = self.scope
        child.messages = [{'role': 'system', 'content': 'frozen system'}]
        child._work_lock = threading.RLock()
        child._pending_work = [('continue task', None)]
        child._work_paused = False
        child._continue_event = None
        child._round_id = 1
        child._persist_conversation = Mock()
        child._finish_round = Mock()
        return child

    def test_queue_updates_only_same_parent_without_waking_idle(self):
        record = {'conversation_id': 'parent', 'status': 'idle', 'worktree_revision': 1}
        other = {'conversation_id': 'different', 'status': 'running'}
        ended = {'conversation_id': 'parent', 'status': 'terminated'}
        self.manager.tasks = {'one': record, 'two': other, 'three': ended}
        queue_binding(self.manager, self.state)
        self.assertEqual(record['pending_worktree_binding'], self.state)
        self.assertEqual(record['status'], 'idle')
        self.assertNotIn('pending_worktree_binding', other)
        self.assertNotIn('pending_worktree_binding', ended)
        self.manager._save_state.assert_called_once()

    def test_idle_consumption_precedes_work_and_retains_fixed_write_root(self):
        child = self.instance()
        self.assertTrue(child._accept_work(new_round=True))
        self.assertEqual(child.messages[0], {'role': 'system', 'content': 'frozen system'})
        self.assertIn('工作树环境变更', child.messages[1]['content'])
        self.assertNotIn('使用 enter_worktree', child.messages[1]['content'])
        self.assertEqual(child.messages[2]['content'], 'continue task')
        self.assertEqual(fixed_execution_root(child.execution_scope), self.tree)
        self.assertEqual(fixed_write_root(child.execution_scope), self.work)
        self.assertEqual(child.execution_scope.workspace_root, self.scope.workspace_root)
        self.assertEqual(child.execution_scope.access_level, 'working_dir_write')
        self.assertNotIn('pending_worktree_binding', child.task_record)
        self.assertIn('绝对路径', child.messages[1]['content'])

    def test_invalid_root_preserves_message_and_binding_for_retry(self):
        child = self.instance()
        child.task_record['pending_worktree_binding']['directory'] = str(self.root / 'missing')
        self.assertFalse(child._accept_work(new_round=True))
        self.assertEqual(child._pending_work, [('continue task', None)])
        self.assertIn('pending_worktree_binding', child.task_record)
        self.assertEqual(child.execution_scope, self.scope)
        self.assertTrue(child._work_paused)
        child._finish_round.assert_called_once()
        self.assertEqual(len(child.messages), 1)

    def test_notice_persistence_failure_never_advances_scope(self):
        child = self.instance()
        child._persist_conversation.side_effect = OSError('fixture write failed')
        self.assertFalse(child._accept_work(new_round=True))
        self.assertEqual(child.execution_scope, self.scope)
        self.assertTrue(child._pending_work)
        self.assertIn('pending_worktree_binding', child.task_record)

    def test_missing_existing_root_without_pending_retains_work(self):
        child = self.instance()
        child.task_record.pop('pending_worktree_binding')
        child.execution_scope = replace(self.scope, execution_root=self.root / 'missing')
        self.assertFalse(child._accept_work(new_round=True))
        self.assertEqual(child._pending_work, [('continue task', None)])
        self.assertTrue(child._work_paused)
        child._finish_round.assert_called_once()

    def test_registry_retry_does_not_duplicate_notice_or_lose_pending(self):
        child = self.instance()
        self.manager._save_state.side_effect = OSError('fixture registry failed')
        with self.assertRaises(OSError):
            apply_pending(child)
        self.assertIn('pending_worktree_binding', child.task_record)
        self.manager._save_state.side_effect = None
        apply_pending(child)
        self.assertEqual(len(child.messages), 2)
        self.assertEqual(child.task_record['worktree_revision'], 2)


class RunningBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_running_child_finishes_whole_tool_batch_before_switch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            work, tree = root / 'child', root / 'tree'
            work.mkdir(); tree.mkdir()
            manager = SimpleNamespace(terminal=SimpleNamespace(mcp_tools_enabled=False),
                _save_state=lambda: None, _state_lock=threading.RLock())
            record = dict(task_id='child', agent_id=1, access_level='workspace_write',
                workspace_root=str(root), working_dir=str(work), execution_root=str(root),
                task_root=str(work), conversation_id='parent',
                **{key: str(work / (key + '.json')) for key in ('output_file', 'stats_file', 'progress_file', 'conversation_file')})
            child = SubAgentTask(manager, record, 'task', 'frozen', 'fake', 'thinking', display_name='Research')
            child._build_client = lambda: (None, 'fake')
            child._apply_usage = lambda usage: None
            child._mark_idle = lambda: None
            child._forward_output_to_master = lambda *args, **kwargs: None
            observed, models = [], []
            async def model(*args):
                models.append([dict(message) for message in child.messages])
                if len(models) == 1:
                    return 'read', '', [{'id': key, 'type': 'function', 'function': {'name': 'read_file', 'arguments': '{}'}} for key in ('a', 'b')], None, []
                return 'done', '', [], None, []
            async def execute(name, args):
                observed.append(fixed_execution_root(child.execution_scope))
                if len(observed) == 1:
                    record['pending_worktree_binding'] = {'directory': str(tree), 'branch': 'task', 'revision': 1}
                return {'success': True, 'content': 'fixture', 'type': 'read'}
            child._call_model, child._execute_tool = model, execute
            with patch('modules.api_pricing.capture_price', return_value=None):
                loop = asyncio.create_task(child._run_loop())
                child._task = loop
                try:
                    for _ in range(300):
                        if child._idle and len(models) == 2:
                            break
                        await asyncio.sleep(0.005)
                    self.assertEqual(len(models), 2)
                    self.assertEqual(observed, [root, root])
                    self.assertEqual(fixed_execution_root(child.execution_scope), tree)
                    roles = [message['role'] for message in models[1]]
                    assistant = roles.index('assistant')
                    self.assertEqual(roles[assistant:assistant + 4], ['assistant', 'tool', 'tool', 'user'])
                    self.assertEqual(child.messages[0]['content'], 'frozen')
                finally:
                    child._cancelled = True
                    loop.cancel()
                    await asyncio.gather(loop, return_exceptions=True)


if __name__ == '__main__':
    unittest.main()
