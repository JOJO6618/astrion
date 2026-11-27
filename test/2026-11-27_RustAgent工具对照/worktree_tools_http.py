"""Switch the actual Agent execution capability through model tools."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from knowledge_media_http import Browser, LocalServices, ROOT, call, fixture_environment, model_catalog_host, stored_document, wait_for_task

BRANCH = 'feature/roundtrip'
FIRST_CHILD = 'Inspect the original child marker'
SECOND_CHILD = 'Inspect the rebound child marker'


class WorktreeServices(LocalServices):
    def calls(self, body):
        names = {tool['function']['name'] for tool in body['tools']}
        messages = body['messages']
        results = {m['tool_call_id']:json.loads(m['content']) for m in messages if m['role'] == 'tool'}
        user = [m['content'] for m in messages if m['role']=='user' and isinstance(m['content'],str)]
        if any(FIRST_CHILD in message for message in user[:2]):
            return self.child(user, results)
        assert {'enter_worktree','exit_worktree','read_file','write_file'} <= names, names
        if 'child-create' not in results:
            return [call('create_sub_agent','child-create',{'name':'reader','summary':'Read a marker',
                'task':FIRST_CHILD,'intelligence':'same','access_level':'workspace_write','working_dir':'.'})]
        assert results['child-create']['success'], results
        if not self.idle(results, 'first-status'):
            self.server.first_child.wait(2)
            return [call('get_sub_agent_status',f'first-status-{len(results)}',{'names':['reader']})]
        enter = {'intent':'Use fixture worktree','target':{'action':'create',
                'branch_name':BRANCH,'start_ref':'HEAD','include_uncommitted':False}}
        if 'mixed' not in results:
            return [call('enter_worktree','mixed',enter),call('read_file','original',{'path':'marker.txt'})]
        assert results['mixed']['success'] is False, results
        assert results['original']['content'].strip() == 'workspace value', results
        if 'enter' not in results:
            return [call('enter_worktree','enter',enter)]
        assert results['enter']['success'] and results['enter']['state']['branch'] == BRANCH, results
        if 'tree-read' not in results:
            return [call('read_file','tree-read',{'path':'marker.txt'}),
                    call('write_file','tree-write',{'file_path':'worktree-only.txt','content':'Changed worktree'})]
        assert results['tree-read']['content'].strip() == 'base value', results
        assert results['tree-write']['success'], results
        if 'child-resume' not in results:
            return [call('send_message_to_sub_agent','child-resume',{'name':'reader','message':SECOND_CHILD})]
        assert results['child-resume']['success'], results
        if 'exit-busy' not in results:
            return [call('exit_worktree','exit-busy',{'intent':'Check active child prevents exit'})]
        assert results['exit-busy']['success'] is False and '子智能体' in results['exit-busy']['error'], results
        self.server.allow_child_finish.set()
        if not self.idle(results, 'second-status'):
            self.server.second_child.wait(2)
            return [call('get_sub_agent_status',f'second-status-{len(results)}',{'names':['reader']})]
        if 'exit' not in results:
            return [call('exit_worktree','exit',{'intent':'Return to workspace'})]
        assert results['exit']['success'] and results['exit']['state']['active'] is False, results
        if 'back' not in results:
            return [call('read_file','back',{'path':'marker.txt'})]
        assert results['back']['content'].strip() == 'workspace value', results
        return 'Switched worktree and returned to main workspace'

    def idle(self, results, prefix):
        recent = next((value for name,value in reversed(list(results.items())) if name.startswith(prefix)), None)
        if recent is None:
            return False
        return any(agent['status']=='idle' for agent in recent.get('results',[]))

    def child(self, user, results):
        if not any(SECOND_CHILD in message for message in user):
            if 'child-original' not in results:
                return [call('read_file','child-original',{'path':'marker.txt'})]
            assert results['child-original']['content'].strip() == 'workspace value', results
            self.server.first_child.set()
            return 'Child first read original workspace value'
        assert any('工作树环境变更' in message and BRANCH in message for message in user), user
        if 'child-rebound' not in results:
            return [call('read_file','child-rebound',{'path':'marker.txt'})]
        assert results['child-rebound']['content'].strip() == 'base value', results
        assert self.server.allow_child_finish.wait(10), 'Parent did not check the busy exit'
        self.server.second_child.set()
        return 'Child resumed and read the worktree value'



def git(project, *args):
    subprocess.run(['git','-C',str(project),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid',
                    *args], check=True, capture_output=True)


def run():
    binary = Path(os.environ.get('ASTRION_HEADLESS_BIN', ROOT/'backend-rust/target-master/debug/astrion-headless')).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix='astrion-worktree-') as temporary, ThreadingHTTPServer(('127.0.0.1', 0), WorktreeServices) as service:
        root = Path(temporary)
        env = fixture_environment(root)
        env['ASTRION_RESOURCE_ROOT'] = str(ROOT)
        data = Path(env['DATA_DIR'])
        data.mkdir()
        workspace = Path(env['HOST_PROJECT_PATH'])
        git(workspace,'init','-b','main')
        (workspace/'marker.txt').write_text('base value\n')
        git(workspace,'add','marker.txt')
        git(workspace,'commit','-m','fixture')
        (workspace/'marker.txt').write_text('workspace value\n')
        service.origin = f'http://127.0.0.1:{service.server_port}'
        service.errors = []
        service.first_child = threading.Event()
        service.second_child = threading.Event()
        service.allow_child_finish = threading.Event()
        config = Path(env['DEPLOY_CONFIG_DIR'])/'custom_models.json'
        config.write_text(json.dumps({'models':[{'model_name':'chat-local','model_id':'chat-local',
            'url':service.origin+'/v1','apikey':'fixture-only'}]}))
        (data/'personalization.json').write_text(json.dumps({'git_worktree_enabled':True,'auto_generate_title':False}))
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
                    reply = browser.api('POST','/api/tasks?workspace_id=default',{'message':'Switch and read fixture',
                        'conversation_id':cid,'model_key':'chat-local'},status=202)
                    assert wait_for_task(browser, reply) == cid
                except Exception as error:
                    raise AssertionError((service.errors, (root/'host.log').read_text())) from error
                doc = stored_document(data/'conversations/default/conversations.sqlite3', cid)
                assert doc['metadata']['git_worktree']['revision'] == 2, doc['metadata']['git_worktree']
                assert doc['metadata']['git_worktree']['active'] is False
                assert 'git_worktree_notice' not in doc['metadata']
                assert not (workspace/'worktree-only.txt').exists()
                written, = (workspace/'.astrion/worktrees').glob('*/worktree-only.txt')
                assert written.read_text() == 'Changed worktree'
                assert not service.errors, service.errors
        finally:
            service.shutdown()
            worker.join()
    print('Agent worktree tools rejected mixed batches and exit while a child held the worktree.')


if __name__ == '__main__':
    run()
