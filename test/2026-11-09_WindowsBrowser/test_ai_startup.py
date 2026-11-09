"""Windows private credential transport and platform capability checks; synthetic token only."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


class Startup(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows stdin handle regression')
    def test_pipe_is_consumed_and_subprocess_stdin_remains_valid(self):
        source = '''
import ctypes, importlib.util, json, os, subprocess, sys
from ctypes import wintypes
spec=importlib.util.spec_from_file_location('credential_fixture',sys.argv[1])
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
assert module.desktop_browser_token() == 'synthetic-browser-token-'*3
assert 'ASTRION_DESKTOP_BROWSER_TOKEN_FD' not in os.environ
assert 'ASTRION_DESKTOP_BROWSER_TOKEN' not in os.environ
kernel=ctypes.WinDLL('kernel32');kernel.GetStdHandle.argtypes=[wintypes.DWORD];kernel.GetStdHandle.restype=wintypes.HANDLE
assert kernel.GetStdHandle(-10 & 0xffffffff) not in (None,ctypes.c_void_p(-1).value)
child="import os,sys; assert sys.stdin.buffer.read()==b''; assert not os.environ.get('ASTRION_DESKTOP_BROWSER_TOKEN_FD'); print('valid-empty-stdin')"
for _ in range(3):
    value=subprocess.run([sys.executable,'-c',child],capture_output=True,text=True,timeout=10,check=True)
    assert value.stdout.strip()=='valid-empty-stdin'
print(json.dumps({'pipe_consumed':True,'subprocess_stdin_valid':True,'token_env_removed':True}))
'''
        env = os.environ.copy()
        env['ASTRION_DESKTOP_BROWSER_TOKEN_FD'] = '0'
        env['ASTRION_DESKTOP_BROWSER_TOKEN'] = 'must-be-removed'
        result = subprocess.run([sys.executable, '-c', source, str(ROOT / 'config/desktop_browser.py')], input='synthetic-browser-token-' * 3 + '\n', env=env, capture_output=True, text=True, timeout=35, check=True)
        self.assertTrue(all(json.loads(result.stdout).values()))
        self.assertNotIn('synthetic-browser-token', result.stdout)

    def test_windows_requires_desktop_and_private_token(self):
        spec = importlib.util.spec_from_file_location('availability_fixture', ROOT / 'modules/native_browser/availability.py')
        module = importlib.util.module_from_spec(spec)
        config = types.ModuleType('config')
        config.IS_HOST_MODE = True
        credentials = types.ModuleType('config.desktop_browser')
        credentials.desktop_browser_token = lambda: 'synthetic-token'
        env = {'ASTRION_DESKTOP_VERSION': 'test', 'ASTRION_DESKTOP_BROWSER_PORT': '45678'}
        with patch.dict(sys.modules, {'config': config, 'config.desktop_browser': credentials}), patch.dict(os.environ, env, clear=True), patch.object(sys, 'platform', 'win32'):
            spec.loader.exec_module(module)
            self.assertTrue(module.native_browser_supported())
            module.desktop_browser_token = lambda: ''
            self.assertFalse(module.native_browser_supported())
            module.desktop_browser_token = lambda: 'synthetic-token'
            os.environ['ASTRION_DESKTOP_BROWSER_PORT'] = '99999'
            self.assertFalse(module.native_browser_supported())

    def test_macos_keeps_existing_bridge_port(self):
        spec = importlib.util.spec_from_file_location('availability_fixture', ROOT / 'modules/native_browser/availability.py')
        module = importlib.util.module_from_spec(spec)
        config = types.ModuleType('config')
        config.IS_HOST_MODE = True
        credentials = types.ModuleType('config.desktop_browser')
        credentials.desktop_browser_token = lambda: 'synthetic-token'
        with patch.dict(sys.modules, {'config': config, 'config.desktop_browser': credentials}), patch.dict(os.environ, {'ASTRION_DESKTOP_VERSION': 'test', 'ASTRION_DESKTOP_BRIDGE_PORT': '45678'}, clear=True), patch.object(sys, 'platform', 'darwin'):
            spec.loader.exec_module(module)
            self.assertTrue(module.native_browser_supported())
            self.assertEqual(module.bridge_port(), 45678)


if __name__ == '__main__':
    unittest.main(verbosity=2)
