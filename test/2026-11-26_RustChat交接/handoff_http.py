"""Exercise Chat handoff through the real Host HTTP process and an isolated SSE provider."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from contextlib import closing
from pathlib import Path
from uuid import uuid4

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / '2026-11-24_Rust后端'))
sys.path.insert(0, str(HERE.parent / '2026-11-26_RustChat媒体'))
from agent_process_roundtrip import ROOT, fixture_environment, rust
from chat_web_roundtrip import stored_document
from media_roundtrip import login, png_image, request, upload

CHAT = '/api/chat'
CONTINUE = '不切换到 Agent，继续在 Chat 中处理当前任务'
SUMMARY = '# Frozen handoff summary\nUse the verified latest branch.'


class Provider(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        messages = body['messages']
        user = next(message['content'] for message in reversed(messages) if message['role'] == 'user')
        system = next(message['content'] for message in messages if message['role'] == 'system')
        if body['model'] == 'agent-upstream':
            self.server.agent.append((body, user))
            return self.answer('Agent execution reached the model')
        assert body['model'] == 'chat-upstream', body['model']
        if '请为接手的 Agent 总结' in system:
            self.server.summaries.append((body, user))
            scenario = next((key for key in self.server.blocks if key in user), None)
            if scenario:
                arrived, resume, closed = self.server.blocks[scenario]
                arrived.set()
                resume.wait(10)
                try:
                    return self.answer(SUMMARY)
                except (BrokenPipeError, ConnectionResetError):
                    closed.set()
                    return None
            if 'fail-then-retry' in user and not self.server.sent_failure:
                self.server.sent_failure = True
                self.send_error(429, 'fixture handoff summary quota')
                return None
            return self.answer(SUMMARY)
        self.server.chats.append(user)
        if user == CONTINUE:
            return self.answer('continued in Chat')
        if user == 'root-branch':
            count = self.server.chats.count(user)
            if count == 1:
                return self.answer('old branch only\n<suggest_agent reason="old"/>')
            return self.answer('latest branch only\n<suggest_agent reason="one"/>\n<suggest_agent reason="two"/>')
        return self.answer('Chat suggestion\n<suggest_agent reason="transfer"/>')

    def answer(self, text):
        chunk = {'choices': [{'index': 0, 'delta': {'content': text}, 'finish_reason': 'stop'}]}
        payload = ('data: ' + json.dumps(chunk) + '\n\ndata: [DONE]\n\n').encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_):
        pass


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf8')


def wait(label, read, *, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = read()
        if result:
            return result
        time.sleep(.025)
    raise AssertionError(f'Timed out waiting for {label}')


class Browser:
    def __init__(self, url, data):
        self.url, self.data = url, data
        self.auth = login(url)

    def api(self, method, route, payload=None, *, status=200, auth=True, headers=None):
        body = None if payload is None else json.dumps(payload).encode()
        sent = {**(self.auth if auth else {}), **(headers or {})}
        if body is not None:
            sent['Content-Type'] = 'application/json'
        raw, _ = request(self.url, method, route, headers=sent, body=body, status=status)
        value = json.loads(raw)
        assert value['success'] == (status == 200), value
        return value.get('data', value)

    def database(self):
        return self.data / 'chat' / 'conversations' / 'conversations.sqlite3'

    def view(self, conversation):
        return self.api('GET', f'{CHAT}/conversations/{conversation}')['document']

    def handoffs(self, conversation):
        return self.view(conversation)['metadata']['chat']['handoffs']

    def edit_stored(self, conversation, mutate):
        with closing(sqlite3.connect(self.database())) as db, db:
            db.execute('BEGIN IMMEDIATE')
            document = stored_document(self.database(), conversation)
            previous = json.dumps(document['metadata']['chat'], ensure_ascii=False)
            messages = [json.dumps(value, ensure_ascii=False) for value in document['messages']]
            mutate(document)
            current = json.dumps(document['metadata']['chat'], ensure_ascii=False)
            if current != previous:
                db.execute(
                    'UPDATE fields SET value=? WHERE conversation=? AND scope=\'metadata\' AND key=\'chat\'',
                    (current, conversation),
                )
            for sequence, (old, message) in enumerate(zip(messages, document['messages'], strict=True)):
                value = json.dumps(message, ensure_ascii=False)
                if value != old:
                    db.execute(
                        'UPDATE messages SET body=? WHERE conversation=? AND seq=?',
                        (value, conversation, sequence),
                    )

    def presentation(self, conversation, owner, *, release=False):
        return self.api('POST', f'{CHAT}/conversations/{conversation}/presentation',
                        {'presentation_id': owner, 'release': release})

    def start(self, text, **fields):
        return self.api('POST', f'{CHAT}/tasks', {'message': text, 'operation_id': uuid4().hex, **fields})

    def finish(self, started):
        def poll():
            state = self.api('GET', f'{CHAT}/tasks/{started["task_id"]}')
            if state['status'] == 'running':
                return None
            assert state['status'] == 'succeeded', state
            return state
        return wait('Chat completion', poll)

    def state(self, conversation, handoff, *, status=200, auth=True):
        return self.api('GET', f'{CHAT}/conversations/{conversation}/handoff?handoff_id={handoff}', status=status, auth=auth)

    def decide(self, conversation, handoff, decision, *, status=200, **fields):
        return self.api('POST', f'{CHAT}/conversations/{conversation}/handoff',
                        {'handoff_id': handoff, 'decision': decision, 'operation_id': uuid4().hex, **fields}, status=status)

    def fresh(self, text, owner='page-one'):
        task = self.start(text, presentation_id=owner, model_key='chat-local')
        self.finish(task)
        conversation = task['conversation_id']
        entries = self.handoffs(conversation)
        assert len(entries) == 1, entries
        return conversation, next(iter(entries))


def countdown(browser, provider):
    conversation, id = browser.fresh('countdown')
    initial = browser.state(conversation, id)
    assert initial['countdown_enabled'] and 26 < initial['deadline'] - initial['server_time'] <= 30, initial
    with closing(sqlite3.connect(browser.database())) as watcher:
        before, = watcher.execute('PRAGMA data_version').fetchone()
        browser.state(conversation, id)
        browser.decide(conversation, id, 'observe', presentation_id='page-one')
        browser.decide(conversation, id, 'pause', presentation_id='unrelated-page')
        assert watcher.execute('PRAGMA data_version').fetchone() == (before,), 'reading or heartbeat rewrote the database'
    browser.edit_stored(conversation, lambda doc: doc['metadata']['chat']['handoffs'][id].update(deadline=time.time()-0.01))
    assert browser.state(conversation, id)['status'] == 'pending'
    state = browser.decide(conversation, id, 'observe', presentation_id='page-one')
    assert state['status'] == 'declined' and state['decision_source'] == 'timeout', state
    wait('one automatic Chat continuation', lambda: CONTINUE in provider.chats)
    browser.decide(conversation, id, 'decline')
    assert provider.chats.count(CONTINUE) == 1, provider.chats

    conversation, id = browser.fresh('reload')
    state = browser.presentation(conversation, 'page-two')
    assert state['document']['metadata']['chat']['handoff_presentation_id'] == 'page-two', state
    assert not state['document']['metadata']['chat']['handoffs'][id]['countdown_enabled'], state
    browser.presentation(conversation, 'page-one', release=True)
    assert browser.view(conversation)['metadata']['chat']['handoff_presentation_id'] == 'page-two'
    assert not browser.decide(conversation, id, 'observe', presentation_id='page-two')['countdown_enabled']

    conversation, id = browser.fresh('suspended')
    paused = browser.decide(conversation, id, 'pause', presentation_id='page-one')
    assert paused['status'] == 'pending' and not paused['countdown_enabled'] and paused['deadline'] is None
    browser.state(conversation, id, auth=False, status=401)
    print('countdown, post-deadline lease, duplicate decline, release and session auth passed')


def agent_flow(browser, provider, workspace):
    task = browser.start('root-branch', model_key='chat-local', presentation_id='page-one')
    browser.finish(task)
    conversation = task['conversation_id']
    old = next(iter(browser.handoffs(conversation)))
    original_turn = browser.view(conversation)['metadata']['chat']['turns'][0]['id']
    task = browser.start('', conversation_id=conversation, regenerate_turn_id=original_turn)
    browser.finish(task)
    suggestions = browser.handoffs(conversation)
    assert suggestions[old]['status'] == 'superseded', suggestions
    browser.decide(conversation, old, 'accept', status=409)
    id, sibling = [key for key, item in suggestions.items() if item['status'] == 'pending']
    latest_img = png_image((34, 120, 42))
    old_img = png_image((85, 13, 32))
    latest_ref = upload(browser.url, browser.auth, latest_img, 'latest.png')['data']
    old_ref = upload(browser.url, browser.auth, old_img, 'old.png')['data']

    def add_media(doc):
        for message in doc['messages']:
            if message['role'] != 'assistant':
                continue
            if 'latest branch' in message['content']:
                message['media_refs'] = [latest_ref]
            if 'old branch' in message['content']:
                message['media_refs'] = [old_ref]

    browser.edit_stored(conversation, add_media)
    arrived, resume, _ = provider.blocks['latest branch only']
    preparing = browser.decide(conversation, id, 'accept', workspace_id='default', project_path='../ignored')
    assert preparing['status'] == 'preparing', preparing
    assert arrived.wait(5), provider.summaries
    assert browser.decide(conversation, id, 'accept')['status'] == 'preparing'
    assert len(provider.summaries) == 1, provider.summaries
    browser.api('POST', f'{CHAT}/tasks', {'conversation_id': conversation, 'parent_id': preparing['leaf_id'],
                                         'message': 'blocked while preparing', 'operation_id': uuid4().hex}, status=409)
    resume.set()

    def completed():
        item = browser.state(conversation, id)
        assert item['status'] != 'failed', item
        return item if item['status'] == 'completed' else None

    accepted = wait('completed handoff', completed)
    assert accepted['workspace_id'] != 'default', accepted
    assert {'materials', 'agent_task_id', 'runtime_id', 'operation_id', 'tag'}.isdisjoint(accepted), accepted
    assert browser.state(conversation, sibling)['status'] == 'superseded'
    assert 'old branch only' not in provider.summaries[0][1]
    assert not provider.summaries[0][0].get('tools')
    wait('actual Agent model request', lambda: provider.agent)
    agent_body, prompt = provider.agent[0]
    relative = f'.astrion/handoff/{id}'
    assert f'{relative}/summary.md' in prompt and str(workspace) not in prompt, prompt
    assert any(tool['function']['name'] == 'astrion_echo' for tool in agent_body['tools']), agent_body
    materials = workspace / relative
    assert materials.joinpath('summary.md').read_text().strip() == SUMMARY
    transcript = materials.joinpath('conversation.md').read_text()
    assert 'latest branch only' in transcript and 'old branch only' not in transcript
    assert '<suggest_agent' not in transcript
    assert [path.read_bytes() for path in materials.joinpath('media').iterdir()] == [latest_img]
    database = browser.data / 'conversations' / accepted['workspace_id'] / 'conversations.sqlite3'
    agent_id = accepted['agent_conversation_id']

    def agent_done():
        doc = stored_document(database, agent_id)
        return doc if any(m['content'] == 'Agent execution reached the model' for m in doc['messages']) else None

    agent = wait('Agent assistant saved', agent_done)
    assert agent['metadata']['handoff_from_chat'] == {'conversation_id': conversation, 'handoff_id': id}
    assert sum(message['role'] == 'user' for message in agent['messages']) == 1
    assert browser.decide(conversation, id, 'accept')['agent_conversation_id'] == accepted['agent_conversation_id']
    assert len(provider.agent) == 1
    return conversation, id, accepted, database


def recovery(browser, provider, prior):
    conversation, id, accepted, database = prior
    # Reproduce a process loss after Agent admission and before the Chat receipt was written.
    def interrupt(doc):
        state = doc['metadata']['chat']['handoffs'][id]
        state.update(status='preparing', runtime_id='previous-process')
        for key in ('agent_conversation_id', 'agent_task_id', 'agent_url', 'workspace_id'):
            state.pop(key, None)
    agent_id = accepted['agent_conversation_id']
    before = stored_document(database, agent_id)
    browser.edit_stored(conversation, interrupt)
    assert browser.state(conversation, id)['status'] == 'failed'
    browser.decide(conversation, id, 'accept')
    result = wait('replayed handoff receipt', lambda: (
        (item := browser.state(conversation, id)) if (item := browser.state(conversation, id))['status'] == 'completed' else None))
    assert result['agent_conversation_id'] == accepted['agent_conversation_id']
    assert len(provider.agent) == 1 and len(provider.summaries) == 1
    assert stored_document(database, agent_id) == before
    print('frozen branch, branch-only media, real Agent execution and post-admission recovery passed')


def failure_and_cancel(browser, provider):
    conversation, id = browser.fresh('fail-then-retry')
    browser.decide(conversation, id, 'accept')

    def failed():
        item = browser.state(conversation, id)
        return item if item['status'] == 'failed' else None

    state = wait('upstream failure', failed)
    assert '429' in state['error'] and 'quota' in state['error'], state
    assert len(provider.agent) == 1
    browser.decide(conversation, id, 'accept')
    wait('retried Agent task', lambda: len(provider.agent) == 2)
    wait('retried receipt', lambda: browser.state(conversation, id)['status'] == 'completed')

    conversation, id = browser.fresh('cancel-summary')
    arrived, resume, _ = provider.blocks['cancel-summary']
    browser.decide(conversation, id, 'accept')
    assert arrived.wait(5)
    browser.api('DELETE', f'{CHAT}/conversations/{conversation}')
    resume.set()
    browser.state(conversation, id, status=404)
    browser.decide(conversation, id, 'accept', status=404)
    assert len(provider.agent) == 2, provider.agent
    print('upstream failure, retry and deletion cancellation passed')


def run(scope):
    binary = Path(os.environ.get('ASTRION_HEADLESS_BIN', ROOT/'backend-rust/target-master/debug/astrion-headless')).resolve(strict=True)
    with tempfile.TemporaryDirectory(dir=HERE) as temp, ThreadingHTTPServer(('127.0.0.1', 0), Provider) as provider:
        directory = Path(temp)
        env = fixture_environment(directory)
        env['ASTRION_RESOURCE_ROOT'] = str(ROOT)
        desktop = directory / 'home' / 'Desktop'
        desktop.mkdir()
        workspace = desktop / 'Astrion' / 'Default'
        endpoint = f'http://127.0.0.1:{provider.server_port}/v1'
        data = Path(env['DATA_DIR'])
        dump(Path(env['DEPLOY_CONFIG_DIR']) / 'custom_models.json', [
            {'model_name': 'chat-local', 'model_id': 'chat-upstream', 'url': endpoint, 'apikey': 'fixture-only'},
            {'model_name': 'agent-local', 'model_id': 'agent-upstream', 'url': endpoint, 'apikey': 'fixture-only'},
        ])
        dump(data / 'personalization.json', {'default_model': 'agent-local'})
        provider.chats, provider.summaries, provider.agent = [], [], []
        provider.blocks = {key: tuple(threading.Event() for _ in range(3)) for key in ('latest branch only', 'cancel-summary')}
        provider.sent_failure = False
        thread = threading.Thread(target=provider.serve_forever, daemon=True)
        thread.start()
        try:
            with rust(env, binary, endpoint + '/chat/completions', directory / 'first.log', web=True) as url:
                browser = Browser(url, data)
                countdown(browser, provider)
                if scope == 'countdown':
                    return
                prior = agent_flow(browser, provider, workspace)
            with rust(env, binary, endpoint + '/chat/completions', directory / 'second.log', web=True) as url:
                browser = Browser(url, data)
                recovery(browser, provider, prior)
                failure_and_cancel(browser, provider)
        finally:
            for _, resume, _ in provider.blocks.values():
                resume.set()
            provider.shutdown()
            thread.join()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--scope', choices=('countdown', 'all'), default='all')
    run(parser.parse_args().scope)
