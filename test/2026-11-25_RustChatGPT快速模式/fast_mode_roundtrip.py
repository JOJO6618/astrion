"""Check the Agent Fast preference against assembled Rust HTTP listeners.

Run: python3.11 -B test/2026-11-25_RustChatGPT快速模式/fast_mode_roundtrip.py
"""
from __future__ import annotations

from contextlib import contextmanager
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import tempfile
from threading import Event, Thread
import time
import unittest
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / '2026-11-24_Rust后端'))
from agent_process_roundtrip import MODEL, ROOT, fixture_environment, rust

API = '/api/chatgpt-fast-mode'
PRIORITY = 'openai-codex/fixture-priority'
LEGACY = 'openai-codex/fixture-legacy-fast'
NO_TIER = 'openai-codex/fixture-standard'
TIMESTAMP = '2024-09-05T11:22:33.019'
BINARY = Path(os.environ.get('ASTRION_HEADLESS_BIN', ROOT / 'backend-rust/target-master/debug/astrion-headless'))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def register(env, entries):
    path = Path(env['HOST_WORKSPACES_FILE'])
    write_json(path, {'default_workspace_id': 'alpha', 'workspaces': [
        {'workspace_id': name, 'label': name, 'path': str(directory)} for name, directory in entries.items()
    ]})


@contextmanager
def fixture():
    with tempfile.TemporaryDirectory(prefix='fast-mode-', dir=HERE) as name:
        root = Path(name).resolve()
        env = fixture_environment(root)
        env['ASTRION_RESOURCE_ROOT'] = str(ROOT)
        env['AGENT_DEFAULT_MODEL'] = PRIORITY
        alpha = Path(env['HOST_PROJECT_PATH'])
        beta = root / 'beta'
        beta.mkdir()
        register(env, {'alpha': alpha, 'beta': beta})
        source_models = Path(env['DEPLOY_CONFIG_DIR']) / 'custom_models.json'
        write_json(source_models, {'models': [{
            'model_name': MODEL, 'model_id': MODEL, 'url': 'http://127.0.0.1:1/v1',
            'apikey': 'synthetic-model-key', 'responses_auth': 'codex_oauth', 'supports_chatgpt_fast_mode': True,
        }]})
        data = Path(env['DATA_DIR'])
        write_json(data / 'personalization.json', {'ui_locale': 'en-US'})
        write_json(data / 'codex_auth.json', {'tokens': {
            'access_token': 'synthetic-access-token', 'refresh_token': 'synthetic-refresh-token',
        }})
        common = {'supported_in_api': True, 'visibility': 'list'}
        write_json(data / 'codex_models.json', {'models': [
            {**common, 'slug': 'fixture-priority', 'service_tiers': [{'id': 'priority'}]},
            {**common, 'slug': 'fixture-legacy-fast', 'additional_speed_tiers': ['fast']},
            {**common, 'slug': 'fixture-standard', 'service_tiers': [{'id': 'flex'}]},
        ]})
        yield root, env, data


def seed(data, workspace, cid, model, *, enabled=False):
    path = data / 'conversations' / workspace / f'{cid}.json'
    document = {
        'id': cid, 'title': 'Fast setting fixture', 'created_at': TIMESTAMP, 'updated_at': TIMESTAMP,
        'messages': [{'role': 'user', 'content': 'seed message', 'message_id': f'msg_{cid}'}],
        'metadata': {'title_locked': True, 'model_key': model, 'chatgpt_fast_mode': enabled,
                     'reasoning_effort': 'high', 'legacy_unknown': {'nested': ['保留', True]},
                     'frozen_system_prompt': 'Synthetic frozen prompt for HTTP acceptance.'},
    }
    write_json(path, document)
    index = path.with_name('index.json')
    records = json.loads(index.read_bytes()) if index.exists() else {}
    records[cid] = {'title': document['title'], 'updated_at': TIMESTAMP, 'future_index_field': 'keep'}
    write_json(index, records)
    return path, document


def request(base, method, path, *, headers=None, payload=None, status=200):
    url = urlsplit(base)
    sent = {'Origin': base, **(headers or {})}
    raw = None
    if payload is not None:
        sent['Content-Type'] = 'application/json'
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    connection = HTTPConnection(url.hostname, url.port, timeout=8)
    try:
        connection.request(method, path, raw, sent)
        with connection.getresponse() as response:
            raw = response.read()
            assert response.status == status, (method, path, status, response.status, raw)
            data = json.loads(raw) if raw and response.headers.get('Content-Type', '').startswith('application/json') else raw
            return data, response.headers
    finally:
        connection.close()


def login(base):
    csrf, headers = request(base, 'GET', '/api/csrf-token')
    guest = headers['Set-Cookie'].split(';', 1)[0]
    result, headers = request(base, 'POST', '/host-login', payload={},
                              headers={'Cookie': guest, 'X-CSRF-Token': csrf['token']})
    assert result['success'] is True, result
    cookie = headers['Set-Cookie'].split(';', 1)[0]
    csrf, _ = request(base, 'GET', '/api/csrf-token', headers={'Cookie': cookie})
    return {'Cookie': cookie, 'X-CSRF-Token': csrf['token']}


