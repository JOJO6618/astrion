from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from modules.host_auth import (
    HostAuthError, disable_host_password, host_auth_path,
    load_host_auth, set_host_password, verify_host_password,
)

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "Synthetic test 123!"


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.data = Path(self.directory.name)

    def test_missing_configuration_does_not_create_files_or_require_password(self):
        self.assertFalse(load_host_auth(self.data).enabled)
        self.assertEqual(list(self.data.iterdir()), [])

    def test_salted_hash_and_permissions_without_plaintext(self):
        first = set_host_password(self.data, PASSWORD)
        self.assertTrue(verify_host_password(first, PASSWORD))
        self.assertFalse(verify_host_password(first, "incorrect 123"))
        raw = host_auth_path(self.data).read_text()
        self.assertNotIn(PASSWORD, raw)
        self.assertTrue(first.password_hash.startswith("scrypt:32768:8:1$"))
        second = set_host_password(self.data, PASSWORD)
        self.assertNotEqual(first.password_hash, second.password_hash)
        self.assertNotEqual(first.generation, second.generation)
        if os.name != "nt":
            self.assertEqual(host_auth_path(self.data).stat().st_mode & 0o777, 0o600)

    def test_disable_requires_password_and_reset_reenables(self):
        before = set_host_password(self.data, PASSWORD)
        with self.assertRaises(HostAuthError):
            disable_host_password(self.data, "wrong password")
        self.assertEqual(load_host_auth(self.data), before)
        disabled = disable_host_password(self.data, PASSWORD)
        self.assertFalse(disabled.enabled)
        self.assertEqual(disabled.password_hash, "")
        self.assertNotEqual(before.generation, disabled.generation)
        after = set_host_password(self.data, "Another test 123")
        self.assertTrue(after.enabled)
        self.assertNotEqual(disabled.generation, after.generation)

    def test_spaces_are_part_of_password_and_invalid_inputs_leave_state_unchanged(self):
        password = "  pass  word  "
        before = set_host_password(self.data, password)
        self.assertTrue(verify_host_password(before, password))
        self.assertFalse(verify_host_password(before, password.strip()))
        for value in ("short", "x" * 1025, None, 123):
            with self.assertRaises(HostAuthError):
                set_host_password(self.data, value)
        self.assertEqual(load_host_auth(self.data), before)

    def test_damaged_config_never_becomes_password_free_and_script_can_repair(self):
        for payload in ("broken", "{}", json.dumps({"version": 1, "enabled": "false"}),
                        json.dumps({"version": 1, "enabled": True,
                                    "generation": "g" * 20, "password_hash": "bad"})):
            host_auth_path(self.data).write_text(payload)
            with self.assertRaises(HostAuthError):
                load_host_auth(self.data)
        set_host_password(self.data, PASSWORD)
        self.assertTrue(load_host_auth(self.data).enabled)

    @unittest.skipIf(os.name == "nt", "POSIX link test")
    def test_config_symlink_and_broken_link_are_rejected(self):
        path = host_auth_path(self.data)
        path.symlink_to(self.data / "missing")
        with self.assertRaises(HostAuthError):
            load_host_auth(self.data)
        with self.assertRaises(HostAuthError):
            set_host_password(self.data, PASSWORD)
        self.assertTrue(path.is_symlink())

    def test_failed_atomic_write_keeps_previous_credentials(self):
        before = set_host_password(self.data, PASSWORD)
        with patch("modules.host_auth.atomic_write_json", side_effect=OSError("synthetic")):
            with self.assertRaises(OSError):
                set_host_password(self.data, "Another test 123")
        self.assertEqual(load_host_auth(self.data), before)

    def test_script_uses_service_data_resolution_and_never_echoes_password(self):
        target = self.data / "explicit-data"
        env = dict(os.environ, DATA_DIR=str(target), ASTRION_IGNORE_DOTENV="1",
                   ASTRION_DATA_ROOT=str(self.data / "runtime"))
        result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/host_password.py"),
                                 "--password", PASSWORD], cwd=self.data, env=env,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(verify_host_password(load_host_auth(target), PASSWORD))
        self.assertNotIn(PASSWORD, result.stdout + result.stderr)
        self.assertIn(str(target / "host_auth.json"), result.stdout)
        bad = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/host_password.py"),
                              "--password", "short"], cwd=self.data, env=env,
                             capture_output=True, text=True, timeout=30)
        self.assertNotEqual(bad.returncode, 0)
        self.assertNotIn("short", bad.stdout + bad.stderr)
        self.assertTrue(verify_host_password(load_host_auth(target), PASSWORD))

    def test_script_rejects_web_mode(self):
        env = dict(os.environ, TERMINAL_SANDBOX_MODE="docker", ASTRION_IGNORE_DOTENV="1",
                   ASTRION_DATA_ROOT=str(self.data))
        result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/host_password.py"),
                                 "--password", PASSWORD], env=env,
                                capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.data / "web/data/host_auth.json").exists())
