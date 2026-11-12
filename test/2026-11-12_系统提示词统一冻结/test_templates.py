from __future__ import annotations

from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from support import Owner, ROOT, isolated_builders
from core.main_terminal_parts.context.prompt import PromptMixin
from modules.system_prompt_snapshot import SYSTEM_PROMPT_KEY


class TemplateTest(unittest.TestCase):
    def test_missing_main_agent_template_is_an_error_not_a_generic_prompt(self):
        with self.assertRaises(FileNotFoundError):
            PromptMixin.load_prompt(SimpleNamespace(), "main_agent/nonexistent-required-template-test")
        self.assertEqual(PromptMixin.load_prompt(SimpleNamespace(), "nonexistent-legacy-template-test"), "你是 Astrion，一个智能助手。")

    def test_static_rules_have_no_initialization_placeholders(self):
        text = (ROOT / "prompts/main_agent/static.txt").read_text()
        for placeholder in ("{model_description}", "{{STATE}}", "{modify_history_dir}",
                            "{global_memory}", "{workspace_memory_content}", "{skills_list}"):
            self.assertNotIn(placeholder, text)
        self.assertIn("以下规则仅在后文初始化信息说明当前环境提供内置浏览器时适用", text)
        self.assertIn("以下规则仅在后文初始化信息说明当前环境提供预览面板时适用", text)

    def test_real_private_memory_preparation_commits_with_the_full_snapshot(self):
        with tempfile.TemporaryDirectory(prefix="system_private_template_") as directory, isolated_builders():
            owner = Owner(Path(directory), metadata={"workspace_memory_enabled": True})
            snapshot = {"path": "/synthetic/MEMORY.md", "version": "saved-version", "exists": True,
                        "content": "Actual rendered memory body with {literal_braces}"}
            with patch("modules.workspace_memory_context.read_snapshot", return_value=snapshot):
                frozen = owner.build_messages(owner.context(), "q")[0]["content"]
            metadata = owner.saved()["metadata"]
            self.assertIn(snapshot["content"], frozen)
            self.assertIn("/synthetic/MEMORY.md", frozen)
            self.assertNotIn("{workspace_memory_maintenance}", frozen)
            self.assertEqual(metadata["workspace_memory_snapshot"]["version"], "saved-version")
            self.assertEqual(metadata["workspace_memory_write_version"], "saved-version")
            self.assertEqual(metadata[SYSTEM_PROMPT_KEY], frozen)
            self.assertNotIn("frozen_workspace_memory_prompt", metadata)


if __name__ == "__main__":
    unittest.main()
