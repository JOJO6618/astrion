"""Exercise real Rust todo tool calls and workspace-scoped Web reads across restart.

Usage: python3.11 -B http_contract.py /path/to/astrion-headless
"""

from copy import deepcopy
from http.cookiejar import CookieJar
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / '2026-11-24_Rust后端'))
from agent_process_roundtrip import MODEL, ROOT, fixture_environment, rust

A, B = 'default', 'other'
IDS = {A: 'conv_20261126_120000_001', B: 'conv_20261126_120000_002'}
TODO = '/api/todo-list'
CREATE, UPDATE = 'todo_create', 'todo_update_task'
CATEGORY_DISABLED = 'todo-category-disabled'
CASES = {
    'first-turn': [
        (CREATE, {'overview': '  复核原对话记录  ', 'tasks': [
            {'title': ' 保留原消息 '}, {'title': ' '}, {'title': '核对重启后待办'}, {'title': ' 比对两个工作区 '},
        ]}),
        (UPDATE, {'task_indices': [2, 10], 'completed': True}),
        (UPDATE, {'task_index': 2, 'completed': True}),
        (UPDATE, {'task_indices': [3, 1, 3], 'completed': True}),
    ],
    'other-turn': [(UPDATE, {'task_index': 1, 'completed': True})],
    'restart-turn': [(UPDATE, {'task_indices': [2, 1, 2], 'completed': False})],
    CATEGORY_DISABLED: [],
}


def python_phase(folder, operation):
    """Use production TodoManager and the detached Python conversation reader."""
    sys.path.insert(0, str(ROOT))
    from modules.todo_manager import TodoManager
    from utils.conversation_manager import ConversationManager
    from utils.context_manager.todo_annotation_mixin import TodoAnnotationMixin

    class Context(TodoAnnotationMixin):
        def __init__(self):
            self.todo_list = None

        def set_todo_list(self, todo):
            self.todo_list = deepcopy(todo)

    contexts = {workspace: Context() for workspace in (A, B)}
    managers = {workspace: TodoManager(context) for workspace, context in contexts.items()}
    initial = {}
    for workspace, context in contexts.items():
        manager = managers[workspace]
        result = manager.create_todo_list(f'旧版待办 {workspace}', [
            {'title': f'{workspace} 原步骤一'}, {'title': f'{workspace} 原步骤二'},
        ])
        assert result['success']
        initial[workspace] = manager.get_snapshot()
    if operation == 'expected':
        expected = {}
        for case, calls in CASES.items():
            workspace = B if case == 'other-turn' else A
            manager = managers[workspace]
            context = contexts[workspace]
            stage_results = []
            for tool, args in calls:
                if tool == CREATE:
                    result = manager.create_todo_list(args['overview'], args['tasks'])
                else:
                    indices = args['task_indices'] if 'task_indices' in args else args['task_index']
                    result = manager.update_task_status(indices, args['completed'])
                stage_results.append({'result': result, 'saved': manager.get_snapshot(), 'live': context.get_todo_snapshot()})
            expected[case] = stage_results
        print('PYTHON=' + json.dumps({'initial': initial, 'cases': expected}, ensure_ascii=False))
    else:
        data = Path(os.environ['DATA_DIR'])
        current = {}
        for workspace, identifier in IDS.items():
            project = folder / ('workspace' if workspace == A else 'other-workspace')
            manager = ConversationManager(base_dir=data, project_path=project, read_only=True)
            document = manager.read_conversation(identifier)
            assert document is not None
            current[workspace] = document['todo_list']
        print('PYTHON=' + json.dumps(current, ensure_ascii=False))


def python_oracle(folder, env, operation):
    result = subprocess.run([sys.executable, '-B', __file__, '--python', str(folder), operation], cwd=ROOT,
                            env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(next(line.removeprefix('PYTHON=') for line in result.stdout.splitlines() if line.startswith('PYTHON=')))


def wait_for(label, check):
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.025)
    raise AssertionError(f'Timed out waiting for {label}')


