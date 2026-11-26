"""Exercise account Chat edits and branch preservation across two Host Web processes."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from http.cookiejar import CookieJar
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from contextlib import closing
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

from agent_process_roundtrip import HERE, ROOT, fixture_environment, rust


def stored_document(database: Path, conversation: str) -> dict:
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as db, db:
        result = {}
        for scope, key, raw in db.execute(
            "SELECT scope,key,value FROM fields WHERE conversation=? ORDER BY scope<>'root'",
            (conversation,),
        ):
            target = result if scope == 'root' else result.setdefault(scope, {})
            target[key] = json.loads(raw)
        result['messages'] = [json.loads(raw) for raw, in db.execute(
            'SELECT body FROM messages WHERE conversation=? ORDER BY seq', (conversation,),
        )]
        return result


def migrate_fixture(browser, expected: int) -> None:
    route = '/api/conversation-storage/migration'
    assert browser.request(route)['pending'] == expected
    browser.request(route, {})
    deadline = time.monotonic() + 8
    while True:
        report = browser.request(route)
        if not report['running']:
            assert report['migrated'] == expected and report['failed'] == 0, report
            return
        assert time.monotonic() < deadline, report
        time.sleep(0.03)


def python_phase(action: str, cid: str) -> None:
    sys.path.insert(0, str(ROOT))
    from modules.chat_mode.storage import ChatStorage
    from modules.chat_mode.tree import MessageTree

    storage = ChatStorage(Path(os.environ['DATA_DIR']) / 'chat')
    if action == 'seed':
        cid = storage.create()
        left = storage.begin(cid, 'seed-question', message={
            'role': 'user', 'message_id': 'msg_question', 'content': '旧问题',
            'metadata': {'future_extension': ['未来字段', 7]},
        })['value']['id']
        storage.append(cid, left, {'role': 'assistant', 'message_id': 'msg_left', 'content': '左分支'})
        storage.finish(cid, left)
        right = storage.begin(cid, 'seed-right', regenerate_turn_id=left)['value']['id']
        storage.append(cid, right, {'role': 'assistant', 'message_id': 'msg_right', 'content': '右分支'})
        storage.finish(cid, right)
        storage.edit(cid, 'python-seed-edit', 'msg_right', '右分支（已编辑）')
        print('SESSION=' + cid)
        return
    database = Path(os.environ['DATA_DIR']) / 'chat/conversations/conversations.sqlite3'
    tree = MessageTree(stored_document(database, cid))
    assert tree.siblings('msg_left') == ['msg_left', 'msg_right']
    assert tree.node('msg_left')['content'] == 'Rust 第一次编辑'
    assert tree.node('msg_right')['content'] == 'Rust 重启后编辑'
    assert tree.node('msg_question')['metadata']['future_extension'] == ['未来字段', 7]
    latest = tree.path(tree.state['latest_leaf_id'], latest=False)
    assert [message['content'] for message in latest] == ['旧问题', 'Rust 第一次编辑', '继续左分支', '只答左分支']
    receipt = tree.state['commands']['edit-msg_right']['result']
    assert receipt['value'] == {'message_id': 'msg_right'}


class Browser:
    def __init__(self, base: str):
        self.base = base
        self.cookies = build_opener(HTTPCookieProcessor(CookieJar()))
        self.token = ''

    def request(self, path: str, body: dict | None = None, *, status: int = 200) -> dict:
        # A stale Agent workspace header must have no effect on account Chat.
        headers = {'Origin': self.base, 'X-Astrion-Workspace-Id': 'unregistered-agent-workspace'}
        if body is not None:
            headers.update({'Content-Type': 'application/json', 'X-CSRF-Token': self.token})
        req = Request(self.base + path, data=None if body is None else json.dumps(body).encode(), headers=headers)
        try:
            response = self.cookies.open(req, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            text = response.read().decode()
            assert response.status == status, (path, response.status, text[:1000])
            return json.loads(text)

    def login(self) -> None:
        self.token = self.request('/api/csrf-token')['token']
        self.request('/host-login', {})
        self.token = self.request('/api/csrf-token')['token']


class LocalModel(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        assert self.path == '/v1/chat/completions'
        assert request['model'] == 'fixture-chat'
        history = [m['content'] for m in request['messages'] if m['role'] in ('user', 'assistant')]
        assert history[-3:] == ['旧问题', 'Rust 第一次编辑', '继续左分支'], history
        assert 'Rust 重启后编辑' not in history
        delta = {'choices': [{'index': 0, 'delta': {'content': '只答左分支'}, 'finish_reason': 'stop'}]}
        data = ('data: ' + json.dumps(delta) + '\n\ndata: [DONE]\n\n').encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args: object) -> None:
        pass


def run() -> None:
    binary = Path(os.environ['ASTRION_HEADLESS_BIN']).resolve(strict=True)
    with tempfile.TemporaryDirectory(dir=HERE) as temporary, ThreadingHTTPServer(('127.0.0.1', 0), LocalModel) as upstream:
        root = Path(temporary)
        env = fixture_environment(root)
        env['ASTRION_RESOURCE_ROOT'] = str(ROOT)
        endpoint = f'http://127.0.0.1:{upstream.server_port}/v1'
        models = [{'model_name': 'fixture-chat', 'model_id': 'fixture-chat', 'url': endpoint, 'apikey': 'fixture-key'}]
        (root / 'config' / 'custom_models.json').write_text(json.dumps({'models': models}))
        threading.Thread(target=upstream.serve_forever, daemon=True).start()

        def python(action: str, cid: str = '') -> str:
            result = subprocess.run([sys.executable, '-B', __file__, action, cid], env=env,
                                    cwd=ROOT, check=True, text=True, stdout=subprocess.PIPE, timeout=30)
            return result.stdout

        cid = next(line.removeprefix('SESSION=') for line in python('seed').splitlines() if line.startswith('SESSION='))
        doc_path, = (Path(env['DATA_DIR']) / 'chat').rglob(cid + '.json')
        before = (doc_path.stat().st_mtime_ns, doc_path.read_bytes())
        database = doc_path.parent / 'conversations.sqlite3'
        url = '/api/chat/conversations/' + cid
        previous = None
        for node, content in [('msg_left', 'Rust 第一次编辑'), ('msg_right', 'Rust 重启后编辑')]:
            with rust(env, binary, 'http://127.0.0.1:1/v1/chat/completions', root / 'rust.log', web=True) as base:
                browser = Browser(base)
                browser.request(url, status=401)
                browser.login()
                if previous is None:
                    blocked = browser.request(url, status=409)
                    assert blocked['code'] == 'conversation_migration_required'
                    migrate_fixture(browser, 1)
                doc = browser.request(url)['data']['document']
                assert doc['id'] == cid
                seed_retry = {'operation_id': 'python-seed-edit', 'message_id': 'msg_right',
                              'content': '右分支（已编辑）', 'revision': 0}
                seed_receipt = doc['metadata']['chat']['commands']['python-seed-edit']['result']
                assert browser.request(url + '/edit', seed_retry)['data'] == seed_receipt
                if previous:
                    retry, receipt = previous
                    assert browser.request(url + '/edit', retry)['data'] == receipt
                assert before == (doc_path.stat().st_mtime_ns, doc_path.read_bytes())
                revision = doc['metadata']['chat']['revision']
                body = {'operation_id': 'edit-' + node, 'message_id': node, 'content': content, 'revision': revision}
                result = browser.request(url + '/edit', body)['data']
                updated = stored_document(database, cid)
                assert updated['metadata']['chat']['revision'] == revision + 1
                assert next(m for m in updated['messages'] if m['message_id'] == node)['content'] == content
                browser.request(url + '/edit', {**body, 'operation_id': 'stale-' + node}, status=409)
                assert stored_document(database, cid) == updated
                if previous:
                    send = {'conversation_id': cid, 'operation_id': 'continue-left', 'parent_id': 'msg_left',
                            'model_key': 'fixture-chat', 'message': '继续左分支'}
                    task = browser.request('/api/chat/tasks', send)['data']
                    deadline = time.monotonic() + 8
                    while True:
                        state = browser.request('/api/chat/tasks/' + task['task_id'])['data']
                        if state['status'] in ('succeeded', 'failed', 'cancelled'):
                            assert state['status'] == 'succeeded', (state['status'], state.get('error'))
                            break
                        assert time.monotonic() < deadline, state['status']
                        time.sleep(0.03)
                previous = (body, result)
        python('verify', cid)
        assert before == (doc_path.stat().st_mtime_ns, doc_path.read_bytes())
        upstream.shutdown()
        print('Chat legacy migration, archived source, CAS, replay, and selected-branch generation passed.')


if __name__ == '__main__':
    if len(sys.argv) == 3:
        python_phase(*sys.argv[1:])
    else:
        run()
