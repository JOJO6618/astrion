"""Export parsing regression: synthetic data only; never opens a real profile."""
import copy
import csv
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('chrome_worker', ROOT / 'desktop/src-tauri/src/browser/chrome_worker.py')
worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worker)


class ExportTests(unittest.TestCase):
    def cookie(self, **changes):
        row = dict(domain='example.test', name='synthetic', value='fake-value', path='/', secure=True,
                   httpOnly=True, hostOnly=True, session=False, sameSite='no_restriction', expirationDate=4102444800)
        row.update(changes)
        return row

    def cookies(self, rows):
        return worker.file_preview(dict(format='cookie-json', content=json.dumps(dict(format='astrion-cookie-export', version=1, cookies=rows))))

    def csv(self, rows, header=('name', 'url', 'username', 'password', 'note')):
        out = io.StringIO(newline='')
        writer = csv.writer(out)
        writer.writerow(header)
        writer.writerows(rows)
        return worker.file_preview(dict(format='password-csv', content='\ufeff' + out.getvalue()))

    def test_chrome_csv_quoted_multiline_unicode(self):
        value = '合成,"口令"\r\n第二行'
        result = self.csv([['名称', 'https://EXAMPLE.test:443/login', 'user,中文', value, 'note']])
        self.assertEqual(result['passwords'][0]['password'], value)
        self.assertEqual(result['passwords'][0]['username'], 'user,中文')
        self.assertEqual(result['passwords'][0]['origin'], 'https://example.test')
        self.assertEqual(result['stats']['passwords']['ready'], 1)
        self.assertEqual(result['stats']['cookies']['ready'], 0)

    def test_csv_row_failures_and_empty_password(self):
        result = self.csv([
            ['n', 'https://a.test/', 'u', '', ''],
            ['n', 'file:///private', 'u', 'synthetic', ''],
            ['n', 'https://name:secret@a.test/', 'u', 'synthetic', ''],
            ['n', 'https://a.test/', 'u', 'synthetic', '', 'extra'],
        ])
        stat = result['stats']['passwords']
        self.assertEqual((stat['read'], stat['ready'], stat['skipped'], stat['failed']), (4, 0, 1, 3))

    def test_csv_invalid_header_and_truncated_quote(self):
        for content in ['url,username,secret\na,b,c', 'url,url,username,password', 'url,username,password\na,b,"unfinished']:
            with self.subTest(content=content), self.assertRaisesRegex(ValueError, 'invalid_import_file'):
                worker.file_preview(dict(format='password-csv', content=content))

    def test_limits_and_nul(self):
        for content, reason in [('x' * (4 * 1024 * 1024 + 1), 'import_file_too_large'), ('a\x00b', 'invalid_import_file')]:
            with self.assertRaisesRegex(ValueError, reason):
                worker.file_preview(dict(format='password-csv', content=content))
        with self.assertRaisesRegex(ValueError, 'import_file_too_many_records'):
            self.csv([['n', 'https://a.test', 'u', 'p', '']] * 10001)

    def test_partition_keys_host_scope_and_session(self):
        partition = dict(topLevelSite='https://top.test', hasCrossSiteAncestor=True)
        result = self.cookies([self.cookie(partitionKey=partition), self.cookie(domain='example.test', hostOnly=False, session=True)])
        self.assertEqual(result['cookies'][0]['partitionKey'], partition)
        self.assertEqual(result['cookies'][0]['domain'], 'example.test')
        self.assertEqual(result['cookies'][1]['domain'], '.example.test')
        self.assertEqual(result['cookies'][1]['expires'], -1)
        self.assertEqual(result['stats']['cookies']['ready'], 2)

    def test_cookie_validation_does_not_weaken_partition(self):
        changes = [dict(secure=False), dict(partitionKey=dict(topLevelSite='https://top.test')),
                   dict(partitionKey=dict(topLevelSite='file:///private', hasCrossSiteAncestor=False)),
                   dict(partitionKey=dict(topLevelSite='https://top.test/path', hasCrossSiteAncestor=False)),
                   dict(hostOnly='true'), dict(domain='name:secret@example.test'), dict(domain='..example.test'),
                   dict(path='relative'), dict(value='bad\x00value'), dict(expirationDate=float('nan')),
                   dict(domain='.example.test', hostOnly=True)]
        result = self.cookies([self.cookie(**change) for change in changes])
        self.assertEqual(result['stats']['cookies']['failed'], len(changes))
        self.assertEqual(result['cookies'], [])

    def test_expired_cookie_skipped(self):
        result = self.cookies([self.cookie(expirationDate=1)])
        self.assertEqual(result['stats']['cookies']['reasons'], {'expired': 1})

    def test_json_requires_versioned_bundle(self):
        for value in [[], {}, dict(format='astrion-cookie-export', version=True, cookies=[]), dict(format='astrion-cookie-export', version=2, cookies=[])]:
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'invalid_import_file'):
                worker.file_preview(dict(format='cookie-json', content=json.dumps(value)))

    def test_history_reasons_from_synthetic_sqlite(self):
        import sqlite3
        from contextlib import closing
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'Default').mkdir()
            with closing(sqlite3.connect(root / 'Default/History')) as db, db:
                db.execute('CREATE TABLE urls(url TEXT,title TEXT,last_visit_time INTEGER)')
                db.executemany('INSERT INTO urls VALUES(?,?,?)', [
                    ('chrome://newtab', 'internal', 13300000000000000),
                    ('file:///private', 'file', 13300000000000000),
                    ('https://', 'invalid', 13300000000000000),
                    ('https://example.test', 'no time', 0),
                    ('https://example.test', 'valid', 13300000000000000),
                ])
            result = worker.preview(root, dict(source='Default', selected=dict(passwords=False, cookies=False, history=True)))
            self.assertEqual(result['stats']['history']['reasons'], dict(history_non_web_url=2, history_invalid_url=1, history_invalid_time=1))
            self.assertEqual(result['stats']['history']['ready'], 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
