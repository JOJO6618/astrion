"""Round-trip Chat media through production Python and headless Rust HTTP.

Run:
  ASTRION_HEADLESS_BIN=backend-rust/target-master/debug/astrion-headless \
  python3.11 -B test/2026-11-26_RustChat媒体/media_roundtrip.py
"""
from __future__ import annotations

import hashlib
import json
import os
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from datetime import datetime, timedelta
from http.client import HTTPConnection
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / '2026-11-24_Rust后端'))
from agent_process_roundtrip import ROOT, fixture_environment, rust

def png_image(color: tuple[int, int, int], size: tuple[int, int] = (9, 7)) -> bytes:
    from PIL import Image

    stream = BytesIO()
    Image.new('RGB', size, color).save(stream, format='PNG')
    return stream.getvalue()


def oversized_png_header(width: int = 10001, height: int = 10000) -> bytes:
    """A valid PNG header/IEND only; dimensions alone exceed the pixel cap."""
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (struct.pack('>I', len(payload)) + kind + payload
                + struct.pack('>I', zlib.crc32(kind + payload) & 0xffffffff))

    header = struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header) + chunk(b'IEND', b'')


def mp4_video() -> bytes:
    # Only the container signature is inspected by Chat; no decoder is invoked.
    return b'\x00\x00\x00\x18ftypqt  \x00\x00\x00\x00qt  '


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def media_id(payload: bytes) -> str:
    return 'sha256:' + sha(payload)


def write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def python_app(root: Path):
    """The real Chat media Blueprint with trusted Host identity/scope patched in."""
    from unittest.mock import patch

    from flask import Flask

    from modules.chat_mode.storage import ChatStorage
    from server.chat_mode import chat_mode_bp
    from server.chat_mode_scope import ChatScope
    from server.runtime.context import TrustedPrincipal

    class Context:
        def __init__(self, chat_root: Path):
            self.identity = TrustedPrincipal('host', None, conversation_kind='chat')
            self.scope = ChatScope('host', chat_root, chat_root.parent)
            self.storage = ChatStorage(self.scope.root)

        def __enter__(self):
            self.patches = [
                patch('server.auth_helpers.is_logged_in', return_value=True),
                patch('server.chat_mode.context.principal', return_value=self.identity),
                patch('server.chat_mode.context.scope_for_principal', return_value=self.scope),
            ]
            for item in self.patches:
                item.start()
            return self

        def __exit__(self, *exc):
            for item in reversed(self.patches):
                item.stop()

    app = Flask(__name__)
    app.secret_key = 'chat-media-fixture'
    app.register_blueprint(chat_mode_bp)
    context = Context(root)
    return app, context


