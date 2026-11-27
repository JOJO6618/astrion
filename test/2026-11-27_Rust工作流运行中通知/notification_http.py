"""Exercise live and idle workflow exit through Host Web with a loopback model."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / '2026-11-27_RustAgent工具对照'))
from knowledge_media_http import Browser, LocalServices, ROOT, call, fixture_environment, model_catalog_host

WORKSPACE = '?workspace_id=default'
PREFIX = '[系统通知|workflow]\n'
EXIT_TEXT = '用户已退出工作流'
CALLS = ['workflow-list-a', 'workflow-list-b']
DEFINITION = {
    'name': 'fixture',
    'description': 'Local notification fixture',
    'reviewMode': 'readonly',
    'endConditions': 'stop',
    'maxStageRounds': 10,
    'nodes': [
        {'id': 'start', 'kind': 'start', 'next': 'step'},
        {'id': 'step', 'kind': 'stage', 'name': 'One', 'goal': 'Fixture only',
         'instructions': 'Say done', 'next': 'stop'},
        {'id': 'stop', 'kind': 'end', 'name': 'Stop'},
    ],
}


class Model(LocalServices):
    def calls(self, request):
        self.server.requests.append(request['messages'])
        if len(self.server.requests) == 1:
            self.server.started.set()
            assert self.server.release.wait(10), 'Test did not release the first model round'
            return [call('list_workflows', identity, {}) for identity in CALLS]
        return 'Local model reply'


def until(fn, *, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(.03)
    raise AssertionError('Timed out waiting for workflow task')


def practice(browser, service):
    browser.api('PUT', '/api/workflows/fixture', {'workflow': DEFINITION})

    def activate():
        conversation = browser.api('POST', '/api/conversations' + WORKSPACE, {
            'settings': {'model_key': 'chat-local', 'work_mode': 'execute', 'run_mode': 'fast',
                         'thinking_mode': False, 'permission_mode': 'readonly', 'execution_mode': 'sandbox',
                         'network_permission': 'restricted', 'reasoning_effort': None},
        }, status=201)['conversation_id']
        admitted = browser.api('POST', '/api/workflow/activate' + WORKSPACE, {
            'name': 'fixture', 'conversation_id': conversation,
        })
        assert admitted['success'], admitted
        return conversation, admitted['task_id']

    def deactivate(conversation, *, dispatched):
        result = browser.api('POST', '/api/workflow/deactivate' + WORKSPACE, {
            'conversation_id': conversation,
        })
        assert result['success'] and result['dispatched'] is dispatched, result

    def finish(task):
        response = browser.api('GET', f'/api/tasks/{task}' + WORKSPACE)['data']
        if response['status'] == 'running':
            return None
        assert response['status'] == 'succeeded', (response, service.errors)
        return response

    def history(conversation):
        return browser.api('GET', f'/api/conversations/{conversation}/messages' + WORKSPACE)['data']['messages']

    conversation, task = activate()
    assert service.started.wait(8), service.errors
    # Exit while the model is responding. The already-started assistant round
    # still submits two calls after the UI request; neither result may be split.
    deactivate(conversation, dispatched=False)
    service.release.set()
    terminal = until(lambda: finish(task))
    event, = [event['data'] for event in terminal['events']
              if event['type'] == 'user_message' and event['data'].get('workflow_notice')]
    assert event['conversation_id'] == conversation and event['task_id'] == task, event
    assert event['message'] == event['content'] and event['content'].startswith(PREFIX + EXIT_TEXT), event
    for row in (event, event['metadata']):
        assert row['visibility'] == 'compact' and row['starts_work'] is False, row
        assert row['is_auto_generated'] is True and row['auto_message_type'] == 'runtime_workflow', row

    saved, = [entry for entry in history(conversation) if entry.get('metadata', {}).get('workflow_notice')]
    assert saved['role'] == 'user' and saved['content'] == event['content'], saved
    assert saved['message_id'] == event['message_id'] == event['metadata']['message_id'], (saved, event)
    assert saved['metadata']['visibility'] == 'compact' and saved['metadata']['starts_work'] is False, saved
    reopened = browser.api('GET', f'/api/conversations/{conversation}/bootstrap' + WORKSPACE)['data']
    shown, = [entry for entry in reopened['messages'] if entry.get('message_id') == event['message_id']]
    assert shown['metadata']['visibility'] == 'compact' and shown['metadata']['starts_work'] is False, shown
    ordered = [message for message in service.requests[1]
               if (message.get('tool_call_id') in CALLS or message.get('content') == event['content'])]
    assert [(row['role'], row.get('tool_call_id')) for row in ordered] == [
        ('tool', CALLS[0]), ('tool', CALLS[1]), ('user', None),
    ], ordered

    idle, idle_activation = activate()
    until(lambda: finish(idle_activation))
    deactivate(idle, dispatched=True)
    until(lambda: len(service.requests) >= 4)
    until(lambda: history(idle)[-1]['role'] == 'assistant')
    notice, = [entry for entry in history(idle) if entry.get('metadata', {}).get('workflow_notice')]
    assert notice['content'].startswith(EXIT_TEXT), notice
    assert notice['metadata']['visibility'] == 'chat' and notice['metadata']['starts_work'] is True, notice
    assert len(service.requests) == 4, service.requests
    assert not service.errors, service.errors


def run():
    binary = Path(os.environ['ASTRION_HEADLESS_BIN']).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix='astrion-workflow-notice-') as directory, \
            ThreadingHTTPServer(('127.0.0.1', 0), Model) as service:
        root = Path(directory)
        environment = fixture_environment(root)
        environment['ASTRION_RESOURCE_ROOT'] = str(ROOT)
        (Path(environment['DEPLOY_CONFIG_DIR']) / 'custom_models.json').write_text(json.dumps({
            'models': [{'model_name': 'chat-local', 'model_id': 'chat-local',
                        'url': f'http://127.0.0.1:{service.server_port}/v1', 'apikey': 'fixture-only'}],
        }))
        service.started = threading.Event()
        service.release = threading.Event()
        service.requests = []
        service.errors = []
        thread = threading.Thread(target=service.serve_forever, daemon=True)
        thread.start()
        try:
            with model_catalog_host(binary, environment, root / 'host.log') as host:
                practice(Browser(host), service)
        finally:
            service.release.set()
            service.shutdown()
            thread.join()


if __name__ == '__main__':
    run()
