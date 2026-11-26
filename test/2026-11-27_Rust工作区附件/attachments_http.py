"""Exercise workspace uploads, scoped downloads and Agent media through production HTTP."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shlex
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / '2026-11-24_Rust后端'))
sys.path.insert(0, str(HERE.parent / '2026-11-26_RustChat媒体'))
from agent_process_roundtrip import ROOT, fixture_environment, rust
from media_roundtrip import login, png_image, request

PNG = png_image((201, 35, 125))


class Provider(BaseHTTPRequestHandler):
    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        user = next(message['content'] for message in reversed(payload['messages'])
                    if message['role'] == 'user' and isinstance(message['content'], list))
        self.server.inputs.append(user)
        delta = {'choices':[{'index':0,'delta':{'content':'Received the attachment'},'finish_reason':'stop'}]}
        data = ('data: ' + json.dumps(delta) + '\n\ndata: [DONE]\n\n').encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_):
        pass


class Browser:
    def __init__(self, base):
        self.base = base
        self.auth = login(base)

    def api(self, method, route, body=None, *, status=200, headers=None):
        send = {**self.auth, **(headers or {})}
        if body is not None:
            body = json.dumps(body).encode()
            send['Content-Type'] = 'application/json'
        content, _ = request(self.base, method, route, headers=send, body=body, status=status)
        return json.loads(content)

    def upload(self, name, data, *, status=200, mime='application/octet-stream', workspace='default'):
        boundary = uuid.uuid4().hex
        body = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="placeholder"\r\n'
            f'Content-Type: {mime}\r\n\r\n'
        ).encode() + data
        if name is not None:
            body += f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="filename"\r\n\r\n{name}'.encode()
        body += f'\r\n--{boundary}--\r\n'.encode()
        raw, _ = request(self.base, 'POST', '/api/upload', headers={
            **self.auth, 'X-Astrion-Workspace-Id':workspace,
            'Content-Type':f'multipart/form-data; boundary={boundary}',
        }, body=body, status=status)
        result = json.loads(raw)
        assert result['success'] == (status == 200), result
        return result

    def file(self, path, *, status=200, headers=None, workspace='default'):
        sep = '&' if '?' in path else '?'
        return request(self.base, 'GET', path + sep + 'workspace_id=' + workspace,
                       headers={**self.auth, **(headers or {})}, status=status)


def fixtures(root):
    environment = fixture_environment(root)
    environment['ASTRION_RESOURCE_ROOT'] = str(ROOT)
    config = Path(environment['HOST_WORKSPACES_FILE'])
    value = json.loads(config.read_text())
    second = root / 'second'
    second.mkdir()
    value['workspaces'].append({'workspace_id':'second','label':'Second','path':str(second)})
    config.write_text(json.dumps(value))
    return environment


def wait_for_task(browser, task):
    metadata = task.get('data', task)
    identity = metadata.get('task_id') or metadata['id']
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        payload = browser.api('GET', f'/api/tasks/{identity}?workspace_id=default')
        row = payload.get('data', payload)
        if row['status'] == 'succeeded':
            return metadata['conversation_id']
        assert row['status'] not in ('failed', 'cancelled'), row
        time.sleep(.02)
    raise AssertionError('Agent task did not complete')


def practice(browser, environment, provider):
    data = Path(environment['DATA_DIR'])
    project = Path(environment['HOST_PROJECT_PATH'])
    request(browser.base, 'GET', '/user_upload/example.png', status=401)
    request(browser.base, 'GET', '/workspace/example.png', status=401)

    first = browser.upload('../../资料.txt', b'first', mime='text/plain')
    second = browser.upload('资料.txt', b'second', mime='text/plain')
    image = browser.upload('图像.png', PNG, mime='image/png')
    assert first['path'] == '.astrion/user_upload/资料.txt'
    assert second['path'] == '.astrion/user_upload/资料_1.txt'
    assert first['sha256'] == hashlib.sha256(b'first').hexdigest()
    assert first['size'] == 5 and first['scan']['status'] == 'skipped'
    assert browser.upload('unsafe-extension.invalid', b'raw', status=400)['code'] == 'extension_forbidden'

    image_route = '/user_upload/' + quote('图像.png')
    assert browser.file(image_route)[0] == PNG
    assert browser.file(image_route, workspace='second', status=404)[0]
    assert browser.file('/workspace/' + quote(image['path']))[0] == PNG
    assert browser.file('/workspace/' + quote(first['path']), status=415)[0]
    assert browser.file(image_route, status=206, headers={'Range':'bytes=0-7'})[0] == PNG[:8]
    assert browser.file('/api/file/content?path=' + quote(first['path']))[0] == b'first'
    assert browser.file('/api/gui/files/download?path=' + quote(second['path']))[0] == b'second'
    browser.file('/user_upload/%2e%2e/hidden.png', status=403)
    if os.name != 'nt':
        outside = project.parent / 'outside.png'
        outside.write_bytes(PNG)
        (project / '.astrion/user_upload/linked.png').symlink_to(outside)
        browser.file('/user_upload/linked.png', status=404)

    policy = data / 'admin_policy.json'
    policy.write_text(json.dumps({'global':{'ui_blocks':{'block_upload':True}}}))
    assert browser.upload('blocked.png', PNG, mime='image/png', status=403)['code'] == 'upload_disabled'
    policy.unlink()

    task = browser.api('POST', '/api/tasks?workspace_id=default', {
        'message':'Describe the attachments', 'images':[{'path':image['path'],'mime_type':'image/png'}],
        'files':[first['path']],
    }, status=202)
    cid = wait_for_task(browser, task)
    history = browser.api('GET', f'/api/conversations/{cid}/messages?workspace_id=default')['data']
    message = next(msg for msg in history['messages'] if msg['role'] == 'user')
    assert first['path'] in json.dumps(message['metadata']['files'], ensure_ascii=False)
    references = message.get('media_refs') or message['metadata']['media_refs']
    reference, = references
    assert reference['source_path'] == image['path']
    assert browser.file('/api/conversations/media/' + quote(reference['media_id']))[0] == PNG
    model_message, = provider.inputs
    image_part, = [part for part in model_message if part['type'] == 'image_url']
    assert base64.b64decode(image_part['image_url']['url'].split(',',1)[1]) == PNG
    assert history['messages'][-1]['content'] == 'Received the attachment'


def run():
    binary = Path(os.environ.get('ASTRION_HEADLESS_BIN', ROOT / 'backend-rust/target-master/debug/astrion-headless')).resolve(strict=True)
    with tempfile.TemporaryDirectory(dir=HERE) as temporary, ThreadingHTTPServer(('127.0.0.1', 0), Provider) as provider:
        root = Path(temporary)
        environment = fixtures(root)
        endpoint = f'http://127.0.0.1:{provider.server_port}/v1/chat/completions'
        provider.inputs = []
        thread = threading.Thread(target=provider.serve_forever, daemon=True)
        thread.start()
        try:
            with rust(environment, binary, endpoint, root/'uploads.log', web=True) as base:
                practice(Browser(base), environment, provider)
            helper = root / 'scanner fixture.py'
            helper.write_text('import sys\nfrom pathlib import Path\nsys.exit(1 if Path(sys.argv[1]).read_bytes() == b"reject" else 0)\n')
            scanning = {**environment, 'UPLOAD_CLAMAV_ENABLED':'1', 'UPLOAD_CLAMAV_BIN':sys.executable,
                        'UPLOAD_CLAMAV_ARGS':shlex.quote(str(helper))}
            with rust(scanning, binary, endpoint, root/'scanner.log', web=True) as base:
                browser = Browser(base)
                assert browser.upload('passed.txt', b'accept')['scan']['status'] == 'passed'
                assert browser.upload('failed.txt', b'reject', status=400)['code'] == 'scan_failed'
                browser.file('/user_upload/failed.txt', status=404)
                assert not list((Path(environment['DATA_DIR'])/'upload_quarantine').iterdir())
        finally:
            provider.shutdown()
            thread.join()
    print('Workspace upload, isolated scanning, private downloads and Agent attachment display passed.')


if __name__ == '__main__':
    run()
