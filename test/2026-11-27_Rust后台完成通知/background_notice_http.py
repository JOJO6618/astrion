"""Real Web schedule -> local background command -> notice-driven Agent continuation."""
from __future__ import annotations

from contextlib import contextmanager
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
sys.path.insert(0, str(HERE.parent / '2026-11-27_RustAgent工具对照'))
from knowledge_media_http import Browser, LocalServices, ROOT, call, fixture_environment

PREFIX = '[系统通知|background_command]'
MODES = ('idle', 'inline')


def command(mode):
    return f"sleep 7; printf 'fixture-{mode}-done\\n'"


class Model(LocalServices):
    def calls(self, body):
        user = [m['content'] for m in body['messages']
                if m.get('role') == 'user' and isinstance(m.get('content'), str)]
        mode = next((m for m in MODES if f'Test {m} command notice' in user), None)
        if mode is None:
            return 'A fixture title'
        notices = [m for m in user if m.startswith(PREFIX)]
        if notices:
            self.server.notices[mode].append(notices)
            return f'Completed {mode} continuation'
        result = next((json.loads(m['content']) for m in body['messages']
                       if m.get('tool_call_id') == f'local-{mode}'), None)
        if result is not None:
            assert result['status'] == 'running_background', result
            self.server.launches[mode].append(result)
            if mode == 'inline':
                # Keep the main turn active until the command finishes, so
                # notice delivery must use the inline path, not idle admission.
                time.sleep(3)
            return 'The original main turn only'
        return [call('run_command', f'local-{mode}', {
            'command': command(mode), 'timeout': 16, 'run_in_background': True,
        })]


@contextmanager
def host_fixture(binary, env, log):
    args = [str(binary), '--port', '0', '--workspace', env['HOST_PROJECT_PATH'],
            '--data-dir', env['DATA_DIR'], '--web', '--static-root', str(ROOT / 'static')]
    with log.open('w', encoding='utf-8') as writer, subprocess.Popen(
        args, env={**env, 'ASTRION_DESKTOP_STOP_STDIN': '1'},
        stdin=subprocess.PIPE, stdout=writer, stderr=subprocess.STDOUT,
    ) as process:
        try:
            for _ in range(1000):
                text = log.read_text(encoding='utf-8')
                assert process.poll() is None, text
                address = re.search(r'Astrion Rust server listening on (127\.0\.0\.1:\d+)', text)
                if address:
                    break
                time.sleep(.025)
            else:
                raise AssertionError(log.read_text(encoding='utf-8'))
            yield 'http://' + address.group(1)
        except BaseException:
            # Red-path fixture failure must not strand a server holding a
            # pending schedule, or replace the useful assertion on teardown.
            process.kill()
            process.wait(timeout=5)
            raise
        else:
            process.stdin.close()
            try:
                assert process.wait(timeout=15) == 0, log.read_text(encoding='utf-8')
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
                raise


def exercise(browser, mode):
    definition = {
        'schema_version': 1, 'name': f'Local {mode} notice', 'enabled': False,
        'schedule': {'type': 'daily', 'timezone': 'Asia/Shanghai', 'times': ['09:00']},
        'target': {'conversation': {'mode': 'new'}}, 'message': f'Test {mode} command notice',
        'configuration': {'mode': 'custom', 'model_key': 'chat-local', 'work_mode': 'execute',
                          'permission_mode': 'unrestricted', 'execution_mode': 'direct'},
        'notifications': {'enabled': False},
    }
    entry = browser.api('POST', '/api/schedules', {'definition': definition, 'workspace_id': 'default'})
    identifier = entry['data']['definition']['id']
    browser.api('POST', f'/api/schedules/{identifier}/actions', {'action': 'run', 'request_id': f'local-{mode}'})
    runs_endpoint = f'/api/schedules/{identifier}/runs'
    deadline = time.monotonic() + 22
    while time.monotonic() < deadline:
        # The schedule's read-only runs route does not dispatch conversation
        # notices; deliberately do not call a conversation running-status route here.
        rows = browser.api('GET', runs_endpoint)['data']
        if rows and rows[0]['state'] == 'success':
            run = rows[0]
            assert f'Completed {mode} continuation' in run['summary'], run
            return run
        time.sleep(.15)
    raise AssertionError(f'{mode} did not finish without a viewer: {rows}')


def main():
    binary = Path(os.environ['ASTRION_HEADLESS_BIN']).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix='astrion-bg-') as directory, ThreadingHTTPServer(('127.0.0.1', 0), Model) as service:
        tmp = Path(directory)
        env = fixture_environment(tmp)
        env['ASTRION_RESOURCE_ROOT'] = str(ROOT)
        (Path(env['DEPLOY_CONFIG_DIR']) / 'custom_models.json').write_text(json.dumps({
            'models': [{'model_name': 'chat-local', 'model_id': 'chat-local',
                        'url': f'http://127.0.0.1:{service.server_port}/v1', 'apikey': 'fixture-only'}],
        }), encoding='utf-8')
        data = Path(env['DATA_DIR'])
        data.mkdir()
        (data / 'personalization.json').write_text(json.dumps({'auto_generate_title': False, 'ui_locale': 'en-US'}), encoding='utf-8')
        service.errors = []
        service.notices = {mode: [] for mode in MODES}
        service.launches = {mode: [] for mode in MODES}
        worker = threading.Thread(target=service.serve_forever, daemon=True)
        worker.start()
        try:
            with host_fixture(binary, env, tmp / 'host.log') as host:
                browser = Browser(host)
                for mode in MODES:
                    run = exercise(browser, mode)
                    # Open the conversation after the server already dispatched
                    # and confirm the legacy trigger cannot submit it again.
                    cid = run['conversation_id']
                    for _ in range(3):
                        browser.api('GET', f'/api/conversations/{cid}/running-status?workspace_id=default')
                    assert len(service.launches[mode]) == 1, service.launches
                idle = f'{PREFIX}\nBackground commands finished.\n\nCommand: {command("idle")}\nExit code: 0\n\nOutput:\nfixture-idle-done'
                inline = f'{PREFIX}\n[Background run_command finished]\nfixture-inline-done'
                assert [[idle]] == [[notice.rstrip() for notice in notices] for notices in service.notices['idle']], service.notices
                assert service.notices['inline'] == [[inline]], service.notices
                assert all(len(events) == 1 and len(events[0]) == 1 for events in service.notices.values()), service.notices
                assert not service.errors, service.errors
        finally:
            service.shutdown()
            worker.join()
    print('Unattended idle continuation, inline delivery, localization and deduplication passed')


if __name__ == '__main__':
    main()
