"""Capture a normal Web Agent's requests against a loopback model."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / '2026-11-27_RustAgent工具对照'))
from knowledge_media_http import (
    Browser, LocalServices, ROOT, call, fixture_environment, model_catalog_host, wait_for_task,
)

STATUS = 'get_sub_agent_status'
WORKTREE = 'enter_worktree'
READ = 'read_file'
HINT_MARKER = '[系统自动插入的提示信息] '
CATALOG = '## 本对话按需加载目录'
DISABLED = '=== 工具可用性提醒 ==='
URL_SUFFIX = '?workspace_id=default'


class Provider(LocalServices):
    def calls(self, body):
        self.server.captures.append(body)
        user = next(row['content'] for row in reversed(body['messages']) if row['role'] == 'user')
        receipts = [json.loads(row['content']) for row in body['messages'] if row['role'] == 'tool']
        if user == 'Fixture three' and not receipts:
            return [call('load_tools', 'local-load', {
                'tool_names': [STATUS, WORKTREE], 'intent': '加载测试工具',
            })]
        if user == 'Fixture three':
            receipt, = receipts
            assert receipt['success'] is True, receipt
            assert receipt['loaded_now'] == [STATUS, WORKTREE], receipt
            for tool in receipt['tools']:
                assert_declares_intent(tool['function']['parameters'])
        return 'Model fixture completed'


def assert_declares_intent(parameters):
    assert parameters['properties']['intent']['type'] == 'string', parameters
    assert parameters['required'].count('intent') == 1, parameters


def schemas(body):
    return {tool['function']['name']: tool['function']['parameters'] for tool in body['tools']}


def preferences(browser, patch):
    payload = browser.api('POST', '/api/personalization' + URL_SUFFIX, patch)
    assert payload['success'], payload


def create(browser, *, work_mode='execute', permission_mode='approval', mode='sandbox'):
    return browser.api('POST', '/api/conversations' + URL_SUFFIX, {'settings': {
        'model_key': 'chat-local',
        'work_mode': work_mode,
        'permission_mode': permission_mode,
        'execution_mode': mode,
        'network_permission': 'restricted',
        'run_mode': 'fast',
        'thinking_mode': False,
        'reasoning_effort': None,
    }}, status=201)['conversation_id']


def send(browser, conversation_id, message):
    task = browser.api('POST', '/api/tasks' + URL_SUFFIX, {
        'conversation_id': conversation_id, 'message': message, 'model_key': 'chat-local',
    }, status=202)
    assert wait_for_task(browser, task) == conversation_id


def run():
    binary = Path(os.environ.get('ASTRION_HEADLESS_BIN',
                                 ROOT / 'backend-rust/target/debug/astrion-headless')).resolve(strict=True)
    with tempfile.TemporaryDirectory() as temporary, ThreadingHTTPServer(('127.0.0.1', 0), Provider) as provider:
        root = Path(temporary).resolve()
        env = fixture_environment(root)
        env['ASTRION_RESOURCE_ROOT'] = str(ROOT)
        data = Path(env['DATA_DIR'])
        data.mkdir()
        (data / 'personalization.json').write_text(json.dumps({'auto_generate_title': False}))
        provider.origin = f'http://127.0.0.1:{provider.server_port}'
        provider.captures, provider.errors = [], []
        config = Path(env['DEPLOY_CONFIG_DIR']) / 'custom_models.json'
        config.write_text(json.dumps({'models': [{
            'model_name': 'chat-local', 'model_id': 'chat-local',
            'url': provider.origin + '/v1', 'apikey': 'fixture-only',
        }]}))
        thread = threading.Thread(target=provider.serve_forever, daemon=True)
        thread.start()
        try:
            with model_catalog_host(binary, env, root / 'host.log') as listening:
                browser = Browser(listening)
                preferences(browser, {
                    'enabled': True, 'morality_level': 'high', 'adult_content_restriction': 'low',
                    'tool_intent_enabled': False, 'skill_hints_enabled': False,
                    'silent_tool_disable': False, 'disabled_tool_categories': ['sub_agent'],
                    'tool_loading_enabled': True, 'tool_loading_deferred': [STATUS, WORKTREE],
                    'git_worktree_enabled': False,
                })
                first = create(browser, mode='direct')
                send(browser, first, 'TERMINAL only for fixture one')
                a, = provider.captures
                frozen = a['messages'][0]['content']
                assert '【道德准则：高】' in frozen and '【成人内容限制：低】' in frozen
                assert '运行模式：执行（execute）' in frozen
                assert '权限模式：人工审批（approval）' in frozen
                assert '执行环境：完全访问（direct）' in frozen
                assert '网络权限：受限（restricted）' in frozen
                assert CATALOG not in frozen and '## Git worktree' not in frozen
                assert DISABLED in frozen and f'{STATUS}：已被设置禁用' in frozen
                tool_map = schemas(a)
                assert not {'load_tools', STATUS, WORKTREE} & tool_map.keys()
                assert 'intent' not in tool_map[READ]['properties']
                assert all(HINT_MARKER not in row['content'] for row in a['messages'])

                preferences(browser, {'tool_intent_enabled': True, 'skill_hints_enabled': True, 'morality_level': 'low'})
                send(browser, first, 'TERMINAL / 终端 for fixture two')
                b = provider.captures[-1]
                assert b['messages'][0]['content'] == frozen
                user_index = next(i for i, row in enumerate(b['messages']) if row['content'] == 'TERMINAL / 终端 for fixture two')
                hint = b['messages'][user_index + 1]
                assert hint['role'] == 'system' and hint['content'].startswith(HINT_MARKER)
                assert hint['content'].count('terminal-guide') == 1, hint
                assert_declares_intent(schemas(b)[READ])

                preferences(browser, {
                    'git_worktree_enabled': True, 'silent_tool_disable': True,
                    'disabled_tool_categories': [],
                })
                second = create(browser, work_mode='plan', permission_mode='readonly')
                send(browser, second, 'Fixture three')
                c, d = provider.captures[-2:]
                prompt = c['messages'][0]['content']
                assert '运行模式：计划（plan）' in prompt and '权限模式：只读（readonly）' in prompt
                assert '执行环境：受限执行（sandbox）' in prompt
                assert CATALOG in prompt and '## Git worktree' in prompt and DISABLED not in prompt
                catalog = prompt.split(CATALOG, 1)[1].strip().split('\n\n', 1)[0]
                assert STATUS in catalog and WORKTREE in catalog
                assert '【道德准则：低】' in prompt and '【成人内容限制：低】' in prompt
                tools = schemas(c)
                assert tools[STATUS]['properties'] == tools[WORKTREE]['properties'] == {}
                assert_declares_intent(tools['load_tools'])
                assert c['tools'] == d['tools'], 'Loading complete schemas changed the stub prefix'
                assert not provider.errors, provider.errors
        finally:
            provider.shutdown()
            thread.join()
    print('Agent intent, keyword hints, frozen settings, worktree and tool catalogs verified end to end.')


if __name__ == '__main__':
    run()
