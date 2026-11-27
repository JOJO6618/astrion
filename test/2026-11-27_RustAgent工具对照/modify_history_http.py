"""Synthetic model goes through real Browser Agent admission and native file writes."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from knowledge_media_http import (
    Browser, LocalServices, ROOT, call, fixture_environment, model_catalog_host,
    stored_document, wait_for_task,
)


class Services(LocalServices):
    def calls(self, body):
        messages = body['messages']
        system = next(m['content'] for m in messages if m['role'] == 'system')
        user = next(m['content'] for m in messages if m['role'] == 'user')
        enabled = user.startswith('Archive')
        assert ('本对话修改留痕' in system) == enabled, system
        out = {m['tool_call_id']: json.loads(m['content']) for m in messages if m['role'] == 'tool'}
        if 'create' not in out:
            return [call('write_file', 'create', {'file_path':'notes.txt', 'content':'interim\n'})]
        assert out['create']['success'], out
        if 'replace' not in out:
            return [call('write_file', 'replace', {'file_path':'notes.txt', 'content':'final\n'})]
        assert out['replace']['success'], out
        return 'Both fixture writes completed.'


def run():
    binary = Path(os.environ.get('ASTRION_HEADLESS_BIN',
        ROOT/'backend-rust/target-master/debug/astrion-headless')).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix='astrion-history-') as temporary, \
            ThreadingHTTPServer(('127.0.0.1', 0), Services) as service:
        root = Path(temporary)
        env = fixture_environment(root)
        env['ASTRION_RESOURCE_ROOT'] = str(ROOT)
        data = Path(env['DATA_DIR'])
        data.mkdir()
        workspace = Path(env['HOST_PROJECT_PATH'])
        service.errors = []
        preferences = data/'personalization.json'
        config = Path(env['DEPLOY_CONFIG_DIR'])/'custom_models.json'
        config.write_text(json.dumps({'models':[{'model_name':'chat-local','model_id':'chat-local',
            'url':f'http://127.0.0.1:{service.server_port}/v1','apikey':'fixture-only'}]}))
        worker = threading.Thread(target=service.serve_forever, daemon=True)
        worker.start()
        try:
            with model_catalog_host(binary, env, root/'host.log') as listening:
                browser = Browser(listening)
                for enabled in (True, False):
                    path = workspace/'notes.txt'
                    path.unlink(missing_ok=True)
                    preferences.write_text(json.dumps({
                        'auto_generate_title':False, 'versioning_enabled_by_default':False,
                        'modify_history_enabled': enabled,
                    }))
                    created = browser.api('POST','/api/conversations?workspace_id=default',{'settings':{
                        'model_key':'chat-local','work_mode':'execute','permission_mode':'unrestricted',
                        'execution_mode':'sandbox','network_permission':'restricted','run_mode':'fast',
                        'thinking_mode':False,'reasoning_effort':None,
                    }}, status=201)
                    cid = created['conversation_id']
                    reply = browser.api('POST','/api/tasks?workspace_id=default', {
                        'message':'Archive both edits' if enabled else 'Keep only UI history',
                        'conversation_id':cid, 'model_key':'chat-local',
                    }, status=202)
                    assert wait_for_task(browser, reply) == cid, service.errors
                    document = stored_document(data/'conversations/default/conversations.sqlite3', cid)
                    user = next(m for m in document['messages'] if m['role'] == 'user')
                    entry, = user['metadata']['edit_summary']['files']
                    assert entry['status'] == 'added' and entry['added'] == 1, entry
                    assert entry['lines'] == [{'type':'add','content':'final','new_no':1}], entry
                    edited, = document['metadata']['edited_files']
                    assert edited['path'] == 'notes.txt' and edited['op'] == 'write', edited
                    history = workspace/'.astrion/modify_history'/cid
                    if enabled:
                        patch, = history.glob('*.diff')
                        text = patch.read_text(encoding='utf-8')
                        assert '+final' in text and 'interim' not in text and '进行中' not in text, text
                        subprocess.run(['git','apply','-R',str(patch)], cwd=workspace, check=True)
                        assert not path.exists()
                        subprocess.run(['git','apply',str(patch)], cwd=workspace, check=True)
                        assert path.read_text() == 'final\n'
                    else:
                        assert not history.exists(), history
        finally:
            service.shutdown()
            worker.join()
        assert not service.errors, service.errors
    print('Production Agent: persisted cards and Quick list, two writes yield one apply/reverse patch, and archive setting is independent.')


if __name__ == '__main__':
    run()
