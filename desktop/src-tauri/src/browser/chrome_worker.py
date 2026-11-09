"""Read only user-selected Chrome databases. Secrets travel in a private pipe only."""
from __future__ import annotations

import base64
import csv
import io
import math
import ctypes
from ctypes import wintypes
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
import uuid

KINDS = ('passwords', 'cookies', 'history')
MAX_ROWS = 100000


def origin(value: str) -> str:
    from urllib.parse import urlsplit
    parsed = urlsplit(value)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('invalid_record')
    host = parsed.hostname.encode('idna').decode('ascii').lower()
    if ':' in host:
        host = '[' + host + ']'
    port = parsed.port
    return f'{parsed.scheme}://{host}' + (f':{port}' if port and port != {'http': 80, 'https': 443}[parsed.scheme] else '')


def chrome_time(value: object) -> int:
    return max(0, int(value or 0) // 1000 - 11644473600000)


def unprotect(value: bytes) -> bytes:
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    buffer = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
    source, target = Blob(len(value), buffer), Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    crypt.CryptUnprotectData.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                                        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    crypt.CryptUnprotectData.restype = wintypes.BOOL
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not crypt.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ValueError('windows_decryption_failed')
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        ctypes.memset(target.data, 0, target.size)
        kernel.LocalFree(target.data)


def decrypt(value: bytes, key: bytes | None, decrypt_dpapi=unprotect) -> bytes:
    if value.startswith(b'v20'):
        raise ValueError('unsupported_encryption')
    if value[:3] in (b'v10', b'v11'):
        if key is None:
            raise ValueError('windows_decryption_failed')
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        return AESGCM(key).decrypt(value[3:15], value[15:], None)
    return decrypt_dpapi(value)


def regular(path: Path) -> None:
    # Reject symlink/junction inputs before asking SQLite or DPAPI to read them.
    for entry in [path, *path.parents]:
        if entry.is_symlink() or (hasattr(entry, 'is_junction') and entry.is_junction()):
            raise ValueError('invalid_source')
    if not path.is_file() or path.stat().st_size > 1024 ** 3:
        raise ValueError('database_unavailable')


def root_directory() -> Path:
    return Path(os.environ['LOCALAPPDATA']) / 'Google/Chrome/User Data'


def sources(root: Path) -> list[dict]:
    if not root.is_dir():
        return []
    names = {}
    local_state = root / 'Local State'
    if local_state.exists():
        regular(local_state)
        names = json.loads(local_state.read_text(encoding='utf-8')).get('profile', {}).get('info_cache', {})
    result = []
    for directory in sorted(root.iterdir()):
        if re.fullmatch(r'Default|Profile \d+', directory.name) and directory.is_dir() and not directory.is_symlink() and not directory.is_junction():
            result.append({'id': directory.name, 'name': str(names.get(directory.name, {}).get('name') or directory.name)[:200]})
    return result


def read_table(file: Path, table: str) -> list[dict]:
    regular(file)
    try:
        with closing(sqlite3.connect(file.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)) as connection:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute(f'SELECT * FROM {table} LIMIT {MAX_ROWS}')]
    except sqlite3.OperationalError as error:
        raise ValueError('database_busy' if 'locked' in str(error).lower() else 'database_unavailable') from None


def issue(stats: dict, reason: str, failed: bool = True) -> None:
    key = 'failed' if failed else 'skipped'
    stats[key] += 1
    stats['reasons'][reason] = stats['reasons'].get(reason, 0) + 1


