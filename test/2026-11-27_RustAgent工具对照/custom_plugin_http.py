"""Verify that an installed legacy tool is neither exposed nor executable."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

from knowledge_media_http import Browser, HERE, LocalServices, ROOT, call, fixture_environment, model_catalog_host, wait_for_task

PLUGIN = 'format_fixture'
TEXT = 'Built-in tool result'
LEGACY_ATTEMPT = 'Attempt to call a retired tool'


class Services(LocalServices):
    def calls(self, body):
        definitions = {tool['function']['name']:tool['function'] for tool in body['tools']}
        assert PLUGIN not in definitions, definitions.keys()
        assert 'astrion_echo' in definitions, definitions.keys()
        messages = body['messages']
        if any(message['role'] == 'user' and message['content'] == LEGACY_ATTEMPT for message in messages):
            return [call(PLUGIN, 'retired', {'left':8, 'right':9, 'text':TEXT})]
        results = {message['tool_call_id']:message['content'] for message in messages if message['role'] == 'tool'}
        if 'echo' not in results:
            return [call('astrion_echo', 'echo', {'text':TEXT})]
        assert results['echo'] == TEXT, results
        return 'The built-in tool returned its result'


def wait_for_rejected_call(browser, task):
    metadata = task.get('data', task)
    identity = metadata.get('task_id') or metadata['id']
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        payload = browser.api('GET', f'/api/tasks/{identity}?workspace_id=default')
        row = payload.get('data', payload)
        if row['status'] == 'failed':
            assert row['error'] == f'Unknown tool: {PLUGIN}', row
            return
        assert row['status'] not in ('succeeded', 'cancelled'), row
        time.sleep(.02)
    raise AssertionError('The retired tool attempt did not finish')


def run():
    binary = Path(os.environ.get('ASTRION_HEADLESS_BIN', ROOT/'backend-rust/target-master/debug/astrion-headless')).resolve(strict=True)
    with tempfile.TemporaryDirectory(dir=HERE) as temporary, ThreadingHTTPServer(('127.0.0.1',0), Services) as service:
        root = Path(temporary)
        env = fixture_environment(root)
        env.update(ASTRION_RESOURCE_ROOT=str(ROOT))
        data = Path(env['DATA_DIR'])
        directory = data/'custom_tools'/PLUGIN
        directory.mkdir(parents=True)
        (data/'personalization.json').write_text(json.dumps({'auto_generate_title':False}))
        (directory/'definition.json').write_text(json.dumps({
            'id':PLUGIN,'description':'Add two integers and format the provided label',
            'parameters':{'type':'object','properties':{'left':{'type':'integer'},'right':{'type':'integer'},
                          'text':{'type':'string'}}},'required':['left','right','text'],'timeout':5,
        }))
        source = "a = {left}\nb = {right}\nlabel = '{text}'\nprint(str(a+b) + ': ' + label)\n"
        (directory/'execution.py').write_text(source)
        (directory/'return.json').write_text(json.dumps({'template':'Fixture result {output}'}))
        service.origin = f'http://127.0.0.1:{service.server_port}'
        service.errors = []
        config = Path(env['DEPLOY_CONFIG_DIR'])/'custom_models.json'
        config.write_text(json.dumps({'models':[{'model_name':'chat-local','model_id':'chat-local',
            'url':service.origin+'/v1','apikey':'fixture-only'}]}))
        worker = threading.Thread(target=service.serve_forever, daemon=True)
        worker.start()
        try:
            with model_catalog_host(binary,env,root/'host.log') as listening:
                browser = Browser(listening)
                try:
                    created = browser.api('POST','/api/conversations?workspace_id=default',{'settings':{
                        'model_key':'chat-local','work_mode':'execute','permission_mode':'unrestricted',
                        'execution_mode':'sandbox','network_permission':'restricted','run_mode':'fast',
                        'thinking_mode':False,'reasoning_effort':None,
                    }},status=201)
                    cid = created['conversation_id']
                    accepted = browser.api('POST','/api/tasks?workspace_id=default',{'message':'Run a built-in tool',
                        'model_key':'chat-local','conversation_id':cid},status=202)
                    assert wait_for_task(browser,accepted) == cid
                    attempted = browser.api('POST','/api/tasks?workspace_id=default',{'message':LEGACY_ATTEMPT,
                        'model_key':'chat-local','conversation_id':cid},status=202)
                    wait_for_rejected_call(browser, attempted)
                    assert (directory/'execution.py').read_text() == source
                except Exception as error:
                    raise AssertionError((service.errors, (root/'host.log').read_text())) from error
                assert not service.errors, service.errors
        finally:
            service.shutdown()
            worker.join()
    print('PASS: built-in tool executed, legacy tool stayed off the menu, and direct stale calls failed')


if __name__ == '__main__':
    run()
