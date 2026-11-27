"""Production Agent and child read and write knowledge, media and saved webpages."""
from __future__ import annotations

import base64
import contextlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
for folder in ('2026-11-24_Rust后端','2026-11-27_Rust工作区附件','2026-11-27_Rust内置网页工具'):
    sys.path.insert(0, str(HERE.parent / folder))
from attachments_http import Browser, PNG, wait_for_task
from agent_process_roundtrip import ROOT, fixture_environment
from chat_web_roundtrip import stored_document
from web_tools_http import ARTICLE, LocalServices, call

MAIN = 'Exercise normal Agent tools'
CHILD = 'Exercise delegated Agent tools'
SKILL = 'transaction-fixture'
IMAGE = 'fixture.png'


@contextlib.contextmanager
def model_catalog_host(binary, env, log):
    args = [str(binary),'--port','0','--workspace',env['HOST_PROJECT_PATH'],'--data-dir',env['DATA_DIR'],
            '--web','--static-root',str(ROOT/'static')]
    with log.open('w') as output:
        proc = subprocess.Popen(args, env={**env,'ASTRION_DESKTOP_STOP_STDIN':'1'},
                                stdin=subprocess.PIPE, stdout=output, stderr=subprocess.STDOUT)
        try:
            for _ in range(400):
                content = log.read_text()
                assert proc.poll() is None, content
                address = re.search(r'Astrion Rust server listening on (127\.0\.0\.1:\d+)', content)
                if address:
                    yield 'http://' + address.group(1)
                    break
                time.sleep(.025)
            else:
                raise AssertionError(log.read_text())
        finally:
            proc.stdin.close()
            assert proc.wait(timeout=15) == 0, log.read_text()


class Services(LocalServices):
    def calls(self, body):
        names = {tool['function']['name'] for tool in body['tools']}
        messages = body['messages']
        user = [m['content'] for m in messages if m['role']=='user' and isinstance(m['content'],str)]
        results = {m['tool_call_id']:json.loads(m['content']) for m in messages if m['role'] == 'tool'}
        if any(CHILD in message for message in user[:2]):
            return self.child(names, messages, results)
        assert user[0] == MAIN, user
        assert {'create_sub_agent','update_memory','update_project_memory','view_image','save_webpage'} <= names, names
        if 'register' not in results:
            return [
                call('update_project_memory','register',{'name':SKILL,'description':'Transaction fixture','content':'Topic wording one.'}),
                call('recall_project_memory','recall',{'name':SKILL}),
                call('update_project_memory','update',{'name':SKILL,'description':'Transaction fixture','content':'Topic wording two.'}),
                call('update_memory','global',{'operation':'append','content':'Fixture global entry'}),
                call('search_skills','main-skill',{'keywords':['transaction']}),
                call('view_image','main-image',{'path':IMAGE}),
                call('save_webpage','main-save',{'url':self.server.origin+'/article','target_path':'page.txt'}),
                call('create_sub_agent','child',{'name':'reader','summary':'Read tools','task':CHILD,
                    'working_dir':'.','intelligence':'same','access_level':'workspace_write'}),
            ]
        for id in ('register','recall','update','global','main-skill','main-image','main-save','child'):
            assert results[id]['success'], (id, results[id])
        assert 'Topic wording one.' in results['recall']['content'], results
        assert results['main-skill']['results'][0]['skill_id'] == SKILL, results['main-skill']
        self.assert_image(messages)
        assert self.server.child_done.wait(6), 'Child did not consume its results: ' + str(self.server.errors)
        return 'Main Agent received the tool results'

    def child(self, names, messages, results):
        assert {'read_skill','search_project_memory','recall_project_memory','extract_webpage',
                'read_mediafile','todo_create','todo_update_task','save_webpage'} <= names, names
        assert 'update_memory' not in names and 'update_project_memory' not in names
        if 'child-skill' not in results:
            return [
                call('read_skill','child-skill',{'skill_name':SKILL}),
                call('search_project_memory','child-search',{'keywords':['Transaction fixture']}),
                call('recall_project_memory','child-memory',{'name':SKILL}),
                call('read_mediafile','child-image',{'path':IMAGE}),
                call('todo_create','child-todo',{'overview':'Fixture','tasks':[{'title':'Read tools'}]}),
                call('todo_update_task','child-check',{'task_index':1,'completed':True}),
                call('save_webpage','child-save',{'url':self.server.origin+'/article','target_path':'child-page.txt'}),
            ]
        for id in ('child-skill','child-search','child-memory','child-image','child-todo','child-check','child-save'):
            assert results[id]['success'], (id, results[id])
        assert 'Topic wording two.' in results['child-memory']['content'], results
        assert 'Fixture Skill Body' in results['child-skill']['content'], results
        assert results['child-check']['todo_list']['all_done'] is True, results
        self.assert_image(messages)
        self.server.child_done.set()
        return 'Child received knowledge, a local image and its todo list'

    def assert_image(self, messages):
        images = [part for m in messages if isinstance(m['content'],list)
                  for part in m['content'] if part['type']=='image_url']
        assert images and base64.b64decode(images[-1]['image_url']['url'].split(',')[1]) == PNG, images


