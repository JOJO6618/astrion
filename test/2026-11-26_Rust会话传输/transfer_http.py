"""Isolated production Host exercise for portable archives and workspace moves."""
from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import warnings
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / '2026-11-24_Rust后端'))
sys.path.insert(0, str(HERE.parent / '2026-11-26_RustChat媒体'))
from agent_process_roundtrip import ROOT, fixture_environment, rust
from chat_web_roundtrip import stored_document
from media_roundtrip import login, png_image, request

DATE = '2026-11-24T00:00:00.000Z'
HANDOFF = '.astrion/handoff/historical'
ASSETS = {
    f'{HANDOFF}/summary.md': b'# Summary\n',
    f'{HANDOFF}/conversation.md': b'[image](media/reference.png)\n',
    f'{HANDOFF}/manifest.json': b'{}',
    f'{HANDOFF}/media/reference.png': png_image((16, 120, 190)),
}
PNG = png_image((4, 72, 200))


class Provider(BaseHTTPRequestHandler):
    def do_POST(self):
        messages = json.loads(self.rfile.read(int(self.headers['Content-Length'])))['messages']
        if messages[-1]['role'] != 'tool':
            self.server.started.set()
            assert self.server.release.wait(10), 'Busy-conversation check did not release the model'
            delta = {'tool_calls':[{'id':'read_restored','index':0,'type':'function','function':{
                'name':'read_file','arguments':json.dumps({'path':f'/previous/workspace/{HANDOFF}/summary.md','type':'read'}),
            }}]}
            finish = 'tool_calls'
        else:
            result = json.loads(messages[-1]['content'])
            assert result['success'] and '# Summary' in result['content'], result
            delta = {'content':'Read the restored material'}
            finish = 'stop'
        event = json.dumps({'choices':[{'index':0,'delta':delta,'finish_reason':finish}]})
        output = f'data: {event}\n\ndata: [DONE]\n\n'.encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Content-Length', str(len(output)))
        self.end_headers()
        self.wfile.write(output)

    def log_message(self, *_):
        pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def archive(document, *, invalid=None):
    encoded = json.dumps(document, ensure_ascii=False, separators=(',', ':')).encode()
    kind = document['metadata'].get('conversation_kind', 'agent')
    resources = []
    payload = {}
    for path, data in ASSETS.items():
        key = 'assets/' + digest(data)
        payload[key] = data
        resources.append({
            'kind': 'material', 'mime_type': None, 'media_id': None, 'sha256': digest(data),
            'file': key, 'paths': [path, '/previous/workspace/' + path], 'size': len(data),
        })
    key = 'assets/' + digest(PNG)
    payload[key] = PNG
    resources.append({
        'kind':'image','mime_type':'image/png','media_id':'sha256:' + digest(PNG),'sha256':digest(PNG),
        'file':key,'paths':['/previous/workspace/incorporated.png'],'size':len(PNG),
    })
    manifest = {
        'format': 'astrion-conversation', 'version': 1, 'kind': kind,
        'document_sha256': digest(encoded), 'source': {'project_path':'/previous/workspace','environment':'host'},
        'resources': resources,
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as package:
        package.writestr('manifest.json', json.dumps(manifest))
        package.writestr('conversation.json', encoded)
        for name, original in payload.items():
            if invalid == 'missing' and name == key:
                continue
            package.writestr(name, b'X' * len(original) if invalid == 'checksum' and name == key else original)
        if invalid == 'duplicate':
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', UserWarning)
                package.writestr('conversation.json', encoded)
        if invalid == 'extra':
            package.writestr('unused.txt', b'extra')
        if invalid == 'linked':
            item = zipfile.ZipInfo('assets/' + digest(b'link'))
            item.create_system = 3
            item.external_attr = 0o120777 << 16
            package.writestr(item, b'link')
    return output.getvalue()


def document(chat):
    root = {
        'id': 'conv_original', 'title': 'Fixture title', 'created_at': DATE, 'updated_at': DATE,
        'messages': [
            {'role':'user','message_id':'user','content':f'Read /previous/workspace/{HANDOFF}/summary.md.',
             'timestamp':DATE, 'media_refs':[{
                 'media_id':'sha256:' + digest(PNG), 'kind':'image','mime_type':'image/png',
                 'source_path':'/previous/workspace/incorporated.png',
             }]},
            {'role':'assistant','message_id':'left','content':'original left response','timestamp':DATE},
            {'role':'assistant','message_id':'right','content':'original right response','timestamp':DATE},
        ],
        'metadata': {'project_path':'/previous/workspace','frozen_system_prompt':'frozen fixture',
                     'future_field':['preserve',7], 'work_mode':'execute','permission_mode':'unrestricted'},
    }
    if chat:
        for message, parent, turn in zip(root['messages'], [None, 'user', 'user'], ['turn_left','turn_left','turn_right']):
            message['metadata'] = {'chat':{'parent_id':parent,'turn_id':turn}}
        root['metadata'].update(conversation_kind='chat', project_path=None, chat={
            'version':1,'revision':3,'active_turn_id':None,'latest_leaf_id':'right','summaries':[],
            'turns':[{'id':'turn_left','parent_id':'user','tip_id':'left','status':'complete'},
                     {'id':'turn_right','parent_id':'user','tip_id':'right','status':'complete'}],
            'commands':{'previous':{'fingerprint':'old'}},
            'handoffs':{'right':{'status':'completed','materials':HANDOFF,'workspace_id':'previous',
                                 'presentation_id':'old','task_id':'old','leaf_id':'right'}},
            'handoff_count':1,
        })
    return root


class Browser:
    def __init__(self, url):
        self.url = url
        self.auth = login(url)

    def send(self, method, path, body=None, *, status=200, auth=True):
        headers = dict(self.auth if auth else {})
        if body is not None:
            headers['Content-Type'] = 'application/json'
            body = json.dumps(body).encode()
        content, _ = request(self.url, method, path, headers=headers, body=body, status=status)
        payload = json.loads(content)
        if status == 200:
            assert payload['success'], payload
        return payload.get('data', payload)

    def upload(self, query, payload, operation, *, status=200, model=None):
        boundary = uuid.uuid4().hex
        fields = [('operation_id', operation.encode(), '')]
        if model:
            fields.append(('model_key', model.encode(), ''))
        fields.append(('file', payload, '; filename="fixture.astrion.zip"'))
        body = bytearray()
        for name, value, suffix in fields:
            body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"{suffix}\r\n\r\n'.encode())
            body.extend(value)
            body.extend(b'\r\n')
        body.extend(f'--{boundary}--\r\n'.encode())
        raw, _ = request(self.url, 'POST', '/api/conversations/import?' + query,
                         headers={**self.auth, 'Content-Type':f'multipart/form-data; boundary={boundary}'},
                         body=body, status=status)
        result = json.loads(raw)
        assert result['success'] == (status == 200), result
        return result.get('data', result)

    def export(self, cid, query):
        metadata = self.send('POST', f'/api/conversations/{cid}/export?{query}')
        assert metadata['filename'] == cid + '.astrion.zip'
        request(self.url, 'GET', metadata['url'], status=401)
        payload, headers = request(self.url, 'GET', metadata['url'], headers=self.auth)
        assert headers['cache-control'] == 'no-store'
        assert headers['content-type'] == 'application/zip'
        return metadata, payload


def verify_archive(payload, data, environment, kind):
    path = data / f'portable-{kind}.astrion.zip'
    path.write_bytes(payload)
    script = '''
import sys
from modules.conversation_transfer.archive import read_archive
with open(sys.argv[1], 'rb') as stream:
    document, manifest, files = read_archive(stream)
assert manifest['kind'] == sys.argv[2]
assert any('.astrion/handoff/historical/media/reference.png' in row['paths'] for row in manifest['resources'])
assert len(document['messages']) >= 4
assert files
'''
    subprocess.run([sys.executable, '-B', '-c', script, str(path), kind], cwd=ROOT, env=environment, check=True)


def exercise_agent(browser, data, environment, workspaces):
    raw = document(False)
    payload = archive(raw)
    scope = 'workspace_id=default'
    for malformed in ('missing', 'checksum', 'duplicate', 'extra', 'linked'):
        browser.upload(scope, archive(raw, invalid=malformed), 'invalid-' + malformed, status=400)
    browser.upload('kind=chat', payload, 'wrong-kind', status=400)
    browser.upload(scope, payload, 'unknown-model', model='not-available', status=400)
    imported = browser.upload(scope, payload, 'agent-import')
    assert browser.upload(scope, payload, 'agent-import') == imported
    browser.upload('workspace_id=second', payload, 'agent-import', status=409)
    cid = imported['conversation_id']
    assert cid != raw['id']
    first = data / 'conversations/default/conversations.sqlite3'
    current = stored_document(first, cid)
    assert current['messages'][:-1] == raw['messages']
    assert current['messages'][-1]['metadata']['starts_work'] is False
    assert current['metadata']['frozen_system_prompt'] == 'frozen fixture'
    assert current['metadata']['permission_mode'] == 'readonly'
    paths = current['metadata']['conversation_transfer']['paths']
    assert (workspaces[0] / paths[HANDOFF] / 'media/reference.png').read_bytes() == ASSETS[f'{HANDOFF}/media/reference.png']
    exports = workspaces[0] / '.astrion/conversation_exports'
    exports.mkdir(parents=True)
    expired = exports / ('a' * 32 + '.astrion.zip')
    retained = exports / 'other.astrion.zip'
    expired.write_bytes(b'expired')
    retained.write_bytes(b'other')
    os.utime(expired, (0, 0))
    os.utime(retained, (0, 0))
    metadata, portable = browser.export(cid, scope)
    assert not expired.exists() and retained.exists()
    assert metadata['path'].startswith('.astrion/conversation_exports/')
    request(browser.url, 'GET', metadata['url'].replace('workspace_id=default', 'workspace_id=second'),
            headers=browser.auth, status=404)
    verify_archive(portable, data, environment, 'agent')

    nav = browser.send('POST', '/api/conversation-tabs/commands', {
        'command_id':'open-imported','action':'open',
        'payload':{'conversation_id':cid,'workspace_id':'default','conversation_kind':'agent'},
    })
    tabs = browser.send('GET', '/api/conversation-tabs')['tabs']
    keys = {row['key'] for row in tabs if row['conversation_id'] == cid and row['workspace_id'] == 'default'}
    assert len(keys) == 1, nav
    browser.send('POST', '/api/conversation-tabs/commands', {
        'command_id':'attach-source','action':'attach','payload':{'key':next(iter(keys)),'renderer_id':'old-renderer'},
    })
    route = f'/api/conversations/{cid}/migrate?{scope}'
    move = {'operation_id':'move-agent','target_workspace_id':'second'}
    backup = data / 'conversation_transfer_backups'
    backup.write_bytes(b'blocked')
    browser.send('POST', route, move, status=500)
    second = data / 'conversations/second/conversations.sqlite3'
    copied = stored_document(second, cid)
    assert copied['metadata']['project_path'] == str(workspaces[1])
    assert copied['messages'][:-1] == current['messages']
    assert stored_document(first, cid) == current
    browser.send('POST', route, {**move,'operation_id':'different-operation'}, status=409)
    backup.unlink()
    assert browser.send('POST', route, move) == {'conversation_id':cid,'workspace_id':'second'}
    assert browser.send('POST', route, move) == {'conversation_id':cid,'workspace_id':'second'}
    assert stored_document(second, cid) == copied
    assert not stored_document(first, cid)['messages']
    assert len(list(backup.glob('*.json'))) == 1
    tabs = browser.send('GET', '/api/conversation-tabs')['tabs']
    assert {row['key'] for row in tabs if row['conversation_id'] == cid and row['workspace_id'] == 'second'} == keys
    browser.send('POST', '/api/conversation-tabs/commands', {
        'command_id':'old-renderer-failure','action':'fail',
        'payload':{'key':next(iter(keys)),'renderer_id':'old-renderer','error':'stale'},
    }, status=409)
    for path, original in ASSETS.items():
        mapped = copied['metadata']['conversation_transfer']['paths'][path]
        assert (workspaces[1] / mapped).read_bytes() == original
    _, moved = browser.export(cid, 'workspace_id=second')
    verify_archive(moved, data, environment, 'agent')
    return cid, copied, move


def exercise_chat(browser, data, environment):
    raw = document(True)
    payload = archive(raw)
    cid = browser.upload('kind=chat', payload, 'chat-import')['conversation_id']
    again = browser.upload('kind=chat', payload, 'chat-import')
    assert again == {'conversation_id':cid,'workspace_id':None}
    database = data / 'chat/conversations/conversations.sqlite3'
    current = stored_document(database, cid)
    assert current['messages'][:-1] == raw['messages']
    notice = current['messages'][-1]
    assert notice['metadata']['chat']['parent_id'] == 'right'
    tree = current['metadata']['chat']
    assert tree['latest_leaf_id'] == notice['message_id']
    assert tree['commands'] == {}
    state, = tree['handoffs'].values()
    assert state['source_handoff_id'] == 'right' and state['status'] == 'completed'
    assert state['countdown_enabled'] is False and 'presentation_id' not in state
    assert state['leaf_id'] == notice['message_id']
    metadata, portable = browser.export(cid, 'kind=chat')
    assert metadata['path'] is None
    verify_archive(portable, data, environment, 'chat')
    blob, _ = request(browser.url, 'GET', '/api/chat/media/sha256:' + digest(PNG), headers=browser.auth)
    assert blob == PNG
    return cid


def resume_agent(browser, provider, cid, data):
    task = browser.send('POST', '/api/tasks?workspace_id=second',
                        {'conversation_id':cid,'message':'Read the old summary path'}, status=202)
    identity = task.get('task_id') or task.get('id')
    assert identity, task
    assert provider.started.wait(8), task
    try:
        browser.send('POST', f'/api/conversations/{cid}/migrate?workspace_id=second',
                     {'operation_id':'busy','target_workspace_id':'default'}, status=409)
    finally:
        provider.release.set()
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        status = browser.send('GET', f'/api/tasks/{identity}?workspace_id=second')
        if status['status'] == 'succeeded':
            break
        assert status['status'] not in ('failed', 'cancelled'), status
        time.sleep(0.02)
    else:
        raise AssertionError('Agent did not complete after reading migrated materials')
    document = stored_document(data / 'conversations/second/conversations.sqlite3', cid)
    assert document['messages'][-1]['content'] == 'Read the restored material'
    return document


def run():
    binary = Path(os.environ.get('ASTRION_HEADLESS_BIN', ROOT / 'backend-rust/target-master/debug/astrion-headless')).resolve(strict=True)
    with tempfile.TemporaryDirectory(dir=HERE) as temporary, ThreadingHTTPServer(('127.0.0.1',0), Provider) as provider:
        root = Path(temporary)
        environment = fixture_environment(root)
        environment['ASTRION_RESOURCE_ROOT'] = str(ROOT)
        data = Path(environment['DATA_DIR'])
        second = root / 'second'
        second.mkdir()
        workspaces = (Path(environment['HOST_PROJECT_PATH']), second)
        config = Path(environment['HOST_WORKSPACES_FILE'])
        value = json.loads(config.read_text())
        value['workspaces'].append({'workspace_id':'second','label':'Second','path':str(second)})
        config.write_text(json.dumps(value))
        endpoint = f'http://127.0.0.1:{provider.server_port}/v1/chat/completions'
        provider.started, provider.release = threading.Event(), threading.Event()
        thread = threading.Thread(target=provider.serve_forever, daemon=True)
        thread.start()
        try:
            with rust(environment, binary, endpoint, root / 'transfer.log', web=True) as base:
                browser = Browser(base)
                cid, _, move = exercise_agent(browser, data, environment, workspaces)
                chat = exercise_chat(browser, data, environment)
                saved = resume_agent(browser, provider, cid, data)
            with rust(environment, binary, endpoint, root / 'restart.log', web=True) as base:
                browser = Browser(base)
                route = f'/api/conversations/{cid}/migrate?workspace_id=default'
                assert browser.send('POST', route, move)['workspace_id'] == 'second'
                assert stored_document(data / 'conversations/second/conversations.sqlite3', cid) == saved
                assert browser.upload('kind=chat', archive(document(True)), 'chat-import')['conversation_id'] == chat
        finally:
            provider.release.set()
            provider.shutdown()
            thread.join()
    print('Portable Agent/Chat archives, resources, failure recovery and stable desktop relocation passed.')


if __name__ == '__main__':
    run()
