"""A real child model loop shares the parent cost ledger without changing main tokens."""
from __future__ import annotations

import datetime
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent/'2026-11-27_RustAgent工具对照'))
from knowledge_media_http import Browser, LocalServices, ROOT, call, fixture_environment, model_catalog_host, wait_for_task

CHILD = 'Calculate the fixture request usage'
MAIN = 'Delegate the fixture usage check'


class Service(LocalServices):
    def do_POST(self):
        try:
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            messages = body['messages']
            text = [message['content'] for message in messages
                    if message['role'] == 'user' and isinstance(message['content'],str)]
            child = any(CHILD in item for item in text[:2])
            results = {message['tool_call_id']:json.loads(message['content']) for message in messages if message['role']=='tool'}
            usage = {'prompt_tokens':10,'completion_tokens':2,'total_tokens':12}
            if child:
                if 'todo' in results:
                    assert results['todo']['success'], results
                    payload = 'Child finished after one request without provider usage'
                    usage = None
                    self.server.child_finished.set()
                else:
                    payload = [call('todo_create','todo',{'overview':'Metrology fixture','tasks':[{'title':'Acknowledge fixture'}]})]
                    usage = {'prompt_tokens':20,'completion_tokens':3,'total_tokens':23,
                             'prompt_tokens_details':{'cached_tokens':5}}
            else:
                assert text[0] == MAIN, text
                self.server.main_responses += 1
                if 'create' in results:
                    assert results['create']['success'], results
                    assert self.server.child_finished.wait(6), self.server.errors
                    payload = 'Parent received the child result'
                else:
                    payload = [call('create_sub_agent','create',{'name':'Accounting fixture','summary':'Fixture metrology',
                        'task':CHILD,'intelligence':'same','access_level':'workspace_write','working_dir':'.'})]
            if isinstance(payload,str):
                delta, finish = {'content':payload},'stop'
            else:
                for index,item in enumerate(payload):
                    item['index'] = index
                delta, finish = {'tool_calls':payload},'tool_calls'
            output = {'choices':[{'index':0,'delta':delta,'finish_reason':finish}]}
            if usage is not None:
                output['usage'] = usage
            self.write(200,'text/event-stream',f'data: {json.dumps(output)}\n\ndata: [DONE]\n\n'.encode())
        except AssertionError as error:
            self.server.errors.append(str(error))
            self.write(400,'application/json',b'{"error":"fixture assertion failed"}')


def run():
    binary = Path(os.environ.get('ASTRION_HEADLESS_BIN',ROOT/'backend-rust/target-master/debug/astrion-headless')).resolve(strict=True)
    with tempfile.TemporaryDirectory(dir=HERE) as tmp, ThreadingHTTPServer(('127.0.0.1',0),Service) as service:
        root = Path(tmp)
        env = fixture_environment(root)
        env['ASTRION_RESOURCE_ROOT'] = str(ROOT)
        data = Path(env['DATA_DIR'])
        data.mkdir()
        (data/'personalization.json').write_text(json.dumps({'auto_generate_title':False}))
        now = datetime.datetime.now(datetime.timezone.utc)
        (data/'exchange_rates.json').write_text(json.dumps({'usd_cny':7.1,
            'rate_date':now.date().isoformat(),'fetched_at':now.isoformat(),'source':'fixture'}))
        origin = f'http://127.0.0.1:{service.server_port}'
        config = Path(env['DEPLOY_CONFIG_DIR'])/'custom_models.json'
        config.write_text(json.dumps({'models':[{'model_name':'chat-local','model_id':'chat-local',
            'url':origin+'/v1','apikey':'fixture-only'}]}))
        service.main_responses = 0
        service.errors = []
        service.child_finished = threading.Event()
        worker = threading.Thread(target=service.serve_forever,daemon=True)
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
                    accepted = browser.api('POST','/api/tasks?workspace_id=default',{'message':MAIN,
                        'model_key':'chat-local','conversation_id':cid},status=202)
                    assert wait_for_task(browser,accepted) == cid
                    ledger_file = data/'api_costs'/f'{cid}.json'
                    deadline = time.monotonic()+6
                    while time.monotonic()<deadline:
                        ledger = json.loads(ledger_file.read_text())
                        child = next((actor for actor in ledger['actors'].values() if actor['kind']=='sub_agent'),None)
                        if child and child['requests']==2:
                            break
                        time.sleep(.03)
                    else:
                        raise AssertionError(ledger)
                    assert child['name']=='Accounting fixture'
                    assert child['input_tokens']==20 and child['cached_input_tokens']==5 and child['output_tokens']==3, child
                    assert child['total_tokens']==23 and child['unpriced_requests']==2 and not child['historical_unpriced'], child
                    child_requests = [request for request in ledger['requests'].values() if request['actor_id']==child['id']]
                    assert len(child_requests)==2 and sorted(request['total_tokens'] for request in child_requests)==[0,23], child_requests
                    stats = browser.api('GET',f'/api/conversations/{cid}/token-statistics?workspace_id=default')['data']
                    main = ledger['actors']['main']
                    assert stats['total_input_tokens']==main['input_tokens']==10*service.main_responses, stats
                    assert stats['total_output_tokens']==main['output_tokens']==2*service.main_responses, stats
                    assert next(actor for actor in stats['costs']['actors'] if actor['kind']=='sub_agent')==child, stats
                    with sqlite3.connect(f'file:{data/"sub_agents.sqlite3"}?mode=ro',uri=True) as database:
                        raw = database.execute('SELECT stats FROM child_task WHERE id=?',(child['id'],)).fetchone()[0]
                    saved = json.loads(raw)
                    assert saved['cost_ledger_requests']==2 and saved['token_usage']['total']==23, saved
                except Exception as error:
                    raise AssertionError((service.errors,(root/'host.log').read_text())) from error
                assert not service.errors,service.errors
        finally:
            service.shutdown()
            worker.join()
    print('Child usage and unpriced replies reached the parent ledger and the production token-statistics API.')


if __name__ == '__main__':
    run()
