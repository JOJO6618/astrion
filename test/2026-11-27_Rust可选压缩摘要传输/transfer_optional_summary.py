"""Production Web compatibility with the v1 archive's optional compact history."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import sys
import tempfile
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / '2026-11-26_Rust会话传输'))
from transfer_http import (Browser, DATE, PNG, ROOT, digest, fixture_environment,
                           rust, stored_document)


SUMMARY = '.astrion/compact_result/retired-summary.md'
BODY = b'# Earlier work\n'
SOURCE = '/previous/workspace'
TARGET = 'workspace_id=second'
INITIAL = 'workspace_id=default'


def document(path=SUMMARY):
    return {
        'id': 'conv_original', 'title': 'Old compact fixture',
        'created_at': DATE, 'updated_at': DATE,
        'messages': [{'role': 'user', 'message_id': 'original',
                      'content': f'Previous summary: {SOURCE}/{path}', 'timestamp': DATE}],
        'metadata': {'project_path': SOURCE, 'frozen_system_prompt': 'Original prompt',
                     'deep_compression_records': [{'compact_file': path, 'count': 1}]},
    }


def package(raw, *, manifest_rows=(), files=()):
    contents = json.dumps(raw, ensure_ascii=False).encode()
    manifest = {'format': 'astrion-conversation', 'version': 1, 'kind': 'agent',
                'document_sha256': digest(contents), 'source': {'project_path': SOURCE, 'environment': 'host'},
                'resources': list(manifest_rows)}
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr('conversation.json', contents)
        bundle.writestr('manifest.json', json.dumps(manifest))
        for name, payload in files:
            bundle.writestr(name, payload)
    return output.getvalue()


def inspect(payload, expected, raw):
    with zipfile.ZipFile(io.BytesIO(payload)) as bundle:
        manifest = json.loads(bundle.read('manifest.json'))
        current = json.loads(bundle.read('conversation.json'))
        assert current['messages'][0] == raw['messages'][0], current['messages']
        assert current['metadata']['deep_compression_records'] == raw['metadata']['deep_compression_records']
        assert len(manifest['resources']) == int(expected), manifest
        if expected:
            record, = manifest['resources']
            assert SUMMARY in record['paths'], record
            assert bundle.read(record['file']) == BODY


def exercise(browser, base, workspace):
    raw = document()
    row = {'kind': 'material', 'mime_type': None, 'media_id': None,
           'sha256': digest(BODY), 'file': 'assets/' + digest(BODY),
           'paths': [SUMMARY, SOURCE + '/' + SUMMARY], 'size': len(BODY)}

    for state in ('missing', 'restored-then-removed', 'available'):
        available = state != 'missing'
        resources = [row] if available else []
        files = [(row['file'], BODY)] if available else []
        original = package(raw, manifest_rows=resources, files=files)
        cid = browser.upload(INITIAL, original, f'old-v1-{state}')['conversation_id']
        inspect(browser.export(cid, INITIAL)[1], available, raw)
        if state == 'restored-then-removed':
            db = base / 'conversations/default/conversations.sqlite3'
            mapping = stored_document(db, cid)['metadata']['conversation_transfer']['paths'][SUMMARY]
            moved_file = workspace / mapping
            assert moved_file.read_bytes() == BODY
            moved_file.unlink()
            # Re-export and migrate even if a previously restored optional summary was removed.
            inspect(browser.export(cid, INITIAL)[1], False, raw)
        route = f'/api/conversations/{cid}/migrate?{INITIAL}'
        moved = browser.send('POST', route, {'operation_id': f'move-{state}', 'target_workspace_id': 'second'})
        assert moved == {'conversation_id': cid, 'workspace_id': 'second'}, moved
        inspect(browser.export(cid, TARGET)[1], state == 'available', raw)

    responses = [
        (package(document('.astrion/handoff/old/summary.md')), 'no-handoff', 'handoff material'),
        (package(raw, manifest_rows=[row]), 'advertised-missing', 'checksum'),
        (package(raw, manifest_rows=[row], files=[(row['file'], b'x' * len(BODY))]), 'advertised-corrupt', 'checksum'),
    ]
    media = document()
    media['messages'][0]['media_refs'] = [{'kind': 'image', 'media_id': 'sha256:' + digest(PNG)}]
    responses.append((package(media), 'no-required-media', 'image/video'))
    for payload, operation, error in responses:
        rejected = browser.upload(INITIAL, payload, operation, status=400)
        assert error in json.dumps(rejected, ensure_ascii=False), rejected


def run():
    path = Path(os.environ['ASTRION_HEADLESS_BIN']).resolve(strict=True)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        env = fixture_environment(root)
        env['ASTRION_RESOURCE_ROOT'] = str(ROOT)
        second = root / 'second'
        second.mkdir()
        config = Path(env['HOST_WORKSPACES_FILE'])
        registry = json.loads(config.read_text())
        registry['workspaces'].append({'workspace_id': 'second', 'label': 'Second', 'path': str(second)})
        config.write_text(json.dumps(registry))
        # Archive handling does not call a model.
        with rust(env, path, 'http://127.0.0.1:9/v1/chat/completions', root / 'host.log', web=True) as url:
            exercise(Browser(url), Path(env['DATA_DIR']), Path(env['HOST_PROJECT_PATH']))
    print('Optional historical summary import/export/migration passed; declared and required assets remain strict.')


if __name__ == '__main__':
    run()
