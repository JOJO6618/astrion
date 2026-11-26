"""Exercise the CrossPet writer through the actual Rust Web and headless listeners.

Usage: python3 -B http_contract.py /path/to/astrion-headless
"""

import argparse
from contextlib import contextmanager
from hashlib import sha256
from http.cookiejar import CookieJar
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener
from uuid import uuid4

HERE = Path(__file__).resolve().parent
SETTINGS = '/api/crosspet'
TABS = '/api/conversation-tabs/commands'
FILES = ('astrion-state.json', 'astrion-host.json', 'seen-astrion')
A, B = 'private_workspace_alpha', 'private_workspace_beta'
MODELS = {A: ('private-connection-alpha', 'gpt-crosspet-fixture', 'gpt'),
          B: ('private-connection-beta', 'kimi-crosspet-fixture', 'kimi')}
UPSTREAM_SECRET = 'synthetic-local-provider-secret'
PROMPT_WRITE = 'private prompt alpha: exercise the approval bridge'
PROMPT_STOP = 'private prompt beta: hold until canceled'
PROMPT_DISABLED = 'private prompt beta: task after disabling export'
PROMPT_HEADLESS = 'private prompt alpha: real headless observation'
ANSWER = 'private model completion: fixture reply'
TOOL_ID = 'private-tool-call-1234'
TOOL_FILE = 'private-tool-argument-a419.txt'
TOOL_CONTENT = 'private result and tool content 9245'


def wait_for(label, check, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.035)
    raise AssertionError(f'Timed out waiting for {label}')


