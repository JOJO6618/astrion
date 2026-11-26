"""Exercise the Python composer store and the authenticated Rust Web route on one data root."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from http.cookiejar import CookieJar
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

from agent_process_roundtrip import HERE, ROOT, fixture_environment, rust, writer


def python_draft(action: str, cid: str) -> None:
    sys.path.insert(0, str(ROOT))
    from flask import Flask
    from modules.composer_drafts import read_draft, save_draft
    from server.composer_drafts import draft_payload, resolve_draft
    from server.conversation import _resolve_input_draft_path
    from utils.conversation_manager import ConversationManager

    data = Path(os.environ['DATA_DIR'])
    manager = ConversationManager(base_dir=str(data), project_path=os.environ['HOST_PROJECT_PATH'], read_only=True)
    workspace = SimpleNamespace(workspace_id='default', username='host', data_dir=data)
    context = SimpleNamespace(workspace_id='default', manager_for=lambda _: manager)
    with Flask(__name__).test_request_context('/api/input-draft', query_string={'conversation_id': cid}):
        path, scope = resolve_draft(context, workspace, 'host')
        if action == 'seed':
            payload = draft_payload({'content': 'Python 原稿', 'files': ['附件.txt']})
            assert save_draft(path, {**payload, 'scope': scope}, client_id='window-A', sequence=1)
            legacy, _ = _resolve_input_draft_path(workspace, 'host')
            assert save_draft(legacy, {'content': '旧版未发送', 'images': ['图.png']})
        else:
            draft = read_draft(path)
            assert draft['content'] == 'Rust 保存' and draft['writer_sequences'] == {'window-A': 2}
            assert draft['scope'] == scope and draft['files'] == ['附件.txt'] and draft['revision'] == 2
            assert save_draft(path, {**draft, 'content': 'Python 接管'}, base_revision=2)
            assert read_draft(path)['revision'] == 3


def run() -> None:
    binary = Path(os.environ['ASTRION_HEADLESS_BIN']).resolve(strict=True)
    with tempfile.TemporaryDirectory(dir=HERE) as temporary:
        root = Path(temporary)
        env = fixture_environment(root)
        env['ASTRION_RESOURCE_ROOT'] = str(ROOT)
        cid = writer(env, 'seed')
        def python_phase(action: str) -> None:
            subprocess.run([sys.executable, '-B', __file__, action, cid], check=True, cwd=ROOT, env=env, timeout=30)
        python_phase('seed')
        with rust(env, binary, 'http://127.0.0.1:1/v1/chat/completions', root / 'draft.log', web=True) as url:
            client = build_opener(HTTPCookieProcessor(CookieJar()))
            csrf = ''
            def api(path: str, body: dict | None = None, *, status: int = 200) -> dict:
                headers = {'Origin': url, 'X-CSRF-Token': csrf, 'Content-Type': 'application/json'}
                req = Request(url + path, headers=headers, data=json.dumps(body).encode() if body is not None else None)
                try:
                    response = client.open(req, timeout=5)
                except HTTPError as error:
                    response = error
                with response:
                    result = json.load(response)
                    assert response.status == status, (path, response.status, result)
                    return result
            csrf = api('/api/csrf-token')['token']
            api('/host-login', {})
            csrf = api('/api/csrf-token')['token']
            path = '/api/input-draft?conversation_id=' + cid
            draft = api(path)['data']
            assert (draft['content'], draft['files'], draft['revision']) == ('Python 原稿', ['附件.txt'], 1)
            assert 'writer_sequences' not in draft
            body = {'content': 'Rust 保存', 'files': draft['files'], 'client_id': 'window-A', 'sequence': 2, 'base_revision': 1}
            assert api(path, body)['data'] == {'saved': True, 'revision': 2}
            assert api(path, body)['data'] == {'saved': False, 'revision': 2}
            api(path, {**body, 'client_id': 'window-B', 'content': '过期窗口'}, status=409)
            assert api(path)['data']['content'] == body['content']
            new_path = '/api/input-draft?conversation_id=new'
            migrated = api(new_path)['data']
            assert (migrated['content'], migrated['images'], migrated['revision']) == ('旧版未发送', ['图.png'], 1)
            assert api(new_path)['data'] == migrated
        python_phase('resume')
        print('Python draft → Rust Web CAS and legacy migration → Python save passed.')


if __name__ == '__main__':
    if len(sys.argv) == 3:
        python_draft(*sys.argv[1:])
    else:
        run()
