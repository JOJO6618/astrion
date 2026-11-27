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
from knowledge_media_http import Browser, LocalServices, ROOT, fixture_environment, model_catalog_host

AGENT = 'Retry Agent fixture'
CHAT = 'Retry Chat fixture'
CANCEL = 'Cancel Chat during retry wait'
PERMANENT = 'Do not retry an unauthorized request'
PARTIAL = 'Uncommitted partial output'


def sse(text, *, complete):
    chunk = {'choices': [{'index': 0, 'delta': {'content': text},
                          'finish_reason': 'stop' if complete else None}]}
    suffix = 'data: [DONE]\n\n' if complete else ''
    return (f'data: {json.dumps(chunk)}\n\n' + suffix).encode()


class Services(LocalServices):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        user = next((m['content'] for m in reversed(body['messages'])
                     if m['role'] == 'user' and m['content'] in self.server.scenarios), '')
        if not user:
            self.server.errors.append(body)
            return self.write(400, 'application/json', b'{"error":"unexpected synthetic request"}')
        self.server.requests[user].append(body['messages'])
        count = len(self.server.requests[user])
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
        (data/'personalization.json').write_text(json.dumps({'auto_generate_title': False}))
        cfg = Path(env['DEPLOY_CONFIG_DIR'])/'custom_models.json'
        cfg.write_text(json.dumps({'models': [{'model_name': 'retry-local', 'model_id': 'retry-local',
                       'url': f'http://127.0.0.1:{service.server_port}/v1', 'apikey': 'fixture-only'}]}))
        service.scenarios = {AGENT, CHAT, CANCEL, PERMANENT}
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
                assert retry_seen(row) and not events(row, 'stream_reset'), row
                chat = until(browser, chat_url, lambda r: r['status'] != 'running')
                assert chat['status'] == 'succeeded', chat
                assert [e['attempt'] for e in events(chat, 'api_request_start')] == [1, 2], chat
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
                assert {k: len(v) for k, v in service.requests.items()} == {
                    AGENT: 2, CHAT: 2, CANCEL: 1, PERMANENT: 1,
                }, service.requests
                assert service.requests[AGENT][0] == service.requests[AGENT][1]
                assert service.requests[CHAT][0] == service.requests[CHAT][1]
                assert not service.errors, service.errors
        finally:
            service.shutdown()
            worker.join()
    print('Agent HTTP 503, Chat interrupted SSE, cancellation and permanent 401 passed')


if __name__ == '__main__':
    run()
