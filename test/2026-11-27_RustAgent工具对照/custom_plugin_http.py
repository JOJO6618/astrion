"""Read a mathematical fixture from the Host registry and run it in the sandbox."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from knowledge_media_http import Browser, HERE, LocalServices, ROOT, call, fixture_environment, model_catalog_host, wait_for_task

PLUGIN = 'format_fixture'
TEXT = 'Fixture "quoted" and \'apostrophe\'\nsecond line'


class Services(LocalServices):
    def calls(self, body):
        definitions = {tool['function']['name']:tool['function'] for tool in body['tools']}
        assert definitions[PLUGIN]['parameters']['properties']['left']['type'] == 'integer'
        results = {m['tool_call_id']:json.loads(m['content']) for m in body['messages'] if m['role']=='tool'}
        if 'math' not in results:
            return [call(PLUGIN,'math',{'left':8,'right':9,'text':TEXT}),
                    call(PLUGIN,'invalid',{'left':'8','right':9,'text':TEXT})]
        result = results['math']
        assert result['success'], result
        assert result['custom_tool'] and result['tool_id'] == PLUGIN, result
        assert result['output'] == f'17: {TEXT}\n', result
        assert result['message'] == f'Fixture result {result["output"]}', result
        assert results['invalid']['success'] is False, results
        return 'The installed sandboxed Python fixture returned the expected sum'


def run():
    binary = Path(os.environ.get('ASTRION_HEADLESS_BIN', ROOT/'backend-rust/target-master/debug/astrion-headless')).resolve(strict=True)
    with tempfile.TemporaryDirectory(dir=HERE) as temporary, ThreadingHTTPServer(('127.0.0.1',0), Services) as service:
        root = Path(temporary)
        env = fixture_environment(root)
        env.update(ASTRION_RESOURCE_ROOT=str(ROOT), ASTRION_CUSTOM_PYTHON=str(Path(sys.executable).resolve()),
                   CUSTOM_TOOLS_ENABLED='1')
        data = Path(env['DATA_DIR'])
        directory = data/'custom_tools'/PLUGIN
        directory.mkdir(parents=True)
        (data/'personalization.json').write_text(json.dumps({'auto_generate_title':False}))
        (directory/'definition.json').write_text(json.dumps({
            'id':PLUGIN,'description':'Add two integers and format the provided label',
            'parameters':{'type':'object','properties':{'left':{'type':'integer'},'right':{'type':'integer'},
                          'text':{'type':'string'}}},'required':['left','right','text'],'timeout':5,
        }))
        (directory/'execution.py').write_text("a = {left}\nb = {right}\nlabel = '{text}'\nprint(str(a+b) + ': ' + label)\n")
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
                    accepted = browser.api('POST','/api/tasks?workspace_id=default',{'message':'Run the plugin fixture',
                        'model_key':'chat-local','conversation_id':cid},status=202)
                    assert wait_for_task(browser,accepted) == cid
                except Exception as error:
                    raise AssertionError((service.errors, (root/'host.log').read_text())) from error
                assert not service.errors, service.errors
        finally:
            service.shutdown()
            worker.join()
    print('Host registry exposed and ran the sandboxed Python fixture with typed model input.')


if __name__ == '__main__':
    run()
