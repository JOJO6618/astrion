"""Offline regression: synthetic identities/files only, no server or real config."""
from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]


def source(path, names, **bindings):
    tree = ast.parse((ROOT / path).read_text(encoding='utf-8'))
    nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))
             and node.name in names]
    assert len(nodes) == len(names), (path, names)
    future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    module = ast.fix_missing_locations(ast.Module(body=[future, *nodes], type_ignores=[]))
    namespace = {'__file__': str(ROOT / path), **bindings}
    exec(compile(module, path, 'exec'), namespace)
    return SimpleNamespace(**{name: namespace[name] for name in names}), namespace


def identity_decorator(*args, **kwargs):
    if len(args) == 1 and callable(args[0]):
        return args[0]
    return lambda function: function


def module(name, **attrs):
    result = ModuleType(name)
    result.__dict__.update(attrs)
    return result


class DraftTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'drafts' / 'test.json'
        lock = threading.RLock()
        self.drafts, _ = source('modules/composer_drafts.py', ['read_draft', 'atomic_write', 'save_draft'],
                               json=json, os=os, Path=Path, tempfile=tempfile,
                               directory_lock=lambda _: lock,
                               replace_with_retry=lambda src, dst: os.replace(src, dst))

    def test_parallel_writes_are_atomic(self):
        barrier = threading.Barrier(12)
        def save(index):
            barrier.wait()
            self.drafts.save_draft(self.path, {'content': str(index) * 1000},
                                   client_id=str(index), sequence=1)
        with ThreadPoolExecutor(max_workers=12) as pool:
            list(pool.map(save, range(12)))
        result = self.drafts.read_draft(self.path)
        self.assertEqual(len(result['writer_sequences']), 12)
        self.assertIn(result['content'], [str(i) * 1000 for i in range(12)])
        self.assertEqual(list(self.path.parent.glob('*.tmp')), [])

    def test_late_sequence_cannot_overwrite_or_resurrect_cleared_text(self):
        self.assertTrue(self.drafts.save_draft(self.path, {'content': 'latest'}, client_id='browser', sequence=5))
        self.assertFalse(self.drafts.save_draft(self.path, {'content': 'old'}, client_id='browser', sequence=4))
        self.assertTrue(self.drafts.save_draft(self.path, {'content': ''}, client_id='browser', sequence=6))
        self.assertFalse(self.drafts.save_draft(self.path, {'content': 'beacon'}, client_id='browser', sequence=5))
        self.assertEqual(self.drafts.read_draft(self.path)['content'], '')

    def test_failed_replace_retains_last_committed_draft(self):
        self.drafts.save_draft(self.path, {'content': 'old'})
        namespace = self.drafts.atomic_write.__globals__
        with patch.dict(namespace, replace_with_retry=Mock(side_effect=OSError('synthetic disk error'))):
            with self.assertRaises(OSError):
                self.drafts.save_draft(self.path, {'content': 'new'})
        self.assertEqual(self.drafts.read_draft(self.path)['content'], 'old')
        self.assertEqual(list(self.path.parent.glob('*.tmp')), [])


class SkillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.src = root / 'source' / 'one'; self.src.mkdir(parents=True)
        (self.src / 'SKILL.md').write_text('first', encoding='utf-8')
        self.project = root / 'workspace'; self.project.mkdir()
        self.lock = threading.Lock()
        revision, _ = source('modules/skills_revision.py', ['tree_revision'], Path=Path, os=os)
        skill_catalog = module('modules.skills_manager', _get_sync_lock=lambda _: self.lock,
            _get_skill_source_map=lambda *a, **k: {'one': self.src}, ensure_agent_skills_dir=lambda *a: None,
            get_skills_catalog=lambda *a, **k: [{'id': 'one'}],
            resolve_enabled_skills=lambda enabled, _: ['one'] if enabled is None else list(enabled))
        self.patch = patch.dict(sys.modules, {'modules.skills_manager': skill_catalog})
        self.patch.start(); self.addCleanup(self.patch.stop)
        self.skills, _ = source('modules/skills_sync.py', ['sync_workspace_skills'], Path=Path,
            shutil=shutil, WORKSPACE_SKILLS_DIRNAME='.astrion/skills', tree_revision=revision.tree_revision,
            tr=lambda key: key, _VERSIONS={}, logger=Mock())

    def test_parallel_preparation_copies_once(self):
        original = shutil.copytree
        with patch.object(shutil, 'copytree', wraps=original) as copy:
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(lambda _: self.skills.sync_workspace_skills(self.project), range(8)))
        self.assertTrue(all(r['success'] for r in results))
        self.assertEqual(sum(not r['cached'] for r in results), 1)
        self.assertEqual(copy.call_count, 1)

    def test_content_configuration_and_target_deletion_invalidate(self):
        sync = lambda enabled=None: self.skills.sync_workspace_skills(self.project, enabled)
        sync(); self.assertTrue(sync()['cached'])
        (self.src / 'SKILL.md').write_text('changed', encoding='utf-8')
        self.assertFalse(sync()['cached'])
        self.assertEqual((self.project / '.astrion/skills/one/SKILL.md').read_text(), 'changed')
        self.assertFalse(sync([])['cached'])
        self.assertFalse((self.project / '.astrion/skills/one').exists())
        shutil.rmtree(self.project / '.astrion/skills')
        self.assertFalse(sync()['cached'])

    def test_failure_is_retryable_and_project_agents_remain_untouched(self):
        project_skill = self.project / '.agents/skills/private/SKILL.md'
        project_skill.parent.mkdir(parents=True); project_skill.write_text('owned-by-project')
        with patch.object(shutil, 'copytree', side_effect=OSError('synthetic copy error')):
            self.assertFalse(self.skills.sync_workspace_skills(self.project)['success'])
        self.assertFalse(self.skills.sync_workspace_skills(self.project)['cached'])
        self.assertEqual(project_skill.read_text(), 'owned-by-project')


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.workspace = SimpleNamespace(username='host', workspace_id='ws', project_path=ROOT,
                                         data_dir=ROOT, label='test')
        self.state = SimpleNamespace(user_terminals={}, container_manager=SimpleNamespace(ensure_container=Mock()),
                                     user_manager=SimpleNamespace(get_user=lambda _: None))
        self.locks = {}; self.lock_guard = threading.Lock()
        def create(**params):
            time.sleep(0.015)
            return SimpleNamespace(project_path=params['project_path'], model_key='test',
                context_manager=SimpleNamespace(), sub_agent_manager=SimpleNamespace(restore_for_activation=Mock()),
                update_container_session=Mock())
        self.factory = Mock(side_effect=create)
        self.resources, self.namespace = source('server/context/resources.py',
            ['_resource_lock', '_make_terminal_key', 'find_runtime', '_set_terminal_workspace_label', '_discard_runtime', 'get_user_resources'],
            threading=threading, time=time, Path=Path, _LOCKS=self.locks, _LOCKS_GUARD=self.lock_guard,
            debug_log=lambda _: None, state=self.state, resolve_workspace=lambda *a, **k: self.workspace,
            load_personalization_config=lambda _: {}, WebTerminal=self.factory,
            _apply_admin_policy=Mock(), _apply_workspace_personalization_preferences=Mock(),
            _ensure_workspace_skills_synced=Mock(), _resolve_user_role=lambda *a, **k: 'admin',
            timed=identity_decorator, timing_note=lambda *a, **k: None)
        self.identity = SimpleNamespace(is_api_user=False, preferred_run_mode='fast', preferred_thinking_mode=False,
                                        preferred_model_key=None)

    def acquire(self):
        return self.resources.get_user_resources('host', 'ws', conversation_id='conv_test',
                                                  identity=self.identity, update_session=False)[0]

    def test_parallel_acquisition_constructs_one_runtime(self):
        with ThreadPoolExecutor(max_workers=12) as pool:
            terminals = list(pool.map(lambda _: self.acquire(), range(12)))
        self.assertEqual(self.factory.call_count, 1)
        self.assertTrue(all(item is terminals[0] for item in terminals))

    def test_failed_preparation_never_publishes_half_runtime(self):
        self.namespace['_ensure_workspace_skills_synced'].side_effect = RuntimeError('synthetic preparation error')
        with self.assertRaises(RuntimeError): self.acquire()
        self.assertEqual(self.state.user_terminals, {})
        self.namespace['_ensure_workspace_skills_synced'].side_effect = None
        self.assertIsNotNone(self.acquire())


