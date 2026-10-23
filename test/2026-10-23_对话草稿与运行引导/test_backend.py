"""Offline tests use synthetic conversations and temporary storage only."""
from __future__ import annotations

import ast
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
from types import ModuleType, SimpleNamespace
from typing import Any, Callable, Dict, List, Optional
import unittest
from unittest.mock import Mock, patch
import uuid

from flask import Flask, jsonify, request

ROOT = Path(__file__).resolve().parents[2]


def load_functions(path, names, **bindings):
    tree = ast.parse((ROOT / path).read_text())
    nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    assert len(nodes) == len(names), (path, names)
    future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    namespace = dict(bindings)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[future, *nodes], type_ignores=[])), path, 'exec'), namespace)
    return SimpleNamespace(**{name: namespace[name] for name in names})


def stub(name, **attrs):
    result = ModuleType(name)
    result.__dict__.update(attrs)
    return result


class DraftTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        lock = threading.RLock()
        self.storage = load_functions('modules/composer_drafts.py', ['read_draft', 'save_draft', 'atomic_write'],
            json=json, os=os, Path=Path, tempfile=tempfile, directory_lock=lambda _: lock,
            replace_with_retry=lambda source, target: os.replace(source, target))
        self.manager = SimpleNamespace(read_conversation=lambda cid: {} if cid in {'conv_A', 'conv_B'} else None)
        self.workspace = SimpleNamespace(data_dir=self.root, username='host', workspace_id='ws')
        self.context = SimpleNamespace(workspace_id='ws', manager_for=lambda _: self.manager)
        self.ack = Mock()
        self.api = load_functions('server/composer_drafts.py',
            ['draft_owner', 'resolve_draft', 'draft_payload', 'get_draft', 'put_draft'],
            request=request, jsonify=jsonify, hashlib=hashlib, re=re, Path=Path,
            datetime=datetime, timezone=timezone, read_draft=self.storage.read_draft,
            save_draft=self.storage.save_draft, directory_lock=lambda _: lock,
            acknowledge_recovered=self.ack)
        self.app = Flask(__name__)
        self.app.add_url_rule('/draft', 'get', lambda: self.api.get_draft(self.context, self.workspace, 'synthetic'), methods=['GET'])
        self.app.add_url_rule('/draft', 'put', lambda: self.api.put_draft(self.context, self.workspace, 'synthetic'), methods=['POST'])
        self.client = self.app.test_client()

    def put(self, cid, text, revision=0, **extra):
        return self.client.post('/draft?conversation_id=' + cid, json={
            'content': text, 'base_revision': revision, 'client_id': 'browser', 'sequence': revision + 1, **extra})

    def test_isolation_and_explicit_identity(self):
        self.assertEqual(self.put('conv_A', 'A').status_code, 200)
        self.assertEqual(self.put('conv_B', 'B').status_code, 200)
        self.assertEqual(self.client.get('/draft?conversation_id=conv_A').json['data']['content'], 'A')
        self.assertEqual(self.client.get('/draft?conversation_id=conv_B').json['data']['content'], 'B')
        self.assertEqual(self.client.get('/draft').status_code, 400)
        self.assertEqual(self.client.get('/draft?conversation_id=conv_missing').status_code, 404)
        self.assertEqual(self.client.get('/draft?conversation_id=../../outside').status_code, 400)
        self.assertEqual(len(list(self.root.rglob('*.json'))), 2)

    def test_stale_writer_cannot_overwrite_or_resurrect(self):
        self.assertEqual(self.put('conv_A', 'first').json['data']['revision'], 1)
        response = self.client.post('/draft?conversation_id=conv_A', json={
            'content': 'other', 'base_revision': 0, 'client_id': 'other', 'sequence': 1})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.put('conv_A', '', 1).status_code, 200)
        self.assertFalse(self.put('conv_A', 'late', 0).json['data']['saved'])
        self.assertEqual(self.client.get('/draft?conversation_id=conv_A').json['data']['content'], '')

    def test_recovery_is_acknowledged_only_after_durable_save(self):
        self.assertEqual(self.put('conv_A', 'recovered', recovered_ids=['old'], files=['a.txt'], images=['a.png']).status_code, 200)
        self.ack.assert_called_once_with(self.context, 'synthetic', ['old'])
        self.ack.reset_mock()
        self.assertEqual(self.put('conv_A', 'invalid', 1, images=[{}]).status_code, 409)
        self.ack.assert_not_called()
        data = self.client.get('/draft?conversation_id=conv_A').json['data']
        self.assertEqual(data['images'], ['a.png'])
        self.assertNotIn('writer_sequences', data)

    def test_atomic_failure_preserves_previous_draft(self):
        self.put('conv_A', 'committed')
        namespace = self.storage.atomic_write.__globals__
        with patch.dict(namespace, replace_with_retry=Mock(side_effect=OSError('synthetic disk error'))):
            with self.assertRaises(OSError):
                self.storage.save_draft(next(self.root.rglob('*.json')), {'content': 'new'})
        self.assertEqual(self.client.get('/draft?conversation_id=conv_A').json['data']['content'], 'committed')