def python_phase(action: str) -> dict:
    sys.path.insert(0, str(ROOT))
    data = Path(os.environ['DATA_DIR'])
    chat = data / 'chat'
    if action == 'seed':
        python_a = png_image((3, 120, 60), (5, 4))
        write(Path(os.environ['SEED_FILE']), python_a)
        app, context = python_app(chat)
        with context, app.test_client() as client:
            response = client.post(
                '/api/chat/media',
                data={'media': (BytesIO(python_a), 'python.png')},
                content_type='multipart/form-data',
            )
            assert response.status_code == 200, (response.status_code, response.data[:1000])
            payload = response.get_json()
            assert payload['success'] is True, payload
            reference = payload['data']
            assert set(reference) == {'media_id', 'kind', 'mime_type', 'size', 'sha256'}, reference
            assert reference['media_id'] == media_id(python_a), reference
            assert reference['kind'] == 'image' and reference['mime_type'] == 'image/png', reference
            assert reference['size'] == len(python_a) and reference['sha256'] == sha(python_a), reference
            # Production Python remains the source of truth for the seeded file.
            from modules.chat_mode.media import resolve_refs
            from utils.media_store import MediaStore

            resolved = resolve_refs(chat, [reference['media_id']])[0]
            assert resolved == reference, resolved
            assert MediaStore(chat).load_bytes_by_media_id(reference['media_id']) == python_a
            # Unauthenticated Python route is a 401 as well.
            anonymous = python_app(chat)[0].test_client()
            from unittest.mock import patch
            with patch('server.auth_helpers.is_logged_in', return_value=False):
                assert anonymous.get('/api/chat/media/' + reference['media_id']).status_code == 401
        return {'seed': reference}

    if action == 'takeover':
        rust_id = os.environ['RUST_MEDIA_ID']
        rust_bytes = Path(os.environ['RUST_MEDIA_FILE']).read_bytes()
        video_id = os.environ['RUST_VIDEO_ID']
        video_bytes = Path(os.environ['RUST_VIDEO_FILE']).read_bytes()
        python_c = png_image((20, 80, 160), (13, 11))
        write(Path(os.environ['PYTHON_C_FILE']), python_c)
        draft_bytes = png_image((11, 201, 31), (5, 5))
        handoff_bytes = png_image((20, 30, 210), (6, 4))
        write(Path(os.environ['PYTHON_DRAFT_FILE']), draft_bytes)
        write(Path(os.environ['PYTHON_HANDOFF_FILE']), handoff_bytes)

        app, context = python_app(chat)
        with context, app.test_client() as client:
            # Python production HTTP takes over the media Rust wrote and indexed.
            response = client.get('/api/chat/media/' + rust_id)
            assert response.status_code == 200, (response.status_code, response.data[:1000])
            assert response.data == rust_bytes
            assert response.headers['Content-Type'].startswith('image/png')
            assert response.headers['Cache-Control'] == 'private, no-store'
            assert response.headers['X-Content-Type-Options'] == 'nosniff'
            assert rust_id in response.headers.get('Content-Disposition', '')

            video = client.get('/api/chat/media/' + video_id)
            assert video.status_code == 200 and video.data == video_bytes
            assert video.headers['Content-Type'] == 'video/quicktime'

            from modules.chat_mode.media import resolve_refs
            from utils.media_store import MediaStore

            resolved = resolve_refs(chat, [rust_id, video_id])
            assert resolved[0]['media_id'] == rust_id and resolved[0]['kind'] == 'image'
            assert resolved[1]['media_id'] == video_id and resolved[1]['kind'] == 'video'
            store = MediaStore(chat)
            assert store.load_bytes_by_media_id(rust_id) == rust_bytes
            index = json.loads((chat / 'conversations/media_store/index.json').read_text())
            seed_id = os.environ['SEED_ID']
            assert index['future_index_field'] == {'keep': [1, 2, 3]}, index.keys()
            assert index['media'][seed_id]['future_entry_field'] == 'keep', index['media'][seed_id]
            for reference in (resolved[0], resolved[1]):
                entry = index['media'][reference['media_id']]
                assert entry['sha256'] == reference['sha256']
                assert entry['size'] == reference['size']
                assert entry['kind'] == reference['kind']
                assert entry['mime_type'] == reference['mime_type']
                assert store.load_bytes_by_media_id(reference['media_id']) is not None

            uploaded = client.post(
                '/api/chat/media',
                data={'media': (BytesIO(python_c), 'python-c.png')},
                content_type='multipart/form-data',
            )
            assert uploaded.status_code == 200, (uploaded.status_code, uploaded.data[:1000])
            c_ref = uploaded.get_json()['data']
            assert c_ref['media_id'] == media_id(python_c)

            # GC references: media used only by an inactive Chat branch must survive.
            orphan_bytes = png_image((240, 240, 20), (4, 4))
            orphan_response = client.post(
                '/api/chat/media',
                data={'media': (BytesIO(orphan_bytes), 'orphan.png')},
                content_type='multipart/form-data',
            )
            assert orphan_response.status_code == 200, orphan_response.data[:1000]
            orphan = orphan_response.get_json()['data']

            storage = context.storage
            conversation = storage.create()
            main_turn = storage.begin(
                conversation, 'gc:main',
                message={'message_id': 'gc-main', 'role': 'user', 'content': 'main',
                         'metadata': {'media_refs': [{'media_id': rust_id}]}},
            )['value']
            storage.finish(conversation, main_turn['id'])
            branch_turn = storage.begin(
                conversation, 'gc:branch', parent_id='gc-main',
                message={'message_id': 'gc-branch', 'role': 'user', 'content': 'branch',
                         'metadata': {'media_refs': [{'media_id': c_ref['media_id']}]}},
            )['value']
            storage.finish(conversation, branch_turn['id'])
            # The branch holding c_ref is no longer the latest branch.
            tip_turn = storage.begin(
                conversation, 'gc:tip', parent_id='gc-branch',
                message={'message_id': 'gc-tip', 'role': 'user', 'content': 'tip'},
            )['value']
            storage.finish(conversation, tip_turn['id'])

            # A draft may be the only reference to media before the first send.
            draft_response = client.post(
                '/api/chat/media',
                data={'media': (BytesIO(draft_bytes), 'draft.png')},
                content_type='multipart/form-data',
            )
            assert draft_response.status_code == 200, draft_response.data[:1000]
            draft_ref = draft_response.get_json()['data']
            from modules.composer_drafts import save_draft
            save_draft(chat / 'drafts' / 'gc-draft.json',
                       {'text': 'draft keeps media', 'media': [draft_ref]})

            # A frozen, unfinished handoff snapshot may also be the only reference.
            handoff_response = client.post(
                '/api/chat/media',
                data={'media': (BytesIO(handoff_bytes), 'handoff.png')},
                content_type='multipart/form-data',
            )
            assert handoff_response.status_code == 200, handoff_response.data[:1000]
            handoff_ref = handoff_response.get_json()['data']
            from modules.chat_mode import handoff
            from server.chat_mode.handoff_execution import freeze
            handoff_conversation = storage.create()
            handoff.presentation(storage, handoff_conversation, 'gc-handoff-view')
            handoff_turn = storage.begin(
                handoff_conversation, 'gc:handoff:user',
                message={'message_id': 'gc-handoff-user', 'role': 'user', 'content': 'handoff'},
            )['value']
            storage.append(handoff_conversation, handoff_turn['id'], {
                'message_id': 'gc-handoff-assistant', 'role': 'assistant',
                'content': 'Ready <suggest_agent reason="gc" />',
            })
            storage.finish(handoff_conversation, handoff_turn['id'])
            handoff_state = handoff.register(
                storage, handoff_conversation, context.identity,
                schedule=False, turn_id=handoff_turn['id'],
                presentation_id='gc-handoff-view',
            )
            assert handoff_state and handoff_state['status'] == 'pending', handoff_state
            snapshot = freeze(storage, handoff_conversation, handoff_state['id'])
            snapshot['messages'][-1]['media_refs'] = [handoff_ref]
            snapshot_path = chat / 'handoffs' / handoff_state['id'] / 'snapshot.json'
            snapshot_path.write_text(json.dumps(snapshot), encoding='utf-8')

            index = json.loads((chat / 'conversations/media_store/index.json').read_text())
            old = (datetime.now() - timedelta(days=2)).isoformat()
            for identity in (rust_id, c_ref['media_id'], orphan['media_id'],
                             draft_ref['media_id'], handoff_ref['media_id']):
                index['media'][identity]['updated_at'] = old
            for identity in (rust_id, c_ref['media_id'], draft_ref['media_id'],
                             handoff_ref['media_id']):
                index['media'][identity]['future_kept_field'] = 'keep'
            store._save_index(index)

            from modules.chat_mode.media_gc import collect
            removed = collect(storage, now=time.time())
            assert removed == [orphan['media_id']], removed
            assert store.load_bytes_by_media_id(rust_id) == rust_bytes
            assert store.load_bytes_by_media_id(c_ref['media_id']) == python_c
            assert store.load_bytes_by_media_id(orphan['media_id']) is None
            assert store.load_bytes_by_media_id(draft_ref['media_id']) == draft_bytes
            assert store.load_bytes_by_media_id(handoff_ref['media_id']) == handoff_bytes
            document = storage.read(conversation)
            assert any(message['message_id'] == 'gc-branch' for message in document['messages'])
            index = json.loads((chat / 'conversations/media_store/index.json').read_text())
            assert index['future_index_field'] == {'keep': [1, 2, 3]}, index.keys()
            assert index['media'][os.environ['SEED_ID']]['future_entry_field'] == 'keep'
            for identity in (rust_id, c_ref['media_id'], draft_ref['media_id'],
                             handoff_ref['media_id']):
                assert index['media'][identity]['future_kept_field'] == 'keep', index['media'][identity]

            # Leave an expired orphan for Rust's own GC; the two branch refs above are
            # also expired, so Rust must keep them when its post-write GC runs.
            rust_orphan_bytes = png_image((1, 2, 3), (3, 3))
            rust_orphan_response = client.post(
                '/api/chat/media',
                data={'media': (BytesIO(rust_orphan_bytes), 'rust-orphan.png')},
                content_type='multipart/form-data',
            )
            assert rust_orphan_response.status_code == 200, rust_orphan_response.data[:1000]
            rust_orphan = rust_orphan_response.get_json()['data']
            index = json.loads((chat / 'conversations/media_store/index.json').read_text())
            index['media'][rust_orphan['media_id']]['updated_at'] = old
            store._save_index(index)

            # Account isolation: another Chat scope cannot read this account's IDs.
            other_app, other_context = python_app(data / 'other-chat')
            with other_context, other_app.test_client() as other:
                assert other.get('/api/chat/media/' + rust_id).status_code == 404
                assert other.get('/api/chat/media/' + c_ref['media_id']).status_code == 404
        return {'python_c': c_ref, 'resolved': resolved, 'orphan': orphan,
                'rust_orphan': rust_orphan, 'draft': draft_ref, 'handoff': handoff_ref}

    raise AssertionError(action)


