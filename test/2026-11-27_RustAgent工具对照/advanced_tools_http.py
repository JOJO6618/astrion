"""Model round trip for skills, preferences, scheduled runs, history and VLM."""
from __future__ import annotations

import base64
import json
import os
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

from knowledge_media_http import Browser, HERE, LocalServices, PNG, ROOT, call, fixture_environment, model_catalog_host, wait_for_task

MAIN = 'Exercise advanced production tools'
PRIOR = 'Historic fixture transaction to review'
SCHEDULED = 'Scheduled fixture work'
SKILL = 'Archive_Fixture'


class Services(LocalServices):
    def do_POST(self):
        if self.path != '/ocr/v1/chat/completions':
            return super().do_POST()
        try:
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            assert body['model'] == 'fixture-vision' and body['max_tokens'] == 123
            assert self.headers['Authorization'] == 'Bearer fixture-ocr'
            parts = body['messages'][0]['content']
            assert parts[1]['text'] == 'Describe the pixels'
            assert base64.b64decode(parts[0]['image_url']['url'].split(',')[1]) == PNG
            answer = {'choices':[{'message':{'content':'The configured vision service read the image'}}]}
            self.write(200,'application/json',json.dumps(answer).encode())
        except AssertionError as error:
            self.server.errors.append(str(error))
            self.write(400,'application/json',b'{"error":"invalid vision fixture request"}')

    def calls(self, body):
        schemas = {tool['function']['name'] for tool in body['tools']}
        messages = body['messages']
        user = [m['content'] for m in messages if m['role']=='user' and isinstance(m['content'],str)]
        if user[0] == PRIOR:
            return 'This is the historical Assistant reply.'
        if user[0] == SCHEDULED:
            self.server.schedule_run.set()
            return 'Scheduled operation confirmed by the local model'
        assert user[0] == MAIN, user
        assert {'create_skill','manage_personalization','vlm_analyze','submit_schedule','query_schedules',
                'manage_schedule','conversation_search','conversation_review'} <= schemas, schemas
        assert 'view_image' not in schemas, schemas
        out = {m['tool_call_id']:json.loads(m['content']) for m in messages if m['role']=='tool'}
        if 'profile' not in out:
            return [
                call('manage_personalization','profile',{'action':'read'}),
                call('manage_personalization','theme',{'action':'update','field':'theme','value':'dark'}),
                call('create_skill','archive',{'source_dir':SKILL}),
                call('vlm_analyze','vision',{'path':'fixture.png','prompt':'Describe the pixels'}),
                call('conversation_search','history',{'keywords':['Historic fixture']}),
                call('submit_schedule','submit',{'intent':'保存隔离测试计划','file_path':'schedule.yaml'}),
            ]
        for id in ('profile','theme','archive','vision','history','submit'):
            assert out[id]['success'], (id,out[id])
        assert out['theme']['theme_changed'] is True and out['theme']['new_theme'] == 'dark'
        assert 'secret_key' not in out['profile']['data'], out['profile']
        assert out['archive']['skill_name'] == SKILL and 'sync_warning' not in out['archive'], out['archive']
        assert out['vision']['content'] == 'The configured vision service read the image'
        hit, = out['history']['results']
        assert hit['first_user_message'] == PRIOR, out['history']
        schedule = out['submit']['plan']['definition']['id']
        self.server.schedule_id = schedule
        if 'query' not in out:
            return [
                call('read_skill','read-skill',{'skill_name':SKILL}),
                call('manage_personalization','confirm-profile',{'action':'read'}),
                call('conversation_review','review',{'conversation_id':hit['id'],'mode':'read'}),
                call('query_schedules','query',{'intent':'查询保存的计划','schedule_id':schedule,'include_runs':True}),
                call('manage_schedule','run',{'intent':'运行隔离测试计划','schedule_id':schedule,'action':'run','request_id':'fixture-run'}),
            ]
        assert 'Real skill fixture body' in out['read-skill']['content'], out['read-skill']
        assert out['confirm-profile']['data']['theme'] == 'dark', out['confirm-profile']
        assert 'historical Assistant reply' in out['review']['content'], out['review']
        plan, = out['query']['plans']
        assert plan['definition']['id'] == schedule and plan['runs'] == [], plan
        assert out['run']['success'], out['run']
        return 'Read back preferences, skills, plan and review results'


