"""Manual/browser integration fixture: real Astrion + a synthetic local model.

No real credentials or external model calls. Run with Python 3.11; data stays in
_experiments/schedule-runtime. Ports 8192 (app), 8193 (synthetic model).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO = Path(__file__).resolve().parents[2]
ROOT = REPO / '_experiments' / 'schedule-runtime'
for directory in ('data', 'logs', 'users', 'api', 'config', 'project'):
    (ROOT / directory).mkdir(parents=True, exist_ok=True)
os.environ.update({
    'ASTRION_IGNORE_DOTENV': '1', 'ASTRION_DATA_ROOT': str(ROOT),
    'DATA_DIR': str(ROOT / 'data'), 'LOGS_DIR': str(ROOT / 'logs'),
    'USER_SPACE_DIR': str(ROOT / 'users'), 'API_USER_SPACE_DIR': str(ROOT / 'api'),
    'DEPLOY_CONFIG_DIR': str(ROOT / 'config'), 'TERMINAL_SANDBOX_MODE': 'host',
    'WEB_SERVER_HOST': '127.0.0.1', 'PYTHONDONTWRITEBYTECODE': '1',
})
os.environ.pop('HOST_WORKSPACES_FILE', None)
(ROOT / 'config' / 'custom_models.json').write_text(json.dumps({'models': [{
    'model_name': 'schedule-test-model', 'model_id': 'schedule-test-model',
    'url': 'http://127.0.0.1:8193/v1', 'apikey': 'synthetic-test-key',
    'reasoning_capability': 'fast,thinking', 'supports_tools': True,
}]}))
(ROOT / 'config' / 'host_workspaces.json').write_text(json.dumps({
    'version': 1, 'default_workspace_id': 'schedule-test-workspace',
    'workspaces': [{'workspace_id': 'schedule-test-workspace', 'path': str(ROOT / 'project'), 'label': '隔离测试工作区'}],
    'auto_default_workspace': True,
}))
(ROOT / 'data' / 'personalization.json').write_text(json.dumps({
    'default_model': 'schedule-test-model', 'default_run_mode': 'fast',
    'default_work_mode': 'execute', 'default_permission_mode': 'unrestricted',
    'available_skills': [], 'skip_auto_title_generation': True,
    'review_agents': {key: {'model': 'schedule-test-model'} for key in ('auto_approval', 'goal_review', 'workflow_review')},
}))
sys.path.insert(0, str(REPO))


class SyntheticModel(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({'data': [{'id': 'schedule-test-model'}]}).encode())

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
        messages = payload.get('messages', [])
        user_text = '\n'.join(str(item.get('content', '')) for item in messages if item.get('role') == 'user')
        tools = {item.get('function', {}).get('name') for item in payload.get('tools', [])}
        tool_call = None
        if 'report_goal_status' in tools:
            tool_call = {'id': 'goal_test_done', 'type': 'function', 'function': {'name': 'report_goal_status', 'arguments': json.dumps({'status': 'done', 'message': '隔离目标测试已完成'})}}
        elif 'write_file' in tools and 'SCHEDULE_WRITE_PROBE' in user_text and not any(item.get('role') == 'tool' for item in messages):
            tool_call = {'id': 'write_probe_1', 'type': 'function', 'function': {'name': 'write_file', 'arguments': json.dumps({'intent': '写入隔离验证文件', 'file_path': 'schedule-probe.txt', 'content': 'Synthetic scheduled Agent tool execution verified.\n', 'append': False})}}
        if 'SCHEDULE_SLOW_PROBE' in user_text:
            time.sleep(8)
        content = '隔离测试模型已执行计划指令。此响应来自本地合成模型，用于验证调度与工具链。'
        message = {'role': 'assistant', 'content': None if tool_call else content}
        if tool_call:
            message['tool_calls'] = [tool_call]
        finish = 'tool_calls' if tool_call else 'stop'
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream' if payload.get('stream') else 'application/json')
        self.end_headers()
        if not payload.get('stream'):
            self.wfile.write(json.dumps({'id': 'synthetic-response', 'object': 'chat.completion', 'created': int(time.time()), 'model': 'schedule-test-model', 'choices': [{'index': 0, 'message': message, 'finish_reason': finish}], 'usage': {'prompt_tokens': 20, 'completion_tokens': 12, 'total_tokens': 32}}, ensure_ascii=False).encode())
            return
        delta = {'role': 'assistant'}
        if tool_call:
            delta['tool_calls'] = [{'index': 0, **tool_call}]
        else:
            delta['content'] = content
        for value in [
            {'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]},
            {'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}], 'usage': {'prompt_tokens': 20, 'completion_tokens': 12, 'total_tokens': 32}},
        ]:
            value.update(id='synthetic-response', object='chat.completion.chunk', created=int(time.time()), model='schedule-test-model')
            self.wfile.write(('data: ' + json.dumps(value, ensure_ascii=False) + '\n\n').encode())
            self.wfile.flush()
        self.wfile.write(b'data: [DONE]\n\n')


if __name__ == '__main__':
    model_server = ThreadingHTTPServer(('127.0.0.1', 8193), SyntheticModel)
    threading.Thread(target=model_server.serve_forever, name='synthetic-model', daemon=True).start()
    from server.app_legacy import run_server
    run_server(str(ROOT / 'project'), thinking_mode=False, port=8192, debug=False)