def preview(root: Path, request: dict, dpapi=unprotect) -> dict:
    source = request.get('source')
    if source not in {item['id'] for item in sources(root)}:
        raise ValueError('invalid_source')
    selected = request.get('selected', {})
    if not all(type(selected.get(kind)) is bool for kind in KINDS) or not any(selected.values()):
        raise ValueError('invalid_import_selection')
    directory = root / source
    stats = {kind: {'read': 0, 'ready': 0, 'skipped': 0, 'failed': 0, 'conflicts': 0, 'reasons': {}} for kind in KINDS}
    raw = {kind: [] for kind in KINDS}
    files = {'passwords': [('Login Data', 'logins'), ('Login Data For Account', 'logins')],
             'cookies': [('Network/Cookies', 'cookies')] if (directory / 'Network/Cookies').exists() else [('Cookies', 'cookies')],
             'history': [('History', 'urls')]}
    cookie_version = 0
    for kind in KINDS:
        if not selected[kind]:
            continue
        existing = [(directory / name, table) for name, table in files[kind] if (directory / name).exists()]
        if not existing:
            issue(stats[kind], 'missing_database', False)
        for file, table in existing:
            try:
                raw[kind].extend(read_table(file, table))
                if kind == 'cookies':
                    meta = read_table(file, 'meta')
                    cookie_version = int(next(row['value'] for row in meta if row['key'] == 'version'))
            except (ValueError, OSError, KeyError, StopIteration) as error:
                issue(stats[kind], str(error) if str(error) in ('database_busy', 'invalid_source') else 'database_unavailable')
        stats[kind]['read'] = len(raw[kind])
    key = None
    encrypted = [r.get('password_value', b'') for r in raw['passwords']] + [r.get('encrypted_value', b'') for r in raw['cookies']]
    if any(bytes(v).startswith((b'v10', b'v11')) for v in encrypted):
        try:
            state = root / 'Local State'
            regular(state)
            wrapped = base64.b64decode(json.loads(state.read_text(encoding='utf-8'))['os_crypt']['encrypted_key'])
            if not wrapped.startswith(b'DPAPI'):
                raise ValueError('unsupported_encryption')
            key = dpapi(wrapped[5:])
        except (ValueError, OSError, KeyError):
            key = None
    passwords, cookies, history = [], [], []
    for row in raw['passwords']:
        try:
            if row.get('blacklisted_by_user') or row.get('federation_url') or not row.get('password_value'):
                issue(stats['passwords'], 'not_password', False)
                continue
            normalized = origin(row['origin_url'])
            password = decrypt(bytes(row['password_value']), key, dpapi).decode('utf-8')
            username = str(row.get('username_value') or '')
            if not password or len(password) > 16384 or len(username) > 4096:
                raise ValueError('invalid_record')
            passwords.append({'id': str(uuid.uuid4()), 'origin': normalized, 'username': username, 'password': password,
                              'sourceURL': row['origin_url'], 'modified': chrome_time(row.get('date_password_modified', row.get('date_created', 0)))})
            stats['passwords']['ready'] += 1
        except Exception as error:
            issue(stats['passwords'], str(error) if str(error) in ('unsupported_encryption', 'windows_decryption_failed') else 'invalid_record')
    for row in raw['cookies']:
        try:
            if row.get('top_frame_site_key') or row.get('is_partitioned'):
                raise ValueError('unsupported_partition')
            domain, name, path = row['host_key'], row['name'], row['path']
            origin(('https://' if row.get('is_secure') else 'http://') + domain.lstrip('.'))
            encrypted = bytes(row.get('encrypted_value') or b'')
            plain = decrypt(encrypted, key, dpapi) if encrypted else str(row.get('value') or '').encode('utf-8')
            if encrypted and cookie_version >= 24:
                digest = hashlib.sha256(domain.encode('utf-8')).digest()
                if plain[:32] != digest:
                    raise ValueError('invalid_record')
                plain = plain[32:]
            expires = chrome_time(row.get('expires_utc')) / 1000 if row.get('has_expires', True) else -1
            if expires >= 0 and expires <= time.time():
                issue(stats['cookies'], 'expired', False)
                continue
            cookie = {'domain': domain, 'name': name, 'path': path, 'value': plain.decode('utf-8'), 'secure': bool(row.get('is_secure')),
                      'httpOnly': bool(row.get('is_httponly')), 'expires': expires, 'sameSite': int(row.get('samesite', -1))}
            if any('\x00' in str(cookie[k]) for k in ('domain', 'name', 'path', 'value')):
                raise ValueError('invalid_record')
            cookies.append(cookie)
            stats['cookies']['ready'] += 1
        except Exception as error:
            issue(stats['cookies'], str(error) if str(error) in ('unsupported_encryption', 'windows_decryption_failed', 'unsupported_partition') else 'invalid_record')
    for row in raw['history']:
        try:
            url = row.get('url')
            if not isinstance(url, str):
                raise ValueError('history_invalid_url')
            from urllib.parse import urlsplit
            if urlsplit(url).scheme not in ('http', 'https'):
                raise ValueError('history_non_web_url')
            try:
                origin(url)
            except ValueError:
                raise ValueError('history_invalid_url') from None
            try:
                at = chrome_time(row.get('last_visit_time'))
                if not at:
                    raise ValueError()
            except (ValueError, TypeError, OverflowError):
                raise ValueError('history_invalid_time') from None
            history.append({'id': str(uuid.uuid4()), 'url': url, 'title': str(row.get('title') or url), 'at': at})
            stats['history']['ready'] += 1
        except Exception as error:
            reason = str(error)
            issue(stats['history'], reason if reason in ('history_non_web_url', 'history_invalid_url', 'history_invalid_time') else 'invalid_record', False)
    return {'stats': stats, 'passwords': passwords, 'cookies': cookies, 'history': history}