def create(browser):
    return browser.api('POST','/api/conversations?workspace_id=default',{'settings':{
        'model_key':'chat-local','work_mode':'execute','permission_mode':'unrestricted',
        'execution_mode':'sandbox','network_permission':'restricted','run_mode':'fast',
        'thinking_mode':False,'reasoning_effort':None,
    }},status=201)['conversation_id']


def run():
    binary = Path(os.environ.get('ASTRION_HEADLESS_BIN', ROOT/'backend-rust/target-master/debug/astrion-headless')).resolve(strict=True)
    with tempfile.TemporaryDirectory(dir=HERE) as temporary, ThreadingHTTPServer(('127.0.0.1',0), Services) as service:
        root = Path(temporary)
        env = fixture_environment(root)
        env['ASTRION_RESOURCE_ROOT'] = str(ROOT)
        data = Path(env['DATA_DIR'])
        data.mkdir()
        (data/'personalization.json').write_text(json.dumps({'auto_generate_title': False}))
        workspace = Path(env['HOST_PROJECT_PATH'])
        service.origin = f'http://127.0.0.1:{service.server_port}'
        service.errors = []
        service.schedule_run = threading.Event()
        service.schedule_id = None
        env.update(OCR_API_BASE_URL=service.origin+'/ocr', OCR_API_KEY='fixture-ocr',
                   OCR_MODEL_ID='fixture-vision', OCR_MAX_TOKENS='123')
        config = Path(env['DEPLOY_CONFIG_DIR'])/'custom_models.json'
        config.write_text(json.dumps({'models':[{'model_name':'chat-local','model_id':'chat-local',
            'url':service.origin+'/v1','apikey':'fixture-only'}]}))
        (workspace/'fixture.png').write_bytes(PNG)
        source = workspace/SKILL
        (source/'references').mkdir(parents=True)
        (source/'SKILL.md').write_text(f'---\nname: {SKILL}\ndescription: Workflow fixture\n---\nReal skill fixture body\n')
        (source/'references/fixture.txt').write_text('Skill reference bytes')
        (workspace/'schedule.yaml').write_text('''schema_version: 1
name: Agent fixture schedule
enabled: false
schedule:
  type: daily
  timezone: Asia/Shanghai
  times: ['09:00']
target:
  conversation:
    mode: new
message: Scheduled fixture work
configuration:
  mode: custom
  model_key: chat-local
  work_mode: execute
  permission_mode: unrestricted
  execution_mode: sandbox
notifications:
  enabled: false
''')
        worker = threading.Thread(target=service.serve_forever, daemon=True)
        worker.start()
        try:
            with model_catalog_host(binary, env, root/'host.log') as listening:
                browser = Browser(listening)
                try:
                    for message in (PRIOR, MAIN):
                        cid = create(browser)
                        reply = browser.api('POST','/api/tasks?workspace_id=default',{'message':message,
                            'conversation_id':cid,'model_key':'chat-local'},status=202)
                        assert wait_for_task(browser,reply) == cid
                    assert service.schedule_run.wait(15), service.errors
                    deadline = time.monotonic() + 6
                    while time.monotonic() < deadline:
                        runs = browser.api('GET',f'/api/schedules/{service.schedule_id}/runs')['data']
                        if any(row['state'] == 'success' for row in runs):
                            break
                        time.sleep(.05)
                    else:
                        raise AssertionError(runs)
                except Exception as error:
                    raise AssertionError((service.errors, (root/'host.log').read_text())) from error
                assert not source.exists()
                assert (workspace/'.astrion/skills'/SKILL/'references/fixture.txt').read_text() == 'Skill reference bytes'
                assert (data/'personalization.json').is_file()
                assert not service.errors, service.errors
        finally:
            service.shutdown()
            worker.join()
    print('Agent received skills, preferences, VLM, review and schedule results. The schedule ran in the production scheduler.')


if __name__ == '__main__':
    run()