class GuidanceTests(unittest.TestCase):
    def setUp(self):
        tree = ast.parse((ROOT / 'server/tasks/models.py').read_text())
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'TaskManager')
        names = {'enqueue_runtime_guidance', 'consume_runtime_guidance_for_injection',
                 '_normalize_runtime_pending_queue', '_runtime_pending_public', 'get_runtime_pending_messages'}
        methods = [node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in names]
        namespace = {'Any': Any, 'List': List, 'Dict': Dict, 'Optional': Optional, 'uuid': uuid, 'time': time,
                     'save_queue': Mock(), 'tr': lambda key, **params: key}
        module = ast.Module(body=[ast.ClassDef(name='Harness', bases=[], keywords=[], body=methods, decorator_list=[])], type_ignores=[])
        exec(compile(ast.fix_missing_locations(module), 'guidance_harness', 'exec'), namespace)
        self.rec = SimpleNamespace(username='synthetic', conversation_id='conv_A', status='running', stop_requested=False,
            runtime_guidance_closed=False, runtime_guidance_queue=[], task_id='task', queue_manager=None)
        self.manager = namespace['Harness']()
        self.manager._lock = threading.RLock()
        self.manager._tasks = {'task': self.rec}
        self.patch = patch.dict(sys.modules, {'server.tasks': stub('server.tasks', task_manager=self.manager)})
        self.patch.start(); self.addCleanup(self.patch.stop)
        paths = load_functions('server/attachment_paths.py',
            ['extract_media_path', 'normalize_attached_files', 'build_media_paths_message', 'build_file_paths_message'])
        support = load_functions('server/chat_flow_task_support.py', ['_runtime_message_ui_defaults', 'inject_runtime_user_message'],
            _VALID_SOURCES={'guidance', 'notify', 'media_paths', 'file_paths'}, datetime=datetime, uuid=uuid)
        self.inject = load_functions('server/runtime_guidance.py', ['inject_pending_guidance'],
            inject_runtime_user_message=support.inject_runtime_user_message,
            build_media_paths_message=paths.build_media_paths_message,
            build_file_paths_message=paths.build_file_paths_message).inject_pending_guidance
        self.history, self.messages, self.events = [], [], []
        def add(role, content, **extra):
            saved = {'role': role, 'content': content, **extra}
            self.history.append(saved)
            return saved
        self.context = SimpleNamespace(add_conversation=add, _build_content_with_images=Mock(side_effect=AssertionError('unexpected multimodal payload')))
        self.terminal = SimpleNamespace(context_manager=self.context)

    def consume(self, close=False):
        return self.inject(web_terminal=self.terminal, messages=self.messages, username='synthetic', task_id='task',
                           sender=lambda *event: self.events.append(event), conversation_id='conv_A', close_if_empty=close)

    def test_image_and_file_paths_are_separate_hidden_messages(self):
        result = self.manager.enqueue_runtime_guidance('synthetic', 'task', 'look at this',
            images=['diagram.png'], files=['spec.md'], media_paths_only=True, conversation_id='conv_A')
        self.assertTrue(result['success'])
        self.assertEqual(self.consume(), 1)
        self.assertEqual(len(self.messages), 3)
        self.assertTrue(all(isinstance(message['content'], str) for message in self.messages))
        self.assertIn('[系统通知|media_paths]', self.messages[1]['content'])
        self.assertIn('diagram.png', self.messages[1]['content'])
        self.assertIn('[系统通知|file_paths]', self.messages[2]['content'])
        self.assertEqual(self.history[0]['images'], ['diagram.png'])
        self.assertTrue(self.history[0]['metadata']['media_paths_only'])
        self.assertTrue(all(message['metadata']['visibility'] == 'hidden' for message in self.history[1:]))
        self.context._build_content_with_images.assert_not_called()

    def test_attachment_only_and_atomic_close(self):
        self.assertTrue(self.manager.enqueue_runtime_guidance('synthetic', 'task', '', files=['file.md'], media_paths_only=True)['success'])
        self.assertEqual(self.consume(close=True), 1)
        self.assertFalse(self.rec.runtime_guidance_closed)
        self.assertEqual(self.consume(close=True), 0)
        self.assertTrue(self.rec.runtime_guidance_closed)
        self.assertEqual(self.manager.enqueue_runtime_guidance('synthetic', 'task', 'too late')['code'], 'task_not_running')

    def test_stop_and_wrong_conversation_reject_without_consuming(self):
        self.assertEqual(self.manager.enqueue_runtime_guidance('synthetic', 'task', 'wrong', conversation_id='conv_B')['code'], 'task_not_running')
        self.rec.stop_requested = True
        self.assertEqual(self.manager.enqueue_runtime_guidance('synthetic', 'task', 'stopped')['code'], 'task_not_running')
        self.assertEqual(self.rec.runtime_guidance_queue, [])

    def test_unconsumed_guidance_keeps_attachment_recovery_data(self):
        state = load_functions('server/tasks/queue_state.py', ['guidance_to_pending', 'finish_queue'],
                               deepcopy=deepcopy, uuid=uuid, save_queue=Mock())
        self.manager.enqueue_runtime_guidance('synthetic', 'task', 'keep', images=['a.png'], files=['a.md'], media_paths_only=True)
        self.rec.runtime_pending_queue = []
        state.finish_queue(self.rec, paused=True)
        self.assertEqual(self.rec.runtime_guidance_queue, [])
        self.assertEqual(self.rec.runtime_pending_queue[0]['images'], ['a.png'])
        self.assertEqual(self.rec.runtime_pending_queue[0]['files'], ['a.md'])
        self.assertTrue(self.rec.runtime_guidance_closed)
        snapshot = self.manager.get_runtime_pending_messages('synthetic', 'task')
        self.assertEqual(snapshot[0]['images'], ['a.png'])
        self.assertEqual(snapshot[0]['files'], ['a.md'])
        self.assertTrue(snapshot[0]['media_paths_only'])
        snapshot[0]['images'].clear()
        self.assertEqual(self.rec.runtime_pending_queue[0]['images'], ['a.png'])

    def test_compression_continuation_reopens_guidance_on_the_same_writer(self):
        tree = ast.parse((ROOT / 'server/tasks/compression_requests.py').read_text())
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef))
        method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == 'take_compression_request')
        namespace = {'VIEW_LOCK': threading.RLock(), 'debug_log': lambda *args: None}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), 'compression_harness', 'exec'), namespace)
        self.consume(close=True)
        self.rec.compression_request = 'pending'
        self.rec.compression_requests_closed = False
        self.assertTrue(namespace['take_compression_request'](self.manager, 'synthetic', 'task', close_if_empty=True))
        self.assertFalse(self.rec.runtime_guidance_closed)
        self.assertTrue(self.manager.enqueue_runtime_guidance('synthetic', 'task', 'during compression')['success'])
        self.assertEqual(self.consume(), 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
