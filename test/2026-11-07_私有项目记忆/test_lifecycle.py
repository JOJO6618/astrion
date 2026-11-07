"""Exercise real conversation creation, shallow compression and Git ignores."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import test_workspace_memory as fixtures
from modules.workspace_memory import ENABLED_KEY, PROMPT_KEY, location, prepare_directory
from modules.workspace_memory_context import main_prompt
from utils.conversation_manager import ConversationManager
from utils.context_manager.compression_mixin import CompressionMixin


class LifecycleTest(unittest.TestCase):
    setUp = fixtures.MemoryTest.setUp
    tearDown = fixtures.MemoryTest.tearDown
    seed = fixtures.MemoryTest.seed

    def test_new_agent_conversation_enables_memory_without_creating_execution_resources(self):
        manager = ConversationManager(base_dir=str(self.data), project_path=str(self.project))
        conversation = manager.create_conversation(str(self.project))
        metadata = manager.read_conversation(conversation)["metadata"]
        self.assertTrue(metadata[ENABLED_KEY])
        self.assertIsNone(metadata[PROMPT_KEY])
        self.assertFalse(self.target.file.exists())

    def test_chat_creation_does_not_get_agent_project_memory(self):
        manager = ConversationManager(base_dir=str(self.data / "chat"), conversation_kind="chat")
        conversation = manager.create_conversation("")
        metadata = manager.read_conversation(conversation)["metadata"]
        self.assertNotIn(ENABLED_KEY, metadata)
        self.assertNotIn(PROMPT_KEY, metadata)

    def test_actual_shallow_compression_invalidates_snapshot_but_noop_does_not(self):
        self.seed("before compression")
        before = main_prompt(self.owner)
        cm = self.owner.context_manager
        cm.conversation_history = []
        cm._shallow_compact_round = 0
        unchanged = CompressionMixin._run_auto_shallow_compression(cm, keep_recent_tools=0, keep_user_turn_tools=0)
        self.assertEqual(0, unchanged)
        self.assertEqual(before, cm.conversation_metadata[PROMPT_KEY])
        cm.conversation_history = [{"role": "tool", "name": "read_file", "content": "old tool output"}]
        marked = CompressionMixin._run_auto_shallow_compression(cm, keep_recent_tools=0, keep_user_turn_tools=0)
        self.assertEqual(1, marked)
        self.assertIsNone(cm.conversation_metadata[PROMPT_KEY])
        self.seed("after compression")
        self.assertIn("after compression", main_prompt(self.owner))

    def test_docker_private_ignore_prevents_git_add_from_collecting_memory(self):
        target = location(str(self.project), str(self.data), docker=True)
        prepare_directory(target)
        self.assertEqual("*\n", (target.directory / ".gitignore").read_text())
        # A synthetic copy models what Git sees at the bind target. No actual
        # private file is copied to a repository in application code.
        destination = self.project / ".astrion/astrion-memory"
        destination.mkdir(parents=True)
        shutil.copyfile(target.directory / ".gitignore", destination / ".gitignore")
        (destination / "MEMORY.md").write_text("synthetic-private-content")
        env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
        subprocess.run(["git", "init", "-q", str(self.project)], env=env, check=True, capture_output=True)
        result = subprocess.run(["git", "-C", str(self.project), "check-ignore", "--no-index",
                                 ".astrion/astrion-memory/MEMORY.md"], env=env,
                                check=True, capture_output=True, text=True)
        self.assertIn("MEMORY.md", result.stdout)


if __name__ == "__main__":
    unittest.main()
