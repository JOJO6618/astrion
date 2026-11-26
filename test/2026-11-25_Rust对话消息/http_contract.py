"""Compare the Python messages view with the authenticated Rust Web listener."""
from __future__ import annotations

import base64
import hashlib
import inspect
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from http.cookiejar import CookieJar
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / '2026-11-24_Rust后端'))
from agent_process_roundtrip import MODEL, PNG, ROOT, fixture_environment, rust

ID = 'conv_20261125_212700_001'
EMPTY = 'conv_20261125_212700_002'
UNKNOWN = 'conv_20261125_212700_003'
MISMATCHED = 'conv_20261125_212700_004'
PATH = f'/api/conversations/{ID}/messages'
BOOTSTRAP = f'/api/conversations/{ID}/bootstrap'
DURABLE = 'persist this input first'
STREAMING = 'only in the live projection'
FINISHED = ' and now committed'


def python_oracle(paths):
    """Use the production view, preflight and path parser in an isolated Flask request."""
    sys.path.insert(0, str(ROOT))
    from flask import Flask
    from modules.file_manager.path_mixin import PathMixin
    from server.context import decorators
    from server.conversation import get_conversation_messages
    from utils.conversation_manager import ConversationManager

    data = Path(os.environ['DATA_DIR'])
    catalog = json.loads(Path(os.environ['HOST_WORKSPACES_FILE']).read_text())
    workspaces = {}
    for item in catalog['workspaces']:
        workspace_id, path = item['workspace_id'], Path(item['path'])
        manager = ConversationManager(base_dir=data, project_path=path, read_only=True)
        files = PathMixin()
        files.project_path = path
        context = SimpleNamespace(
            workspace_id=workspace_id,
            manager_for=lambda _id, manager=manager: manager,
            _get_conversation_manager_for_id=lambda _id, manager=manager: manager,
        )
        workspace = SimpleNamespace(workspace_id=workspace_id, project_path=path)
        terminal = SimpleNamespace(context_manager=context, file_manager=files)
        workspaces[workspace_id] = context, terminal, workspace

    app = Flask(__name__)
    view = decorators.with_terminal(inspect.unwrap(get_conversation_messages))
    app.add_url_rule('/api/conversations/<conversation_id>/messages', view_func=view)
    with (
        patch.object(decorators, 'get_current_username', return_value='host'),
        patch.object(decorators, 'get_workspace_data', side_effect=lambda _, workspace: workspaces[workspace or 'default'][0]),
        patch.object(decorators, 'get_user_resources', side_effect=lambda _, workspace_id, **kwargs: workspaces[workspace_id][1:]),
        app.test_client() as client,
    ):
        # Python supplies its independent preview host and token only for loopback
        # clients. This selects the same null fields as the current Rust listener.
        responses = [client.get(path, base_url='http://fixture.example') for path in paths]
        print('ORACLE=' + json.dumps([[reply.status_code, reply.get_json()] for reply in responses]))


class StagedModel(BaseHTTPRequestHandler):
    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        assert request['model'] == MODEL, request['model']
        content = next(message['content'] for message in request['messages'] if message['role'] == 'user')
        assert content == [
            {'type': 'text', 'text': 'stored image'},
            {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + PNG}},
        ], content
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        self.send_text(STREAMING)
        assert self.server.finish.wait(20), 'test never released model response'
        self.send_text(FINISHED)
        self.wfile.write(b'data: [DONE]\n\n')
        self.wfile.flush()

    def send_text(self, text):
        event = {'choices': [{'index': 0, 'delta': {'content': text}, 'finish_reason': None}]}
        self.wfile.write(('data: ' + json.dumps(event) + '\n\n').encode())
        self.wfile.flush()

    def log_message(self, *args):
        pass


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    return path


