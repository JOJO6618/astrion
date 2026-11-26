"""Round-trip account Chat drafts and conversation copying between Python and Rust Host Web."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from base64 import b64decode
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

from agent_process_roundtrip import HERE, ROOT, fixture_environment, rust
from chat_web_roundtrip import migrate_fixture, stored_document


def copied_chat(chat: Path, source: str) -> dict:
    database = chat / 'conversations/conversations.sqlite3'
    with sqlite3.connect(database) as db:
        ids = [identity for identity, in db.execute('SELECT id FROM conversations WHERE id<>?', (source,))]
    copy, = (doc for doc in (stored_document(database, identity) for identity in ids)
             if doc['metadata']['chat'].get('copied_from') == source)
    return copy


def python_phase(action: str, cid: str = '') -> None:
    sys.path.insert(0, str(ROOT))
    from modules.chat_mode.storage import ChatStorage
    from modules.chat_mode.tree import MessageTree
    from modules.composer_drafts import read_draft, save_draft
    from utils.media_store import MediaStore

    data = Path(os.environ['DATA_DIR'])
    chat = data / 'chat'
    storage = ChatStorage(chat)
    if action == 'seed':
        cid = storage.create()
        root = storage.begin(cid, 'python-question', message={
            'role': 'user', 'message_id': 'msg_question', 'content': 'Python 问题',
            'metadata': {'future_extension': ['保留', 7]},
        })['value']['id']
        storage.append(cid, root, {'role': 'assistant', 'message_id': 'msg_answer', 'content': 'Python 回答'})
        storage.finish(cid, root)
        branch = storage.begin(cid, 'python-regenerate', regenerate_turn_id=root)['value']['id']
        storage.append(cid, branch, {'role': 'assistant', 'message_id': 'msg_answer_alt',
                                    'content': 'Python 备选 <suggest_agent reason="continue"/>'})
        storage.finish(cid, branch)
        storage.manager.mutate_conversation(cid, lambda document: document['metadata']['chat'].update(
            handoffs={
                'msg_answer': {'status': 'pending'},
                'msg_answer_alt': {'status': 'preparing', 'presentation_id': 'python-presentation',
                                   'operation_id': 'python-operation', 'task_id': 'python-task',
                                   'agent_task_id': 'python-agent-task'},
            }, handoff_count=2))
        media = MediaStore(chat).store_bytes(
            b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII='),
            kind='image', mime_type='image/png')
        media = {key: media[key] for key in ('media_id', 'kind', 'mime_type', 'size', 'sha256')}
        (chat / 'drafts').mkdir(parents=True, exist_ok=True)
        assert save_draft(chat / 'drafts' / f'{cid}.json', {'text': 'Python 原稿', 'media': [media]},
                          client_id='python', sequence=1)
        assert save_draft(chat / 'drafts' / 'new.json', {'text': 'Python 新对话', 'media': []},
                          client_id='python', sequence=1)
        print('SESSION=' + cid, flush=True)
        return
    if action == 'oracle':
        from modules.chat_mode.duplicate import duplicate
        copied = duplicate(storage, cid, 'python-oracle')
        document = storage.read(copied)
        handoffs = {state['message_id']: state for state in document['metadata']['chat']['handoffs'].values()}
        assert len(handoffs) == 2 and document['metadata']['chat']['handoff_count'] == 2
        assert handoffs['msg_answer']['status'] == 'superseded'
        assert handoffs['msg_answer_alt']['status'] == 'pending'
        assert handoffs['msg_answer_alt']['present'] is True
        for state in handoffs.values():
            assert state['countdown_enabled'] is False and 'presentation_id' not in state
            assert not ({'operation_id', 'task_id', 'agent_task_id'} & set(state))
        storage.manager.delete_conversation(copied)
        print('ORACLE=' + copied, flush=True)
        return
    if action == 'check':
        draft = read_draft(chat / 'drafts' / f'{cid}.json')
        assert draft['text'] == 'Rust 保存' and draft['revision'] == 2, draft
        assert draft['media'] and draft['media'][0]['kind'] == 'image'
        assert draft['media'][0]['mime_type'] == 'image/png' and draft['media'][0]['size'] == 68
        assert draft['writer_sequences'] == {'python': 1, 'rust': 2}, draft
        copy = copied_chat(chat, cid)
        source = storage.read(cid)
        assert copy['title'] == source['title'] + ' (copy)'
        assert {m['message_id'] for m in copy['messages']} == {'msg_question', 'msg_answer', 'msg_answer_alt'}
        assert copy['messages'][0]['metadata']['future_extension'] == ['保留', 7]
        assert copy['metadata']['chat']['commands'] == {}
        handoffs = {state['message_id']: state for state in copy['metadata']['chat']['handoffs'].values()}
        assert len(handoffs) == 2, handoffs
        assert handoffs['msg_answer']['status'] == 'superseded' and handoffs['msg_answer']['present'] is False
        assert handoffs['msg_answer_alt']['status'] == 'pending' and handoffs['msg_answer_alt']['present'] is True
        for state in handoffs.values():
            assert state['id'].startswith('handoff_') and state['source_handoff_id']
            assert state['countdown_enabled'] is False and 'presentation_id' not in state
            assert not ({'operation_id', 'task_id', 'agent_task_id'} & set(state))
        assert copy['metadata']['chat']['handoff_count'] == 2
        assert copy['metadata']['chat']['creation_operation_id'].startswith('copy_')
        assert len(copy['metadata']['chat']['creation_operation_id']) == 69
        assert copy['metadata']['chat']['latest_leaf_id'] == 'msg_answer_alt'
        leaf = copy['metadata']['chat']['latest_leaf_id']
        assert MessageTree(copy).path(leaf, latest=False)[-1]['content'] == 'Python 备选 <suggest_agent reason="continue"/>'
        # Python takes over the same draft after Rust's revision 2.
        assert save_draft(chat / 'drafts' / f'{cid}.json',
                          {**draft, 'text': 'Python 接管'}, base_revision=2)
        assert read_draft(chat / 'drafts' / f'{cid}.json')['revision'] == 3
        source_answers = {message['message_id']: message['content'] for message in storage.read(cid)['messages']}
        assert source_answers['msg_answer'] == 'Python 回答'
        print('COPY=' + copy['id'], flush=True)
        return
    if action == 'final':
        draft = read_draft(chat / 'drafts' / f'{cid}.json')
        assert draft['text'] == 'Python 接管' and draft['revision'] == 3, draft
        assert draft['media'][0]['kind'] == 'image' and draft['media'][0]['size'] == 68
        assert read_draft(chat / 'drafts' / 'new.json')['text'] == 'Python 新对话'
        copy = copied_chat(chat, cid)
        answers = {message['message_id']: message['content'] for message in copy['messages']}
        assert answers['msg_answer'] == 'Rust 修改副本'
        assert answers['msg_answer_alt'] == 'Python 备选 <suggest_agent reason="continue"/>'
        assert copy['metadata']['chat']['commands']['rust-copy-edit']
        print('COPY=' + copy['id'], flush=True)
        return
    raise AssertionError(action)


class Browser:
    def __init__(self, base: str):
        self.base = base
        self.cookies = build_opener(HTTPCookieProcessor(CookieJar()))
        self.token = ''

    def request(self, path: str, body: dict | None = None, *, status: int = 200, method: str | None = None) -> dict:
        headers = {'Origin': self.base, 'X-Astrion-Workspace-Id': 'unregistered-agent-workspace'}
        if body is not None:
            headers.update({'Content-Type': 'application/json', 'X-CSRF-Token': self.token})
        request = Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                          headers=headers, method=method)
        try:
            response = self.cookies.open(request, timeout=5)
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


def run() -> None:
    binary = Path(os.environ['ASTRION_HEADLESS_BIN']).resolve(strict=True)
    with tempfile.TemporaryDirectory(dir=HERE) as temporary:
        root = Path(temporary)
        env = fixture_environment(root)
        env['ASTRION_RESOURCE_ROOT'] = str(ROOT)

        def python(action: str, cid: str = '') -> str:
            result = subprocess.run([sys.executable, '-B', __file__, action, cid], env=env,
                                    cwd=ROOT, check=True, text=True, stdout=subprocess.PIPE, timeout=30)
            return result.stdout

        cid = next(line.removeprefix('SESSION=') for line in python('seed').splitlines()
                   if line.startswith('SESSION='))
        python('oracle', cid)
        draft = '/api/chat/drafts/' + cid
        with rust(env, binary, 'http://127.0.0.1:1/v1/chat/completions', root / 'rust-draft.log', web=True) as base:
            browser = Browser(base)
            browser.request('/api/chat/drafts/new', status=401)
            browser.login()
            migrate_fixture(browser, 1)
            existing = browser.request(draft)['data']
            assert (existing['text'], existing['revision']) == ('Python 原稿', 1), existing
            assert existing['media'][0]['kind'] == 'image' and existing['media'][0]['size'] == 68
            assert (browser.request('/api/chat/drafts/new')['data']['text']) == 'Python 新对话'
            browser.request(draft, {
                'text': '坏引用', 'media': [{'media_id': 'sha256:' + 'f' * 64}],
                'client_id': 'rust', 'sequence': 2, 'base_revision': 1,
            }, status=404, method='PUT')
            unchanged = browser.request(draft)['data']
            assert (unchanged['text'], unchanged['revision']) == ('Python 原稿', 1), unchanged
            saved = browser.request(draft, {
                'text': 'Rust 保存', 'media': existing['media'], 'client_id': 'rust',
                'sequence': 2, 'base_revision': 1,
            }, method='PUT')['data']
            assert (saved['text'], saved['revision'], saved['writer_sequences']) == (
                'Rust 保存', 2, {'python': 1, 'rust': 2}), saved
            assert saved['media'] == existing['media'], saved
            stale = browser.request(draft, {
                'text': '过期窗口', 'media': [], 'client_id': 'python', 'sequence': 1, 'base_revision': 1,
            }, method='PUT')['data']
            assert (stale['text'], stale['revision']) == ('Rust 保存', 2), stale
            browser.request(draft, {
                'text': '冲突窗口', 'media': [], 'client_id': 'rust', 'sequence': 3, 'base_revision': 1,
            }, status=400, method='PUT')
            assert browser.request(draft)['data']['text'] == 'Rust 保存'

            duplicate = '/api/chat/conversations/' + cid + '/duplicate'
            browser.request(duplicate, {}, status=400)
            first = browser.request(duplicate, {'operation_id': 'rust-copy'})['data']['conversation_id']
            assert browser.request(duplicate, {'operation_id': 'rust-copy'})['data']['conversation_id'] == first
            copy = browser.request('/api/chat/conversations/' + first)['data']['document']
            assert copy['title'].endswith(' (copy)')
            assert [m['message_id'] for m in copy['messages']] == ['msg_question', 'msg_answer_alt']
            assert copy['metadata']['chat']['commands'] == {}
            handoffs = {state['message_id']: state for state in copy['metadata']['chat']['handoffs'].values()}
            assert len(handoffs) == 2 and copy['metadata']['chat']['handoff_count'] == 2
            assert handoffs['msg_answer']['status'] == 'superseded'
            assert handoffs['msg_answer_alt']['status'] == 'pending', handoffs
            for state in handoffs.values():
                assert state['countdown_enabled'] is False
                assert 'presentation_id' not in state

        # Restart: both Python-written draft and the copied tree survive.
        with rust(env, binary, 'http://127.0.0.1:1/v1/chat/completions', root / 'rust-restart.log', web=True) as base:
            browser = Browser(base)
            browser.login()
            assert browser.request(draft)['data']['text'] == 'Rust 保存'
            assert browser.request(duplicate, {'operation_id': 'rust-copy'})['data']['conversation_id'] == first

        output = python('check', cid)
        copy = next(line.removeprefix('COPY=') for line in output.splitlines() if line.startswith('COPY='))
        with rust(env, binary, 'http://127.0.0.1:1/v1/chat/completions', root / 'rust-final.log', web=True) as base:
            browser = Browser(base)
            browser.login()
            final = browser.request(draft)['data']
            assert (final['text'], final['revision']) == ('Python 接管', 3), final
            browser.request('/api/chat/conversations/' + copy + '/edit', {
                'operation_id': 'rust-copy-edit', 'message_id': 'msg_answer', 'content': 'Rust 修改副本',
            })
            document = browser.request('/api/chat/conversations/' + copy)['data']['document']
            assert [message['message_id'] for message in document['messages']] == ['msg_question', 'msg_answer_alt']
            answers = {message['message_id']: message['content'] for message in document['messages']}
            assert answers['msg_answer_alt'] == 'Python 备选 <suggest_agent reason="continue"/>'
        python('final', cid)
        print('Chat migration, Rust copy and restart, and shared draft CAS passed.')


if __name__ == '__main__':
    if len(sys.argv) == 3:
        python_phase(sys.argv[1], sys.argv[2])
    else:
        run()
