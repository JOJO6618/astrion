"""Check review completion and real task-local execution authority propagation."""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from modules.execution_scope import current_execution_scope
from modules.full_access_review import run_full_access_review
from modules.tool_approval_manager import ToolApprovalManager
from server.tool_execution_authority import approval_execution_scope, execute_with_authority


class ReviewExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_auto_only_full_access_finishes_without_waiting_for_human(self):
        manager = ToolApprovalManager()
        item = manager.create_request(
            username='user', conversation_id='conv', task_id='task', tool_call_id='tool',
            tool_name='run_command', arguments={'command': 'echo example'}, preview={},
            approval_type='full_access', auto_review_required=True, human_approval_required=False,
        )
        async def reviewer(**kwargs):
            return {'decision': 'approved', 'reason': 'requested by user'}
        events = []
        result = await run_full_access_review(
            manager=manager, approval_id=item['approval_id'], username='user',
            reviewer=reviewer, sender=lambda name, event: events.append((name, event)),
        )
        self.assertEqual(result['decision'], 'approved')
        self.assertIsNone(result['item']['human_decision'])
        self.assertEqual(events[-1][1]['progress']['decision'], 'approved')

    async def test_default_full_access_still_waits_for_human(self):
        manager = ToolApprovalManager()
        item = manager.create_request(
            username='user', conversation_id='conv', task_id='task', tool_call_id='tool',
            tool_name='run_command', arguments={'command': 'echo example'}, preview={},
            approval_type='full_access', auto_review_required=True,
        )
        async def reviewer(**kwargs):
            return {'decision': 'approved'}
        result = await run_full_access_review(
            manager=manager, approval_id=item['approval_id'], username='user',
            reviewer=reviewer, sender=lambda *args: None,
        )
        self.assertEqual(result['decision'], 'pending')
        self.assertEqual(result['item']['auto_review_status'], 'approved')

    async def test_direct_approval_executes_once_with_direct_authority(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            seen = []
            async def handle(name, args):
                seen.append((name, args, current_execution_scope()))
                return '{"success": true}'
            terminal = SimpleNamespace(
                context_manager=SimpleNamespace(project_path=root, current_conversation_id='conv'),
                get_execution_mode=lambda: 'direct', handle_tool_call=handle,
            )
            manager = ToolApprovalManager()
            args = {'command': 'pwd', 'timeout': 10}
            item = manager.create_request(
                username='user', conversation_id='conv', task_id='task', tool_call_id='tool',
                tool_name='run_command', arguments=args, preview={}, approval_type='direct_execution',
                executor_id=id(terminal), workspace_root=str(root), execution_mode='direct',
            )
            approved = manager.decide(item['approval_id'], 'user', 'approved')
            scope = approval_execution_scope(
                terminal, args, approved, task_id='task', tool_call_id='tool',
                expected_root=root, manager=manager, tool_name='run_command',
            )
            await execute_with_authority(terminal, 'run_command', args, scope)
            self.assertEqual(len(seen), 1)
            self.assertEqual(seen[0][2].execution_mode, 'direct')
            self.assertIsNone(current_execution_scope())

    async def test_review_commands_stay_in_readonly_sandbox_when_parent_is_direct(self):
        for relative in ('modules/approval_agent.py', 'modules/workflow_review_agent.py'):
            tree = ast.parse((ROOT / relative).read_text())
            cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name.endswith('Agent'))
            method = next(node for node in cls.body if isinstance(node, ast.AsyncFunctionDef) and node.name == '_run_readonly_command')
            namespace = {'Path': Path, 'Dict': Dict, 'Any': Any, 'DEFAULT_MAX_COMMAND_TIMEOUT': 20}
            exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), relative, 'exec'), namespace)
            seen = []
            async def run_command(**kwargs):
                seen.append((current_execution_scope().execution_mode, kwargs))
                return {'success': True}
            agent = SimpleNamespace(cfg={}, web_terminal=SimpleNamespace(
                context_manager=SimpleNamespace(project_path=ROOT),
                get_execution_mode=lambda: 'direct', terminal_ops=SimpleNamespace(run_command=run_command)))
            await namespace['_run_readonly_command'](agent, 'pwd')
            self.assertEqual(seen[0][0], 'sandbox')
            self.assertIs(seen[0][1]['sandbox_write_access'], False)
            self.assertIsNone(current_execution_scope())


if __name__ == '__main__':
    unittest.main()
