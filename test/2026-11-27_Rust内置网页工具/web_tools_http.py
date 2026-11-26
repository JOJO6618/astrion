"""Production Host built-ins through a local model and configured search service."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / '2026-11-24_Rust后端'))
sys.path.insert(0, str(HERE.parent / '2026-11-26_RustChat媒体'))
sys.path.insert(0, str(HERE.parent / '2026-11-27_Rust工作区附件'))
from agent_process_roundtrip import ROOT, fixture_environment, rust
from attachments_http import Browser, wait_for_task
from chat_web_roundtrip import stored_document

STATUS = 'get_sub_agent_status'
AGENT = 'exercise Agent built-ins'
CHAT = 'exercise Chat built-ins'
ARTICLE = 'The local page explains consistent transactions & stored definitions.'
SEARCH = 'A real search result from the local configured service.'


def call(name, identifier, args):
    return {'index':0,'id':identifier,'type':'function','function':{'name':name,'arguments':json.dumps(args)}}


class LocalServices(BaseHTTPRequestHandler):
    def write(self, status, mime, body):
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == '/article':
            self.server.pages += 1
            self.write(200, 'text/html', (
                '<html><head><title>not body</title></head>'
                '<body><nav>not navigation</nav><main><p>'
                + ARTICLE.replace('&', '&amp;') +
                '</p><script>not script</script></main></body></html>'
            ).encode())
            return
        assert url.path == '/search', self.path
        query = parse_qs(url.query)
        assert query['q'] == ['transaction fixture']
        assert query['format'] == ['json']
        self.server.searches += 1
        self.write(200, 'application/json', json.dumps({'results':[{
            'title':'Consistent transactions','url':self.server.origin + '/article',
            'content':SEARCH,'score':9.2,'publishedDate':'2026-11-24',
        }]}).encode())

    def do_POST(self):
        try:
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            sends = self.calls(body)
            if isinstance(sends, str):
                delta = {'content':sends}
                finish = 'stop'
            else:
                for index, item in enumerate(sends):
                    item['index'] = index
                delta = {'tool_calls':sends}
                finish = 'tool_calls'
            output = json.dumps({'choices':[{'index':0,'delta':delta,'finish_reason':finish}]})
            self.write(200, 'text/event-stream', f'data: {output}\n\ndata: [DONE]\n\n'.encode())
        except AssertionError as error:
            self.server.errors.append(str(error))
            self.write(400, 'application/json', b'{"error":"invalid model fixture request"}')

    def calls(self, body):
        tools = {tool['function']['name']:tool['function'] for tool in body['tools']}
        messages = body['messages']
        user = [message['content'] for message in messages if message['role'] == 'user'][-1]
        results = {msg['tool_call_id']:json.loads(msg['content']) for msg in messages if msg['role'] == 'tool'}
        search_params = {'query':'transaction fixture','country':'china',
                         'start_date':'2026-11-01','end_date':'2026-11-26'}
        if user == CHAT:
            assert 'web_search' in tools and 'extract_webpage' in tools and 'load_tools' not in tools
            if 'chat-search' not in results:
                return [call('web_search','chat-search',search_params),
                        call('extract_webpage','chat-extract',{'url':self.server.origin+'/article'})]
            self.check_web(results['chat-search'], results['chat-extract'])
            return 'Chat received both web results 【cite:' + results['chat-search']['citations'][0]['id'] + ', src_0000000000】'

        assert user == AGENT, user
        assert 'load_tools' in tools and STATUS in tools
        assert tools[STATUS]['parameters']['properties'] == {}, tools[STATUS]
        assert tools['web_search']['parameters']['properties']['query']['type'] == 'string'
        assert any('本对话按需加载目录' in message['content'] for message in messages if message['role'] == 'system')
        snapshot = json.dumps(body['tools'],separators=(',',':'))
        if self.server.schema is None:
            self.server.schema = snapshot
        assert snapshot == self.server.schema, 'Deferred schemas changed after a load'
        if 'blocked' not in results:
            return [call(STATUS,'blocked',{})]
        assert results['blocked']['tool_not_loaded'] is True
        if 'invalid-load' not in results:
            return [call('load_tools','invalid-load',{'tool_names':[STATUS,7]})]
        assert results['invalid-load']['success'] is False, results
        if 'load' not in results:
            return [call('load_tools','load',{'tool_names':[STATUS]})]
        loaded = results['load']
        assert loaded['success'] and loaded['loaded_now'] == [STATUS], loaded
        definition, = loaded['tools']
        assert definition['function']['name'] == STATUS
        assert definition['function']['parameters']['properties'], definition
        if 'loaded-status' not in results:
            return [call(STATUS,'loaded-status',{}),call('web_search','search',search_params),
                    call('extract_webpage','extract',{'url':self.server.origin+'/article'})]
        assert results['loaded-status']['success'], results
        self.check_web(results['search'], results['extract'])
        if 'load-again' not in results:
            return [call('load_tools','load-again',{'tool_names':[STATUS]})]
        assert results['load-again']['success'] is False
        return 'Agent received loaded tool, search and extraction results 【cite:' + results['search']['citations'][0]['id'] + ', src_0000000000】'

    def check_web(self, search, page):
        assert search['success'] and search['results'][0]['content'] == SEARCH, search
        assert search['filters']['provider'] == 'searxng', search
        assert {'country','start_date'} <= set(search['filters']['provider_dropped']), search
        assert page['success'] and page['method'] == 'direct' and page['content'] == ARTICLE, page
        expected = hashlib.sha1((self.server.origin+'/article').encode()).hexdigest()[:10]
        assert page['citations'][0]['id'] == search['citations'][0]['id'] == 'src_' + expected, (page, search)

    def log_message(self, *_):
        pass


def chat(browser):
    accepted = browser.api('POST', '/api/chat/tasks', {
        'message':CHAT,'operation_id':'chat-test','model_key':'chat-local',
    })['data']
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        result = browser.api('GET', '/api/chat/tasks/' + accepted['task_id'])['data']
        if result['status'] == 'succeeded':
            message = result['view']['document']['messages'][-1]
            assert message['content'].startswith('Chat received both web results'), message
            assert message['metadata']['citations'][0]['title'] == 'Consistent transactions', message
            assert 'src_0000000000' not in message['content'], message
            return
        assert result['status'] not in ('failed','cancelled'), result
        time.sleep(.025)
    raise AssertionError('Chat did not consume the web tool results')


def run():
    binary = Path(os.environ.get('ASTRION_HEADLESS_BIN', ROOT / 'backend-rust/target-master/debug/astrion-headless')).resolve(strict=True)
    with tempfile.TemporaryDirectory(dir=HERE) as temporary, ThreadingHTTPServer(('127.0.0.1', 0), LocalServices) as service:
        root = Path(temporary)
        environment = fixture_environment(root)
        environment['ASTRION_RESOURCE_ROOT'] = str(ROOT)
        data = Path(environment['DATA_DIR'])
        data.mkdir()
        service.origin = f'http://127.0.0.1:{service.server_port}'
        service.schema = None
        service.searches = service.pages = 0
        service.errors = []
        config = Path(environment['DEPLOY_CONFIG_DIR']) / 'custom_models.json'
        config.write_text(json.dumps({'models':[{
            'model_name':'chat-local','model_id':'chat-local','url':service.origin+'/v1','apikey':'fixture-only',
        }]}))
        (data/'service_config.json').write_text(json.dumps({
            'search_provider':'searxng','searxng_base_url':service.origin,
            'webpage_direct_extract_enabled':True,'webpage_direct_extract_domains':['127.0.0.1'],
        }))
        (data/'personalization.json').write_text(json.dumps({
            'tool_loading_enabled':True,'tool_loading_deferred':[STATUS],'auto_generate_title':False,
        }))
        thread = threading.Thread(target=service.serve_forever, daemon=True)
        thread.start()
        try:
            with rust(environment, binary, service.origin+'/v1/chat/completions', root/'host.log', web=True) as base:
                browser = Browser(base)
                task = browser.api('POST','/api/tasks?workspace_id=default',{'message':AGENT},status=202)
                cid = wait_for_task(browser, task)
                database = data/'conversations/default/conversations.sqlite3'
                saved = stored_document(database,cid)
                state = saved['metadata']['tool_loading']
                assert state['loaded'] == [STATUS] and state['pending'] == []
                assert 'load_tools' in state['initial_exposed']
                message = saved['messages'][-1]
                assert message['content'].startswith('Agent received loaded tool, search and extraction results'), message
                assert message['metadata']['citations'][0]['title'] == 'Consistent transactions', message
                assert 'src_0000000000' not in message['content'], message
                chat(browser)
                assert service.searches == service.pages == 2, (service.searches,service.pages)
                assert not service.errors, service.errors
        finally:
            service.shutdown()
            thread.join()
    print('Deferred tools, configured search and webpage extraction reached both Agent and Chat models.')


if __name__ == '__main__':
    run()