class DisplayTests(unittest.TestCase):
    def test_bootstrap_is_storage_only_and_uses_one_captured_transaction(self):
        data = {'title': 'test', 'metadata': {'work_mode': 'execute'}, 'messages': [], 'todo_list': {'id': 1}}
        snapshot = {'data': data, 'messages': [], 'display': {'task': None, 'messages': [], 'next_event_idx': 900}}
        manager = SimpleNamespace()
        context = SimpleNamespace(workspace_id='ws', manager_for=lambda cid: manager,
                                  identity=SimpleNamespace(host_mode=True, role='admin'))
        workspace = SimpleNamespace(project_path=ROOT, data_dir=ROOT, username='host')
        captured = Mock(return_value=snapshot)
        preview = module('server.chat.preview', build_preview_data_runtime=lambda *args: {})
        goal = module('modules.goal_state_manager', GoalStateManager=lambda *args: SimpleNamespace(progress_snapshot=lambda: {}))
        with patch.dict(sys.modules, {'server.chat.preview': preview, 'modules.goal_state_manager': goal}):
            routes, _ = source('server/conversation_bootstrap.py', ['_normalize_conv_id', '_existing_file', 'bootstrap_conversation'],
                conversation_bootstrap_bp=SimpleNamespace(route=identity_decorator),
                api_login_required=identity_decorator, with_workspace_data=identity_decorator, timed=identity_decorator,
                task_manager=SimpleNamespace(capture_display=captured), effective_modes=lambda *a: {'execution_mode': 'sandbox'},
                pending_modes=lambda *a: {}, background_status=lambda *a, **k: {'has_running_sub_agents': False}, ACTIVE={'running'},
                FileManager=lambda *a, **k: SimpleNamespace(set_host_execution_mode=lambda _: None),
                is_preview_enabled=lambda: False, jsonify=lambda value=None, **kw: value if value is not None else kw,
                timing_note=lambda *a, **k: None, timed_call=lambda name, fn, *a, **k: fn(*a, **k),
                queue_snapshot=lambda mgr, cid, **kw: {'same_data': kw['data'] is data}, tr=lambda key, **kw: key)
            result = routes.bootstrap_conversation('conv_test', context, workspace, 'host')
        self.assertTrue(result['success']); self.assertEqual(captured.call_count, 1)
        self.assertEqual(result['data']['display']['next_event_idx'], 900)
        self.assertTrue(result['data']['runtime_queue']['same_data'])
        self.assertEqual(result['data']['todo_list'], {'id': 1})

    def test_missing_conversation_returns_404_without_activation(self):
        captured = Mock(return_value=None); activation = Mock(side_effect=AssertionError('must not activate'))
        routes, _ = source('server/conversation_bootstrap.py', ['_normalize_conv_id', 'bootstrap_conversation'],
            conversation_bootstrap_bp=SimpleNamespace(route=identity_decorator), api_login_required=identity_decorator,
            with_workspace_data=identity_decorator, timed=identity_decorator,
            task_manager=SimpleNamespace(capture_display=captured), jsonify=lambda **kw: kw,
            tr=lambda key, **kw: key, background_status=activation)
        result, status = routes.bootstrap_conversation('conv_foreign', SimpleNamespace(
            workspace_id='ws2', manager_for=lambda _: object()), object(), 'host')
        self.assertEqual(status, 404); activation.assert_not_called()

    def test_effective_mode_locks_match_execution_rules(self):
        rules, _ = source('modules/conversation_modes.py', ['_mode', 'resolve_conversation_modes'],
            PERMISSIONS={'readonly', 'approval', 'auto_approval', 'unrestricted'}, WORK_MODES={'plan', 'ask', 'execute'},
            EXECUTION_MODES={'sandbox', 'direct'}, NETWORK_MODES={'restricted', 'full', 'none'})
        planned = rules.resolve_conversation_modes({'work_mode': 'plan', 'permission_mode': 'unrestricted', 'execution_mode': 'direct'})
        self.assertEqual(planned['permission_mode'], 'readonly'); self.assertEqual(planned['execution_mode'], 'sandbox')
        restricted = rules.resolve_conversation_modes({'work_mode': 'execute', 'permission_mode': 'approval', 'execution_mode': 'direct'})
        self.assertEqual(restricted['execution_mode'], 'sandbox')
        hosted = rules.resolve_conversation_modes({'work_mode': 'execute', 'permission_mode': 'unrestricted', 'execution_mode': 'direct'})
        self.assertEqual(hosted['execution_mode'], 'direct')


if __name__ == '__main__':
    unittest.main()
