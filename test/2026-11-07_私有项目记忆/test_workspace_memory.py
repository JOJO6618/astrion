"""Private workspace memory regression: isolated data, no server/model calls."""
from __future__ import annotations

import atexit
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
RUNTIME = Path(tempfile.mkdtemp(prefix="astrion-private-memory-"))
atexit.register(lambda: shutil.rmtree(RUNTIME, ignore_errors=True))
os.environ.update(ASTRION_IGNORE_DOTENV="1", ASTRION_DATA_ROOT=str(RUNTIME),
                  DATA_DIR=str(RUNTIME / "data"), LOGS_DIR=str(RUNTIME / "logs"),
                  USER_SPACE_DIR=str(RUNTIME / "users"), API_USER_SPACE_DIR=str(RUNTIME / "api"),
                  TERMINAL_SANDBOX_MODE="host", WEB_SECRET_KEY="test-only")

from modules.file_manager import FileManager
from modules.execution_plane.local import LocalExecutionBackend
from modules.execution_scope import ExecutionScope, bind_execution_scope
from modules.workspace_memory import (ENABLED_KEY, PROMPT_KEY, SNAPSHOT_KEY, location,
                                      prepare_directory, register_host_memory, host_memory_roots)
from modules.workspace_memory_context import main_prompt, child_prompt, replace_child_memory
from modules.workspace_memory_tools import injected_memory, write_injected_memory
from modules.host_sandbox_policy import get_macos_readable_paths, get_macos_writable_paths


class Context:
    def __init__(self):
        self.conversation_metadata = {ENABLED_KEY: True}
        self.current_conversation_id = None

    def _set_meta_flag(self, key, value, save=True):
        self.conversation_metadata[key] = value


class MemoryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=RUNTIME)
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.data = self.root / "private-data"
        self.owner = SimpleNamespace(project_path=str(self.project), data_dir=str(self.data),
                                     container_session=None, context_manager=Context())
        self.owner.file_manager = FileManager(str(self.project), data_dir=str(self.data))
        self.owner.execution_backend = LocalExecutionBackend(self.owner)
        self.owner.load_prompt = lambda name: (REPO / "prompts" / f"{name}.txt").read_text()
        self.target = location(str(self.project), str(self.data))

    def tearDown(self):
        self.temp.cleanup()

    def seed(self, content):
        prepare_directory(self.target)
        self.target.file.write_text(content)

    def test_paths_are_external_and_path_collisions_are_separated(self):
        a = location(str(self.root / "a-b" / "c"), str(self.data))
        b = location(str(self.root / "a" / "b-c"), str(self.data))
        self.assertNotEqual(a.directory, b.directory)
        self.assertFalse(self.target.directory.is_relative_to(self.project))
        self.assertIn(str(self.target.directory), host_memory_roots(str(self.project)))
        self.assertEqual([], host_memory_roots(str(self.root / "other")))

    def test_full_text_frozen_until_compression_and_survives_resume(self):
        body = "\n".join(f"memory-{n}" for n in range(450))
        self.seed(body)
        first = main_prompt(self.owner)
        self.assertIn(body, first)
        self.seed("changed-on-disk")
        with patch("modules.workspace_memory_context.read_snapshot", side_effect=AssertionError("ordinary turn read disk")):
            self.assertEqual(first, main_prompt(self.owner))
            restored = Context()
            restored.conversation_metadata = dict(self.owner.context_manager.conversation_metadata)
            self.owner.context_manager = restored
            self.assertEqual(first, main_prompt(self.owner))
        self.owner.context_manager.conversation_metadata[PROMPT_KEY] = None
        self.assertIn("changed-on-disk", main_prompt(self.owner))

    def test_legacy_conversation_waits_until_compression(self):
        self.owner.context_manager.conversation_metadata = {}
        with patch("modules.workspace_memory_context.read_snapshot", side_effect=AssertionError("legacy resume read disk")):
            self.assertEqual("", main_prompt(self.owner))

    def test_missing_file_is_empty_snapshot_and_no_preread_is_needed(self):
        text = main_prompt(self.owner)
        self.assertIn("当前记忆为空", text)
        self.assertFalse(self.target.file.exists())
        self.assertIsNotNone(injected_memory(self.owner, self.target.tool_path))
        result = write_injected_memory(self.owner, "write_file", {"file_path": self.target.tool_path, "content": "first\n"})
        self.assertTrue(result["success"], result)
        self.assertEqual("first\n", self.target.file.read_text())
        self.assertEqual(text, main_prompt(self.owner))

    def test_conflicting_write_returns_latest_and_retry_keeps_both_changes(self):
        self.seed("original\n")
        main_prompt(self.owner)
        self.seed("another-conversation\n")
        args = {"file_path": self.target.tool_path, "content": "stale overwrite\n"}
        conflict = write_injected_memory(self.owner, "write_file", args)
        self.assertEqual("workspace_memory_conflict", conflict["enforcement"])
        self.assertEqual("another-conversation\n", conflict["current_content"])
        self.assertEqual("another-conversation\n", self.target.file.read_text())
        args["content"] = conflict["current_content"] + "my addition\n"
        self.assertTrue(write_injected_memory(self.owner, "write_file", args)["success"])
        self.assertEqual(args["content"], self.target.file.read_text())
        # The system snapshot remains the original, including after a write.
        self.assertIn("original\n", main_prompt(self.owner))

    def test_only_exact_injected_file_is_exempt(self):
        main_prompt(self.owner)
        self.assertIsNone(injected_memory(self.owner, str(self.target.directory / "other.md")))
        self.assertIsNone(injected_memory(self.owner, str(self.project / "MEMORY.md")))
        self.owner.context_manager.conversation_metadata.pop(PROMPT_KEY)
        self.assertIsNone(injected_memory(self.owner, self.target.tool_path))

    def test_edit_uses_normal_backend_without_pre_read_tool(self):
        self.seed("one\ntwo\nthree\n")
        main_prompt(self.owner)
        result = write_injected_memory(self.owner, "edit_file", {
            "file_path": self.target.tool_path,
            "replacements": [{"old_string": "one\ntwo\nthree", "new_string": "one\nchanged\nthree", "replace_all": False}],
        })
        self.assertTrue(result["success"], result)
        self.assertEqual("one\nchanged\nthree\n", self.target.file.read_text())

    def test_child_reads_same_project_and_refreshes_only_its_memory_segment(self):
        self.seed("first-child-memory")
        frozen = "frozen base\n" + child_prompt(self.owner) + "\nfixed tail"
        self.assertIn("由主智能体维护", frozen)
        self.seed("new-child-memory")
        new = replace_child_memory(frozen, self.owner)
        self.assertIn("new-child-memory", new)
        self.assertNotIn("first-child-memory", new)
        self.assertTrue(new.startswith("frozen base\n"))
        self.assertTrue(new.endswith("\nfixed tail"))

    def test_child_gets_read_access_without_extra_host_write_root(self):
        prepare_directory(self.target)
        child_dir = self.project / "child"
        child_dir.mkdir()
        scope = ExecutionScope(executor_kind="sub_agent", actor_id="child", workspace_root=self.project,
                               access_level="working_dir_write", working_dir=child_dir)
        with bind_execution_scope(scope):
            self.assertIn(str(self.target.directory), get_macos_readable_paths(str(self.project)))
            self.assertNotIn(str(self.target.directory), get_macos_writable_paths(str(self.project)))
            self.assertFalse(self.owner.file_manager._ensure_host_access(self.target.file, "write")[0])
        self.assertIn(str(self.target.directory), get_macos_writable_paths(str(self.project)))

    def test_nonlocal_backend_is_never_substituted_with_controller_files(self):
        self.seed("controller-private-file")
        self.owner.execution_backend = object()
        with self.assertRaisesRegex(RuntimeError, "执行后端"):
            main_prompt(self.owner)

    def test_symlink_namespace_is_not_followed(self):
        self.data.mkdir()
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        (self.data / "memory").symlink_to(elsewhere, target_is_directory=True)
        with self.assertRaises(PermissionError):
            main_prompt(self.owner)
        self.assertEqual([], list(elsewhere.iterdir()))


if __name__ == "__main__":
    unittest.main()
