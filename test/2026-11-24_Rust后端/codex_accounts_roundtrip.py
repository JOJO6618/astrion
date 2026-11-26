"""Exercise GET /api/codex/accounts on the real Web and headless binaries."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from agent_process_roundtrip import HERE, ROOT, fixture_environment, rust
from chat_web_roundtrip import Browser


def seed(data: Path) -> None:
    (data / 'codex_accounts').mkdir(parents=True, exist_ok=True)
    (data / 'codex_models').mkdir(parents=True, exist_ok=True)
    (data / 'codex_accounts.json').write_text(json.dumps({
        'version': 1,
        'accounts': {
            'openai-codex': {
                'display_hash': 'aaaaaa',
                'created_at': '2026-11-24T00:00:00Z',
                'last_login_at': '2026-11-24T01:00:00Z',
                'legacy': True,
            },
            'openai-codex-second': {
                'display_hash': 'bbbbbb',
                'created_at': '2026-11-25T00:00:00Z',
                'legacy': False,
            },
        },
    }), encoding='utf-8')
    (data / 'codex_auth.json').write_text(json.dumps({
        'tokens': {'access_token': 'legacy-access', 'refresh_token': 'legacy-refresh'}
    }), encoding='utf-8')
    (data / 'codex_accounts' / 'openai-codex-second.json').write_text(json.dumps({
        'tokens': {'access_token': 'second-access', 'refresh_token': ''}
    }), encoding='utf-8')
    (data / 'codex_models.json').write_text(json.dumps({
        'source': 'online',
        'fetched_at': '2026-11-24T00:00:00Z',
        'models': [
            {'slug': 'gpt-5', 'visibility': 'list', 'supported_in_api': True},
            {'slug': 'hidden', 'visibility': 'hide', 'supported_in_api': True},
        ],
    }), encoding='utf-8')
    (data / 'codex_models' / 'openai-codex-second.json').write_text(json.dumps({
        'source': 'cache',
        'models': [{'slug': 'gpt-second', 'visibility': 'list', 'supported_in_api': True}],
    }), encoding='utf-8')


def expected() -> dict:
    return {
        'success': True,
        'primary_account_id': 'openai-codex',
        'accounts': [
            {
                'id': 'openai-codex',
                'hash': 'aaaaaa',
                'display_name': 'OpenAI（ChatGPT 订阅）-aaaaaa',
                'created_at': '2026-11-24T00:00:00Z',
                'last_login_at': '2026-11-24T01:00:00Z',
                'is_legacy': True,
                'connected': True,
                'state': 'ready',
                'credential_missing': False,
                'is_primary': True,
                'models': {
                    'source': 'online',
                    'fetched_at': '2026-11-24T00:00:00Z',
                    'model_count': 1,
                    'total_models': 2,
                    'visible_models': ['openai-codex/gpt-5'],
                },
            },
            {
                'id': 'openai-codex-second',
                'hash': 'bbbbbb',
                'display_name': 'OpenAI（ChatGPT 订阅）-bbbbbb',
                'created_at': '2026-11-25T00:00:00Z',
                'last_login_at': None,
                'is_legacy': False,
                'connected': False,
                'state': 'disconnected',
                'credential_missing': False,
                'is_primary': False,
                'models': {
                    'source': 'cache',
                    'fetched_at': None,
                    'model_count': 1,
                    'total_models': 1,
                    'visible_models': ['openai-codex-second/gpt-second'],
                },
            },
        ],
    }


def run() -> None:
    binary = Path(os.environ['ASTRION_HEADLESS_BIN']).resolve(strict=True)
    with tempfile.TemporaryDirectory(dir=HERE) as temporary:
        root = Path(temporary)
        env = fixture_environment(root)
        env['ASTRION_RESOURCE_ROOT'] = str(ROOT)
        data = Path(env['DATA_DIR'])
        data.mkdir(parents=True, exist_ok=True)
        seed(data)
        with rust(env, binary, 'http://127.0.0.1:1/v1/chat/completions', root / 'web.log', web=True) as base:
            browser = Browser(base)
            browser.request('/api/codex/accounts', status=401)
            browser.login()
            assert browser.request('/api/codex/accounts') == expected()
        token = (data / 'host_api_token').read_text().strip()
        with rust(env, binary, 'http://127.0.0.1:1/v1/chat/completions', root / 'headless.log') as base:
            request = Request(base + '/api/codex/accounts', headers={'Authorization': 'Bearer ' + token})
            try:
                with urlopen(request, timeout=4) as response:
                    raise AssertionError(f'headless returned {response.status}')
            except HTTPError as error:
                with error:
                    assert error.code == 404, error.code
    print('Codex accounts Web payload/order/cache summary and headless 404 passed.')


if __name__ == '__main__':
    run()
