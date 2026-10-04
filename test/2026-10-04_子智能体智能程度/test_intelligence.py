"""离线回归：模型分档、配置迁移、推理强度快照与多智能体隔离。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from modules.sub_agent.intelligence import resolve_intelligence_model
from modules.personalization_manager import (
    load_personalization_config, save_personalization_config,
    sanitize_personalization_payload,
)
from modules.sub_agent.task import SubAgentTask
from core.main_terminal_parts.tools_definition.agent_tools import ToolsDefinitionAgentToolsMixin


class IntelligenceTests(unittest.TestCase):
    @patch('modules.aux_model_resolver.resolve_locked_model_profile')
    def test_same_and_configured_tiers(self, resolve):
        self.assertEqual(resolve_intelligence_model('same', {}, 'main'), 'main')
        for level in ('high', 'medium', 'low'):
            with self.subTest(level=level):
                key = f'model-{level}'
                self.assertEqual(resolve_intelligence_model(
                    level, {f'sub_agent_model_{level}': key}, 'main'), key)
        self.assertEqual(resolve.call_count, 4)

    def test_missing_invalid_or_unconfigured_level_rejected(self):
        for level in (None, '', 'unknown', 'high', 'medium', 'low'):
            with self.subTest(level=level), self.assertRaises(ValueError):
                resolve_intelligence_model(level, {}, 'main')

    @patch('modules.aux_model_resolver.resolve_locked_model_profile', side_effect=RuntimeError('unavailable'))
    def test_unavailable_model_never_falls_back(self, resolve):
        with self.assertRaisesRegex(RuntimeError, 'unavailable'):
            resolve_intelligence_model('high', {'sub_agent_model_high': 'removed'}, 'main')
        resolve.assert_called_once_with('removed')

    def test_legacy_migration_and_explicit_clear(self):
        config = sanitize_personalization_payload({'sub_agent_model': 'old'})
        self.assertEqual(config['sub_agent_model_low'], 'old')
        self.assertNotIn('sub_agent_model', config)
        cleared = sanitize_personalization_payload(
            {'sub_agent_model_low': ''}, fallback=config)
        self.assertEqual(cleared['sub_agent_model_low'], '')
        explicit = sanitize_personalization_payload(
            {'sub_agent_model': 'old', 'sub_agent_model_low': ''})
        self.assertEqual(explicit['sub_agent_model_low'], '')

    def test_migration_persisted_and_partial_save_preserves_tiers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'personalization.json'
            path.write_text(json.dumps({'sub_agent_model': 'old'}), encoding='utf-8')
            config = load_personalization_config(directory)
            self.assertEqual(config['sub_agent_model_low'], 'old')
            stored = json.loads(path.read_text(encoding='utf-8'))
            self.assertNotIn('sub_agent_model', stored)
            save_personalization_config(directory, {'sub_agent_model_high': 'strong'})
            config = load_personalization_config(directory)
            self.assertEqual(config['sub_agent_model_high'], 'strong')
            self.assertEqual(config['sub_agent_model_low'], 'old')

    def test_tool_requires_intelligence_and_removes_thinking_mode(self):
        host = ToolsDefinitionAgentToolsMixin()
        host._inject_intent = lambda value: value
        tools = host._build_agent_tools()
        tool = next(t['function'] for t in tools if t['function']['name'] == 'create_sub_agent')
        self.assertIn('intelligence', tool['parameters']['required'])
        self.assertNotIn('thinking_mode', tool['parameters']['properties'])
        self.assertEqual(tool['parameters']['properties']['intelligence']['enum'],
                         ['same', 'high', 'medium', 'low'])

    def test_multi_agent_tool_keeps_existing_signature(self):
        host = ToolsDefinitionAgentToolsMixin()
        host.multi_agent_mode = True
        tools = host._build_agent_tools()
        tool = next(t['function'] for t in tools if t['function']['name'] == 'create_sub_agent')
        self.assertIn('thinking_mode', tool['parameters']['properties'])
        self.assertNotIn('intelligence', tool['parameters']['properties'])

    def test_restored_traditional_task_forces_thinking(self):
        with tempfile.TemporaryDirectory() as directory:
            record = {
                'task_id': 'task', 'agent_id': 1, 'deliverables_dir': directory,
                'output_file': f'{directory}/output.json',
                'stats_file': f'{directory}/stats.json',
                'progress_file': f'{directory}/progress.json',
                'conversation_file': f'{directory}/conversation.json',
                'task_root': directory,
            }
            for multi_agent in (False, True):
                task = SubAgentTask(
                    manager=SimpleNamespace(), task_record=record,
                    task_message='task', system_prompt='system', model_key='model',
                    thinking_mode='fast', multi_agent_mode=multi_agent,
                )
                self.assertEqual(task.thinking_mode, 'fast' if multi_agent else 'thinking')

    @patch('modules.sub_agent.task.resolve_locked_model_profile', return_value={})
    @patch('modules.sub_agent.task.APIClient')
    def test_client_inherits_snapshot_only_for_traditional(self, client_cls, resolve):
        for multi_agent in (False, True):
            for effort in ('high', None):
                with self.subTest(multi_agent=multi_agent, effort=effort):
                    client = SimpleNamespace(reasoning_effort='initial', apply_profile=MagicMock())
                    client_cls.return_value = client
                    task = SubAgentTask.__new__(SubAgentTask)
                    task.manager = SimpleNamespace(project_path='/tmp/project')
                    task.model_key = 'chosen'
                    task.thinking_mode = 'thinking'
                    task.multi_agent_mode = multi_agent
                    task.task_record = {'reasoning_effort': effort}
                    built, key = task._build_client()
                    self.assertEqual(key, 'chosen')
                    self.assertEqual(built.reasoning_effort, 'initial' if multi_agent else effort)


if __name__ == '__main__':
    unittest.main()