def python(action: str, env: dict[str, str]) -> dict:
    result = subprocess.run(
        [sys.executable, '-B', __file__, action],
        env=env,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout
    line = next(line for line in result.stdout.splitlines() if line.startswith('RESULT='))
    return json.loads(line.removeprefix('RESULT='))


def request(base: str, method: str, path: str, *, headers: dict[str, str] | None = None,
            body: bytes | None = None, status: int = 200) -> tuple[bytes, dict[str, str]]:
    url = urlsplit(base)
    connection = HTTPConnection(url.hostname, url.port, timeout=15)
    sent = {'Origin': base, **(headers or {})}
    try:
        connection.request(method, path, body, sent)
        with connection.getresponse() as response:
            payload = response.read()
            assert response.status == status, (method, path, status, response.status, payload[:1000])
            return payload, {key.lower(): value for key, value in response.getheaders()}
    finally:
        connection.close()


def login(base: str) -> dict[str, str]:
    payload, headers = request(base, 'GET', '/api/csrf-token')
    token = json.loads(payload)['token']
    cookie = headers['set-cookie'].split(';', 1)[0]
    _, login_headers = request(
        base,
        'POST',
        '/host-login',
        headers={'Cookie': cookie, 'X-CSRF-Token': token, 'Content-Type': 'application/json'},
        body=b'{}',
    )
    # Host login rotates the session cookie; use the newly issued one.
    cookie = login_headers['set-cookie'].split(';', 1)[0]
    payload, headers = request(base, 'GET', '/api/csrf-token', headers={'Cookie': cookie})
    return {'Cookie': cookie, 'X-CSRF-Token': json.loads(payload)['token']}


def multipart(field: str, filename: str, payload: bytes, content_type: str = 'application/octet-stream'):
    boundary = '----astrion-chat-media-' + sha(payload)[:16]
    body = (
        f'--{boundary}\r\n'
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        f'Content-Type: {content_type}\r\n\r\n'
    ).encode() + payload + f'\r\n--{boundary}--\r\n'.encode()
    return body, {'Content-Type': f'multipart/form-data; boundary={boundary}'}


def upload(base: str, auth: dict[str, str], payload: bytes, filename: str,
           *, status: int = 200) -> dict:
    body, headers = multipart('media', filename, payload, 'image/png')
    raw, _ = request(base, 'POST', '/api/chat/media', headers={**auth, **headers}, body=body, status=status)
    return json.loads(raw) if raw else {}


def check_media(base: str, auth: dict[str, str], reference: dict, payload: bytes) -> None:
    raw, headers = request(base, 'GET', '/api/chat/media/' + reference['media_id'], headers=auth)
    assert raw == payload
    assert headers['content-type'].split(';', 1)[0] == reference['mime_type'], headers
    assert headers.get('cache-control') == 'private, no-store', headers
    assert headers.get('x-content-type-options') == 'nosniff', headers
    assert reference['media_id'] in headers.get('content-disposition', '')


def rust_http(base: str, directory: Path, seed: dict, seed_bytes: bytes,
              foreign_id: str) -> dict:
    # Authentication is enforced before media lookup.
    request(base, 'GET', '/api/chat/media/' + seed['media_id'], status=401)
    # Host's shared guard validates CSRF before the route-level session check,
    # so an unauthenticated multipart write is blocked as 403.
    request(
        base,
        'POST',
        '/api/chat/media',
        headers={'Content-Type': 'multipart/form-data; boundary=unauth'},
        body=b'--unauth--\r\n',
        status=403,
    )

    auth = login(base)
    check_media(base, auth, seed, seed_bytes)

    image = png_image((180, 30, 40))
    image_ref = upload(base, auth, image, 'rust.png')['data']
    assert set(image_ref) == {'media_id', 'kind', 'mime_type', 'size', 'sha256'}, image_ref
    assert image_ref['media_id'] == media_id(image) and image_ref['sha256'] == sha(image), image_ref
    assert image_ref['kind'] == 'image' and image_ref['mime_type'] == 'image/png', image_ref
    check_media(base, auth, image_ref, image)
    write(directory / 'rust-image.bin', image)

    again = upload(base, auth, image, 'renamed.jpg')['data']
    assert again == image_ref, (again, image_ref)

    video = mp4_video()
    video_ref = upload(base, auth, video, 'rust.mp4')['data']
    assert video_ref['media_id'] == media_id(video), video_ref
    assert video_ref['kind'] == 'video' and video_ref['mime_type'] == 'video/quicktime', video_ref
    check_media(base, auth, video_ref, video)
    write(directory / 'rust-video.bin', video)

    webm = b'\x1aE\xdf\xa3' + b'\x00' * 8 + b'webm' + b'\x00' * 8
    webm_ref = upload(base, auth, webm, 'rust.webm')['data']
    assert webm_ref['kind'] == 'video' and webm_ref['mime_type'] == 'video/webm', webm_ref
    check_media(base, auth, webm_ref, webm)

    for image_format, mime in [
        ('JPEG', 'image/jpeg'), ('WEBP', 'image/webp'), ('GIF', 'image/gif'), ('BMP', 'image/bmp'),
    ]:
        stream = BytesIO()
        from PIL import Image
        Image.new('RGB', (7, 5), (10, 120, 220)).save(stream, format=image_format)
        accepted = upload(base, auth, stream.getvalue(), 'sniffed.bin')['data']
        assert accepted['kind'] == 'image' and accepted['mime_type'] == mime, accepted
        check_media(base, auth, accepted, stream.getvalue())

    stream = BytesIO()
    from PIL import Image
    Image.new('RGB', (7, 5), (10, 120, 220)).save(stream, format='TIFF')
    body, headers = multipart('media', 'rejected.tiff', stream.getvalue(), 'image/tiff')
    request(base, 'POST', '/api/chat/media', headers={**auth, **headers}, body=body, status=400)

    large_image = b'\x89PNG\r\n\x1a\n' + b'\x00' * (25 * 1024 * 1024)
    body, headers = multipart('media', 'large.png', large_image, 'image/png')
    request(base, 'POST', '/api/chat/media', headers={**auth, **headers}, body=body, status=400)

    body, headers = multipart('media', 'pixels.png', oversized_png_header(), 'image/png')
    request(base, 'POST', '/api/chat/media', headers={**auth, **headers}, body=body, status=400)

    oversized_video = b'\x00' * (100 * 1024 * 1024 + 1)
    body, headers = multipart('media', 'large.mp4', oversized_video, 'video/mp4')
    request(base, 'POST', '/api/chat/media', headers={**auth, **headers}, body=body, status=400)

    # Bad field, forged content and unknown IDs do not write anything or leak paths.
    body, headers = multipart('file', 'wrong.png', seed_bytes, 'image/png')
    request(base, 'POST', '/api/chat/media', headers={**auth, **headers}, body=body, status=400)
    body, headers = multipart('media', 'forged.png', b'fake PDF', 'image/png')
    request(base, 'POST', '/api/chat/media', headers={**auth, **headers}, body=body, status=400)
    for brand in (b'avif', b'heic'):
        unsupported = b'\x00\x00\x00\x18ftyp' + brand + b'\x00\x00\x00\x00' + brand
        body, headers = multipart('media', 'image.heif', unsupported, 'image/heic')
        request(base, 'POST', '/api/chat/media', headers={**auth, **headers}, body=body, status=400)
    unknown = 'sha256:' + 'f' * 64
    raw, _ = request(base, 'GET', '/api/chat/media/' + unknown, headers=auth, status=404)
    assert json.loads(raw)['success'] is False
    # Agent media in the same DATA_DIR is not a Chat account store.
    raw, _ = request(base, 'GET', '/api/chat/media/' + foreign_id, headers=auth, status=404)
    assert json.loads(raw)['success'] is False

    # Missing CSRF on a write is rejected by the normal Host protection.
    body, headers = multipart('media', 'no-token.png', seed_bytes, 'image/png')
    request(base, 'POST', '/api/chat/media',
            headers={'Cookie': auth['Cookie'], **headers}, body=body, status=403)

    return {'image': image_ref, 'video': video_ref}


def run() -> None:
    binary = Path(os.environ.get(
        'ASTRION_HEADLESS_BIN', ROOT / 'backend-rust/target-master/debug/astrion-headless'
    )).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix='chat-media-', dir=HERE) as name:
        root = Path(name).resolve()
        env = fixture_environment(root)
        env['ASTRION_RESOURCE_ROOT'] = str(ROOT)
        env['SEED_FILE'] = str(root / 'python-seed.bin')
        seed = python('seed', env)['seed']
        seed_bytes = (root / 'python-seed.bin').read_bytes()
        chat_index = Path(env['DATA_DIR']) / 'chat/conversations/media_store/index.json'
        index = json.loads(chat_index.read_text())
        index['future_index_field'] = {'keep': [1, 2, 3]}
        index['media'][seed['media_id']]['future_entry_field'] = 'keep'
        chat_index.write_text(json.dumps(index), encoding='utf-8')

        foreign = png_image((90, 20, 200), (6, 6))
        foreign_id = media_id(foreign)
        foreign_root = Path(env['DATA_DIR']) / 'conversations/media_store'
        foreign_blob = f'blobs/{sha(foreign)[:2]}/{sha(foreign)}.png'
        write(foreign_root / foreign_blob, foreign)
        (foreign_root / 'index.json').write_text(json.dumps({
            'media': {foreign_id: {
                'media_id': foreign_id, 'sha256': sha(foreign), 'kind': 'image',
                'mime_type': 'image/png', 'size': len(foreign), 'blob_rel_path': foreign_blob,
            }},
        }), encoding='utf-8')

        with rust(env, binary, 'http://127.0.0.1:9/v1/chat/completions', root / 'rust-media.log', web=True) as base:
            uploaded = rust_http(base, root, seed, seed_bytes, foreign_id)

        # Pure headless (no --web Chat surface) must not expose Chat media.
        with rust(env, binary, 'http://127.0.0.1:9/v1/chat/completions', root / 'rust-headless.log', web=False) as base:
            request(base, 'GET', '/api/chat/media/' + seed['media_id'], status=404)

        env.update({
            'SEED_ID': seed['media_id'],
            'RUST_MEDIA_ID': uploaded['image']['media_id'],
            'RUST_MEDIA_FILE': str(root / 'rust-image.bin'),
            'RUST_VIDEO_ID': uploaded['video']['media_id'],
            'RUST_VIDEO_FILE': str(root / 'rust-video.bin'),
            'PYTHON_C_FILE': str(root / 'python-c.bin'),
            'PYTHON_DRAFT_FILE': str(root / 'python-draft.bin'),
            'PYTHON_HANDOFF_FILE': str(root / 'python-handoff.bin'),
        })
        takeover = python('takeover', env)
        c_ref = takeover['python_c']
        chat = Path(env['DATA_DIR']) / 'chat'
        draft_bytes = (root / 'python-draft.bin').read_bytes()
        handoff_bytes = (root / 'python-handoff.bin').read_bytes()
        env['ORPHAN_ID'] = takeover['orphan']['media_id']
        env['RUST_ORPHAN_ID'] = takeover['rust_orphan']['media_id']
        env['DRAFT_MEDIA_ID'] = takeover['draft']['media_id']
        env['HANDOFF_MEDIA_ID'] = takeover['handoff']['media_id']

        # A restarted Rust headless listener reads both Rust-written and
        # Python-written media from the shared production index.
        with rust(env, binary, 'http://127.0.0.1:9/v1/chat/completions', root / 'rust-restart.log', web=True) as base:
            auth = login(base)
            check_media(base, auth, uploaded['image'], (root / 'rust-image.bin').read_bytes())
            check_media(base, auth, c_ref, (root / 'python-c.bin').read_bytes())
            check_media(base, auth, seed, seed_bytes)
            check_media(base, auth, takeover['draft'], draft_bytes)
            check_media(base, auth, takeover['handoff'], handoff_bytes)
            # Rust post-write GC removes the expired orphan but keeps media that is
            # referenced only by an inactive branch, a draft, or a pending handoff snapshot.
            request(base, 'GET', '/api/chat/media/' + env['RUST_ORPHAN_ID'], headers=auth)
            upload(base, auth, png_image((4, 5, 6), (3, 3)), 'gc-trigger.png')
            request(base, 'GET', '/api/chat/media/' + env['RUST_ORPHAN_ID'], headers=auth, status=404)
            check_media(base, auth, uploaded['image'], (root / 'rust-image.bin').read_bytes())
            check_media(base, auth, c_ref, (root / 'python-c.bin').read_bytes())
            check_media(base, auth, takeover['draft'], draft_bytes)
            check_media(base, auth, takeover['handoff'], handoff_bytes)
            index = json.loads((chat / 'conversations/media_store/index.json').read_text())
            assert index['future_index_field'] == {'keep': [1, 2, 3]}, index.keys()
            assert index['media'][env['SEED_ID']]['future_entry_field'] == 'keep'
            for identity in (uploaded['image']['media_id'], c_ref['media_id'],
                             env['DRAFT_MEDIA_ID'], env['HANDOFF_MEDIA_ID']):
                assert index['media'][identity]['future_kept_field'] == 'keep', index['media'][identity]
            raw, _ = request(base, 'GET', '/api/chat/media/' + env['ORPHAN_ID'], headers=auth, status=404)
            assert json.loads(raw)['success'] is False

        print('Chat media Python -> Rust -> Python -> Rust restart passed.')


if __name__ == '__main__':
    if len(sys.argv) > 1:
        result = python_phase(sys.argv[1])
        print('RESULT=' + json.dumps(result, ensure_ascii=False), flush=True)
    else:
        run()