def seed(env, directory):
    data, workspace = (Path(env[key]) for key in ('DATA_DIR', 'HOST_PROJECT_PATH'))
    second = directory / 'other-workspace'
    second.mkdir()
    registry = Path(env['HOST_WORKSPACES_FILE'])
    catalog = json.loads(registry.read_text())
    catalog['workspaces'].append({'workspace_id': 'other', 'label': 'Other', 'path': str(second)})
    write(registry, catalog)
    (workspace / 'page.html').write_text('<title>Fixture</title>')
    (workspace / 'folder').mkdir()
    png = base64.b64decode(PNG)
    digest = hashlib.sha256(png).hexdigest()
    media_id = 'sha256:' + digest
    media = data / 'conversations/media_store'
    blob_path = f'blobs/{digest[:2]}/{digest}.png'
    blob = media / blob_path
    blob.parent.mkdir(parents=True)
    blob.write_bytes(png)
    write(media / 'index.json', {'media': {media_id: {'blob_rel_path': blob_path}}})
    reference = {'media_id': media_id, 'kind': 'image', 'mime_type': 'image/png'}
    metadata = {
        'project_path': str(workspace), 'model_key': MODEL,
        'frozen_system_prompt': 'Fixture system prompt',
        'edited_files': [None, {'path': ' page.html ', 'op': '', 'ts': None},
                         {'path': '/workspace/page.html', 'op': 'create', 'ts': 'old'},
                         {'path': 'gone.html'}, {'path': 'folder'}],
        'preview_targets': [
            {'type': 'file', 'path': 'page.html', 'label': 'preserve custom label', 'future': True},
            {'type': 'file', 'path': 'page.html', 'label': 'duplicate'},
            {'type': 'file', 'path': 'gone.html'},
            {'type': 'server', 'url': 'prefix HTTPS://0.0.0.0:443/a?b=123).', 'label': 'old', 'future': 3},
            {'type': 'server', 'url': 'HTTPS://127.0.0.1:443/a?b=123'},
            {'type': 'server', 'url': 'http://localhost:9412/abcdefghijklmnopqrstuvwxy 中文'},
            {'type': 'server', 'url': 'https://[::1]/app/'},
            {'type': 'server', 'url': 'http://localhost:9412/favicon.ico'},
            {'type': 'server', 'url': 'http://localhost'},
            {'type': 'server', 'url': 'https://example.test/remote'},
            {'type': 'legacy', 'path': 'preserve unknown item', 'custom': 1},
        ],
    }
    history = [
        {'role': 'user', 'content': 'stored image', 'message_id': 'msg_legacy_image',
         'metadata': {'media_refs': [reference], 'unknown': 'preserved'}},
        {'role': 'assistant', 'content': 'stored reply', 'message_id': 'msg_legacy_reply',
         'reasoning_content': 'from history', 'timestamp': '2026-11-25T21:27:00',
         'metadata': {'annotations': [{'type': 'url_citation', 'url': 'https://example.test'}]}},
    ]
    agent_dir = data / 'conversations/default'
    file = write(agent_dir / f'{ID}.json', {'id': ID, 'metadata': metadata, 'messages': history})
    empty = write(agent_dir / f'{EMPTY}.json', {
        'id': EMPTY, 'metadata': {'preview_targets': metadata['preview_targets']}, 'messages': [],
    })
    write(agent_dir / f'{MISMATCHED}.json', {
        'id': MISMATCHED, 'metadata': {'project_path': str(second)}, 'messages': history,
    })
    index = write(agent_dir / 'index.json', {ID: {'title': 'Legacy', 'extension': 4}})
    return file, empty, index


def expected(env, paths):
    result = subprocess.run([sys.executable, '-B', __file__, '--oracle', *paths], cwd=ROOT, env=env,
                            text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(next(line.removeprefix('ORACLE=') for line in result.stdout.splitlines() if line.startswith('ORACLE=')))


def wait_for(check):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.02)
    raise AssertionError('expected runtime state was not observed')


