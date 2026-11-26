"""Verify Git workspace views through both production Rust HTTP listeners."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlencode

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / '2026-11-25_Rust项目存储'))
from project_storage_roundtrip import fixture, listener, login, register, request

SUMMARY = '/api/project/git-summary'
DIFF = '/api/project/git-diff'
CID = 'conv_workspace_git_fixture'
TIMESTAMP = '2024-09-05T11:22:33.019'


def git(repo, *args):
    environment = {key: value for key, value in os.environ.items() if not key.upper().startswith('GIT_')}
    environment.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1', XDG_CONFIG_HOME=str(repo))
    return subprocess.check_output([
        'git', '-c', 'user.name=Git Fixture', '-c', 'user.email=fixture@local.invalid',
        '-c', 'commit.gpgsign=false', '-c', 'core.autocrlf=false', '-C', str(repo), *args,
    ], env=environment, text=True, stderr=subprocess.PIPE).strip()


def file_contents(*changes):
    replacements = dict(changes)
    return ''.join(replacements.get(line, f'line {line:02}') + '\n' for line in range(1, 41))


def seed(data, workspace, binding):
    path = data / 'conversations' / workspace / f'{CID}.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        'id': CID, 'title': 'Git fixture', 'created_at': TIMESTAMP, 'updated_at': TIMESTAMP,
        'messages': [{'role': 'user', 'content': 'Git fixture', 'message_id': 'msg_git_fixture'}],
        'metadata': {'git_worktree_enabled': True, 'git_worktree': binding},
    }), encoding='utf-8')
    return path


def run():
    with fixture() as (root, env, data, alpha, beta):
        git(alpha, 'init', '-b', 'main')
        # A bare repository has no checkout and stops Git from finding the enclosing test checkout.
        git(beta, 'init', '--bare')
        (alpha / 'first.txt').write_text(file_contents(), encoding='utf-8')
        (alpha / 'other name.txt').write_text(file_contents(), encoding='utf-8')
        git(alpha, 'add', '-A')
        git(alpha, 'commit', '-m', 'Baseline')
        (alpha / 'first.txt').write_text(file_contents((8, 'staged')), encoding='utf-8')
        git(alpha, 'add', 'first.txt')
        (alpha / 'first.txt').write_text(file_contents((8, 'staged'), (20, 'unstaged')), encoding='utf-8')
        (alpha / 'other name.txt').write_text(file_contents((15, 'another file')), encoding='utf-8')
        (alpha / 'untracked.txt').write_text('leave untracked\n', encoding='utf-8')
        linked = root / 'linked'
        git(alpha, 'worktree', 'add', '--detach', str(linked), 'HEAD')
        head = git(linked, 'rev-parse', '--short', 'HEAD')
        (linked / 'first.txt').write_text(file_contents((12, 'linked change')), encoding='utf-8')
        binding = {'directory': str(linked), 'active': True, 'branch': '', 'grant': {'path': str(linked), 'added': True}}
        document = seed(data, 'alpha', binding)
        assert_document = document.read_bytes()
        original_index = (alpha / '.git/index').read_bytes()
        query = urlencode({
            'context': 0, 'context_map': json.dumps({'other name.txt': 2}),
            'fold_context_map': json.dumps({'first.txt': {'before:8': 3, 'before:20': 5, 'after': 4}}),
        })

        with listener(root, env) as base:
            browser = login(base)
            bearer = {'Authorization': 'Bearer ' + (data / 'host_api_token').read_text().strip()}
            request(base, '/api/host/workspaces', headers=bearer)
            for path in (SUMMARY, DIFF):
                request(base, path, status=401)
                request(base, path, headers=bearer, status=401)
                request(base, path + '?workspace_id=absent', headers=browser, status=404)
                request(base, path + '?conversation_id=conv_absent', headers=browser, status=400)
            summary, _ = request(base, SUMMARY, headers=browser)
            assert summary == {'success': True, 'data': {
                'has_git': True, 'project_path': str(alpha), 'project_name': alpha.name,
                'branch': 'main', 'upstream': '', 'additions': 3, 'deletions': 3,
                'git_worktree_enabled': False, 'git_worktree': None,
            }}, summary
            diff, _ = request(base, DIFF + '?' + query, headers=browser)
            first, other = diff['data']['files']
            assert first['after_hidden'] == 16 and first['after_fold_key'] == 'after', first
            first_hunk, last_hunk = first['hunks']
            assert first_hunk['lines'][0] == {'kind': 'context', 'text': 'line 05', 'old_line': 5, 'new_line': 5}, first_hunk
            assert first_hunk['before_hidden'] == 4 and first_hunk['before_fold_key'] == 'before:8', first_hunk
            assert last_hunk['before_hidden'] == 6 and last_hunk['lines'][-1]['text'] == 'line 24', last_hunk
            assert other['path'] == 'other name.txt' and other['hunks'][0]['old_count'] == 5, other
            empty, _ = request(base, DIFF, headers=browser, payload={'workspace_id': 'beta'})
            assert empty['data'] == {'has_git': False, 'files': []}, empty
            preferred, _ = request(base, SUMMARY + '?workspace_id=alpha', headers={**browser, 'X-Astrion-Workspace-Id': 'beta'}, payload={'workspace_id': 'absent'})
            assert preferred == summary, preferred
            request(base, SUMMARY + f'?workspace_id=beta&conversation_id={CID}', headers=browser, status=400)
            linked_summary, _ = request(base, SUMMARY + f'?conversation_id={CID}', headers=browser)
            assert linked_summary['data']['project_path'] == str(linked), linked_summary
            assert linked_summary['data']['branch'] == f'HEAD {head}' and linked_summary['data']['git_worktree'] == binding, linked_summary
            linked_diff, _ = request(base, DIFF + f'?conversation_id={CID}', headers=browser)
            assert linked_diff['data']['additions'] == 1 and linked_diff['data']['project_name'] == 'linked', linked_diff
            assert document.read_bytes() == assert_document
            assert (alpha / '.git/index').read_bytes() == original_index

            linked.rename(root / 'renamed')
            stale, _ = request(base, SUMMARY + f'?conversation_id={CID}', headers=browser)
            assert stale['success'] and stale['data']['binding_error'] and stale['data']['project_path'] == str(linked), stale
            failure, _ = request(base, DIFF + f'?conversation_id={CID}', headers=browser, status=400)
            assert failure == {'success': False, 'error': stale['data']['binding_error']}, failure
            register(env, {}, default=None)
            for path in (SUMMARY, DIFF):
                missing, _ = request(base, path, headers=browser)
                assert missing['success'] is False and missing['code'] == 'no_workspace', missing
            register(env, {'alpha': alpha, 'beta': beta})

        with listener(root, env, web=False) as base:
            bearer = {'Authorization': 'Bearer ' + (data / 'host_api_token').read_text().strip()}
            request(base, '/api/host/workspaces', headers=bearer)
            for path in (SUMMARY, DIFF):
                for headers in ({}, bearer):
                    request(base, path, headers=headers, status=401)

    print('Git workspace Web and headless listener contracts passed')


if __name__ == '__main__':
    run()