def call_id(case, number):
    return f'call_todo_{case.replace("-", "_")}_{number}'


class Provider(BaseHTTPRequestHandler):
    def do_POST(self):
        started = False
        try:
            assert self.path == '/v1/chat/completions', self.path
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            assert request['model'] == MODEL
            messages = request['messages']
            case = next(message['content'] for message in reversed(messages) if message['role'] == 'user')
            offered = {tool['function']['name'] for tool in request['tools']}
            if case == CATEGORY_DISABLED:
                assert 'read_file' in offered, offered
                assert not ({CREATE, UPDATE} & offered), offered
            else:
                assert {CREATE, UPDATE} <= offered, offered
            calls = CASES[case]
            results = {message['tool_call_id']: message['content'] for message in messages if message['role'] == 'tool'}
            stage = sum(call_id(case, number) in results for number in range(len(calls)))
            phase = case, stage
            if stage:
                self.server.results[phase] = json.loads(results[call_id(case, stage - 1)])
            assert phase not in self.server.seen, phase
            self.server.seen.add(phase)
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            started = True
            if stage:
                assert self.server.releases[phase].wait(30), phase
            if stage < len(calls):
                name, arguments = calls[stage]
                self.chunk({'tool_calls': [
                    {'index': 0, 'id': call_id(case, stage), 'type': 'function',
                     'function': {'name': name, 'arguments': json.dumps(arguments, ensure_ascii=False)}},
                ]})
                self.chunk({}, 'tool_calls')
            else:
                self.chunk({'content': f'{case} done'})
                self.chunk({}, 'stop')
            self.wfile.write(b'data: [DONE]\n\n')
            self.wfile.flush()
        except Exception as error:
            self.server.errors.append(repr(error))
            if not started:
                self.send_error(500, 'Fixture provider rejected the request')

    def chunk(self, delta, finish=None):
        event = {'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}
        self.wfile.write(('data: ' + json.dumps(event) + '\n\n').encode())

    def log_message(self, *args):
        pass


class Browser:
    def __init__(self, origin):
        self.origin = origin
        self.opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))
        self.csrf = self.api('/api/csrf-token')['token']
        self.api('/host-login', {})
        self.csrf = self.api('/api/csrf-token')['token']

    def api(self, path, body=None, *, expected=200, workspace=None, anonymous=False):
        headers = {'Origin': self.origin, 'Content-Type': 'application/json'}
        if getattr(self, 'csrf', None):
            headers['X-CSRF-Token'] = self.csrf
        if workspace:
            headers['X-Astrion-Workspace-Id'] = workspace
        request = Request(self.origin + path, data=None if body is None else json.dumps(body).encode(), headers=headers)
        opener = build_opener(ProxyHandler({})) if anonymous else self.opener
        try:
            response = opener.open(request, timeout=4)
        except HTTPError as error:
            response = error
        with response:
            raw = response.read()
            assert response.status == expected, (path, response.status, expected, raw)
            return json.loads(raw)

    def todos(self, workspace, *, identifier=None, expected=200):
        query = urlencode({'conversation_id': identifier or IDS[workspace]})
        return self.api(f'{TODO}?{query}', expected=expected, workspace=workspace)

    def start(self, case, workspace):
        payload = {'conversation_id': IDS[workspace], 'message': case, 'model_key': MODEL}
        return self.api('/api/tasks', payload, workspace=workspace, expected=202)['data']['task_id']

    def poll(self, workspace, task):
        return self.api(f'/api/tasks/{task}?from=0', workspace=workspace)['data']


