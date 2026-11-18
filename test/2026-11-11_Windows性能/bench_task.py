"""Run a real two-request Agent task with a fake model and isolated storage."""
import argparse
import asyncio
from collections import defaultdict
import contextlib
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[2])
    parser.add_argument('--size',choices=['empty','long'],default='long')
    parser.add_argument('--label',default='baseline')
    parser.add_argument('--catalog', choices=['minimal','sanitized-desktop'], default='minimal')
    args=parser.parse_args()
    sys.path.insert(0,str(args.repo))
    original_connect=socket.socket.connect
    def deny(sock,address):
        # Windows asyncio builds its internal wakeup socket on loopback.
        if isinstance(address,tuple) and address[0] in {'127.0.0.1','::1','localhost'}:
            return original_connect(sock,address)
        raise OSError('No external network in isolated benchmark')
    socket.socket.connect=deny
    counters=defaultdict(lambda:{'calls':0,'seconds':0.0})
    def measure(cls,name):
        original=getattr(cls,name)
        def wrapped(self,*pos,**kw):
            start=time.perf_counter()
            try:
                return original(self,*pos,**kw)
            finally:
                counters[name]['calls']+=1
                counters[name]['seconds']+=time.perf_counter()-start
        setattr(cls,name,wrapped)
    with tempfile.TemporaryDirectory(prefix='astrion-task-') as tmp:
        root=Path(tmp)
        project=root/'project'
        project.mkdir()
        (project/'sample.txt').write_text('A performance probe\n',encoding='utf-8')
        data=root/'host'/'data'
        data.mkdir(parents=True)
        config=root/'config'
        config.mkdir()
        if args.catalog=='sanitized-desktop':
            live_data=Path.home()/'.astrion'/'astrion-desktop'/'host'/'data'
            providers=json.loads((live_data/'providers.json').read_text(encoding='utf-8'))
            # Keep the discovered models, but replace all request credentials.
            for provider in providers.get('providers',{}).values():
                provider['api_key']='offline-not-a-key'
                provider['headers']={}
            (data/'providers.json').write_text(json.dumps(providers),encoding='utf-8')
            cache=live_data/'modelsdev_cache.json'
            if cache.exists():
                (data/'modelsdev_cache.json').write_bytes(cache.read_bytes())
        (config/'custom_models.json').write_text(json.dumps({'models':[{
            'model_name':'offline-perf','url':'http://127.0.0.1:1','apikey':'fake-not-a-key',
            'context_window':999999999,'thinkmode_status':{'type':'param_toggle','model_id':'offline-perf'},
            'reasoning_capability':'fast'}]}),encoding='utf-8')
        (data/'personalization.json').write_text(json.dumps({
            'auto_generate_title':False,'auto_deep_compress_enabled':False,
            'auto_shallow_compress_enabled':False,'modify_history_enabled':False,
            'default_work_mode':'execute'}),encoding='utf-8')
        os.environ.update({'ASTRION_IGNORE_DOTENV':'1','TERMINAL_SANDBOX_MODE':'host',
                           'ASTRION_DATA_ROOT':str(root),'DATA_DIR':str(data),
                           'DEPLOY_CONFIG_DIR':str(config),'LOGS_DIR':str(root/'logs'),
                           'WEB_API_SILENT':'1'})
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            from core.web_terminal import WebTerminal
            from utils.conversation_manager import ConversationManager
            from server.chat_flow_task_main import handle_task_with_sender
            terminal=WebTerminal(str(project),data_dir=str(data))
            terminal.username='host'
            terminal.user_role='admin'
            cm=terminal.context_manager
            if args.size=='long':
                source_dir=Path.home()/'.astrion'/'astrion-desktop'/'host'/'data'/'conversations'
                source=max(source_dir.glob('**/conv_*.json'),key=lambda p:p.stat().st_size)
                document=json.loads(source.read_text(encoding='utf-8'))
                document['metadata'].update({'project_path':str(project),'project_relative_path':None,
                    'model_key':terminal.model_key,'work_mode':'execute','permission_mode':'unrestricted',
                    'execution_mode':'direct','workspace_memory_enabled':False})
                cm.conversation_manager._save_conversation_file(document['id'],document)
                cm.conversation_manager._update_index(document['id'],document)
                terminal._bound_conversation_id=document['id']
                cm._service_instance_no_history=False
                terminal.load_conversation(document['id'])
            else:
                terminal.create_new_conversation()
                terminal._bound_conversation_id=cm.current_conversation_id
                cm._service_instance_no_history=False
            terminal.set_work_mode('execute',persist=False)
            terminal.set_permission_mode('unrestricted',persist=False)
            terminal.set_execution_mode('direct')
            for name in ['read_conversation','_atomic_write_json','save_conversation','update_conversation_metadata','get_token_statistics']:
                measure(ConversationManager,name)
            events=[]
            requests=[]
            async def chat(messages,tools,stream=True):
                requests.append(round(time.perf_counter()-start,4))
                if len(requests)==1:
                    yield {'choices':[{'delta':{'tool_calls':[{'index':0,'id':'call_perf','type':'function','function':{'name':'read_file','arguments':json.dumps({'path':'sample.txt','type':'read','intent':'Read sample'})}}]},'finish_reason':None}]}
                    yield {'choices':[{'delta':{},'finish_reason':'tool_calls'}]}
                else:
                    yield {'choices':[{'delta':{'content':'Offline benchmark complete.'},'finish_reason':None}]}
                    yield {'choices':[{'delta':{},'finish_reason':'stop'}]}
            terminal.api_client.chat=chat
            def sender(event,payload):
                if event in {'api_request_start','tool_result','tool_execution','task_complete','error','ai_message_start'}:
                    events.append({'event':event,'at':round(time.perf_counter()-start,4)})
            cm.set_web_terminal_callback(sender)
            workspace=SimpleNamespace(project_path=project,data_dir=data,workspace_id='default')
            start=time.perf_counter()
            asyncio.run(handle_task_with_sender(terminal,workspace,'Run the offline performance probe.',[],sender,'perf-task','host'))
            elapsed=time.perf_counter()-start
            terminal._bound_conversation_id=None
            cm._service_instance_no_history=True
            cm.auto_save_enabled=False
        result={'size':args.size,'requests':requests,'elapsed':round(elapsed,4),'events':events,
                'storage':{k:{'calls':v['calls'],'seconds':round(v['seconds'],4)} for k,v in counters.items()}}
        out=Path(__file__).resolve().parent/f'task_{args.size}_{args.label}.json'
        out.write_text(json.dumps(result,indent=2),encoding='utf-8')
        print(json.dumps(result,indent=2),flush=True)
        if len(requests)!=2 or not any(e['event']=='task_complete' for e in events):
            raise AssertionError('Offline task did not complete two model requests')


if __name__=='__main__':
    main()