def check(binary, directory, upstream):
    env = fixture_environment(directory)
    env['ASTRION_RESOURCE_ROOT'] = str(ROOT)
    file, empty, index = seed(env, directory)
    endpoint = f'http://127.0.0.1:{upstream.server_port}/v1/chat/completions'
    with rust(env, binary, endpoint, directory / 'rust.log', web=True) as url:
        client = build_opener(HTTPCookieProcessor(CookieJar()))
        csrf = ''

        def api(path, body=None, *, token=None):
            headers = {'Origin': url, 'X-CSRF-Token': csrf, 'Content-Type': 'application/json'}
            if token is not None:
                headers['Authorization'] = f'Bearer {token}'
            req = Request(url + path, headers=headers, data=json.dumps(body).encode() if body is not None else None)
            try:
                response = client.open(req, timeout=5)
            except HTTPError as error:
                response = error
            with response:
                content = response.read()
                payload = json.loads(content) if content else None
                return [response.status, payload]

        def compare(paths):
            before = [(path.stat().st_mtime_ns, path.read_bytes()) for path in (file, empty, index)]
            oracle = expected(env, paths)
            for path, reply in zip(paths, oracle, strict=True):
                actual = api(path)
                assert actual == reply, (path, actual, reply)
            assert [(path.stat().st_mtime_ns, path.read_bytes()) for path in (file, empty, index)] == before
            return oracle

        token = (Path(env['DATA_DIR']) / 'host_api_token').read_text().strip()
        for unauthenticated in (api(PATH), api(PATH, token=token)):
            assert unauthenticated[0] == 401, unauthenticated
        status, challenge = api('/api/csrf-token')
        assert status == 200
        csrf = challenge['token']
        assert api('/host-login', {})[0] == 200
        csrf = api('/api/csrf-token')[1]['token']
        fixture_paths = [PATH, PATH + '?limit=1', PATH + '?limit=-1', PATH + '?limit=-9', PATH + '?limit=0',
                         PATH + '?limit=wrong&limit=1', PATH + '?limit=1&limit=0', PATH + '?limit=%2B1',
                         PATH + '?limit=-1_000', PATH + '?limit=' + '9' * 30,
                         PATH + '?workspace_id=other', PATH.replace(ID, ID.removeprefix('conv_')),
                         PATH.replace(ID, UNKNOWN), PATH.replace(ID, EMPTY)]
        idle = compare(fixture_paths)[0][1]['data']
        bootstrap = api(BOOTSTRAP)[1]['data']
        assert bootstrap['messages'] == idle['messages'] and not bootstrap['display']['messages']
        assert api(PATH.replace(ID, MISMATCHED))[0] == 404

        # The loopback model streams a partial assistant message and waits for the
        # test. At that point the persisted user admission already belongs to history.
        try:
            status, task = api('/api/tasks', {'conversation_id': ID, 'message': DURABLE})
            assert status == 202, task
            task_id = task.get('data', task)['task_id']

            def streamed():
                status, response = api(BOOTSTRAP)
                assert status == 200, response
                display = response['data']['display']
                return response['data'] if STREAMING in json.dumps(display['messages']) else None

            bootstrap = wait_for(streamed)
            assert bootstrap['running']['is_main_running']
            active = compare([PATH, PATH + '?limit=1'])[0][1]['data']
            assert active['messages'] == bootstrap['messages']
            assert [message['content'] for message in active['messages']] == ['stored image', 'stored reply', DURABLE]
            assert active['total_count'] == 3
        finally:
            upstream.finish.set()

        def terminal():
            status, response = api(f'/api/tasks/{task_id}')
            assert status == 200, response
            state = response['data']
            if state['status'] == 'running':
                return None
            assert state['status'] == 'succeeded', state
            return state

        wait_for(terminal)
        finished = compare([PATH, PATH + '?limit=2'])[0][1]['data']
        assert finished['messages'][-1]['content'] == STREAMING + FINISHED
        assert finished['total_count'] == 4
        assert not api(BOOTSTRAP)[1]['data']['running']['is_main_running']


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--oracle':
        python_oracle(sys.argv[2:])
    else:
        binary = Path(sys.argv[1]).resolve(strict=True)
        with tempfile.TemporaryDirectory(dir=HERE) as temporary, ThreadingHTTPServer(('127.0.0.1', 0), StagedModel) as upstream:
            upstream.finish = threading.Event()
            worker = threading.Thread(target=upstream.serve_forever, daemon=True)
            worker.start()
            try:
                check(binary, Path(temporary), upstream)
            finally:
                upstream.finish.set()
                upstream.shutdown()
                worker.join()
        print('PASS: Python and Rust Web message history agree during idle, streaming and completed states')
