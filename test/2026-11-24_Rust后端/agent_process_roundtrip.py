"""Use two real Rust server processes between two production Python conversation writers."""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from instance_lock_e2e import HERE, ROOT, fixture_environment

PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII='
MODEL = 'process-fixture'
FROZEN = '原样冻结\nAgent process round trip.\n'


def python_writer(action: str, cid: str = '') -> None:
    sys.path.insert(0, str(ROOT))
    from server.instance_lock import acquire_instance_lock
    from utils.conversation_manager import ConversationManager

    data = Path(os.environ['DATA_DIR'])
    workspace = os.environ['HOST_PROJECT_PATH']
    with acquire_instance_lock(data):
        manager = ConversationManager(base_dir=str(data), project_path=workspace, read_only=True)
        if action == 'seed':
            from utils.media_store import MediaStore
            reference = MediaStore(data).store_bytes(base64.b64decode(PNG), kind='image', mime_type='image/png')
            message = {'role': 'user', 'content': 'Python seed', 'message_id': 'msg_original',
                       'metadata': {'media_refs': [{'source_path': 'old/assets/图.png'}, reference]}}
            metadata = {'unknown_v9': [1, '保留'], 'frozen_system_prompt': None}
            mode = os.environ.get('FIXTURE_FREEZE_MODE', 'complete')
            if mode == 'complete':
                metadata['frozen_system_prompt'] = FROZEN
            elif mode == 'legacy':
                metadata.update(frozen_main_system_prompt='旧主提示\n', frozen_disabled_tools_prompt='旧工具约束')
            cid = manager.create_conversation(workspace, model_key=MODEL, initial_messages=[message],
                                              metadata_overrides=metadata)
        else:
            raw = manager.read_conversation(cid)
            expected = Path(os.environ['FIXTURE_PROMPT']).read_text() if 'FIXTURE_PROMPT' in os.environ else FROZEN
            assert raw['metadata']['frozen_system_prompt'] == expected
            assert raw['metadata']['unknown_v9'] == [1, '保留']
            assert raw['messages'][0]['metadata']['media_refs'][0]['source_path'] == 'old/assets/图.png'
            assert [m['content'] for m in raw['messages']] == ['Python seed','turn one','ack turn one','turn two','ack turn two']
            messages = [*raw['messages'], {'role':'user','content':'Python resumed','message_id':'msg_after_restart'}]
            assert manager.save_conversation(cid, messages)
            assert manager.read_conversation(cid)['messages'][-1]['message_id'] == 'msg_after_restart'
        print('SESSION='+cid, flush=True)


class MockModel(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        assert self.path == '/v1/chat/completions' and body['model'] == MODEL
        system = body['messages'][0]
        assert system['role'] == 'system' and isinstance(system['content'], str)
        self.server.received_prompts.append(system['content'])
        seed = next(m for m in body['messages'] if m['role'] == 'user')
        assert seed['content'] == [{'type': 'text', 'text': 'Python seed'},
                                   {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + PNG}}], seed
        answer = 'ack '+body['messages'][-1]['content']
        chunk = {'choices':[{'index':0,'delta':{'content':answer},'finish_reason':None}]}
        text = ('data: '+json.dumps(chunk)+'\n\ndata: [DONE]\n\n').encode()
        self.send_response(200)
        self.send_header('Content-Type','text/event-stream')
        self.send_header('Content-Length',str(len(text)))
        self.end_headers()
        self.wfile.write(text)

    def log_message(self, *args: object) -> None:
        pass


