from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from werkzeug.security import check_password_hash, generate_password_hash
from modules.host_auth_hash import HASH_METHOD, check_host_hash, generate_host_hash
from modules.host_auth import load_host_auth, verify_host_password

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "Synthetic 跨解释器 123!"


class ScryptCompatibilityTests(unittest.TestCase):
    @unittest.skipUnless(hasattr(hashlib, 'scrypt'), 'Werkzeug reference needs native scrypt')
    def test_werkzeug_reference_digest_matches_exactly(self):
        with patch('werkzeug.security.gen_salt', return_value='SyntheticSalt123'):
            reference = generate_password_hash(PASSWORD, method=HASH_METHOD)
        self.assertTrue(check_host_hash(reference, PASSWORD))
        with patch('modules.host_auth_hash.gen_salt', return_value='SyntheticSalt123'):
            self.assertEqual(generate_host_hash(PASSWORD), reference)

    @unittest.skipUnless(hasattr(hashlib, 'scrypt'), 'Werkzeug reference needs native scrypt')
    def test_new_hash_is_readable_by_werkzeug(self):
        encoded = generate_host_hash(PASSWORD)
        self.assertTrue(check_password_hash(encoded, PASSWORD))
        self.assertFalse(check_password_hash(encoded, 'wrong synthetic password'))

    def test_generate_and_check_without_hashlib_scrypt(self):
        # Exercise the precise missing-capability condition on any supported OS.
        with patch.object(hashlib, 'scrypt', None, create=True):
            encoded = generate_host_hash(PASSWORD)
            self.assertTrue(check_host_hash(encoded, PASSWORD))
            self.assertFalse(check_host_hash(encoded, 'wrong synthetic password'))
            self.assertNotEqual(encoded, generate_host_hash(PASSWORD))

    def test_unsupported_hash_parameters_are_rejected_before_derivation(self):
        for value in ('scrypt:1048576:8:1$salt$' + '0' * 128,
                      HASH_METHOD + '$$' + '0' * 128,
                      HASH_METHOD + '$salt$invalid', 'broken'):
            with self.assertRaises(ValueError):
                check_host_hash(value, PASSWORD)

    def test_default_python_script_then_current_python_verifies(self):
        interpreter = shutil.which('python3')
        if not interpreter:
            self.skipTest('python3 not available')
        with tempfile.TemporaryDirectory(prefix='astrion-default-python-password-') as temporary:
            data = Path(temporary) / 'data'
            env = dict(os.environ, DATA_DIR=str(data), ASTRION_IGNORE_DOTENV='1',
                       ASTRION_DATA_ROOT=str(Path(temporary) / 'runtime'), TERMINAL_SANDBOX_MODE='host')
            result = subprocess.run(
                [interpreter, '-B', str(ROOT / 'scripts/host_password.py'), '--password', PASSWORD],
                cwd=temporary, env=env, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn(PASSWORD, result.stdout + result.stderr)
            self.assertTrue(verify_host_password(load_host_auth(data), PASSWORD))
            self.assertFalse(verify_host_password(load_host_auth(data), 'wrong synthetic password'))

    def test_current_python_script_then_default_python_verifies(self):
        interpreter = shutil.which('python3')
        if not interpreter:
            self.skipTest('python3 not available')
        with tempfile.TemporaryDirectory(prefix='astrion-current-python-password-') as temporary:
            data = Path(temporary) / 'data'
            env = dict(os.environ, DATA_DIR=str(data), ASTRION_IGNORE_DOTENV='1',
                       ASTRION_DATA_ROOT=str(Path(temporary) / 'runtime'), TERMINAL_SANDBOX_MODE='host')
            result = subprocess.run(
                [sys.executable, '-B', str(ROOT / 'scripts/host_password.py'), '--password', PASSWORD],
                cwd=temporary, env=env, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            code = (
                "import json, sys; from modules.host_auth import load_host_auth, verify_host_password; "
                "payload=json.load(sys.stdin); state=load_host_auth(payload['data']); "
                "print(json.dumps({'correct': verify_host_password(state,payload['password']), "
                "'wrong': verify_host_password(state,'wrong synthetic password')}))"
            )
            checked = subprocess.run(
                [interpreter, '-B', '-c', code], cwd=ROOT, env=env,
                input=json.dumps({'data': str(data), 'password': PASSWORD}),
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(checked.returncode, 0, checked.stderr)
            self.assertEqual(json.loads(checked.stdout), {'correct': True, 'wrong': False})