def run():
    binary = Path(os.environ.get('ASTRION_HEADLESS_BIN', ROOT/'backend-rust/target-master/debug/astrion-headless')).resolve(strict=True)
    with tempfile.TemporaryDirectory(dir=HERE) as temporary, ThreadingHTTPServer(('127.0.0.1',0), Services) as service:
        root = Path(temporary)
        env = fixture_environment(root)
        env['ASTRION_RESOURCE_ROOT'] = str(ROOT)
        data = Path(env['DATA_DIR'])
        data.mkdir()
        workspace = Path(env['HOST_PROJECT_PATH'])
        service.origin = f'http://127.0.0.1:{service.server_port}'
        service.child_done = threading.Event()
        service.errors = []
        service.pages = service.searches = 0
        config = Path(env['DEPLOY_CONFIG_DIR'])/'custom_models.json'
        config.write_text(json.dumps({'models':[{'model_name':'chat-local','model_id':'chat-local',
            'url':service.origin+'/v1','apikey':'fixture-only','multimodal':'image'}]}))
        (data/'service_config.json').write_text(json.dumps({
            'webpage_direct_extract_enabled':True,'webpage_direct_extract_domains':['127.0.0.1']}))
        (data/'personalization.json').write_text(json.dumps({'require_read_before_edit':True,'auto_generate_title':False}))
        skill = workspace/'.agents/skills'/SKILL
        skill.mkdir(parents=True)
        (skill/'SKILL.md').write_text('---\nname: transaction-fixture\ndescription: Transaction fixture\n---\nFixture Skill Body\n')
        (workspace/IMAGE).write_bytes(PNG)
        worker = threading.Thread(target=service.serve_forever, daemon=True)
        worker.start()
        try:
            with model_catalog_host(binary, env, root/'host.log') as listening:
                browser = Browser(listening)
                try:
                    created = browser.api('POST','/api/conversations?workspace_id=default',{'settings':{
                        'model_key':'chat-local','work_mode':'execute','permission_mode':'unrestricted',
                        'execution_mode':'sandbox','network_permission':'restricted','run_mode':'fast',
                        'thinking_mode':False,'reasoning_effort':None,
                    }},status=201)
                    cid = created['conversation_id']
                    reply = browser.api('POST','/api/tasks?workspace_id=default',{
                        'message':MAIN,'conversation_id':cid,'model_key':'chat-local',
                    },status=202)
                    assert wait_for_task(browser, reply) == cid
                except Exception as error:
                    raise AssertionError((service.errors, (root/'host.log').read_text())) from error
                document = stored_document(data/'conversations/default/conversations.sqlite3', cid)
                assert document['metadata']['project_memory_reads'], document['metadata']
                assert (workspace/'page.txt').read_text() == ARTICLE
                assert (workspace/'child-page.txt').read_text() == ARTICLE
                deadline = time.monotonic() + 4
                while time.monotonic() < deadline:
                    reply = browser.api('GET',f'/api/sub_agents?workspace_id=default&conversation_id={cid}',
                                        headers={'Accept-Language':'en-US'})
                    reader = next((row for row in reply['data'] if row.get('name') == 'reader'
                                   or row.get('display_name') == 'reader'), None)
                    if reader and reader.get('status') == 'idle':
                        break
                    time.sleep(.02)
                else:
                    raise AssertionError('Child did not finish')
                assert 'pages viewed: 1' in reader['stats_summary'].lower(), reader
                assert (data/'memory.md').read_text().find('Fixture global entry') >= 0
                assert service.pages == 2 and not service.errors, service.errors
        finally:
            service.shutdown()
            worker.join()
    print('Agent and child received local media and their production knowledge tool results.')


if __name__ == '__main__':
    run()