def seed(folder, env, initial):
    registry = Path(env['HOST_WORKSPACES_FILE'])
    catalog = json.loads(registry.read_text())
    other = folder / 'other-workspace'
    other.mkdir()
    catalog['workspaces'].append({'workspace_id': B, 'label': 'Other workspace', 'path': str(other)})
    registry.write_text(json.dumps(catalog), encoding='utf-8')
    files = {}
    original_messages = {}
    for workspace, identifier in IDS.items():
        root = Path(env['HOST_PROJECT_PATH']) if workspace == A else other
        file = Path(env['DATA_DIR']) / 'conversations' / workspace / f'{identifier}.json'
        file.parent.mkdir(parents=True)
        user = {'role': 'user', 'content': f'Python history {workspace}', 'message_id': f'msg_legacy_{workspace}',
                'metadata': {'future_note': True}}
        original_messages[workspace] = [user]
        document = {'id': identifier, 'title': f'原会话 {workspace}', 'created_at': '2025-07-01T12:00:00',
                    'updated_at': '2025-07-01T12:00:00', 'messages': [user], 'todo_list': initial[workspace],
                    'metadata': {'project_path': str(root), 'model_key': MODEL, 'future_key': [1, '保留'],
                                 'frozen_system_prompt': 'Frozen fixture prompt'},
                    'future_document_key': {'preserve': True}}
        file.write_text(json.dumps(document, ensure_ascii=False), encoding='utf-8')
        files[workspace] = file
    return files, original_messages


def read_todo(path):
    raw = json.loads(path.read_bytes())
    assert 'all_done' not in raw['todo_list'] and 'instruction' not in raw['todo_list']
    return raw['todo_list']


def todo_events(task):
    return [event for event in task['events'] if event['type'] == 'todo_updated']


def verify_stage(browser, provider, files, oracle, case, workspace, task, stage):
    phase = case, stage
    try:
        wait_for(f'{case} tool result {stage}', lambda: phase in provider.results)
    except AssertionError as error:
        state = browser.poll(workspace, task)
        raise AssertionError(f"{error}. Task status: {state['status']}; error: {state['error']}") from error
    expected = oracle[case][stage - 1]
    assert provider.results[phase] == expected['result'], (case, stage, provider.results[phase], expected['result'])
    assert read_todo(files[workspace]) == expected['saved']
    assert browser.todos(workspace) == {'success': True, 'data': expected['live']}
    poll = browser.poll(workspace, task)
    assert poll['status'] == 'running', poll
    events = todo_events(poll)
    successful = [step['live'] for step in oracle[case][:stage] if step['result']['success']]
    assert [event['data']['todo_list'] for event in events] == successful
    for event in events:
        data = event['data']
        assert (data['task_id'], data['workspace_id'], data['conversation_id']) == (task, workspace, IDS[workspace])
    indices = [event['idx'] for event in events]
    assert indices == sorted(set(indices)), indices
    starts = [event for event in poll['events'] if event['type'] == 'tool_start']
    assert [event['data']['name'] for event in starts] == [name for name, _ in CASES[case][:stage]]


def finish(browser, workspace, task):
    def check():
        state = browser.poll(workspace, task)
        if state['status'] == 'running':
            return None
        assert state['status'] == 'succeeded', state
        return state

    return wait_for('the task to finish', check)