def settings(cid, enabled):
    return {'conversation_id': cid, 'enabled': enabled}


def success(enabled):
    return {'success': True, 'data': {'chatgpt_fast_mode': enabled}}


def failure(error):
    return {'success': False, 'error': error}


def snapshot(*files):
    return {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in files}


class BlockingModel(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers['Content-Length']))
        self.server.arrived.set()
        if not self.server.release.wait(timeout=10):
            self.send_error(504)
            return
        reply = {'choices': [{'delta': {'content': 'fixture complete'}, 'finish_reason': 'stop'}]}
        raw = ('data: ' + json.dumps(reply) + '\n\ndata: [DONE]\n\n').encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


@contextmanager
def model_server():
    with ThreadingHTTPServer(('127.0.0.1', 0), BlockingModel) as server:
        server.arrived = Event()
        server.release = Event()
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            yield server
        finally:
            server.release.set()
            server.shutdown()
            worker.join(timeout=5)
            assert not worker.is_alive()


class FastModeTests(unittest.TestCase):
    def test_workspace_scope_persistence_capabilities_and_session_permission(self):
        with fixture() as (root, env, data):
            first, original = seed(data, 'alpha', 'conv_priority', PRIORITY)
            beta, _ = seed(data, 'beta', 'conv_legacy', LEGACY)
            no_tier, _ = seed(data, 'alpha', 'conv_standard', NO_TIER, enabled=True)
            regular, _ = seed(data, 'alpha', 'conv_regular', MODEL, enabled=True)
            retired, _ = seed(data, 'alpha', 'conv_retired', 'retired-model')
            seed(data, 'alpha', 'conv_default', None)
            first_index = first.with_name('index.json')
            beta_index = beta.with_name('index.json')
            originals = snapshot(first, first_index, beta, beta_index, no_tier, regular, retired)

            with rust(env, BINARY, 'http://127.0.0.1:1/fixture-unused', root / 'rust.log', web=True) as base:
                browser = login(base)
                bearer = {'Authorization': 'Bearer ' + (data / 'host_api_token').read_text().strip()}
                self.assertTrue(request(base, 'GET', '/api/tasks', headers=bearer)[0]['success'])
                for headers in ({}, bearer, {'Cookie': browser['Cookie']}):
                    request(base, 'POST', API, headers=headers, payload=settings('conv_priority', True), status=403)
                request(base, 'POST', API, headers={**browser, 'Origin': 'http://127.0.0.1:1'},
                        payload=settings('conv_priority', True), status=403)

                def toggle(payload, *, path=API, headers=None, status=200):
                    return request(base, 'POST', path, payload=payload, headers={**browser, **(headers or {})}, status=status)[0]

                for payload in (b'{broken', ['enabled', True], b'null', None):
                    self.assertEqual(toggle(payload, status=400), failure('Invalid settings'))
                for value in ('true', 1, 0, None):
                    self.assertEqual(toggle(settings('conv_priority', value), status=400),
                                     failure('ChatGPT fast mode must be a boolean'))
                for cid in (None, 1, 'priority', ' conv_priority ', 'conv_../invalid', 'conv_missing', 'conv_legacy'):
                    self.assertEqual(toggle(settings(cid, True), status=404), failure('Conversation not found'))
                self.assertEqual(toggle(settings('conv_priority', True), path=API + '?conversation_id=conv_standard', status=404),
                                 failure('Conversation not found'))
                for cid in ('conv_standard', 'conv_regular'):
                    self.assertEqual(toggle(settings(cid, True), status=400),
                                     failure('Selected ChatGPT model does not support Fast mode'))
                self.assertEqual(toggle(settings('conv_retired', False), status=400), failure('未知模型 key: retired-model'))
                self.assertEqual(snapshot(*originals), originals, 'rejected requests changed conversation bytes or index')

                self.assertEqual(toggle(settings('conv_priority', True), path=API + '?conversation_id=priority'), success(True))
                raw = json.loads(first.read_bytes())
                self.assertNotEqual(raw.pop('updated_at'), TIMESTAMP)
                original.pop('updated_at')
                original['metadata']['chatgpt_fast_mode'] = True
                self.assertEqual(raw, original)
                index = json.loads(first_index.read_bytes())['conv_priority']
                self.assertEqual(index['future_index_field'], 'keep')
                self.assertNotEqual(index['updated_at'], TIMESTAMP)
                self.assertEqual(snapshot(beta, beta_index), {path: originals[path] for path in (beta, beta_index)})
                boot, _ = request(base, 'GET', '/api/conversations/conv_priority/bootstrap', headers=browser)
                self.assertIs(boot['data']['meta']['chatgpt_fast_mode'], True)
                unchanged = snapshot(first, first_index)
                self.assertEqual(toggle(settings('conv_priority', True)), success(True))
                self.assertEqual(snapshot(*unchanged), unchanged)
                self.assertEqual(toggle(settings('conv_priority', False)), success(False))
                self.assertFalse(json.loads(first.read_bytes())['metadata']['chatgpt_fast_mode'])
                self.assertEqual(toggle(settings('conv_standard', False)), success(False))
                self.assertEqual(toggle(settings('conv_regular', False)), success(False))
                self.assertEqual(toggle(settings('conv_default', True)), success(True))

                self.assertEqual(toggle({**settings('conv_legacy', True), 'workspace_id': 'beta'}), success(True))
                self.assertEqual(toggle({**settings('conv_legacy', False), 'workspace_id': 'alpha'},
                                        headers={'X-Astrion-Workspace-Id': 'beta'}), success(False))
                self.assertEqual(toggle({**settings('conv_legacy', True), 'workspace_id': 'alpha'},
                                        headers={'X-Astrion-Workspace-Id': 'alpha'}, path=API + '?workspace_id=beta'), success(True))
                self.assertEqual(toggle(settings('conv_priority', True), path=API + '?workspace_id=beta', status=404),
                                 failure('Conversation not found'))
                self.assertEqual(toggle(settings('conv_legacy', False), path=API + '?workspace_id=unknown', status=404),
                                 failure('Workspace not found'))
                request(base, 'POST', '/api/host/workspaces/select', headers=browser, payload={'workspace_id': 'beta'})
                self.assertEqual(toggle(settings('conv_legacy', False)), success(False))
                self.assertEqual(toggle(settings('conv_priority', True), status=404), failure('Conversation not found'))
                register(env, {})
                self.assertEqual(toggle(settings('conv_priority', True))['code'], 'no_workspace')

    def test_running_task_blocks_only_its_conversation_and_unblocks_on_finish(self):
        with fixture() as (root, env, data), model_server() as model:
            target, _ = seed(data, 'alpha', 'conv_busy', MODEL, enabled=True)
            idle, _ = seed(data, 'alpha', 'conv_idle', PRIORITY)
            other, _ = seed(data, 'beta', 'conv_other', PRIORITY)
            endpoint = f'http://127.0.0.1:{model.server_port}/v1/chat/completions'
            with rust(env, BINARY, endpoint, root / 'rust.log', web=True) as base:
                browser = login(base)
                bearer = {'Authorization': 'Bearer ' + (data / 'host_api_token').read_text().strip(),
                          'X-Astrion-Workspace-Id': 'alpha'}
                response, _ = request(base, 'POST', '/api/tasks', headers=bearer, status=202, payload={
                    'conversation_id': 'conv_busy', 'message': 'hold this task open',
                })
                task = response.get('task_id') or response['data']['task_id']
                try:
                    self.assertTrue(model.arrived.wait(8), 'fixture model never received the task')
                    tracked = snapshot(target, target.with_name('index.json'))
                    result, _ = request(base, 'POST', API, headers=browser, payload=settings('conv_busy', False), status=409)
                    self.assertEqual(result, failure('Change settings after the current task finishes'))
                    self.assertEqual(snapshot(*tracked), tracked)
                    result, _ = request(base, 'POST', API, headers=browser, payload=settings('conv_idle', True))
                    self.assertEqual(result, success(True))
                    result, _ = request(base, 'POST', API + '?workspace_id=beta', headers=browser, payload=settings('conv_other', True))
                    self.assertEqual(result, success(True))
                finally:
                    model.release.set()
                for _ in range(250):
                    state, _ = request(base, 'GET', '/api/tasks/' + task, headers=bearer)
                    state = state.get('data', state)
                    if state['status'] != 'running':
                        break
                    time.sleep(0.02)
                self.assertEqual(state['status'], 'succeeded', state)
                result, _ = request(base, 'POST', API, headers=browser, payload=settings('conv_busy', False))
                self.assertEqual(result, success(False))
                self.assertFalse(json.loads(target.read_bytes())['metadata']['chatgpt_fast_mode'])
                self.assertTrue(json.loads(idle.read_bytes())['metadata']['chatgpt_fast_mode'])
                self.assertTrue(json.loads(other.read_bytes())['metadata']['chatgpt_fast_mode'])

    def test_headless_has_no_fast_setting_route(self):
        with fixture() as (root, env, data), rust(env, BINARY, 'http://127.0.0.1:1/fixture-unused', root / 'rust.log') as base:
            bearer = {'Authorization': 'Bearer ' + (data / 'host_api_token').read_text().strip()}
            self.assertTrue(request(base, 'GET', '/api/tasks', headers=bearer)[0]['success'])
            request(base, 'POST', API, headers=bearer, payload=settings('conv_missing', True), status=404)


if __name__ == '__main__':
    unittest.main(verbosity=2)
