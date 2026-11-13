"""Create only synthetic Chrome records under the explicit test output directory."""
import base64
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def protect(value: bytes) -> bytes:
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    buffer = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
    source, target = Blob(len(value), buffer), Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    crypt.CryptProtectData.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                                     ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    crypt.CryptProtectData.restype = wintypes.BOOL
    if not crypt.CryptProtectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise RuntimeError('synthetic fixture encryption failed')
    kernel = ctypes.WinDLL('kernel32')
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        ctypes.memset(target.data, 0, target.size)
        kernel.LocalFree(target.data)


def main() -> None:
    destination = Path(sys.argv[1]).resolve()
    root = destination / 'localapp/Google/Chrome/User Data'
    profile = root / 'Default'
    profile.mkdir(parents=True, exist_ok=False)
    key = os.urandom(32)
    (root / 'Local State').write_text(json.dumps({
        'profile': {'info_cache': {'Default': {'name': 'Synthetic import source'}}},
        'os_crypt': {'encrypted_key': base64.b64encode(b'DPAPI' + protect(key)).decode()},
    }), encoding='utf-8')

    def encrypt(value: str, domain: str = '') -> bytes:
        plain = value.encode()
        if domain:
            plain = hashlib.sha256(domain.encode()).digest() + plain
        nonce = os.urandom(12)
        return b'v10' + nonce + AESGCM(key).encrypt(nonce, plain, None)

    modified = int((time.time() + 11644473600) * 1000000)
    with sqlite3.connect(profile / 'Login Data') as db:
        db.execute('CREATE TABLE logins(origin_url TEXT, username_value TEXT, password_value BLOB, date_created INTEGER)')
        db.executemany('INSERT INTO logins VALUES(?,?,?,?)', [
            ('https://db.example.test/login', 'db-user', encrypt('synthetic-db-password'), modified),
            ('https://legacy.example.test/login', 'legacy-user', protect(b'synthetic-legacy-password'), modified),
            ('https://v20.example.test/', 'v20-user', b'v20unsupported-test-record', modified),
        ])
    (profile / 'Network').mkdir()
    with sqlite3.connect(profile / 'Network/Cookies') as db:
        db.executescript("CREATE TABLE meta(key TEXT,value TEXT); INSERT INTO meta VALUES('version','24');"
                         'CREATE TABLE cookies(host_key TEXT,name TEXT,path TEXT,value TEXT,encrypted_value BLOB,'
                         'is_secure INTEGER,is_httponly INTEGER,expires_utc INTEGER,has_expires INTEGER,'
                         'samesite INTEGER,top_frame_site_key TEXT,has_cross_site_ancestor INTEGER)')
        for name, site, ancestor in [('db-plain', '', 0), ('db-chips-true', 'https://top-a.test', 1),
                                      ('db-chips-false', 'https://top-b.test', 0)]:
            db.execute('INSERT INTO cookies VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                       ('cookie.example.test', name, '/', '', encrypt('synthetic-' + name, 'cookie.example.test'),
                        1, 1, modified + 86400 * 1000000, 1, 0, site, ancestor))
        db.execute('INSERT INTO cookies VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                   ('cookie.example.test', 'unsupported', '/', '', b'v20unsupported-cookie', 1, 1, 0, 0, 0, '', 0))
    with sqlite3.connect(profile / 'History') as db:
        db.execute('CREATE TABLE urls(url TEXT,title TEXT,last_visit_time INTEGER)')
        db.executemany('INSERT INTO urls VALUES(?,?,?)', [('https://history.example.test/', 'Synthetic', modified),
                                                       ('file:///synthetic-only', 'Not imported', modified)])
    files = [file for file in root.rglob('*') if file.is_file()]
    (destination / 'source-hashes.json').write_text(json.dumps({
        str(file.relative_to(root)): hashlib.sha256(file.read_bytes()).hexdigest() for file in files
    }), encoding='utf-8')
    print(json.dumps({'synthetic_fixture_ready': True, 'files': len(files)}))


if __name__ == '__main__':
    main()