def file_preview(request: dict) -> dict:
    """Parse user-selected exports only; never resolve a renderer-supplied file path."""
    content = request.get('content')
    if not isinstance(content, str) or len(content.encode('utf-8')) > 4 * 1024 * 1024:
        raise ValueError('import_file_too_large')
    if '\x00' in content:
        raise ValueError('invalid_import_file')
    content = content.removeprefix('\ufeff')
    result = {kind: [] for kind in KINDS}
    result['stats'] = {kind: {'read': 0, 'ready': 0, 'skipped': 0, 'failed': 0, 'conflicts': 0, 'reasons': {}} for kind in KINDS}
    kind = request.get('format')
    if kind == 'password-csv':
        csv.field_size_limit(65536)
        try:
            rows = csv.reader(io.StringIO(content, newline=''), strict=True)
            header = next(rows)
            if len(set(header)) != len(header) or not {'url', 'username', 'password'}.issubset(header):
                raise ValueError('invalid_import_file')
            if len(header) > 20:
                raise ValueError('invalid_import_file')
            for values in rows:
                if not values:
                    continue
                stats = result['stats']['passwords']
                stats['read'] += 1
                if stats['read'] > 10000:
                    raise ValueError('import_file_too_many_records')
                try:
                    if len(values) != len(header):
                        raise ValueError('invalid_record')
                    row = dict(zip(header, values))
                    normalized = origin(row['url'])
                    if not row['password']:
                        issue(stats, 'not_password', False)
                        continue
                    if len(row['password']) > 16384 or len(row['username']) > 4096 or len(row['url']) > 8192:
                        raise ValueError('invalid_record')
                    result['passwords'].append({'id': str(uuid.uuid4()), 'origin': normalized, 'username': row['username'],
                                                'password': row['password'], 'sourceURL': row['url'], 'modified': 0})
                    stats['ready'] += 1
                except (ValueError, UnicodeError):
                    issue(stats, 'invalid_record')
        except (csv.Error, StopIteration):
            raise ValueError('invalid_import_file') from None
    elif kind == 'cookie-json':
        try:
            bundle = json.loads(content)
        except (ValueError, RecursionError):
            raise ValueError('invalid_import_file') from None
        if not isinstance(bundle, dict) or bundle.get('format') != 'astrion-cookie-export' or type(bundle.get('version')) is not int or bundle['version'] != 1 or not isinstance(bundle.get('cookies'), list):
            raise ValueError('invalid_import_file')
        if len(bundle['cookies']) > 10000:
            raise ValueError('import_file_too_many_records')
        for row in bundle['cookies']:
            stats = result['stats']['cookies']
            stats['read'] += 1
            try:
                cookie = exported_cookie(row)
                if cookie['expires'] != -1 and cookie['expires'] <= time.time():
                    issue(stats, 'expired', False)
                    continue
                result['cookies'].append(cookie)
                stats['ready'] += 1
            except (ValueError, TypeError, KeyError, UnicodeError):
                issue(stats, 'invalid_record')
    else:
        raise ValueError('invalid_import_file')
    return result


