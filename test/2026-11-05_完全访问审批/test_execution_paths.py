"""Exercise production policy branches without loading application config or secrets."""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def source_nodes(path, names, *, class_name=None):
    tree = ast.parse((ROOT / path).read_text())
    nodes = tree.body
    if class_name:
        nodes = next(node.body for node in nodes if isinstance(node, ast.ClassDef) and node.name == class_name)
    return [node for node in nodes if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]


def policy_class():
    path = 'core/main_terminal_parts/tools_execution.py'
    nodes = source_nodes(path, {'evaluate_tool_permission'}, class_name='MainTerminalToolsExecutionMixin')
    body = ast.ClassDef(name='Policy', bases=[], keywords=[], body=nodes, decorator_list=[])
    from modules.execution_scope import current_execution_scope, validate_full_access_request
    namespace = dict(Dict=dict, Any=object, Optional=__import__('typing').Optional, Path=Path,
                     current_execution_scope=current_execution_scope,
                     validate_full_access_request=validate_full_access_request, tr=lambda key: key)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[body], type_ignores=[])), path, 'exec'), namespace)
    return namespace['Policy']


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.goal = patch.dict(sys.modules, {'modules.goal_policy': SimpleNamespace(
            interaction_denied=lambda *a: False, DENIED_MESSAGE='goal denied')})
        self.goal.start()
        self.addCleanup(self.goal.stop)
        self.policy = policy_class()()
        self.mode, self.environment = 'approval', 'direct'
        self.policy.get_permission_mode = lambda: self.mode
        self.policy.get_execution_mode = lambda: self.environment
        self.policy._extract_command_risk_markers = lambda cmd: []
        self.policy._is_readonly_run_command_allowed = lambda cmd: True
        self.policy._is_docker_runtime = lambda: False
        self.policy._APPROVAL_REQUIRED_TOOLS = {'run_command', 'terminal_session', 'write_file', 'edit_file'}
        self.policy.context_manager = SimpleNamespace(project_path=ROOT)
        self.policy.file_manager = SimpleNamespace(_validate_path=lambda path: (True, '', ROOT / path))

    def test_every_direct_command_is_reviewed_even_readonly_or_control_input(self):
        for mode in ('approval', 'auto_approval'):
            self.mode = mode
            for name, command in [('run_command', 'ls'), ('terminal_input', 'pwd'), ('terminal_input', '\x03')]:
                with self.subTest(mode=mode, tool=name, command=command):
                    result = self.policy.evaluate_tool_permission(name, {'command': command})
                    self.assertTrue(result['requires_approval'])
                    self.assertEqual(result['approval_type'], 'direct_execution')

    def test_sandbox_readonly_command_and_terminal_keep_existing_behavior(self):
        self.environment = 'sandbox'
        for mode in ('approval', 'auto_approval'):
            self.mode = mode
            self.assertFalse(self.policy.evaluate_tool_permission('run_command', {'command': 'ls'})['requires_approval'])
            self.assertFalse(self.policy.evaluate_tool_permission('terminal_input', {'command': 'pwd'}).get('requires_approval', False))
        self.policy._is_readonly_run_command_allowed = lambda cmd: False
        self.assertTrue(self.policy.evaluate_tool_permission('run_command', {'command': 'touch x'})['requires_approval'])

    def test_auto_file_edit_exemption_is_limited_to_workspace(self):
        self.mode = 'auto_approval'
        self.assertFalse(self.policy.evaluate_tool_permission('write_file', {'file_path': 'example.txt'})['requires_approval'])
        result = self.policy.evaluate_tool_permission('write_file', {'file_path': '/outside/example.txt'})
        self.assertTrue(result['requires_approval'])
        self.assertEqual(result['approval_type'], 'direct_execution')

    def test_manual_file_edit_and_terminal_creation_require_approval(self):
        for tool in ('write_file', 'terminal_session'):
            result = self.policy.evaluate_tool_permission(tool, {'file_path': 'x'})
            self.assertTrue(result['requires_approval'])
            self.assertEqual(result['approval_type'], 'direct_execution')

    def test_unrestricted_direct_remains_unrestricted(self):
        self.mode = 'unrestricted'
        self.assertFalse(self.policy.evaluate_tool_permission('run_command', {'command': 'pwd'}).get('requires_approval', False))

    def test_terminal_identity_matches_environment(self):
        nodes = source_nodes('core/main_terminal_parts/tools_policy.py', {'terminal_readonly_enabled'}, class_name='MainTerminalToolsPolicyMixin')
        namespace = {}
        exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), 'policy', 'exec'), namespace)
        for mode, environment, readonly in [('readonly', 'sandbox', True), ('approval', 'sandbox', True),
                                            ('auto_approval', 'sandbox', True), ('approval', 'direct', False),
                                            ('auto_approval', 'direct', False), ('unrestricted', 'direct', False)]:
            self.mode, self.environment = mode, environment
            self.assertEqual(namespace['terminal_readonly_enabled'](self.policy), readonly)


class TabApprovalTests(unittest.TestCase):
    def enrich(self, rows):
        namespace = dict(List=list, Dict=dict, Any=object, _load_index_titles=lambda _: {},
                         _active_run_conversation_ids=lambda *a: set(),
                         _conversation_is_running=lambda *a: True,
                         request_identity=lambda: 'identity',
                         resolve_workspace=lambda user, ws, identity: SimpleNamespace(project_path=ROOT / ws))
        nodes = source_nodes('server/conversation_tabs.py', {'_normalized_conversation_ids', '_enrich_tabs'})
        exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), 'tabs', 'exec'), namespace)
        tabs = [dict(kind='conv', workspace_id=ws, conversation_id='conv_same') for ws in ('a', 'b')]
        fake_state = SimpleNamespace(tool_approval_manager=SimpleNamespace(list_pending=lambda _: rows))
        with patch.dict(sys.modules, {'server.state': fake_state}):
            namespace['_enrich_tabs']('user', tabs)
        return tabs

    def row(self, **kwargs):
        return dict(workspace_root=str(ROOT / 'a'), conversation_id='conv_same', **kwargs)

    def test_human_wait_is_scoped_to_workspace_and_conversation(self):
        tabs = self.enrich([self.row(human_approval_required=True)])
        self.assertTrue(tabs[0]['awaiting_human_approval'])
        self.assertFalse(tabs[1]['awaiting_human_approval'])

    def test_automatic_only_and_already_human_approved_do_not_wait(self):
        for row in (self.row(human_approval_required=False, auto_review_required=True),
                    self.row(human_approval_required=True, human_decision='approved')):
            self.assertFalse(self.enrich([row])[0]['awaiting_human_approval'])

    def test_dual_approval_waits_for_human_even_during_automatic_review(self):
        row = self.row(human_approval_required=True, auto_review_required=True, auto_review_status='reviewing')
        self.assertTrue(self.enrich([row])[0]['awaiting_human_approval'])

    def test_resolved_requests_clear_dot(self):
        self.assertFalse(self.enrich([])[0]['awaiting_human_approval'])


if __name__ == '__main__':
    unittest.main()