def run(binary, folder, provider):
    env = fixture_environment(folder)
    env['ASTRION_RESOURCE_ROOT'] = str(ROOT)
    env['CROSSPET_STATE_DIR'] = str(folder / 'unused-crosspet')
    py = python_oracle(folder, env, 'expected')
    oracle = py['cases']
    files, original_messages = seed(folder, env, py['initial'])
    endpoint = f'http://127.0.0.1:{provider.server_port}/v1/chat/completions'
    with rust(env, binary, endpoint, folder / 'first.log', web=True) as origin:
        browser = Browser(origin)
        first_bytes = {workspace: path.read_bytes() for workspace, path in files.items()}
        assert browser.api(TODO, anonymous=True, expected=401)['success'] is False
        assert browser.api(TODO, workspace=A) == {'success': True, 'data': None}
        assert browser.todos(A, identifier='conv_unknown', expected=404)['success'] is False
        assert browser.todos(A, identifier=IDS[B], expected=404)['success'] is False
        assert browser.todos(B, identifier=IDS[A], expected=404)['success'] is False
        query = urlencode({'conversation_id': IDS[B], 'workspace_id': B})
        assert browser.api(f'{TODO}?{query}')['data'] == py['initial'][B]
        for workspace in (A, B):
            assert browser.todos(workspace)['data'] == py['initial'][workspace]
            assert files[workspace].read_bytes() == first_bytes[workspace]

        task_a = browser.start('first-turn', A)
        verify_stage(browser, provider, files, oracle, 'first-turn', A, task_a, 1)
        assert files[B].read_bytes() == first_bytes[B]
        task_b = browser.start('other-turn', B)
        verify_stage(browser, provider, files, oracle, 'other-turn', B, task_b, 1)
        assert browser.todos(A)['data'] == oracle['first-turn'][0]['live']
        provider.releases['other-turn', 1].set()
        finish(browser, B, task_b)
        provider.releases['first-turn', 1].set()
        for stage in range(2, len(CASES['first-turn']) + 1):
            verify_stage(browser, provider, files, oracle, 'first-turn', A, task_a, stage)
            provider.releases['first-turn', stage].set()
        finish(browser, A, task_a)
        durable = {A: oracle['first-turn'][-1]['saved'], B: oracle['other-turn'][-1]['saved']}
        for workspace in (A, B):
            assert browser.todos(workspace)['data'] == durable[workspace]
            raw = json.loads(files[workspace].read_bytes())
            assert raw['id'] == IDS[workspace] and raw['messages'][:1] == original_messages[workspace]
            assert raw['metadata']['future_key'] == [1, '保留'] and raw['future_document_key'] == {'preserve': True}
        assert python_oracle(folder, env, 'read') == durable
        prior = {workspace: (path.read_bytes(), path.stat().st_mtime_ns) for workspace, path in files.items()}

    with rust(env, binary, endpoint, folder / 'restart.log', web=True) as origin:
        browser = Browser(origin)
        for workspace in (A, B):
            assert browser.todos(workspace)['data'] == durable[workspace]
            assert (files[workspace].read_bytes(), files[workspace].stat().st_mtime_ns) == prior[workspace]
        task = browser.start('restart-turn', A)
        verify_stage(browser, provider, files, oracle, 'restart-turn', A, task, 1)
        provider.releases['restart-turn', 1].set()
        finish(browser, A, task)
        durable[A] = oracle['restart-turn'][-1]['saved']
        assert browser.todos(A)['data'] == durable[A]
        assert browser.todos(B)['data'] == durable[B]
        assert (files[B].read_bytes(), files[B].stat().st_mtime_ns) == prior[B]
        assert python_oracle(folder, env, 'read') == durable

        settings = browser.api('/api/personalization', {'disabled_tool_categories': ['todo']})
        assert settings['success'] and 'todo' in settings['data']['disabled_tool_categories']
        task = browser.start(CATEGORY_DISABLED, A)
        completed = finish(browser, A, task)
        assert (CATEGORY_DISABLED, 0) in provider.seen
        assert not todo_events(completed)
        assert browser.todos(A)['data'] == read_todo(files[A]) == durable[A]


def main():
    binary = Path(sys.argv[1]).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix='todo-http-', dir=HERE) as temporary, ThreadingHTTPServer(('127.0.0.1', 0), Provider) as provider:
        provider.results = {}
        provider.seen = set()
        provider.errors = []
        provider.releases = {(case, stage): threading.Event() for case, calls in CASES.items()
                             for stage in range(1, len(calls) + 1)}
        model = threading.Thread(target=provider.serve_forever, daemon=True)
        model.start()
        try:
            run(binary, Path(temporary).resolve(), provider)
        finally:
            for release in provider.releases.values():
                release.set()
            provider.shutdown()
            model.join()
            assert not provider.errors, provider.errors
        assert provider.seen == {(case, stage) for case, calls in CASES.items() for stage in range(len(calls) + 1)}
    print('PASS: production Rust todo tools, task events, Python-compatible storage, workspace-scoped reads and restart')


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--python':
        python_phase(Path(sys.argv[2]), sys.argv[3])
    else:
        main()
