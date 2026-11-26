"""Exercise Agent runtime guidance through the Rust production listeners.

Workspace-free Chat has a separate conversation tree and does not yet implement runtime guidance.
Usage: python3 -B http_contract.py /path/to/astrion-headless
"""

import argparse
from base64 import b64decode
from contextlib import contextmanager
from http.cookiejar import CookieJar
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener
from uuid import uuid4

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
A = 'guidance_alpha'
B = 'guidance_beta'
MODEL = 'guidance-model'
TOOL_PROMPT = 'Verify complete tool batches'
MEDIA_PROMPT = 'Wait for quick-entry guidance'
STOP_PROMPT = 'Stop before injecting guidance'
RESTART_PROMPT = 'Continue after restarting the backend'
HEADLESS_PROMPT = 'Continue the media conversation in headless mode'
TEXT = 'Please apply the clarification.'
FALLBACK = '请查看本次附带的附件。'
PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jVcgAAAAASUVORK5CYII='
DATA_URL = 'data:image/png;base64,' + PNG
QUEUE = 'runtime_message_queue'


def load_python_module(path):
    spec = importlib.util.spec_from_file_location(Path(path).stem, REPO / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ATTACHMENTS = load_python_module('server/attachment_paths.py')
PYTHON_QUEUE = load_python_module('server/tasks/queue_state.py')


def wait_for(check, label, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.03)
    raise AssertionError(f'Timed out waiting for {label}')


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


class Provider(BaseHTTPRequestHandler):
    def do_POST(self):
        stage = None
        try:
            assert self.path == '/v1/chat/completions', self.path
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            assert body['model'] == MODEL
            history = body['messages']
            prompt = next(item['content'] for item in reversed(history)
                          if item['role'] == 'user' and item['content'] in (
                              TOOL_PROMPT, MEDIA_PROMPT, STOP_PROMPT, RESTART_PROMPT, HEADLESS_PROMPT))
            stage = (prompt, sum(key[0] == prompt for key in self.server.requests))
            assert stage not in self.server.requests
            self.server.requests[stage] = history
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            release = self.server.release.get(stage)
            if release is not None:
                self.chunk({'content': 'First response'} if prompt != TOOL_PROMPT else {'content': 'Reading the files'})
                self.wfile.flush()
                assert release.wait(20), stage
            if stage == (TOOL_PROMPT, 0):
                calls = [{
                    'index': i, 'id': f'guidance-call-{i}', 'type': 'function',
                    'function': {'name': 'read_file', 'arguments': json.dumps({'path': path, 'type': 'read'})},
                } for i, path in enumerate(('first.md', 'second.md'))]
                self.chunk({'tool_calls': calls})
                self.chunk({}, 'tool_calls')
            else:
                if release is None:
                    self.chunk({'content': 'Final response'})
                self.chunk({}, 'stop')
            self.wfile.write(b'data: [DONE]\n\n')
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError) as exc:
            if stage != (STOP_PROMPT, 0):
                self.server.errors.append(repr(exc))
        except Exception as exc:
            self.server.errors.append(repr(exc))

    def chunk(self, delta, finish=None):
        data = json.dumps({'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]})
        self.wfile.write(f'data: {data}\n\n'.encode())

    def log_message(self, *args):
        pass


@contextmanager
def provider():
    with ThreadingHTTPServer(('127.0.0.1', 0), Provider) as server:
        server.requests = {}
        server.errors = []
        server.release = {(prompt, 0): threading.Event()
                          for prompt in (TOOL_PROMPT, MEDIA_PROMPT, STOP_PROMPT, RESTART_PROMPT, HEADLESS_PROMPT)}
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server
        finally:
            for release in server.release.values():
                release.set()
            server.shutdown()
            thread.join()


class Client:
    def __init__(self, origin, *, token=None, cookie=False):
        self.origin = origin
        self.token = token
        self.csrf = None
        self.opener = build_opener(ProxyHandler({}), *([HTTPCookieProcessor(CookieJar())] if cookie else []))

    def request(self, path, data=None, *, expected=200, workspace=A, csrf=True, origin=None):
        headers = {'Origin': origin or self.origin, 'X-Astrion-Workspace-Id': workspace}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        if data is not None:
            headers['Content-Type'] = 'application/json'
            if csrf and self.csrf:
                headers['X-CSRF-Token'] = self.csrf
            data = json.dumps(data).encode()
        try:
            response = self.opener.open(Request(self.origin + path, data=data, headers=headers), timeout=6)
        except HTTPError as error:
            response = error
        with response:
            raw = response.read()
            assert response.status == expected, (path, expected, response.status, raw)
            return json.loads(raw)

    def login(self):
        self.csrf = self.request('/api/csrf-token')['token']
        self.request('/host-login', {})
        self.csrf = self.request('/api/csrf-token')['token']

    def create(self):
        return self.request('/api/runtime/sessions', {'workspace_id': A, 'model_key': MODEL})['conversation_id']

    def start(self, cid, prompt):
        return self.request('/api/tasks', {'conversation_id': cid, 'message': prompt, 'model_key': MODEL},
                            expected=202)['data']['task_id']

    def guidance(self, task, data, **options):
        return self.request(f'/api/tasks/{task}/runtime_guidance', data, **options)

    def terminal(self, task, expected):
        def check():
            data = self.request(f'/api/tasks/{task}?from=0')['data']
            if data['status'] == 'running':
                return None
            assert data['status'] == expected, data
            return data
        return wait_for(check, expected + ' terminal state')

    def queue(self, cid):
        return self.request(f'/api/conversations/{cid}/bootstrap')['data']['runtime_queue']


class Fixture:
    def __init__(self, root, upstream):
        self.root = root
        self.config = root / 'config'
        self.data = root / 'runtime/host/data'
        self.static = root / 'resources/static'
        for path in (self.config, self.data, self.static, root / 'resources/workflows', root / A, root / B, root / 'tmp'):
            path.mkdir(parents=True)
        registry = self.config / 'host_workspaces.json'
        write_json(registry, {'default_workspace_id': A, 'workspaces': [
            {'workspace_id': workspace, 'path': str(root / workspace), 'label': workspace} for workspace in (A, B)
        ]})
        write_json(self.config / 'custom_models.json', [
            {'model_name': MODEL, 'model_id': MODEL,
             'url': f'http://127.0.0.1:{upstream.server_port}/v1', 'apikey': 'fixture-provider-key'},
        ])
        for filename in ('first.md', 'second.md'):
            (root / A / filename).write_text(filename + ' contents', encoding='utf-8')
        (root / A / 'diagram.png').write_bytes(b64decode(PNG))
        self.env = {key: os.environ[key] for key in ('PATH', 'SystemRoot', 'WINDIR') if key in os.environ}
        self.env.update(
            ASTRION_IGNORE_DOTENV='1', ASTRION_DESKTOP_STOP_STDIN='1', ASTRION_DATA_ROOT=str(root / 'runtime'),
            ASTRION_RESOURCE_ROOT=str(self.static.parent), HOST_WORKSPACES_FILE=str(registry),
            DEPLOY_CONFIG_DIR=str(self.config), LOGS_DIR=str(root / 'logs'),
            TMPDIR=str(root / 'tmp'), TEMP=str(root / 'tmp'), TMP=str(root / 'tmp'),
        )

    def path(self, cid):
        return self.data / 'conversations' / A / f'{cid}.json'

    def document(self, cid):
        return json.loads(self.path(cid).read_bytes())

    @contextmanager
    def backend(self, binary, *, web, log_name):
        args = [str(binary), '--port', '0', '--workspace', str(self.root / A), '--data-dir', str(self.data)]
        if web:
            args += ['--web', '--static-root', str(self.static)]
        log = self.root / log_name
        with log.open('wb') as log_file:
            process = subprocess.Popen(args, cwd=self.root, env=self.env, stdin=subprocess.PIPE,
                                       stdout=log_file, stderr=subprocess.STDOUT)
        try:
            def ready():
                text = log.read_text(encoding='utf-8')
                assert process.poll() is None, text
                match = re.search(r'Astrion Rust server listening on (127\.0\.0\.1:\d+)', text)
                return 'http://' + match[1] if match else None
            origin = wait_for(ready, 'Rust listener')
            bearer = Client(origin, token=(self.data / 'host_api_token').read_text().strip())
            yield origin, bearer
        finally:
            process.stdin.close()
            try:
                status = process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
                raise AssertionError(f'Backend failed to exit after stdin EOF\n{log.read_text()}')
            assert status == 0, log.read_text()


def provider_request(upstream, prompt, number):
    return wait_for(lambda: upstream.requests.get((prompt, number)), (prompt, number))


def guidance_events(terminal, durable):
    events = [e['data'] for e in terminal['events'] if e['type'] == 'user_message' and e['data'].get('runtime_guidance')]
    assert [e['message_id'] for e in events] == [m['message_id'] for m in durable], (events, durable)
    for event, message in zip(events, durable):
        assert event['content'] == message['content']
        assert event['metadata'] == message['metadata']
        assert event['task_id'] == terminal['task_id'] and event['conversation_id'] == terminal['conversation_id']
    return events


def tool_boundary(fixture, upstream, origin, bearer, browser):
    cid = bearer.create()
    task = bearer.start(cid, TOOL_PROMPT)
    provider_request(upstream, TOOL_PROMPT, 0)
    before = fixture.path(cid).read_bytes()
    payload = {'message': '  ' + TEXT + '  ', 'conversation_id': cid,
               'images': [{'path': 'video.mp4', 'fps': 1}], 'videos': ['diagram.png'],
               'files': [' first.md ', 'first.md', 12, 'second.md'], 'media_paths_only': True}
    anon = Client(origin, cookie=True)
    anon.guidance(task, payload, expected=403)
    anon.csrf = anon.request('/api/csrf-token')['token']
    anon.guidance(task, payload, expected=401)
    browser.guidance(task, payload, expected=403, csrf=False)
    browser.guidance(task, payload, expected=403, origin='http://untrusted.invalid')
    browser.token = 'invalid-bearer-token'
    browser.guidance(task, payload, expected=401)
    browser.token = None
    bearer.guidance(task, payload, expected=404, workspace=B)
    bearer.guidance('unknown-guidance-task', payload, expected=404)
    bearer.guidance(task, {'message': ' '}, expected=400)
    bearer.guidance(task, {**payload, 'conversation_id': 'conv_wrong'}, expected=409)
    assert fixture.path(cid).read_bytes() == before

    result = browser.guidance(task, payload)
    assert result == {'success': True, 'data': {'task_id': task, 'queued_count': 1}}
    for count in range(2, 6):
        result = bearer.guidance(task, {'message': f'Queued guidance {count}'}, csrf=False)
        assert result['data']['queued_count'] == count
    full = fixture.path(cid).read_bytes()
    bearer.guidance(task, {'message': 'overflow'}, expected=409)
    assert fixture.path(cid).read_bytes() == full
    accepted = fixture.document(cid)
    assert len(accepted['messages']) == 1
    pending = accepted['metadata'][QUEUE]
    assert browser.queue(cid) == {'messages': [], 'paused': False}
    assert len(pending['guidance']) == 5 and not pending['pending']
    assert pending['guidance'][0]['files'] == ['first.md', 'second.md']
    assert pending['guidance'][0]['images'] == ['diagram.png']
    assert pending['guidance'][0]['videos'] == [{'path': 'video.mp4', 'fps': 1}]

    upstream.release[(TOOL_PROMPT, 0)].set()
    wire = provider_request(upstream, TOOL_PROMPT, 1)[1:]
    assert [message['role'] for message in wire] == ['user', 'assistant', 'tool', 'tool'] + ['user'] * 7
    assert [wire[i]['tool_call_id'] for i in [2, 3]] == ['guidance-call-0', 'guidance-call-1']
    assert [call['id'] for call in wire[1]['tool_calls']] == ['guidance-call-0', 'guidance-call-1']
    assert all(isinstance(message['content'], str) for message in wire)
    expected = [f'[系统通知|guidance]\n{TEXT}', ATTACHMENTS.build_media_paths_message(
        ['diagram.png'], [{'path': 'video.mp4', 'fps': 1}], media_paths_only=True),
        ATTACHMENTS.build_file_paths_message(['first.md', 'second.md']),
        *(f'[系统通知|guidance]\nQueued guidance {i}' for i in range(2, 6))]
    assert [message['content'] for message in wire[4:]] == expected
    terminal = bearer.terminal(task, 'succeeded')
    durable = fixture.document(cid)['messages'][4:-1]
    assert [m['content'] for m in durable] == expected
    assert [m['metadata']['visibility'] for m in durable] == ['chat', 'hidden', 'hidden'] + ['chat'] * 4
    assert durable[0]['images'] == ['diagram.png'] and durable[0]['metadata']['media_paths_only'] is True
    assert durable[0]['media_refs'][0]['source_path'] == 'diagram.png'
    guidance_events(terminal, durable)
    actions = [i for i, event in enumerate(terminal['events']) if event['type'] == 'update_action']
    first_guide = next(i for i, event in enumerate(terminal['events'])
                       if event['type'] == 'user_message' and event['data'].get('runtime_guidance'))
    assert len(actions) == 2 and max(actions) < first_guide
    assert terminal['runtime_queued_messages'] == [] and terminal['runtime_queue_paused'] is False
    assert fixture.document(cid)['metadata'][QUEUE]['guidance'] == []
    assert browser.queue(cid) == {'messages': [], 'paused': False}
    before = fixture.path(cid).read_bytes()
    bearer.guidance(task, payload, expected=409)
    assert fixture.path(cid).read_bytes() == before


def media_boundary(fixture, upstream, bearer):
    cid = bearer.create()
    task = bearer.start(cid, MEDIA_PROMPT)
    provider_request(upstream, MEDIA_PROMPT, 0)
    bearer.guidance(task, {'images': [{'data_url': DATA_URL, 'kind': 'image'}]})
    upstream.release[(MEDIA_PROMPT, 0)].set()
    wire = provider_request(upstream, MEDIA_PROMPT, 1)[1:]
    assert [message['role'] for message in wire] == ['user', 'assistant', 'user'], wire
    assert wire[1]['content'] == 'First response'
    assert wire[2]['content'] == [
        {'type': 'text', 'text': '[系统通知|guidance]\n' + FALLBACK},
        {'type': 'image_url', 'image_url': {'url': DATA_URL}},
    ]
    terminal = bearer.terminal(task, 'succeeded')
    durable = fixture.document(cid)['messages'][2:-1]
    assert len(durable) == 1 and durable[0]['content'] == '[系统通知|guidance]\n' + FALLBACK
    ref = durable[0]['media_refs'][0]
    assert ref['kind'] == 'image' and ref['mime_type'] == 'image/png'
    assert (fixture.data / 'conversations' / ref['store_path']).is_file()
    index = json.loads((fixture.data / 'conversations/media_store/index.json').read_bytes())
    assert index['message_map'][cid][durable[0]['message_id']]['media_ids'] == [ref['media_id']]
    guidance_events(terminal, durable)
    return cid


def recovery(fixture, upstream, bearer, browser):
    cid = bearer.create()
    document = fixture.document(cid)
    document['metadata'][QUEUE] = {'pending': [], 'guidance': ['inherited before cancel'], 'paused': True}
    write_json(fixture.path(cid), document)
    task = bearer.start(cid, STOP_PROMPT)
    provider_request(upstream, STOP_PROMPT, 0)
    result = bearer.guidance(task, {'files': ['first.md'], 'media_paths_only': True})
    assert result['data']['queued_count'] == 1
    assert browser.queue(cid) == {'messages': [], 'paused': True}
    before = fixture.document(cid)['metadata'][QUEUE]
    bearer.request(f'/api/tasks/{task}/cancel', {})
    terminal = bearer.terminal(task, 'stopped')
    bearer.guidance(task, {'message': 'after cancel'}, expected=409)
    upstream.release[(STOP_PROMPT, 0)].set()
    expected = PYTHON_QUEUE.guidance_to_pending(before['guidance'])
    assert terminal['runtime_queued_messages'] == expected and terminal['runtime_queue_paused'] is True
    assert browser.queue(cid) == {'messages': expected, 'paused': True}
    document = fixture.document(cid)
    assert document['metadata'][QUEUE] == {'pending': expected, 'guidance': [], 'paused': True}
    assert all(not message.get('metadata', {}).get('runtime_guidance') for message in document['messages'])
    assert (STOP_PROMPT, 1) not in upstream.requests
    return cid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('binary', type=Path)
    binary = parser.parse_args().binary.resolve(strict=True)
    with tempfile.TemporaryDirectory(dir=HERE, prefix='guidance-fixture-') as temp, provider() as upstream:
        fixture = Fixture(Path(temp), upstream)
        with fixture.backend(binary, web=True, log_name='web.log') as (origin, bearer):
            browser = Client(origin, cookie=True)
            browser.login()
            tool_boundary(fixture, upstream, origin, bearer, browser)
            media_cid = media_boundary(fixture, upstream, bearer)
            cid = recovery(fixture, upstream, bearer, browser)
        # Simulate a Python-created durable queue left by an interrupted process.
        document = fixture.document(cid)
        pending = document['metadata'][QUEUE]
        pending['guidance'] = ['old guidance text', {'text': 'old attachment', 'files': ['second.md']}]
        recoverable = pending['pending']
        expected = recoverable + PYTHON_QUEUE.guidance_to_pending(pending['guidance'])
        write_json(fixture.path(cid), document)
        with fixture.backend(binary, web=True, log_name='web-restart.log') as (origin, bearer):
            browser = Client(origin, cookie=True)
            browser.login()
            assert browser.queue(cid) == {'messages': expected, 'paused': True}
            task = bearer.start(cid, RESTART_PROMPT)
            provider_request(upstream, RESTART_PROMPT, 0)
            assert browser.queue(cid) == {'messages': recoverable, 'paused': True}
            current = 'Guidance submitted after restart'
            assert bearer.guidance(task, {'message': current})['data']['queued_count'] == 1
            assert browser.queue(cid) == {'messages': recoverable, 'paused': True}
            upstream.release[(RESTART_PROMPT, 0)].set()
            wire = provider_request(upstream, RESTART_PROMPT, 1)
            assert all('old guidance' not in str(message['content']) and 'old attachment' not in str(message['content'])
                       for message in wire)
            assert wire[-1]['content'] == '[系统通知|guidance]\n' + current
            terminal = bearer.terminal(task, 'succeeded')
            assert terminal['runtime_queued_messages'] == expected and terminal['runtime_queue_paused'] is False
            assert browser.queue(cid) == {'messages': expected, 'paused': False}
            document = fixture.document(cid)
            assert [message['content'] for message in document['messages']].count('[系统通知|guidance]\n' + current) == 1
            assert document['metadata'][QUEUE] == {'pending': expected, 'guidance': [], 'paused': False}
        with fixture.backend(binary, web=False, log_name='headless.log') as (origin, bearer):
            task = bearer.start(media_cid, HEADLESS_PROMPT)
            wire = provider_request(upstream, HEADLESS_PROMPT, 0)
            assert any(isinstance(item['content'], list) and
                       {'type': 'image_url', 'image_url': {'url': DATA_URL}} in item['content'] for item in wire)
            Client(origin).guidance(task, {'message': 'unsigned'}, expected=401)
            bearer.guidance('unknown-task', {'message': 'signed'}, expected=404)
            assert bearer.guidance(task, {'message': 'Headless clarification'})['data']['queued_count'] == 1
            upstream.release[(HEADLESS_PROMPT, 0)].set()
            wire = provider_request(upstream, HEADLESS_PROMPT, 1)
            assert wire[-1]['content'] == '[系统通知|guidance]\nHeadless clarification'
            bearer.terminal(task, 'succeeded')
        assert not upstream.errors, upstream.errors
        assert len(upstream.requests) == 9, list(upstream.requests)
        assert fixture.document(media_cid)['messages'][2]['media_refs'][0]['kind'] == 'image'
    print('Rust guidance HTTP contract passed on Web and headless listeners')


if __name__ == '__main__':
    main()
