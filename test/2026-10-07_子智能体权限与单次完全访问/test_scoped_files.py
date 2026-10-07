from __future__ import annotations

import asyncio
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from modules.execution_scope import ExecutionScope, bind_execution_scope
from modules.file_manager import FileManager
from modules.file_manager import scoped_io
from modules.host_sandbox_runner import _macos_profile_for_workspace, _scoped_macos_env
from modules.scoped_execution_policy import fixed_workspace_root, scoped_work_path
from modules.sub_agent.creation import SubAgentCreationMixin
from modules.sub_agent.manager import SubAgentManager
from modules.sub_agent.tools import handle_read_mediafile
from modules.custom_tool_registry import CustomToolRegistry, is_reserved_tool_name
from modules.docker_scoped_exec import wrap_scoped_docker_command


class ScopedFilesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.root = self.base / "project"
        self.root.mkdir()
        self.outside = self.base / "project-other"
        self.outside.mkdir()
        self.scope = ExecutionScope("sub_agent", "child", self.root, "workspace_write")
        self.files = FileManager(str(self.root), container_session=SimpleNamespace(mode="host"))
        self.creation = object.__new__(SubAgentCreationMixin)
        self.creation.project_path = self.root

    def test_workspace_file_writes_and_outside_denial(self):
        with bind_execution_scope(self.scope):
            self.files._native_mkdir(self.root / "output")
            with self.files._native_open(self.root / "output" / "result.txt", "w") as stream:
                stream.write("result")
            self.assertEqual(self.files._native_read_text(self.root / "output" / "result.txt"), "result")
            with self.assertRaises(PermissionError):
                self.files._native_open(self.outside / "result.txt", "w")
        self.assertFalse((self.outside / "result.txt").exists())

    def test_path_authorizations_remain_live(self):
        expanded = ExecutionScope("sub_agent", "child", self.root, "sandbox_write")
        with bind_execution_scope(expanded), patch("modules.file_manager.path_mixin.get_macos_writable_paths", return_value=[]):
            self.assertFalse(self.files._ensure_host_access(self.outside / "result.txt", "write")[0])
        with bind_execution_scope(expanded), patch("modules.file_manager.path_mixin.get_macos_writable_paths", return_value=[str(self.outside)]):
            self.assertTrue(self.files._ensure_host_access(self.outside / "result.txt", "write")[0])
        with bind_execution_scope(self.scope), patch("modules.file_manager.path_mixin.get_macos_writable_paths", return_value=[str(self.outside)]):
            self.assertFalse(self.files._ensure_host_access(self.outside / "result.txt", "write")[0])

    def test_symlink_swap_during_open_does_not_write_outside(self):
        parent = self.root / "parent"
        parent.mkdir()
        moved = self.root / "moved"
        target = parent / "result.txt"
        original_open = os.open
        swapped = False
        def swap(path, *args, **kwargs):
            nonlocal swapped
            if path == "parent" and not swapped:
                swapped = True
                parent.rename(moved)
                parent.symlink_to(self.outside, target_is_directory=True)
            return original_open(path, *args, **kwargs)
        with bind_execution_scope(self.scope), patch.object(scoped_io.os, "open", side_effect=swap):
            with self.assertRaises(OSError):
                self.files._native_open(target, "w")
        self.assertTrue(swapped)
        self.assertFalse((self.outside / "result.txt").exists())

    def test_hardlinks_and_special_files_are_refused(self):
        original = self.outside / "secret.txt"
        original.write_text("secret")
        alias = self.root / "alias.txt"
        os.link(original, alias)
        with bind_execution_scope(self.scope), self.assertRaises(PermissionError):
            self.files._native_open(alias, "w")
        self.assertEqual(original.read_text(), "secret")
        fifo = self.root / "fifo"
        os.mkfifo(fifo)
        with bind_execution_scope(self.scope), self.assertRaises(OSError):
            self.files._native_open(fifo, "w")

    def test_media_uses_checked_binary_handle(self):
        image = self.root / "image.png"
        image.write_bytes(b"sample")
        with bind_execution_scope(self.scope):
            result = asyncio.run(handle_read_mediafile(self.root, {"path": "image.png"}, self.files))
            self.assertTrue(result["success"])
        image.unlink()
        image.symlink_to(self.outside / "image.png")
        (self.outside / "image.png").write_bytes(b"private")
        with bind_execution_scope(self.scope):
            result = asyncio.run(handle_read_mediafile(self.root, {"path": "image.png"}, self.files))
            self.assertFalse(result["success"])

    def test_delivery_paths_reject_sibling_and_external_parent(self):
        with self.assertRaises(ValueError):
            self.creation._resolve_deliverables_dir("../project-other/new")
        (self.root / ".astrion").symlink_to(self.outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.creation._resolve_deliverables_dir(None, agent_id=1)
        self.assertFalse((self.outside / "new").exists())
        self.assertFalse((self.outside / "sub_agent_results").exists())

    def test_deliveries_are_new_and_auto_names_do_not_collide(self):
        path = self.creation._resolve_deliverables_dir("output/result")
        self.assertTrue(path.is_dir())
        with self.assertRaises(ValueError):
            self.creation._resolve_deliverables_dir("output/result")
        first = self.creation._resolve_deliverables_dir(None, agent_id=1)
        second = self.creation._resolve_deliverables_dir(None, agent_id=1)
        self.assertNotEqual(first, second)

    def test_restored_root_is_not_reresolved_into_new_authority(self):
        record = {"workspace_root": str(self.root), "access_level": "workspace_write"}
        self.assertTrue(SubAgentManager._has_fixed_access(record))
        self.root.rmdir()
        self.root.symlink_to(self.outside, target_is_directory=True)
        self.assertFalse(SubAgentManager._has_fixed_access(record))
        with self.assertRaises(ValueError):
            fixed_workspace_root(self.scope)

    def test_cwd_does_not_redefine_writable_root(self):
        (self.root / "subdir").mkdir()
        with bind_execution_scope(self.scope):
            self.assertEqual(scoped_work_path(self.root, "subdir"), self.root / "subdir")
            with self.assertRaises(ValueError):
                scoped_work_path(self.root, "../project-other")
            self.assertEqual(fixed_workspace_root(self.scope), self.root)

    def test_macos_profile_and_temp_env_keep_workspace_write_root(self):
        with bind_execution_scope(self.scope), patch("modules.host_sandbox_runner.get_macos_writable_paths", return_value=[str(self.outside)]):
            profile = _macos_profile_for_workspace(self.root, "restricted")
            write_rule = profile.split("(allow file-write*", 1)[1]
            self.assertNotIn(str(self.outside), write_rule)
            self.assertNotIn('(subpath "/tmp")', write_rule)
            self.assertNotIn('(subpath "/private/tmp")', write_rule)
            original = {"TMPDIR": "/outside/tmp"}
            env = _scoped_macos_env(original)
            self.assertTrue(Path(env["TMPDIR"]).is_relative_to(self.root))
            self.assertTrue(Path(env["TMPDIR"]).is_dir())
            self.assertEqual(original["TMPDIR"], "/outside/tmp")

    def test_custom_registry_cannot_override_builtin(self):
        registry = CustomToolRegistry(root=str(self.base / "custom"), enabled=False)
        for name in ("run_command", "write_file", "ask_user", "mcp__server__tool"):
            self.assertTrue(is_reserved_tool_name(name))
            with self.assertRaises(ValueError):
                registry.upsert_tool({"id": name, "execution_code": "print('substituted')"})
        self.assertFalse((self.base / "custom" / "run_command").exists())

    def test_docker_seccomp_denies_cross_process_memory_access(self):
        from modules.docker_scoped_launcher import _install_seccomp
        from unittest.mock import Mock
        calls = []
        def resolve_name(name):
            calls.append(name.decode())
            return len(calls)
        library = Mock()
        library.seccomp_init.return_value = 1
        library.seccomp_syscall_resolve_name.side_effect = resolve_name
        library.seccomp_rule_add.return_value = 0
        library.seccomp_load.return_value = 0
        with patch("modules.docker_scoped_launcher.ctypes.util.find_library", return_value="fake"), \
             patch("modules.docker_scoped_launcher.ctypes.CDLL", return_value=library):
            _install_seccomp()
        for name in ("process_vm_readv", "process_vm_writev", "ptrace", "pidfd_getfd", "io_uring_setup"):
            self.assertIn(name, calls)
        self.assertEqual(library.seccomp_rule_add.call_count, len(calls))
        library.seccomp_load.assert_called_once_with(1)

    def test_docker_workspace_scope_requires_launcher_without_fallback(self):
        with bind_execution_scope(self.scope):
            command = wrap_scoped_docker_command("/workspace", ["/bin/bash", "-lc", "echo ok"])
        self.assertIn("-I", command)
        self.assertIn("-S", command)
        self.assertTrue(any("Landlock ABI >=3" in part for part in command))
        full = ExecutionScope("sub_agent", "child", self.root, "full_access")
        with bind_execution_scope(full), self.assertRaises(ValueError):
            wrap_scoped_docker_command("/workspace", ["true"])


if __name__ == "__main__":
    unittest.main()
