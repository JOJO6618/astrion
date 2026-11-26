"""Run the normal Rust Web host and a private, deterministic SSE title provider."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import threading
from contextlib import closing
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / '2026-11-26_RustChat交接'))
from handoff_http import Browser, Provider, ROOT, dump, fixture_environment, request, rust, wait


class Model(Provider):
    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        latest = request['messages'][-1]['content']
        is_title = isinstance(latest, str) and latest.startswith('请为这个对话首条消息起标题:')
        label = latest.split('"')[1] if is_title else latest
        self.server.received.append((is_title, request['model'], label))
        try:
            if is_title:
                assert not request.get('tools')
                assert '只关注内容' in latest
                assert '标题' in request['messages'][0]['content']
            block = self.server.blocks.get((is_title, label))
            if block:
                arrived, resume = block
                arrived.set()
                assert resume.wait(8), label
            return self.answer('   Generated\n  title 🚀   ' if is_title else f'answer {label}')
        except (AssertionError, BrokenPipeError, ConnectionResetError) as error:
            self.server.errors.append(repr(error))
            if not isinstance(error, (BrokenPipeError, ConnectionResetError)):
                self.send_error(500, 'Invalid fixture request')
            return None


def poll(browser, task, *, chat=False):
    prefix = '/api/chat' if chat else '/api'
    return browser.api('GET', f'{prefix}/tasks/{task["task_id"]}?from=0')


def send_agent(browser, message, **fields):
    raw, _ = request(browser.url, 'POST', '/api/tasks', status=202,
                     headers={**browser.auth, 'content-type': 'application/json', 'x-astrion-workspace-id': 'default'},
                     body=json.dumps({'message': message, 'model_key': 'process-fixture', **fields}).encode())
    return json.loads(raw)['data']


def finished(browser, task, *, chat=False):
    def done():
        current = poll(browser, task, chat=chat)
        if current['status'] == 'running':
            return False
        assert current['status'] == 'succeeded', current
        return current
    return wait('completed model reply', done)


def title_settled(browser, task):
    view = poll(browser, task, chat=True)['view']
    return view['document'] if not view['display']['state']['title_pending'] else None


def verify(browser, provider, data, models):
    preferences = data / 'personalization.json'
    dump(preferences, {'default_model': 'main-chat'})
    blocked_main = provider.blocks[(False, 'Agent default')]
    agent = send_agent(browser, 'Agent default')
    cid = agent['conversation_id']
    assert blocked_main[0].wait(5)
    meta_path = f'/api/conversations/{cid}/metadata?workspace_id=default'
    wait('generated Agent title', lambda: browser.api('GET', meta_path)['title_locked'])
    assert browser.api('GET', meta_path)['title'] == 'Generated title 🚀'
    blocked_main[1].set()
    state = finished(browser, agent)
    assert any(event['type'] == 'conversation_changed' and event['data']['title'] == 'Generated title 🚀'
               for event in state['events']), state
    assert (True, 'title-upstream', 'Agent default') in provider.received

    dump(preferences, {'title_model': ' hidden-title ', 'chat_default_model': 'main-chat'})
    blocked_title = provider.blocks[(True, 'Chat late')]
    chat = browser.start('Chat late', model_key='main-chat')
    chat_id = chat['conversation_id']
    assert blocked_title[0].wait(5)
    state = finished(browser, chat, chat=True)
    assert state['view']['display']['state']['title_pending'] is True
    assert not state['view']['document']['metadata'].get('title_locked')
    blocked_title[1].set()
    titled = wait('late Chat title settled', lambda: title_settled(browser, chat))
    assert titled['title'] == 'Generated title 🚀'
    assert titled['metadata']['title_locked'] is True
    assert any(row['id'] == chat_id and row['title'] == 'Generated title 🚀'
               for row in browser.api('GET', '/api/chat/conversations'))
    assert (True, 'hidden-upstream', 'Chat late') in provider.received
    first = titled['messages'][0]['message_id']
    fork = browser.start('Chat edit', model_key='main-chat', conversation_id=chat_id, edit_message_id=first)
    finished(browser, fork, chat=True)
    assert browser.view(chat_id)['title'] == 'Generated title 🚀'

    blocked_title = provider.blocks[(True, 'Chat rename')]
    race = browser.start('Chat rename', model_key='main-chat')
    race_id = race['conversation_id']
    assert blocked_title[0].wait(5)
    finished(browser, race, chat=True)
    original_title = browser.view(race_id)['title']
    # Reapplying the same visible name must still supersede the background job.
    browser.api('PATCH', f'/api/chat/conversations/{race_id}/title', {'title': original_title})
    blocked_title[1].set()
    renamed = wait('discarded stale title result', lambda: title_settled(browser, race))
    assert renamed['title'] == original_title and renamed['metadata']['title_locked'] is True

    # An unresolved explicitly selected model and a disabled preference both
    # leave the primary request independent of the auxiliary model.
    for label, config in [
        ('Invalid explicit model', {'title_model': 'missing'}),
        ('User disabled titles', {'auto_generate_title': False}),
    ]:
        dump(preferences, config)
        finished(browser, browser.start(label, model_key='main-chat'), chat=True)

    dump(preferences, {})
    empty = browser.api('POST', '/api/runtime/sessions', {'model_key': 'process-fixture'})
    empty_id = empty['conversation_id']
    db_path = data / 'conversations' / 'default' / 'conversations.sqlite3'
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("INSERT INTO fields VALUES (?,'metadata','skip_auto_title_generation','true')", (empty_id,))
    task = send_agent(browser, 'Stored skip', conversation_id=empty_id)
    finished(browser, task)
    dump(models, {'models': []})
    task = send_agent(browser, 'Fixed model only')
    finished(browser, task)
    assert provider.errors == [], provider.errors
    assert sorted((model, text) for title, model, text in provider.received if title) == [
        ('hidden-upstream', 'Chat late'), ('hidden-upstream', 'Chat rename'), ('title-upstream', 'Agent default'),
    ], provider.received
    print('Agent default selection and event, Chat late title and branch, rename protection, and title skips passed.')


def main():
    binary = Path(os.environ.get(
        'ASTRION_HEADLESS_BIN', ROOT / 'backend-rust/target-master/debug/astrion-headless',
    )).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix='astrion-auto-title-') as temp, ThreadingHTTPServer(('127.0.0.1', 0), Model) as provider:
        directory = Path(temp).resolve()
        env = fixture_environment(directory)
        data = Path(env['DATA_DIR'])
        models = Path(env['DEPLOY_CONFIG_DIR']) / 'custom_models.json'
        endpoint = f'http://127.0.0.1:{provider.server_port}/v1'
        dump(models, {'models': [
            {'model_name': key, 'model_id': model, 'url': endpoint, 'apikey': 'fixture-only', 'visible': visible}
            for key, model, visible in [
                ('hidden-title', 'hidden-upstream', False),
                ('title-profile', 'title-upstream', True),
                ('main-chat', 'chat-upstream', True),
            ]
        ]})
        provider.received, provider.errors = [], []
        provider.blocks = {
            key: (threading.Event(), threading.Event())
            for key in [(False, 'Agent default'), (True, 'Chat late'), (True, 'Chat rename')]
        }
        thread = threading.Thread(target=provider.serve_forever, daemon=True)
        thread.start()
        try:
            with rust(env, binary, endpoint + '/chat/completions', directory / 'rust.log', web=True) as url:
                verify(Browser(url, data), provider, data, models)
        finally:
            for _, resume in provider.blocks.values():
                resume.set()
            provider.shutdown()


if __name__ == '__main__':
    main()
