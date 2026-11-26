"""Check refresh routing and authentication on the shipped Web and headless listeners."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '2026-11-25_Rust项目存储'))
from project_storage_roundtrip import fixture, listener, login, request


def verify():
    with fixture() as (root, env, data, _, _):
        refresh = '/api/codex/models/refresh?account_id=missing-fixture-account'
        bulk = '/api/providers/refresh-all'
        for web in (True, False):
            with listener(root, env, web=web) as base:
                bearer = {'Authorization': 'Bearer ' + (data / 'host_api_token').read_text().strip()}
                for headers in ({}, bearer):
                    request(base, bulk, method='POST', headers=headers, payload={}, status=403)
                    request(base, refresh, method='POST', headers=headers, payload={}, status=403 if web else 404)
                if web:
                    csrf, headers = request(base, '/api/csrf-token')
                    guest = {'Cookie': headers['Set-Cookie'].split(';', 1)[0], 'X-CSRF-Token': csrf['token']}
                    for path in (bulk, refresh):
                        request(base, path, method='POST', headers=guest, payload={}, status=401)
                    session = login(base)
                    request(base, bulk, method='POST', headers={'Cookie': session['Cookie']}, payload={}, status=403)
                    failure, _ = request(base, refresh, method='POST', headers=session, payload={}, status=404)
                    assert failure['success'] is False and failure['error'] == 'account_not_found', failure
                    result, _ = request(base, bulk, method='POST', headers=session, payload={})
                    assert result['success'] and result['count'] == 0 and result['failed'] == 0, result


if __name__ == '__main__':
    verify()
    print('Production refresh listeners and authentication verified')
