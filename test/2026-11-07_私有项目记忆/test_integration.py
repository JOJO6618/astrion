"""Frozen child prompts and real Host tool guard checks."""
from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

import test_workspace_memory as fixtures
from modules.sub_agent.history import SubAgentHistoryMixin
from modules.workspace_memory_context import child_prompt, main_prompt
from modules.workspace_memory import PROMPT_KEY


class ChildAndGuardTest(unittest.TestCase):
    setUp = fixtures.MemoryTest.setUp
    tearDown = fixtures.MemoryTest.tearDown
    seed = fixtures.MemoryTest.seed

    def test_child_frozen_file_survives_resume_and_refreshes_on_compression_only(self):
        self.seed("child-v1")
        frozen = "base stays fixed\n" + child_prompt(self.owner)
        prompt_file = self.root / "system_prompt.txt"
        prompt_file.write_text(frozen)
        child = SimpleNamespace(system_prompt=frozen, system_prompt_file=prompt_file,
                                manager=SimpleNamespace(terminal=self.owner))
        self.seed("child-v2")
        self.assertEqual(frozen, SubAgentHistoryMixin._rebuild_system_prompt(child))
        refreshed = SubAgentHistoryMixin._rebuild_system_prompt(child, refresh_memory=True)
        self.assertIn("child-v2", refreshed)
        self.assertNotIn("child-v1", refreshed)
        self.assertTrue(refreshed.startswith("base stays fixed\n"))
        self.assertEqual(refreshed, prompt_file.read_text())
        self.assertEqual(refreshed, SubAgentHistoryMixin._rebuild_system_prompt(child))

    def test_main_read_guard_exempts_only_injected_memory_and_readonly_stays_denied(self):
        from core.main_terminal_parts.tools_execution import MainTerminalToolsExecutionMixin
        self.seed("known memory")
        main_prompt(self.owner)
        # Use actual guard and permission implementations, with only unrelated
        # terminal configuration replaced by small deterministic callbacks.
        terminal = self.owner
        terminal.get_permission_mode = lambda: "unrestricted"
        terminal.get_execution_mode = lambda: "sandbox"
        terminal._is_plan_work_mode = lambda: False
        terminal._has_read_file_in_conversation = lambda path: False
        terminal._target_file_exists = lambda path: True
        terminal._is_plan_workspace_file = lambda args: False
        terminal._READONLY_ALLOWED_TOOLS = MainTerminalToolsExecutionMixin._READONLY_ALLOWED_TOOLS
        self.assertIsNone(MainTerminalToolsExecutionMixin._check_read_before_edit_prerequisite(
            terminal, "edit_file", {"file_path": self.target.tool_path}))
        normal = MainTerminalToolsExecutionMixin._check_read_before_edit_prerequisite(
            terminal, "edit_file", {"file_path": str(self.project / "normal.md")})
        self.assertEqual("read_before_edit_required", normal["enforcement"])
        terminal.get_permission_mode = lambda: "readonly"
        with patch("modules.goal_policy.interaction_denied", return_value=False):
            decision = MainTerminalToolsExecutionMixin.evaluate_tool_permission(
                terminal, "write_file", {"file_path": self.target.tool_path, "content": "denied"})
        self.assertFalse(decision["allowed"])
        self.assertEqual("known memory", self.target.file.read_text())

    def test_snapshot_is_persisted_together_and_failure_does_not_freeze(self):
        self.seed("persist me")
        cm = self.owner.context_manager
        cm.current_conversation_id = "conv-test"
        manager = Mock()
        manager.update_conversation_metadata.return_value = False
        cm.conversation_manager = manager
        with self.assertRaisesRegex(RuntimeError, "无法保存"):
            main_prompt(self.owner)
        self.assertNotIn(PROMPT_KEY, cm.conversation_metadata)
        manager.update_conversation_metadata.return_value = True
        main_prompt(self.owner)
        saved = manager.update_conversation_metadata.call_args.args[1]
        self.assertIn(PROMPT_KEY, saved)
        self.assertIn("workspace_memory_snapshot", saved)
        self.assertIn("workspace_memory_write_version", saved)


if __name__ == "__main__":
    unittest.main()