def exported_cookie(row: dict) -> dict:
    if not isinstance(row, dict):
        raise ValueError('invalid_record')
    for key, limit in (('domain', 253), ('name', 4096), ('value', 16384), ('path', 4096)):
        value = row.get(key)
        if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError('invalid_record')
    domain, path = row['domain'], row['path']
    if not domain or domain.startswith('..') or any(c in domain for c in '/:@?#\\') or not path.startswith('/'):
        raise ValueError('invalid_record')
    normalized = origin('https://' + domain.lstrip('.'))
    if normalized != 'https://' + domain.lstrip('.').lower():
        raise ValueError('invalid_record')
    for key in ('secure', 'httpOnly', 'hostOnly', 'session'):
        if type(row.get(key)) is not bool:
            raise ValueError('invalid_record')
    if row['hostOnly'] and domain.startswith('.'):
        raise ValueError('invalid_record')
    if not row['hostOnly'] and not domain.startswith('.'):
        domain = '.' + domain
    same_site = {'unspecified': -1, 'no_restriction': 0, 'lax': 1, 'strict': 2}.get(row.get('sameSite'))
    if same_site is None or (same_site == 0 and not row['secure']):
        raise ValueError('invalid_record')
    expires = -1 if row['session'] else row.get('expirationDate')
    if type(expires) not in (float, int) or not math.isfinite(expires) or (not row['session'] and expires <= 0):
        raise ValueError('invalid_record')
    cookie = {key: row[key] for key in ('name', 'value', 'path', 'secure', 'httpOnly')}
    cookie.update(domain=domain, expires=expires, sameSite=same_site)
    partition = row.get('partitionKey')
    if partition is not None:
        if not isinstance(partition, dict) or not row['secure'] or type(partition.get('hasCrossSiteAncestor')) is not bool:
            raise ValueError('invalid_record')
        site = partition.get('topLevelSite')
        if not isinstance(site, str) or origin(site) != site.rstrip('/'):
            raise ValueError('invalid_record')
        cookie['partitionKey'] = {'topLevelSite': site.rstrip('/'), 'hasCrossSiteAncestor': partition['hasCrossSiteAncestor']}
    return cookie


def main() -> None:
    try:
        raw = sys.stdin.buffer.read(32 * 1024 * 1024 + 1)
        if len(raw) > 32 * 1024 * 1024:
            raise ValueError('import_file_too_large')
        request = json.loads(raw.decode('utf-8'))
        if request['action'] == 'preview-file':
            result = file_preview(request['value'])
        else:
            root = root_directory()
            result = {'sources': sources(root)} if request['action'] == 'sources' else preview(root, request['value'])
        response = {'ok': True, 'value': result}
    except Exception as error:
        reason = str(error)
        response = {'ok': False, 'error': reason if reason in ('invalid_source', 'invalid_import_selection', 'invalid_import_file', 'import_file_too_large', 'import_file_too_many_records') else 'chrome_read_failed'}
    sys.stdout.buffer.write(json.dumps(response, ensure_ascii=False).encode('utf-8'))


if __name__ == '__main__':
    main()
