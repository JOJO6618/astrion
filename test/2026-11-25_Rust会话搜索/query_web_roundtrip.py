"""Read Python Agent conversations through the assembled Rust Web and headless servers."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / '2026-11-24_Rust后端'))
from agent_process_roundtrip import ROOT, fixture_environment, rust
from chat_web_roundtrip import Browser

WORKSPACES = {'workshop-a': '研发甲', 'workshop-b': '研发乙'}
A, B = WORKSPACES
SEARCH = '/api/conversations/search'
STATISTICS = '/api/conversations/statistics'
QUERY = 'nOrTh sTaR'
A_HITS = [('conv_title_new', 'title'), ('conv_title_old', 'title'),
          ('conv_content_new', 'content'), ('conv_content_old', 'content')]
B_HITS = [('conv_b_title', 'title'), ('conv_b_content', 'content')]


def seed_python_conversations() -> None:
    sys.path.insert(0, str(ROOT))
    from utils.conversation_manager import ConversationManager

    data = os.environ['DATA_DIR']
    catalog = json.loads(Path(os.environ['HOST_WORKSPACES_FILE']).read_bytes())
    managers = {entry['workspace_id']: ConversationManager(
        base_dir=data, project_path=entry['path'], read_only=True,
    ) for entry in catalog['workspaces']}
    created = '2026-11-24T09:00:00'

    def save(workspace, cid, title, minute, messages, *, legacy=False):
        manager = managers[workspace]
        assert manager.current_workspace_id == workspace
        document = {
            'id': cid, 'title': title, 'created_at': created,
            'updated_at': f'2026-11-25T09:{minute:02d}:00',
            'messages': [
                {'role': role, 'content': text, 'message_id': f'msg_{pos}', 'timestamp': created}
                for pos, (role, text) in enumerate(messages)
            ],
            'metadata': {
                'project_path': str(manager.current_project_path), 'thinking_mode': False,
                'run_mode': 'fast', 'total_messages': len(messages), 'total_tools': 0,
                'status': 'active', 'fixture_extension': {'保留': True},
            },
            'todo_list': None,
            'token_statistics': {'total_input_tokens': 700, 'total_output_tokens': 29,
                                 'total_tokens': 729, 'current_context_tokens': 43},
        }
        if legacy:
            document['messages'].append({
                'role': 'assistant', 'content': '', 'message_id': 'msg_tool', 'timestamp': created,
                'tool_calls': [{'id': 'call_fixture', 'type': 'function',
                                'function': {'name': 'astrion_echo', 'arguments': '{"text":"fixture"}'}}],
            })
            document['metadata'].update(status='archived', thinking_mode=True, run_mode='thinking',
                                        total_messages=len(document['messages']), total_tools=1)
            # Preserve older numeric encodings and the missing current-context counter on disk.
            document['token_statistics'] = {'total_input_tokens': ' 071 ', 'total_output_tokens': 9.75,
                                            'total_tokens': 333}
            manager._atomic_write_json(manager._get_conversation_file_path(cid), document)
        else:
            manager._save_conversation_file(cid, document)
        manager._update_index(cid, document)

    save(A, 'conv_title_new', 'North Star 最新标题', 8, [('user', '普通首句。')])
    save(A, 'conv_title_old', 'North Star 较早标题', 1, [('user', 'North Star 也出现在首句。')])
    save(A, 'conv_content_new', '多模态内容', 10, [
        ('assistant', 'assistant-only-marker'),
        ('user', [{'type': 'text', 'text': 'NORTH\t'},
                  {'type': 'image_url', 'image_url': {'url': 'https://fixture.invalid/media-only-marker'}},
                  'Star 是首句！after-sentence-marker']),
        ('user', 'later-user-marker'),
    ])
    save(A, 'conv_content_old', '旧内容', 9, [('user', {'text': 'North Star? 英文标点后的内容'})], legacy=True)
    save(A, 'conv_late_only', '第二句与后续消息才匹配', 11, [
        ('user', '这句不匹配。North Star 只能在第二句出现。'),
        ('user', 'North Star 也在后续消息出现。'),
    ])
    save(B, 'conv_b_title', '仅乙 North Star', 6, [('user', '普通首句。')])
    save(B, 'conv_b_content', '乙的内容', 12, [('user', 'North Star 出现在乙的首句。')])
    for number in range(52):
        save(A, f'conv_capacity_{number:02d}', f'容量校验 {number:02d}', number, [])

    for workspace, expected in ((A, A_HITS), (B, B_HITS)):
        rows = managers[workspace].search_conversations(QUERY)
        assert [(row['id'], row['match_type']) for row in rows] == expected, rows
    stats = managers[A].get_token_statistics('conv_content_new')
    assert (stats['total_tokens'], stats['current_context_tokens']) == (729, 43), stats
    assert managers[A].get_current_context_tokens('conv_content_old') == 0

    expected = {workspace: manager.get_statistics() for workspace, manager in managers.items()}
    assert expected[A]['status_distribution']['archived'] == expected[A]['total_tools'] == 1, expected[A]
    assert expected[A]['token_statistics'] == {
        'total_input_tokens': 56 * 700 + 71, 'total_output_tokens': 56 * 29 + 9,
        'total_tokens': 56 * 729 + 80, 'conversations_with_stats': 57,
    }, expected[A]
    assert expected[B]['total_conversations'] == 2, expected[B]
    (Path(data).parent / 'python_statistics.json').write_text(json.dumps(expected), encoding='utf-8')

    # Simulate an old partial index after computing the Python reference on the same files.
    manager = managers[A]
    partial = json.loads(manager.index_file.read_bytes())
    del partial['conv_content_old']
    manager._save_index(partial)
    assert len(partial) == expected[A]['total_conversations'] - 1


def get(base, route, *, status=200, opener=None, headers=None):
    if opener is None:
        opener = build_opener(ProxyHandler({}))
    request = Request(base + route, headers={'Origin': base, **(headers or {})})
    try:
        response = opener.open(request, timeout=5)
    except HTTPError as error:
        response = error
    with response:
        body = response.read()
        assert response.status == status, (route, status, response.status, body[:1000])
        if response.headers.get_content_type() == 'application/json':
            return json.loads(body)
        return body


def check_web(base: str, data: Path, workspaces: dict[str, Path], expected_statistics: dict, before: dict):
    browser = Browser(base)
    browser.request('/api/usage', status=401)
    browser.login()

    def read(route, **kwargs):
        return get(base, route, opener=browser.cookies, **kwargs)

    def search(workspace=None, q=QUERY, *, status=200, **options):
        params = dict(options)
        if workspace is not None:
            params['workspace_id'] = workspace
        if q is not None:
            params['q'] = q
        return read(f'{SEARCH}?{urlencode(params)}', status=status)

    def flat(workspace, expected, *, q=QUERY, **options):
        body = search(workspace, q, **options)
        assert body['success'] is True, body
        page = body['data']
        rows = page['results']
        assert page == {'query': q.strip(), 'count': len(expected), 'results': rows}, page
        assert [(row['id'], row['match_type']) for row in rows] == expected, rows
        path = str(workspaces[workspace or A])
        for row in rows:
            assert set(row) == {'id', 'title', 'created_at', 'updated_at', 'project_path', 'match_type'}, row
            assert row['project_path'] == path and row['created_at'] == '2026-11-24T09:00:00', row

    flat(None, A_HITS, q=f'  {QUERY}\t ')
    flat(B, B_HITS)
    flat(A, [], q='仅乙')
    flat(B, [B_HITS[0]], q='仅乙')
    for excluded in ('after-sentence-marker', 'later-user-marker', 'assistant-only-marker',
                     'media-only-marker', '英文标点后的内容'):
        flat(A, [], q=excluded)

    for spelling, limit, a_count, b_count in (('1', 50, len(A_HITS), len(B_HITS)), ('True', 1, 1, 1)):
        result = search(B, all_workspaces=spelling, limit=limit)
        assert result['success'] is True, result
        grouped = result['data']
        groups = grouped['groups']
        assert grouped == {'query': QUERY, 'groups': groups, 'total': a_count + b_count}, grouped
        assert [(group['workspace_id'], group['label'], group['count']) for group in groups] == [
            (A, WORKSPACES[A], a_count), (B, WORKSPACES[B], b_count),
        ], grouped
        for group, expected in zip(groups, (A_HITS[:a_count], B_HITS[:b_count])):
            assert [(row['id'], row['match_type']) for row in group['results']] == expected, group
    grouped = search(q='仅乙', all_workspaces='true')['data']
    assert grouped['total'] == 1 and [group['workspace_id'] for group in grouped['groups']] == [B], grouped

    capacity = [(f'conv_capacity_{number:02d}', 'title') for number in reversed(range(52))]
    for limit, count in (('0', 1), ('999', 50), ('invalid', 20)):
        flat(A, capacity[:count], q='容量校验', limit=limit)
    for q in (None, '', ' \t '):
        error = search(q=q, status=400)
        assert error['success'] is False and error['error'] == 'Missing query parameter', error
    assert search('unknown-workshop', status=404)['success'] is False

    for workspace in (None, A, B):
        route = STATISTICS if workspace is None else f'{STATISTICS}?{urlencode({"workspace_id": workspace})}'
        result = read(route)
        assert result == {'success': True, 'data': expected_statistics[workspace or A]}, (workspace, result)
    assert read(f'{STATISTICS}?workspace_id=unknown-workshop', status=404)['success'] is False

    for workspace, cid, current in (
        (A, 'conv_content_new', 43), (A, 'content_new', 43), (A, 'conv_content_old', 0),
        (A, 'conv_does_not_exist', 0), (B, 'conv_content_new', 0), (B, 'conv_b_content', 43),
    ):
        route = f'/api/conversations/{cid}/tokens?{urlencode({"workspace_id": workspace})}'
        assert read(route) == {'success': True, 'data': {'total_tokens': current}}, route
    assert read('/api/usage') == {'success': True, 'applicable': False, 'data': None}
    get(base, '/api/usage', status=401, headers=bearer(data))
    assert snapshot(data) == before, 'Web GETs rewrote a conversation or the partial index'

    flat(A, A_HITS)
    flat(A, [], q='South Star')
    relative = Path('conversations') / A / 'conv_content_new.json'
    path = data / relative
    previous_mtime, previous_bytes = before[relative]
    changed = previous_bytes.replace(b'"NORTH\\t"', b'"SOUTH\\t"', 1)
    assert changed != previous_bytes and len(changed) == len(previous_bytes)
    # Preserve the old index and file size while changing the conversation's bytes and mtime.
    replacement = path.with_suffix('.tmp')
    replacement.write_bytes(changed)
    os.utime(replacement, ns=(path.stat().st_atime_ns, previous_mtime + 2_000_000_000))
    replacement.replace(path)
    expected_after_edit = {**before, relative: (path.stat().st_mtime_ns, changed)}

    flat(A, [('conv_content_new', 'content')], q='South Star')
    flat(A, [hit for hit in A_HITS if hit[0] != 'conv_content_new'])
    flat(B, [], q='South Star')
    flat(B, B_HITS)
    assert snapshot(data) == expected_after_edit, 'Post-edit search changed the conversation files or index'
    return expected_after_edit


def bearer(data: Path) -> dict[str, str]:
    token = (data / 'host_api_token').read_text(encoding='utf-8').strip()
    assert token
    return {'Authorization': f'Bearer {token}'}


def snapshot(data: Path) -> dict[Path, tuple[int, bytes]]:
    return {path.relative_to(data): (path.stat().st_mtime_ns, path.read_bytes())
            for path in (data / 'conversations').rglob('*') if path.is_file()}


def run() -> None:
    binary = Path(os.environ.get('ASTRION_HEADLESS_BIN',
                  ROOT / 'backend-rust/target-master/debug/astrion-headless')).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix='query-', dir=HERE) as temporary:
        root = Path(temporary).resolve()
        env = fixture_environment(root)
        env['ASTRION_RESOURCE_ROOT'] = str(root)
        data = Path(env['DATA_DIR'])
        paths = {A: Path(env['HOST_PROJECT_PATH']), B: root / 'second-workspace'}
        paths[B].mkdir()
        Path(env['HOST_WORKSPACES_FILE']).write_text(json.dumps({
            'default_workspace_id': A,
            'workspaces': [{'workspace_id': ws, 'label': label, 'path': str(paths[ws])}
                           for ws, label in WORKSPACES.items()],
        }, ensure_ascii=False), encoding='utf-8')
        subprocess.run([sys.executable, '-B', __file__, '--seed'], env=env, cwd=ROOT, check=True, timeout=30)
        expected_statistics = json.loads((root / 'python_statistics.json').read_bytes())
        before = snapshot(data)
        assert len(before) == 61, before.keys()
        index_bytes = before[Path('conversations') / A / 'index.json'][1]
        assert 'conv_content_old' not in json.loads(index_bytes)

        endpoint = 'http://127.0.0.1:1/v1/chat/completions'
        with rust(env, binary, endpoint, root / 'web.log', web=True) as base:
            after_edit = check_web(base, data, paths, expected_statistics, before)
        assert snapshot(data) == after_edit, 'Web shutdown changed the fixture files'

        with rust(env, binary, endpoint, root / 'headless.log') as base:
            authorization = bearer(data)
            workspaces = get(base, '/api/host/workspaces', headers=authorization)['data']['workspaces']
            assert [row['workspace_id'] for row in workspaces] == [A, B], workspaces
            for headers in ({}, authorization):
                get(base, '/api/usage', status=401, headers=headers)
                get(base, '/api/usage-stats', status=404, headers=headers)
        assert snapshot(data) == after_edit, 'Headless GETs changed the Python conversation or index files'

    print('Python conversation search, cache refresh, statistics, context tokens and usage auth passed.')


if __name__ == '__main__':
    if sys.argv[1:] == ['--seed']:
        seed_python_conversations()
    else:
        run()