class Model(BaseHTTPRequestHandler):
    def do_POST(self):
        started = False
        stage = None
        try:
            assert self.path == '/v1/chat/completions', self.path
            assert self.headers.get('Authorization') == f'Bearer {UPSTREAM_SECRET}'
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            messages = request['messages']
            prompt = next(message['content'] for message in reversed(messages) if message['role'] == 'user')
            workspace = {PROMPT_WRITE: A, PROMPT_STOP: B, PROMPT_DISABLED: B, PROMPT_HEADLESS: A}[prompt]
            assert request['model'] == MODELS[workspace][1]
            tools = [message for message in messages if message['role'] == 'tool']
            stage = ('after_write' if tools else 'before_write') if prompt == PROMPT_WRITE else prompt
            assert stage not in self.server.seen, stage
            if stage == 'before_write':
                assert any(tool['function']['name'] == 'write_file' for tool in request['tools'])
            if stage == 'after_write':
                assert tools[-1]['tool_call_id'] == TOOL_ID
                assert json.loads(tools[-1]['content'])['success'] is True
            self.server.seen.add(stage)
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            started = True
            block = self.server.release.get(stage)
            if block is not None:
                assert block.wait(25), f'Provider stage {stage!r} was never released'
            if stage == 'before_write':
                self.chunk({'tool_calls': [{
                    'index': 0, 'id': TOOL_ID, 'type': 'function',
                    'function': {'name': 'write_file', 'arguments': json.dumps({
                        'file_path': TOOL_FILE, 'content': TOOL_CONTENT,
                    })},
                }]})
                self.chunk({}, 'tool_calls')
            else:
                self.chunk({'content': ANSWER})
                self.chunk({}, 'stop')
            self.wfile.write(b'data: [DONE]\n\n')
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError) as error:
            if stage != PROMPT_STOP:
                self.server.errors.append(repr(error))
        except Exception as error:
            self.server.errors.append(repr(error))
            if not started:
                self.send_error(500, 'Fixture provider rejected the request')

    def chunk(self, delta, finish=None):
        event = {'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}
        self.wfile.write(('data: ' + json.dumps(event) + '\n\n').encode())

    def log_message(self, *args):
        pass


@contextmanager
def upstream():
    with ThreadingHTTPServer(('127.0.0.1', 0), Model) as server:
        server.release = {stage: threading.Event() for stage in ('before_write', 'after_write', PROMPT_STOP, PROMPT_HEADLESS)}
        server.errors = []
        server.seen = set()
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            yield server
        finally:
            for release in server.release.values():
                release.set()
            server.shutdown()
            worker.join()


def write_json(path, value):
    path.write_text(json.dumps(value), encoding='utf-8')


class Fixture:
    def __init__(self, root, model):
        self.root = root
        self.data = root / 'runtime/host/data'
        self.config = root / 'deployment'
        self.resources = root / 'resources'
        self.output = root / 'crosspet-state'
        self.pet_config = root / 'pet/astrion.json'
        for directory in (self.data, self.config, self.resources / 'static', self.resources / 'workflows',
                          self.pet_config.parent, root / 'tmp', root / A, root / B):
            directory.mkdir(parents=True)
        self.preference = self.config / 'crosspet.json'
        self.ignored_output = root / 'crosspet-nonselected-state'
        write_json(self.pet_config, {'version': 1, 'enabled': False, 'state_dir': str(self.ignored_output)})
        registry = self.config / 'host_workspaces.json'
        write_json(registry, {'default_workspace_id': A, 'workspaces': [
            {'workspace_id': key, 'path': str(root / key), 'label': f'Fixture {key}'} for key in (A, B)
        ]})
        write_json(self.config / 'custom_models.json', [
            {'model_name': key, 'model_id': name, 'url': f'http://127.0.0.1:{model.server_port}/v1',
             'apikey': UPSTREAM_SECRET}
            for key, name, _ in MODELS.values()
        ])
        write_json(self.data / 'personalization.json', {'default_permission_mode': 'approval', 'default_work_mode': 'execute'})
        # Pass only the fixture paths and base OS variables to the backend.
        self.env = {key: os.environ[key] for key in ('PATH', 'SystemRoot', 'WINDIR') if key in os.environ}
        self.env.update(
            ASTRION_IGNORE_DOTENV='1', ASTRION_DESKTOP_STOP_STDIN='1',
            ASTRION_RESOURCE_ROOT=str(self.resources), ASTRION_DATA_ROOT=str(root / 'runtime'),
            HOST_WORKSPACES_FILE=str(registry), DEPLOY_CONFIG_DIR=str(self.config), LOGS_DIR=str(root / 'logs'),
            ASTRION_CROSSPET_CONFIG=str(self.pet_config), CROSSPET_STATE_DIR=str(self.output),
            TMPDIR=str(root / 'tmp'), TEMP=str(root / 'tmp'), TMP=str(root / 'tmp'),
        )
        self.forbidden = [str(root), A, B, UPSTREAM_SECRET, TOOL_ID, TOOL_FILE, TOOL_CONTENT,
                          ANSWER, PROMPT_WRITE, PROMPT_STOP, PROMPT_DISABLED, PROMPT_HEADLESS,
                          *(key for key, _, _ in MODELS.values())]

    def triplet(self):
        return tuple(((self.output / name).stat().st_mtime_ns, (self.output / name).read_bytes()) for name in FILES)

    def read_snapshot(self):
        try:
            state_path = self.output / FILES[0]
            raw = state_path.read_bytes()
            foreground_raw = (self.output / FILES[1]).read_bytes()
            heartbeat_raw = (self.output / FILES[2]).read_bytes()
            if raw != state_path.read_bytes():
                return None
        except FileNotFoundError:
            return None
        assert len(raw) <= 1024 * 1024
        snapshot = json.loads(raw)
        foreground = json.loads(foreground_raw)
        heartbeat = int(heartbeat_raw)
        # The individual files are atomic; the three replacements are ordered.
        if foreground != snapshot['foreground'] or heartbeat != int(snapshot['ts']):
            return None
        assert snapshot['source'] == 'astrion' and snapshot['version'] == 1
        assert re.fullmatch(r'[0-9a-f]{32}', snapshot['instance'])
        assert snapshot['sequence'] > 0 and len(snapshot['sessions']) <= 256
        assert abs(heartbeat - time.time()) < 30
        assert set(snapshot) <= {'source', 'version', 'instance', 'sequence', 'ts', 'foreground', 'sessions', 'quota'}
        assert set(foreground) == {'session', 'model', 'character', 'ts'}
        assert not foreground['session'] or re.fullmatch(r'[0-9a-f]{32}', foreground['session'])
        for identity, row in snapshot['sessions'].items():
            assert re.fullmatch(r'[0-9a-f]{32}', identity)
            assert set(row) <= {'character', 'model', 'pose', 'event', 'tool', 'ts', 'event_ts', 'revision',
                                'active', 'tools', 'after', 'afterTs'}
            assert row['revision'] >= 1 and row['event_ts'] <= row['ts'] and row['tools'] >= 0
        quota = snapshot.get('quota', {}).get('gpt')
        assert quota is None or (quota['connected'] is False and not {'epoch', 'at', 'rateLimits'} & quota.keys())
        for secret in self.forbidden:
            safe = all(secret.encode() not in value for value in (raw, foreground_raw, heartbeat_raw))
            assert safe, 'CrossPet output contains private fixture data'
        return snapshot

    def observe(self, label, condition):
        last = None

        def check():
            nonlocal last
            current = self.read_snapshot()
            if current is not None:
                last = current
            return current if current is not None and condition(current) else None

        try:
            return wait_for(label, check)
        except AssertionError as error:
            raise AssertionError(f'{error}. Last snapshot: {last}') from error

    def no_outputs(self):
        assert not self.output.exists() and not self.ignored_output.exists()

    def set_export(self, client, enabled):
        expected = {'success': True, 'supported': True, 'enabled': enabled}
        assert client.api(SETTINGS, {'enabled': enabled}) == expected
        assert client.api(SETTINGS, workspace=B) == expected
        assert json.loads(self.preference.read_bytes()) == {'version': 1, 'enabled': enabled}
        assert not self.ignored_output.exists()


class Client:
    def __init__(self, origin, *, browser=False, bearer=None):
        self.origin = origin
        self.opener = build_opener(ProxyHandler({}), *([HTTPCookieProcessor(CookieJar())] if browser else []))
        self.bearer = bearer
        self.csrf = None

    def api(self, path, payload=None, *, expected=200, workspace=None, send_csrf=True, method=None, origin=None):
        headers = {'Origin': origin or self.origin}
        if self.bearer:
            headers['Authorization'] = f'Bearer {self.bearer}'
        if workspace:
            headers['X-Astrion-Workspace-Id'] = workspace
        data = None
        if payload is not None:
            headers['Content-Type'] = 'application/json'
            if send_csrf and self.csrf:
                headers['X-CSRF-Token'] = self.csrf
            data = json.dumps(payload).encode()
        request = Request(self.origin + path, data=data, headers=headers, method=method)
        try:
            response = self.opener.open(request, timeout=4)
        except HTTPError as error:
            response = error
        with response:
            raw = response.read()
            assert response.status == expected, (path, method, response.status, expected, raw)
            return json.loads(raw) if raw else None

    def challenge(self):
        self.csrf = self.api('/api/csrf-token')['token']

    def start(self, workspace, conversation, prompt):
        payload = {'conversation_id': conversation, 'message': prompt, 'model_key': MODELS[workspace][0]}
        result = self.api('/api/tasks', payload, workspace=workspace, expected=202)
        return result['data']['task_id']

    def task(self, workspace, task):
        return self.api(f'/api/tasks/{task}?from=0', workspace=workspace)['data']

    def terminal(self, workspace, task, status):
        def check():
            result = self.task(workspace, task)
            if result['status'] == 'running':
                return None
            assert result['status'] == status, result
            return result

        return wait_for(f'task terminal status {status}', check)

    def navigate(self, action, expected=200, **payload):
        return self.api(TABS, {'command_id': uuid4().hex, 'action': action, 'payload': payload}, expected=expected)


@contextmanager
def backend(binary, fixture, *, web):
    args = [str(binary), '--port', '0', '--workspace', str(fixture.root / A), '--data-dir', str(fixture.data)]
    if web:
        args += ['--web', '--static-root', str(fixture.resources / 'static')]
    log = fixture.root / ('web.log' if web else 'headless.log')
    with log.open('wb') as output:
        process = subprocess.Popen(args, cwd=fixture.root, env=fixture.env, stdin=subprocess.PIPE,
                                   stdout=output, stderr=subprocess.STDOUT)
    try:
        def ready():
            text = log.read_text(encoding='utf-8')
            assert process.poll() is None, text
            address = re.search(r'Astrion Rust server listening on (127\.0\.0\.1:\d+)', text)
            return f'http://{address.group(1)}' if address else None

        yield wait_for('the Rust loopback listener address', ready)
    finally:
        process.stdin.close()
        try:
            status = process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
            raise AssertionError(f'fixture backend did not exit after stdin EOF\n{log.read_text()}')
        assert status == 0, log.read_text()


def session_key(workspace, conversation):
    source = json.dumps(['host', workspace, conversation.removeprefix('conv_')])
    return sha256(source.encode()).hexdigest()[:32]


def foreground(snapshot, identity, workspace):
    _, model, character = MODELS[workspace]
    focus = snapshot['foreground']
    return (focus['session'], focus['model'], focus['character']) == (identity, model, character)


def pose(snapshot, identity, name, event, active, tools):
    row = snapshot['sessions'].get(identity, {})
    return (row.get('pose'), row.get('event'), row.get('active'), row.get('tools')) == (name, event, active, tools)


def web_contract(binary, fixture, model):
    with backend(binary, fixture, web=True) as origin:
        token = (fixture.data / 'host_api_token').read_text().strip()
        assert token
        fixture.forbidden.append(token)
        bearer = Client(origin, bearer=token)
        anonymous = Client(origin)
        browser = Client(origin, browser=True)
        anonymous.api(SETTINGS, expected=401)
        bearer.api(SETTINGS, expected=401)
        preauth_bearer = Client(origin, browser=True, bearer=token)
        preauth_bearer.challenge()
        preauth_bearer.api(SETTINGS, {'enabled': True}, expected=401)
        browser.challenge()
        prior_csrf = browser.csrf
        browser.api('/host-login', {})
        browser.challenge()
        assert browser.csrf != prior_csrf
        browser.api(SETTINGS, {'enabled': True}, expected=403, send_csrf=False)
        browser.api(SETTINGS, {'enabled': True}, expected=403, origin='http://untrusted.invalid')
        browser.api(SETTINGS, {'enabled': 'true'}, expected=400)
        assert browser.api(SETTINGS) == {'success': True, 'supported': True, 'enabled': False}
        assert not fixture.preference.exists()
        fixture.no_outputs()
        fixture.set_export(browser, True)
        first = fixture.observe('the initial empty protocol files', lambda current: not current['sessions'])

        conversations = {
            workspace: bearer.api('/api/runtime/sessions', {'workspace_id': workspace, 'model_key': MODELS[workspace][0]},
                                  workspace=workspace)['conversation_id']
            for workspace in (A, B)
        }
        fixture.forbidden.extend(conversations.values())
        identities = {workspace: session_key(workspace, conv) for workspace, conv in conversations.items()}
        assert identities[A] != identities[B]
        browser.navigate('open', expected=409, workspace_id=B, conversation_id=conversations[A])
        tab_a = browser.navigate('open', workspace_id=A, conversation_id=conversations[A])['result']['key']
        fixture.observe('workspace A model to be focused', lambda current: foreground(current, identities[A], A))
        tab_b = browser.navigate('open', workspace_id=B, conversation_id=conversations[B])['result']['key']
        fixture.observe('workspace B model to be focused', lambda current: foreground(current, identities[B], B))
        browser.navigate('focus', key=tab_a)
        fixture.observe('focus to move back to workspace A', lambda current: foreground(current, identities[A], A))

        task_a = browser.start(A, conversations[A], PROMPT_WRITE)
        fixture.forbidden.append(task_a)
        try:
            thinking = fixture.observe('active main model before tool use', lambda current: pose(
                current, identities[A], 'thinking', 'BeforeModel', True, 0))
        except AssertionError as error:
            state = browser.task(A, task_a)
            raise AssertionError(f"{error}. Task status: {state['status']}; error: {state['error']}") from error
        assert foreground(thinking, identities[A], A)
        model.release['before_write'].set()
        approval = wait_for('the real write approval', lambda: next(iter(browser.api(
            f'/api/tool-approvals/pending?conversation_id={conversations[A]}', workspace=A)['items']), None))
        assert approval['tool_name'] == 'write_file' and approval['task_id'] == task_a
        fixture.forbidden.append(approval['approval_id'])
        approval_path = f"/api/tool-approvals/{approval['approval_id']}/decision"
        assert not (fixture.root / A / TOOL_FILE).exists()
        waiting = fixture.observe('tool approval action', lambda current: pose(
            current, identities[A], 'asking', 'PermissionRequest', True, 1))
        assert waiting['sessions'][identities[A]]['tool'] == 'write_file'
        browser.navigate('focus', key=tab_b)
        switched = fixture.observe('workspace B focused while A remains waiting', lambda current: foreground(
            current, identities[B], B) and pose(current, identities[A], 'asking', 'PermissionRequest', True, 1))
        assert switched['sessions'][identities[A]]['revision'] == waiting['sessions'][identities[A]]['revision']
        browser.api(approval_path, {'decision': 'approved'}, workspace=B, expected=403)
        assert browser.api(approval_path, {'decision': 'approved'}, workspace=A)['item']['status'] == 'approved'
        wait_for('the approved workspace file write', lambda: (fixture.root / A / TOOL_FILE).is_file())
        assert (fixture.root / A / TOOL_FILE).read_text() == TOOL_CONTENT
        resumed = fixture.observe('model resumption after approval', lambda current: pose(
            current, identities[A], 'thinking', 'BeforeModel', True, 1))
        assert resumed['sessions'][identities[A]]['revision'] > waiting['sessions'][identities[A]]['revision']
        model.release['after_write'].set()
        successful_task = browser.terminal(A, task_a, 'succeeded')
        assert any(event['type'] == 'task_complete' for event in successful_task['events'])
        fixture.observe('the successful main task completion', lambda current: pose(
            current, identities[A], 'happy', 'Stop', False, 1))

        task_b = bearer.start(B, conversations[B], PROMPT_STOP)
        fixture.forbidden.append(task_b)
        fixture.observe('the other workspace task becomes active', lambda current: foreground(
            current, identities[B], B) and pose(current, identities[B], 'thinking', 'BeforeModel', True, 0))
        wait_for('the cancelable provider request to start', lambda: PROMPT_STOP in model.seen)
        bearer.api(f'/api/tasks/{task_b}/cancel', {}, workspace=B)
        bearer.terminal(B, task_b, 'stopped')
        model.release[PROMPT_STOP].set()
        stopped = fixture.observe('the canceled task and the retained successful session', lambda current: pose(
            current, identities[B], 'surprised', 'Stop', False, 0) and pose(current, identities[A], 'happy', 'Stop', False, 1))
        assert stopped['sequence'] > first['sequence'] and stopped['instance'] == first['instance']

        fixture.set_export(browser, False)
        disabled_at = time.monotonic()
        frozen = fixture.triplet()
        browser.navigate('focus', key=tab_a)
        quiet_task = bearer.start(B, conversations[B], PROMPT_DISABLED)
        fixture.forbidden.append(quiet_task)
        bearer.terminal(B, quiet_task, 'succeeded')
        # Finish a real task and span a full 10-second heartbeat plus its probe cadence.
        while time.monotonic() - disabled_at < 12.5:
            assert fixture.triplet() == frozen, 'CrossPet status changed after disable returned'
            time.sleep(0.15)
        assert fixture.triplet() == frozen
        assert set(path.name for path in fixture.output.iterdir()) == set(FILES)

        fixture.set_export(browser, True)
        browser.navigate('open', workspace_id=B, conversation_id=conversations[B])
        final = fixture.observe('export resumption after re-enable', lambda current: current['sequence'] > stopped['sequence']
                                and foreground(current, identities[B], B))
        assert final['instance'] == stopped['instance']
    return token, conversations[A], final['instance']


def headless_contract(binary, fixture, model, token, conversation, previous_instance):
    with backend(binary, fixture, web=False) as origin:
        anonymous = Client(origin)
        bearer = Client(origin, bearer=token)
        for client in (anonymous, bearer):
            client.api(SETTINGS, expected=401)
            client.api(SETTINGS, method='HEAD', expected=401)
            client.api(SETTINGS, {'enabled': False}, expected=403)
        assert json.loads(fixture.preference.read_bytes()) == {'version': 1, 'enabled': True}
        fresh = fixture.observe('headless snapshot from persisted opt-in', lambda current: current['instance'] != previous_instance)
        assert not fresh['sessions']
        task = bearer.start(A, conversation, PROMPT_HEADLESS)
        fixture.forbidden.append(task)
        identity = session_key(A, conversation)
        fixture.observe('the headless task starts', lambda current: pose(current, identity, 'thinking', 'BeforeModel', True, 0))
        model.release[PROMPT_HEADLESS].set()
        bearer.terminal(A, task, 'succeeded')
        finished = fixture.observe('the headless task finishes', lambda current: pose(current, identity, 'happy', 'Stop', False, 0))
        assert finished['instance'] == fresh['instance']
        assert set(path.name for path in fixture.output.iterdir()) == set(FILES)
        assert not fixture.ignored_output.exists()


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument('binary', type=Path)
    binary = args.parse_args().binary.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix='crosspet-http-', dir=HERE) as temporary, upstream() as model:
        fixture = Fixture(Path(temporary).resolve(), model)
        try:
            token, conversation, instance = web_contract(binary, fixture, model)
            headless_contract(binary, fixture, model, token, conversation, instance)
        finally:
            assert not model.errors, model.errors
        assert model.seen == {'before_write', 'after_write', PROMPT_STOP, PROMPT_DISABLED, PROMPT_HEADLESS}
    print('PASS: production Rust Web and headless CrossPet HTTP, navigation, task poses, three files and clean shutdown')
    print('Scope: disconnected quota only. Connected OAuth account selection and quota refresh are not exercised.')


if __name__ == '__main__':
    main()