def writer(env: dict[str,str], action: str, cid: str='') -> str:
    result = subprocess.run([sys.executable, '-B', __file__, action, cid], env=env, cwd=ROOT,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    return next(line.removeprefix('SESSION=') for line in result.stdout.splitlines() if line.startswith('SESSION='))


@contextmanager
def rust(env: dict[str,str], binary: Path, endpoint: str, log: Path, *, web: bool = False):
    args = [str(binary), '--port','0','--workspace',env['HOST_PROJECT_PATH'],'--data-dir',env['DATA_DIR'],
            '--model',MODEL,'--endpoint',endpoint]
    if web:
        args.extend(['--web', '--static-root', str(ROOT / 'static')])
    with log.open('w') as output:
        proc = subprocess.Popen(args, env={**env,'ASTRION_DESKTOP_STOP_STDIN':'1'}, stdin=subprocess.PIPE,
                                stdout=output, stderr=subprocess.STDOUT)
        try:
            for _ in range(500):
                logs=log.read_text()
                assert proc.poll() is None, logs
                ready = re.search(r'Astrion Rust server listening on (127\.0\.0\.1:\d+)', logs)
                if ready:
                    yield 'http://'+ready.group(1)
                    return
                time.sleep(0.02)
            raise AssertionError(log.read_text())
        finally:
            proc.stdin.close()
            assert proc.wait(timeout=15)==0,log.read_text()


def run(mode: str) -> None:
    binary = Path(os.environ.get('ASTRION_HEADLESS_BIN',ROOT/'backend-rust/target/debug/astrion-headless')).resolve(strict=True)
    with tempfile.TemporaryDirectory(dir=HERE) as temp, ThreadingHTTPServer(('127.0.0.1',0),MockModel) as provider:
        provider.received_prompts = []
        worker=threading.Thread(target=provider.serve_forever,daemon=True)
        worker.start()
        try:
            directory=Path(temp)
            env=fixture_environment(directory)
            env['ASTRION_RESOURCE_ROOT']=str(ROOT)
            env['FIXTURE_FREEZE_MODE'] = mode
            env['FIXTURE_PROMPT'] = str(directory / 'first-prompt.txt')
            cid=writer(env,'seed')
            data=Path(env['DATA_DIR'])
            path=data/'conversations'/'default'/(cid+'.json')
            original=json.loads(path.read_text())
            endpoint=f'http://127.0.0.1:{provider.server_port}/v1/chat/completions'
            for message, count in [('turn one',1),('turn two',3)]:
                before = (path.stat().st_mtime_ns,path.read_bytes())
                with rust(env,binary,endpoint,directory/'rust.log') as url:
                    headers={'Authorization':'Bearer '+(data/'host_api_token').read_text().strip(),
                             'X-Astrion-Workspace-Id':'default','Content-Type':'application/json'}
                    def api(route: str, payload: dict|None=None) -> dict:
                        req=Request(url+route,data=None if payload is None else json.dumps(payload).encode(),headers=headers)
                        try:
                            with urlopen(req,timeout=4) as response:
                                return json.load(response)
                        except HTTPError as error:
                            raise AssertionError(f'{route} returned {error.code}: {error.read().decode()}') from error
                    history=api('/api/runtime/sessions/'+cid+'/history')['conversation']
                    assert len(history['messages'])==count
                    assert before==(path.stat().st_mtime_ns,path.read_bytes()), 'read changed the original file'
                    accepted=api('/api/tasks',{'conversation_id':cid,'message':message,'model_key':MODEL})
                    task=accepted['data']['task_id'] if 'data' in accepted else accepted['task_id']
                    for _ in range(500):
                        state=api('/api/tasks/'+task+'?from=0')['data']
                        if state['status']!='running': break
                        time.sleep(.02)
                    assert state['status']=='succeeded',state
                raw=json.loads(path.read_text())
                assert raw['id']==cid and raw['messages'][0]==original['messages'][0]
                assert raw['messages'][-1]['content']=='ack '+message
                prompt = raw['metadata']['frozen_system_prompt']
                assert prompt == provider.received_prompts[-1]
                if count == 1:
                    if mode == 'complete':
                        assert prompt == FROZEN
                    elif mode == 'legacy':
                        sys.path.insert(0, str(ROOT))
                        from modules.system_prompt_snapshot import legacy_system_text
                        assert prompt == legacy_system_text(original['metadata'])
                    else:
                        static = (ROOT / 'prompts/main_agent/static.txt').read_text().strip()
                        assert static in prompt and env['HOST_PROJECT_PATH'] in prompt
                    Path(env['FIXTURE_PROMPT']).write_text(prompt)
            assert provider.received_prompts == [Path(env['FIXTURE_PROMPT']).read_text()] * 2
            assert writer(env,'resume',cid)==cid
            print(f'{mode}: Python → Rust → restarted Rust → Python passed.')
        finally:
            provider.shutdown()
            worker.join()


if __name__=='__main__':
    if len(sys.argv)>1: python_writer(*sys.argv[1:])
    else:
        for mode in ('complete', 'legacy', 'fresh'):
            run(mode)
