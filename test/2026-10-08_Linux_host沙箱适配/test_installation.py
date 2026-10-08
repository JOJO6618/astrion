from __future__ import annotations

from contextlib import ExitStack, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


class InstallationTests(unittest.TestCase):
    def test_status_import_does_not_change_application_import_paths(self):
        before = list(sys.path)
        from modules.linux_sandbox import setup
        self.assertEqual(sys.path, before)
        with self.assertRaises(ValueError):
            setup.resolve_user("0")

    def _exercise(self, fail=False):
        from modules.linux_sandbox import installer
        from modules.linux_sandbox import setup
        from modules.linux_sandbox import install_check
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp, ExitStack() as stack:
            root = Path(tmp).resolve()
            target = root / "libexec" / "astrion-sandbox"
            target.mkdir(parents=True)
            (target / "previous-marker").write_text("prior installation")
            config = root / "config" / "helper.json"
            config.parent.mkdir()
            old_config = json.dumps({"allowed_uids": [1001]})
            config.write_text(old_config)
            config.chmod(0o600)
            profile, service = root / "profiles", root / "service"
            profile.write_text("previous profile")
            service.write_text("previous service")
            executables = {key: "/usr/bin/" + key for key in ("python", "bwrap", "systemd_run", "systemctl", "bpftool")}
            commands = []
            def run(argv, **_):
                commands.append(argv)
                if argv[0] == "cc":
                    Path(argv[argv.index("-o")+1]).write_text("synthetic binary")
                return SimpleNamespace(returncode=0, stdout="--bind-fd --ro-bind-fd")
            def export(path, **_):
                path.write_bytes(b"synthetic policy")
                return {}
            stack.enter_context(patch.object(installer, "INSTALL_ROOT", str(target)))
            stack.enter_context(patch.object(installer, "CONFIG", config))
            stack.enter_context(patch.object(installer, "PROFILE", profile))
            stack.enter_context(patch.object(installer, "SERVICE", service))
            stack.enter_context(patch.object(installer, "require_installation_runtime"))
            stack.enter_context(patch.object(installer, "trusted_path", side_effect=lambda raw, **_: Path(raw)))
            stack.enter_context(patch.object(installer, "run", side_effect=run))
            stack.enter_context(patch.object(installer, "export_policy", side_effect=export))
            stack.enter_context(patch.object(installer.os, "geteuid", return_value=0))
            stack.enter_context(patch.object(installer.subprocess, "run", side_effect=run))
            stack.enter_context(patch.object(setup, "dependencies", return_value=(executables, [])))
            verify = stack.enter_context(patch.object(install_check, "verify_install",
                                                    side_effect=RuntimeError("synthetic verification failure") if fail else None))
            with redirect_stdout(StringIO()):
                if fail:
                    with self.assertRaisesRegex(RuntimeError, "synthetic verification failure"):
                        installer.install_helper(SimpleNamespace(pw_uid=1000, pw_gid=1000, pw_name="fixture"))
                else:
                    installer.install_helper(SimpleNamespace(pw_uid=1000, pw_gid=1000, pw_name="fixture"))
            verify.assert_called_once()
            self.assertFalse(any(command[0] == "apt-get" for command in commands))
            if fail:
                self.assertEqual((target / "previous-marker").read_text(), "prior installation")
                self.assertEqual(config.read_text(), old_config)
                self.assertEqual(profile.read_text(), "previous profile")
                self.assertEqual(service.read_text(), "previous service")
                self.assertEqual(config.stat().st_mode & 0o777, 0o600)
                self.assertEqual(len(list(target.parent.glob("astrion-sandbox.failed-*"))), 1)
            else:
                self.assertTrue((target / "server.py").exists())
                self.assertEqual(json.loads(config.read_text())["allowed_uids"], [1000, 1001])
                self.assertEqual(len(list(target.parent.glob("astrion-sandbox.backup-*"))), 1)

    def test_success_preserves_previous_helper_and_authorized_users(self):
        self._exercise()

    def test_verification_failure_restores_previous_installation(self):
        self._exercise(fail=True)

    def test_source_shell_entry_does_not_launch_astrion(self):
        root = Path(__file__).resolve().parents[2]
        script = root / "scripts/setup-linux-sandbox.sh"
        result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run(["bash", str(script), "--help"], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--user", result.stdout)
        self.assertIn("without sudo", result.stdout)

    def test_non_linux_import_does_not_require_unix_transport(self):
        # Fresh import in a subprocess: Windows lacks fcntl, pwd and AF_UNIX
        # transport support. Importing the existing platform runner must work.
        source = '''import importlib.abc,sys
class Guard(importlib.abc.MetaPathFinder):
 def find_spec(self,name,path,target=None):
  if name in {'fcntl','pwd'} or name.startswith('modules.linux_sandbox.plans'):
   raise ImportError('simulated Windows-only environment')
sys.meta_path.insert(0,Guard())
from modules.host_sandbox_runner import SandboxPlan, WSL_DEFAULT_SANDBOX_DISTRO
assert WSL_DEFAULT_SANDBOX_DISTRO=='astrion-sandbox'
'''
        result = subprocess.run([sys.executable, "-B", "-c", source], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
