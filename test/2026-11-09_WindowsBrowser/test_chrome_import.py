"""Synthetic SQLite/AES-GCM fixtures; never open an actual Chrome profile."""
import base64
from contextlib import closing
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

WORKER = Path(__file__).resolve().parents[2] / 'desktop/src-tauri/src/browser/chrome_worker.py'
spec = importlib.util.spec_from_file_location('chrome_worker', WORKER)
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profile = self.root / 'Default'
        self.profile.mkdir()
        self.key = b'k' * 32
        (self.root / 'Local State').write_text(json.dumps({'os_crypt': {'encrypted_key': base64.b64encode(b'DPAPIsynthetic').decode()}}))

    def encrypt(self, text):
        nonce = b'n' * 12
        return b'v10' + nonce + AESGCM(self.key).encrypt(nonce, text, None)

    def test_aes_gcm_and_application_bound_records(self):
        self.assertEqual(worker.decrypt(self.encrypt(b'synthetic'), self.key), b'synthetic')
        with self.assertRaisesRegex(ValueError, 'unsupported_encryption'):
            worker.decrypt(b'v20application-bound', self.key)
        damaged = bytearray(self.encrypt(b'synthetic'))
        damaged[-1] ^= 1
        with self.assertRaises(Exception):
            worker.decrypt(bytes(damaged), self.key)

    def test_complete_preview_counts_and_no_source_writes(self):
        with closing(sqlite3.connect(self.profile / 'Login Data')) as db, db:
            db.execute('CREATE TABLE logins(origin_url TEXT, username_value TEXT, password_value BLOB)')
            db.executemany('INSERT INTO logins VALUES(?,?,?)', [
                ('https://example.test/login', 'synthetic-user', self.encrypt(b'synthetic-password')),
                ('https://example.test/', 'unsupported', b'v20unsupported'),
                ('file:///private', 'invalid', self.encrypt(b'synthetic')),
            ])
        (self.profile / 'Network').mkdir()
        with closing(sqlite3.connect(self.profile / 'Network/Cookies')) as db, db:
            db.execute('CREATE TABLE meta(key TEXT,value TEXT)')
            db.execute("INSERT INTO meta VALUES('version','24')")
            db.execute('CREATE TABLE cookies(host_key TEXT,name TEXT,path TEXT,encrypted_value BLOB,has_expires INT,is_secure INT,top_frame_site_key TEXT)')
            digest = hashlib.sha256(b'example.test').digest()
            db.executemany('INSERT INTO cookies VALUES(?,?,?,?,?,?,?)', [
                ('example.test', 'session', '/', self.encrypt(digest + b'synthetic-cookie'), 0, 1, ''),
                ('example.test', 'partition', '/', self.encrypt(digest + b'x'), 0, 1, 'https://site.test'),
                ('example.test', 'bad-host-hash', '/', self.encrypt(b'0' * 32 + b'x'), 0, 1, ''),
            ])
        with closing(sqlite3.connect(self.profile / 'History')) as db, db:
            db.execute('CREATE TABLE urls(url TEXT,title TEXT,last_visit_time INTEGER)')
            db.executemany('INSERT INTO urls VALUES(?,?,?)', [
                ('https://example.test/', 'Synthetic', 13300000000000000),
                ('file:///private', 'Skipped', 13300000000000000),
            ])
        before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*') if p.is_file()}
        result = worker.preview(self.root, {'source': 'Default', 'selected': dict.fromkeys(worker.KINDS, True)}, lambda _: self.key)
        self.assertEqual(result['stats']['passwords']['ready'], 1)
        self.assertEqual(result['stats']['passwords']['failed'], 2)
        self.assertEqual(result['stats']['passwords']['reasons']['unsupported_encryption'], 1)
        self.assertEqual(result['cookies'][0]['value'], 'synthetic-cookie')
        self.assertEqual(result['stats']['cookies']['ready'], 1)
        self.assertEqual(result['stats']['cookies']['failed'], 2)
        self.assertEqual(result['stats']['history']['ready'], 1)
        self.assertEqual(result['stats']['history']['skipped'], 1)
        after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before, after, 'source databases must remain byte-for-byte unchanged')

    def test_source_traversal_and_empty_selection_rejected(self):
        with self.assertRaisesRegex(ValueError, 'invalid_source'):
            worker.preview(self.root, {'source': '../Default', 'selected': dict.fromkeys(worker.KINDS, True)})
        with self.assertRaisesRegex(ValueError, 'invalid_import_selection'):
            worker.preview(self.root, {'source': 'Default', 'selected': dict.fromkeys(worker.KINDS, False)})

    def test_origins_reject_credentials_and_non_web_schemes(self):
        self.assertEqual(worker.origin('https://EXAMPLE.test:443/login'), 'https://example.test')
        for url in ['https://name:secret@example.test/', 'file:///private', 'javascript:alert(1)']:
            with self.assertRaises(ValueError):
                worker.origin(url)


if __name__ == '__main__':
    unittest.main()
