"""Exercise retry / clean restart through the authenticated Agent and Chat Web APIs."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from collections import defaultdict
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / '2026-11-27_RustAgent工具对照'))
from knowledge_media_http import Browser, LocalServices, ROOT, call, fixture_environment, model_catalog_host

AGENT = 'Retry Agent fixture'
CHAT = 'Retry Chat fixture'
CANCEL = 'Cancel Chat during retry wait'
PERMANENT = 'Do not retry an unauthorized request'
PARTIAL = 'Uncommitted partial output'
DELEGATE = 'Delegate a retry fixture to a child'
CHILD = 'Child retry fixture task'
CHILD_ANSWER = 'Child recovered after a stream interruption'
REVIEWED = 'Exercise an auto-reviewed command'
REVIEW = 'Auto-approval review fixture'


def sse(text, *, complete):
    chunk = {'choices': [{'index': 0, 'delta': {'content': text},
                          'finish_reason': 'stop' if complete else None}]}
    suffix = 'data: [DONE]\n\n' if complete else ''
    return (f'data: {json.dumps(chunk)}\n\n' + suffix).encode()


class Services(LocalServices):
    def send_tool(self, name, identifier, arguments):
        tool = call(name, identifier, arguments)
        chunk = {'choices': [{'index': 0, 'delta': {'tool_calls': [tool]},
                              'finish_reason': 'tool_calls'}]}
        return self.write(200, 'text/event-stream', f'data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n'.encode())

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        messages = body['messages']
        tools = {tool['function']['name'] for tool in body.get('tools', [])}
        inputs = [m['content'] for m in messages if m['role'] == 'user' and isinstance(m['content'], str)]
        if 'approve_decision' in tools:
            user = REVIEW
        elif any(CHILD in value for value in inputs[:2]):
            user = CHILD
        else:
            user = next((value for value in reversed(inputs) if value in self.server.scenarios), '')
        if not user:
            self.server.errors.append(body)
            return self.write(400, 'application/json', b'{"error":"unexpected synthetic request"}')
        self.server.requests[user].append(messages)
        count = len(self.server.requests[user])
        if user == DELEGATE and count == 1:
            return self.send_tool('create_sub_agent', 'delegate-retry', {
                'name': 'retry-child', 'summary': 'Check retry handling', 'task': CHILD,
                'working_dir': '.', 'intelligence': 'same', 'access_level': 'workspace_write',
            })
        if user == REVIEWED and count == 1:
            return self.send_tool('run_command', 'reviewed-command', {
                'command': 'printf "approved\\n" >> retry-review.txt', 'timeout': 5,
            })
        if user in {CHILD, REVIEW} and count == 1:
            return self.write(200, 'text/event-stream', sse(PARTIAL, complete=False))
        if user == REVIEW:
            return self.send_tool('approve_decision', 'approve-retry', {
                'decision': 'approved', 'reason': 'The fixture only appends one line to a temporary workspace.',
            })
        if user == CHILD:
            return self.write(200, 'text/event-stream', sse(CHILD_ANSWER, complete=True))
        if user in {DELEGATE, REVIEWED}:
            return self.write(200, 'text/event-stream', sse('Fixture finished', complete=True))
        if user == PERMANENT:
            return self.write(401, 'application/json', b'{"error":"synthetic unauthorized"}')
        if user == AGENT and count == 1:
            return self.write(503, 'application/json', b'{"error":"synthetic API unavailable"}')
        if count == 1:
            return self.write(200, 'text/event-stream', sse(PARTIAL, complete=False))
        return self.write(200, 'text/event-stream', sse('Recovered synthetic answer', complete=True))


def until(browser, url, done, timeout=18):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = browser.api('GET', url).get('data')
        if done(row):
            return row
        time.sleep(.05)
    raise AssertionError(row)


def events(row, name):
    return [event['data'] for event in row['events'] if event['type'] == name]


def retry_seen(row):
    return any(event.get('retry') for event in events(row, 'error'))


def run():
    binary = Path(os.environ['ASTRION_HEADLESS_BIN']).resolve(strict=True)
    with tempfile.TemporaryDirectory() as td, ThreadingHTTPServer(('127.0.0.1', 0), Services) as service:
        tmp = Path(td)
        env = fixture_environment(tmp)
        env['ASTRION_RESOURCE_ROOT'] = str(ROOT)
        data = Path(env['DATA_DIR'])
        data.mkdir()
        (data/'personalization.json').write_text(json.dumps({
            'auto_generate_title': False,
            'review_agents': {'auto_approval': {'model': 'retry-local'}},
        }))
        cfg = Path(env['DEPLOY_CONFIG_DIR'])/'custom_models.json'
        cfg.write_text(json.dumps({'models': [{'model_name': 'retry-local', 'model_id': 'retry-local',
                       'url': f'http://127.0.0.1:{service.server_port}/v1', 'apikey': 'fixture-only'}]}))
        service.scenarios = {AGENT, CHAT, CANCEL, PERMANENT, DELEGATE, REVIEWED}
        service.requests = defaultdict(list)
        service.errors = []
        worker = threading.Thread(target=service.serve_forever, daemon=True)
        worker.start()
        try:
            with model_catalog_host(binary, env, tmp/'host.log') as listening:
                browser = Browser(listening)
                agent = browser.api('POST', '/api/tasks?workspace_id=default',
                                    {'message': AGENT, 'model_key': 'retry-local'}, status=202)
                agent_id = agent.get('data', agent)['task_id']
                agent_url = f'/api/tasks/{agent_id}?workspace_id=default&from=0'

                def start_agent(prompt, permission, execution):
                    conversation = browser.api('POST', '/api/conversations?workspace_id=default', {
                        'settings': {'model_key': 'retry-local', 'work_mode': 'execute',
                                     'permission_mode': permission, 'execution_mode': execution,
                                     'network_permission': 'restricted', 'run_mode': 'fast',
                                     'thinking_mode': False, 'reasoning_effort': None},
                    }, status=201)['conversation_id']
                    task = browser.api('POST', '/api/tasks?workspace_id=default', {
                        'message': prompt, 'model_key': 'retry-local', 'conversation_id': conversation,
                    }, status=202)
                    task_id = task.get('data', task)['task_id']
                    return conversation, f'/api/tasks/{task_id}?workspace_id=default&from=0'

                delegated, delegate_url = start_agent(DELEGATE, 'unrestricted', 'sandbox')
                _, reviewed_url = start_agent(REVIEWED, 'auto_approval', 'direct')

                def start_chat(prompt, operation):
                    chat = browser.api('POST', '/api/chat/tasks',
                                      {'message': prompt, 'model_key': 'retry-local',
                                       'operation_id': operation})['data']
                    return f"/api/chat/tasks/{chat['task_id']}"

                chat_url = start_chat(CHAT, 'retry-chat')
                until(browser, chat_url, retry_seen)
                row = until(browser, agent_url, lambda r: r['status'] != 'running')
                assert row['status'] == 'succeeded', row
                assert [e['attempt'] for e in events(row, 'api_request_start')] == [1, 2], row
                assert [e['max_attempts'] for e in events(row, 'api_request_start')] == [10, 10], row
                assert retry_seen(row) and not events(row, 'stream_reset'), row
                chat = until(browser, chat_url, lambda r: r['status'] != 'running')
                assert chat['status'] == 'succeeded', chat
                assert [e['attempt'] for e in events(chat, 'api_request_start')] == [1, 2], chat
                assert [e['max_attempts'] for e in events(chat, 'api_request_start')] == [10, 10], chat
                assert [e['attempt'] for e in events(chat, 'stream_reset')] == [2], chat
                history = json.dumps(chat['view']['document'])
                assert PARTIAL not in history and 'Recovered synthetic answer' in history, history

                cancelled_url = start_chat(CANCEL, 'retry-cancel')
                until(browser, cancelled_url, retry_seen)
                browser.api('POST', cancelled_url + '/cancel')
                cancelled = until(browser, cancelled_url, lambda r: r['status'] != 'running')
                assert cancelled['status'] == 'stopped', cancelled
                assert PARTIAL not in json.dumps(cancelled['view']['document']), cancelled

                denied = until(browser, start_chat(PERMANENT, 'retry-auth'),
                               lambda r: r['status'] != 'running')
                assert denied['status'] == 'failed' and not retry_seen(denied), denied
                assert until(browser, delegate_url, lambda r: r['status'] != 'running')['status'] == 'succeeded'
                children_url = f'/api/sub_agents?workspace_id=default&conversation_id={delegated}'
                child, = until(browser, children_url, lambda rows: rows and rows[0]['status'] != 'running')
                assert child['status'] == 'idle' and child['final_result'] == CHILD_ANSWER, child
                reviewed = until(browser, reviewed_url, lambda r: r['status'] != 'running')
                assert reviewed['status'] == 'succeeded', reviewed
                written = Path(env['HOST_PROJECT_PATH'])/'retry-review.txt'
                assert written.is_file() and written.read_text() == 'approved\n', reviewed
                for prompt, count in {AGENT: 2, CHAT: 2, CANCEL: 1, PERMANENT: 1, CHILD: 2, REVIEW: 2}.items():
                    assert len(service.requests[prompt]) == count, (prompt, service.requests[prompt])
                    if count == 2:
                        assert service.requests[prompt][0] == service.requests[prompt][1], prompt
                assert not service.errors, service.errors
        finally:
            service.shutdown()
            worker.join()
    print('Agent 503, interrupted Chat / child / auto-review streams, cancellation and permanent 401 passed')


if __name__ == '__main__':
    run()
