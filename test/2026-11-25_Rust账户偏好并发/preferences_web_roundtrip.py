"""Exercise Agent and Chat account preferences against a real Rust Host listener.

Run from the repository with Python backend dependencies and the built binary:
    python3.11 -B test/2026-11-25_Rust账户偏好并发/preferences_web_roundtrip.py
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Barrier
import unittest
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / '2026-11-24_Rust后端'))
from agent_process_roundtrip import ROOT, fixture_environment, rust

AGENT = '/api/personalization'
CHAT = '/api/chat/settings'
MEMORY = '/api/chat/memory'
PERSONAL = Path('personalization.json')
SERVICES = Path('service_config.json')
SECRETS = Path('secrets.json')
MEMORY_FILE = Path('chat/memory/memories.json')
PREFERENCE_FILES = (PERSONAL, SERVICES, SECRETS)
TRACKED = (*PREFERENCE_FILES, MEMORY_FILE)
UNKNOWN = {'legacy_extension': {'保留': [True, None, {'revision': 7}], 'empty': {}}}
FIXTURE_SECRET = 'fixture-search-secret-never-sent-upstream'
LEGACY_SECRET = 'fixture-inline-search-secret'
MEMORY_APPEND = {'operation': 'append', 'content': '  新增跨入口测试记忆  '}


def seed_python() -> None:
    sys.path.insert(0, str(ROOT))
    from types import SimpleNamespace

    from modules.chat_mode.memory import ChatMemory
    from modules.personalization_manager import SECRET_KEYS, save_personalization_config
    from server.chat_mode.settings import settings_payload
    from utils.atomic_io import atomic_write_json

    data = Path(os.environ['DATA_DIR'])
    initial = {
        'theme': 'dark', 'user_name': '偏好用户', 'recent_conversations_prompt_limit': 14,
        'search_provider': 'exa', 'exa_api_key': FIXTURE_SECRET,
        'chat_instructions': 'Python 写入的指令', 'chat_default_thinking_mode': False,
        'chat_default_reasoning_effort': 'medium', 'chat_network_enabled': True,
        'chat_deep_compress_trigger_tokens': 310_000,
    }
    stored = save_personalization_config(data, initial)
    agent = {key: stored[key] for key in initial}
    assert agent == initial, agent
    scope = SimpleNamespace(root=data / 'chat', preferences_dir=data, username='host')
    chat = settings_payload(scope)
    assert chat['models'] == [], chat['models']
    assert chat['personalization']['exa_api_key'] == FIXTURE_SECRET
    for key in SECRET_KEYS:
        chat['personalization'].pop(key, None)
    personal = json.loads((data / PERSONAL).read_bytes())
    atomic_write_json(data / PERSONAL, {**personal, **UNKNOWN, 'exa_api_key': LEGACY_SECRET})

    memory = ChatMemory(scope.root)
    memory.update('append', 'Python 保存的全局记忆')
    memory.write_topic('layout', '查询工作习惯时读取', '原始专题内容\n第二行')
    document = memory.snapshot()
    document['future_memory'] = {'未知字段': ['保留', 3]}
    atomic_write_json(memory.path, document)
    before = memory.path.read_bytes()
    original_memory = memory.snapshot()
    memory.update(**MEMORY_APPEND)
    next_memory = memory.snapshot()
    memory.path.write_bytes(before)

    atomic_write_json(data.parent / 'python-reference.json', {
        'agent': agent, 'chat': chat, 'memory': original_memory, 'after_append': next_memory,
    })


def prepare(root: Path):
    env = fixture_environment(root)
    env['ASTRION_RESOURCE_ROOT'] = str(root)
    subprocess.run([sys.executable, '-B', __file__, '--seed'], env=env, cwd=ROOT, check=True, timeout=30)
    return env, json.loads((root / 'python-reference.json').read_bytes())


def request(base, method, path, *, payload=None, headers=None, status=200, barrier=None):
    url = urlsplit(base)
    sent = {'Origin': base, **(headers or {})}
    body = None
    if payload is not None:
        sent['Content-Type'] = 'application/json'
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    with closing(HTTPConnection(url.hostname, url.port, timeout=8)) as connection:
        if barrier is not None:
            connection.connect()
            barrier.wait(timeout=8)
        connection.request(method, path, body, sent)
        with connection.getresponse() as response:
            raw = response.read()
            assert response.status == status, (method, path, status, response.status, raw[:1000])
            return json.loads(raw), response.headers


def login(base: str) -> dict[str, str]:
    csrf, response = request(base, 'GET', '/api/csrf-token')
    guest = response['Set-Cookie'].split(';', 1)[0]
    logged_in, response = request(base, 'POST', '/host-login', payload={},
                                  headers={'Cookie': guest, 'X-CSRF-Token': csrf['token']})
    assert logged_in['success'] is True, logged_in
    cookie = response['Set-Cookie'].split(';', 1)[0]
    assert cookie != guest
    csrf, _ = request(base, 'GET', '/api/csrf-token', headers={'Cookie': cookie})
    return {'Cookie': cookie, 'X-CSRF-Token': csrf['token']}


def selected(source: dict, names) -> dict:
    return {key: source[key] for key in names}


def snapshot(data: Path, files=TRACKED) -> dict:
    return {path: ((data / path).stat().st_mtime_ns, (data / path).read_bytes()) for path in files}


def check_python_reference(base, data, browser, reference):
    before = snapshot(data)
    chat, _ = request(base, 'GET', CHAT, headers=browser)
    assert chat['success'] is True, chat
    assert snapshot(data) == before, 'Reading Chat preferences changed a legacy account file'
    expected = reference['chat']
    assert selected(chat['data'], ('settings', 'models', 'username', 'theme')) == selected(
        expected, ('settings', 'models', 'username', 'theme'),
    ), chat
    visible = set(reference['agent']) - {'exa_api_key'}
    assert selected(chat['data']['personalization'], visible) == selected(expected['personalization'], visible)
    serialized = json.dumps(chat)
    assert FIXTURE_SECRET not in serialized and LEGACY_SECRET not in serialized
    assert 'exa_api_key' not in chat['data']['personalization']

    agent, _ = request(base, 'GET', AGENT, headers=browser)
    assert agent['success'] is True
    assert selected(agent['data'], reference['agent']) == reference['agent']
    assert selected(json.loads((data / PERSONAL).read_bytes()), UNKNOWN) == UNKNOWN

    preferences = snapshot(data, PREFERENCE_FILES)
    memory, _ = request(base, 'GET', MEMORY, headers=browser)
    assert memory == {'success': True, 'data': reference['memory']}, memory
    saved, _ = request(base, 'POST', MEMORY, payload=MEMORY_APPEND, headers=browser)
    assert saved == {'success': True, 'data': reference['after_append']}, saved
    memory, _ = request(base, 'GET', MEMORY, headers=browser)
    assert memory == saved
    assert json.loads((data / MEMORY_FILE).read_bytes()) == reference['after_append']
    assert snapshot(data, PREFERENCE_FILES) == preferences


def check_concurrent_updates(base, data, browser, host):
    initial = snapshot(data, (SERVICES, SECRETS, MEMORY_FILE))
    with ThreadPoolExecutor(max_workers=2) as executor:
        for round_number in range(1, 13):
            barrier = Barrier(2)
            agent_patch = {'user_name': f'Agent {round_number}', 'recent_conversations_prompt_limit': round_number}
            chat_patch = {'chat_instructions': f'第 {round_number} 轮 Chat 指令',
                          'chat_network_enabled': round_number % 2 == 0}
            agent_write = executor.submit(request, base, 'POST', AGENT, payload=agent_patch,
                                          headers=browser if round_number % 2 else host, barrier=barrier)
            chat_write = executor.submit(request, base, 'PATCH', CHAT, payload=chat_patch,
                                         headers=browser, barrier=barrier)
            agent, _ = agent_write.result()
            chat, _ = chat_write.result()
            assert agent['success'] is True and selected(agent['data'], agent_patch) == agent_patch, agent
            assert chat['success'] is True and selected(chat['data']['settings'], chat_patch) == chat_patch, chat

            expected = {**agent_patch, **chat_patch}
            agent, _ = request(base, 'GET', AGENT, headers=host)
            chat, _ = request(base, 'GET', CHAT, headers=browser)
            assert selected(agent['data'], expected) == expected, (round_number, agent)
            assert selected(chat['data']['personalization'], expected) == expected, (round_number, chat)
            assert FIXTURE_SECRET not in json.dumps(chat)
            stored = json.loads((data / PERSONAL).read_bytes())
            assert selected(stored, expected) == expected and selected(stored, UNKNOWN) == UNKNOWN, stored
    assert snapshot(data, (SERVICES, SECRETS, MEMORY_FILE)) == initial


def check_corrupt_files(base, data, browser, host):
    records = (
        (PERSONAL, b'{"legacy_extension": [1,\n'), (PERSONAL, b'[]\n'),
        (SERVICES, b'{"search_provider":\n'), (SECRETS, b'{"exa_api_key":\n'),
    )
    for relative, garbage in records:
        path = data / relative
        healthy = path.read_bytes()
        path.write_bytes(garbage)
        before = snapshot(data)
        try:
            calls = [('GET', AGENT, None, browser, 500), ('POST', AGENT, {'user_name': 'rejected'}, host, 500)]
            if relative in (PERSONAL, SERVICES):
                calls.extend([
                    ('GET', CHAT, None, browser, 503),
                    ('PATCH', CHAT, {'chat_instructions': 'rejected'}, browser, 503),
                ])
            for method, route, payload, credentials, status in calls:
                result, _ = request(base, method, route, payload=payload, headers=credentials, status=status)
                assert result['success'] is False and result.get('error'), (relative, method, route, result)
                assert snapshot(data) == before, (relative, method, route, 'modified a preference or memory file')
            if relative == SECRETS:
                result, _ = request(base, 'GET', CHAT, headers=browser)
                assert result['success'] is True and 'exa_api_key' not in result['data']['personalization'], result
                assert snapshot(data) == before, 'Chat touched the damaged secret store'
        finally:
            path.write_bytes(healthy)

    path = data / MEMORY_FILE
    healthy = path.read_bytes()
    for garbage in (b'{"version": 1,\n', b'{"version": 1, "global": {}, "topics": {}}\n'):
        path.write_bytes(garbage)
        before = snapshot(data)
        try:
            for method, payload in (('GET', None), ('POST', MEMORY_APPEND)):
                result, _ = request(base, method, MEMORY, payload=payload, headers=browser, status=400)
                assert result['success'] is False and result.get('error'), result
                assert snapshot(data) == before, (method, 'modified a preference or memory file')
        finally:
            path.write_bytes(healthy)


@contextmanager
def account_listener():
    executable = 'astrion-headless.exe' if os.name == 'nt' else 'astrion-headless'
    binary = Path(os.environ.get('ASTRION_HEADLESS_BIN',
                  ROOT / 'backend-rust/target-master/debug' / executable)).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix='account-', dir=HERE) as temporary:
        root = Path(temporary).resolve()
        env, reference = prepare(root)
        data = Path(env['DATA_DIR'])
        with rust(env, binary, 'http://127.0.0.1:1/v1/chat/completions', root / 'web.log', web=True) as base:
            browser = login(base)
            token = (data / 'host_api_token').read_text(encoding='utf-8').strip()
            assert token
            yield base, data, browser, {'Authorization': f'Bearer {token}'}, reference


class AccountPreferencesHttp(unittest.TestCase):
    def setUp(self):
        self.base, self.data, self.browser, self.host, self.reference = self.enterContext(account_listener())

    def test_python_settings_and_memory(self):
        request(self.base, 'GET', CHAT, headers=self.host, status=401)
        request(self.base, 'GET', MEMORY, headers=self.host, status=401)
        check_python_reference(self.base, self.data, self.browser, self.reference)

    def test_concurrent_agent_and_chat_updates(self):
        # Finish any legacy migration before measuring the concurrent patch pairs.
        request(self.base, 'GET', AGENT, headers=self.browser)
        check_concurrent_updates(self.base, self.data, self.browser, self.host)

    def test_corrupt_files_remain_untouched(self):
        check_corrupt_files(self.base, self.data, self.browser, self.host)
        chat, _ = request(self.base, 'GET', CHAT, headers=self.browser)
        assert chat['success'] is True and chat['data']['settings'] == self.reference['chat']['settings']
        memory, _ = request(self.base, 'GET', MEMORY, headers=self.browser)
        assert memory == {'success': True, 'data': self.reference['memory']}


if __name__ == '__main__':
    if sys.argv[1:] == ['--seed']:
        seed_python()
    else:
        unittest.main()
