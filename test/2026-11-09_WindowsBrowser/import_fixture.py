"""Create a synthetic Chrome tree under the explicitly supplied experiment root."""
from contextlib import closing
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import sqlite3


def protect(value: bytes) -> bytes:
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    buffer = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
    source, target = Blob(len(value), buffer), Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    crypt.CryptProtectData.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    crypt.CryptProtectData.restype = wintypes.BOOL
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not crypt.CryptProtectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise RuntimeError('synthetic DPAPI fixture failed')
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        ctypes.memset(target.data, 0, target.size)
        kernel.LocalFree(target.data)


def create(root: Path) -> Path:
    local = root / 'fixture-local-appdata'
    chrome = local / 'Google/Chrome/User Data'
    profile = chrome / 'Default'
    (profile / 'Network').mkdir(parents=True)
    (chrome / 'Local State').write_text(json.dumps({'profile': {'info_cache': {'Default': {'name': 'Synthetic Chrome profile'}}}}), encoding='utf-8')
    with closing(sqlite3.connect(profile / 'Login Data')) as db, db:
        db.execute('CREATE TABLE logins(origin_url TEXT,username_value TEXT,password_value BLOB)')
        db.executemany('INSERT INTO logins VALUES(?,?,?)', [
            ('https://import.example.test/login', 'imported-user', protect(b'imported-password-a')),
            ('https://import.example.test/other', 'imported-user', protect(b'imported-password-b')),
            ('https://import.example.test/', 'unsupported', b'v20unsupported-synthetic')])
    with closing(sqlite3.connect(profile / 'Network/Cookies')) as db, db:
        db.execute('CREATE TABLE meta(key TEXT,value TEXT)')
        db.execute("INSERT INTO meta VALUES('version','24')")
        db.execute('CREATE TABLE cookies(host_key TEXT,name TEXT,path TEXT,value TEXT,encrypted_value BLOB,has_expires INT,is_secure INT)')
        db.execute('INSERT INTO cookies VALUES(?,?,?,?,?,?,?)', ('.import.example.test','synthetic-import','/','synthetic-cookie',b'',0,1))
    with closing(sqlite3.connect(profile / 'History')) as db, db:
        db.execute('CREATE TABLE urls(url TEXT,title TEXT,last_visit_time INT)')
        db.execute('INSERT INTO urls VALUES(?,?,?)', ('https://import.example.test/','Synthetic imported history',13300000000000000))
    return local
