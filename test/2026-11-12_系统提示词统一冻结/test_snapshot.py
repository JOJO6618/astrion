from __future__ import annotations

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from support import Owner, ROOT, composer, isolated_builders
from modules.system_prompt_snapshot import (
    LEGACY_COLLABORATION_RULES, LEGACY_PROMPT_KEYS, SYSTEM_PROMPT_KEY,
    has_frozen_system, reset_system_prompt_updates,
)


class SystemSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="system_snapshot_case_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        stack = isolated_builders()
        self.addCleanup(stack.close)

    def test_complete_system_is_saved_in_one_field_and_reused_without_builders(self):
        owner = Owner(self.root)
        messages = owner.build_messages(owner.context(), "question")
        self.assertTrue(has_frozen_system(messages))
        frozen = messages[0]["content"]
        self.assertTrue(frozen.startswith("provider base\n\nbridge\n\n" + owner.load_prompt("main_agent/static").strip()))
        self.assertIn("当前模型视觉能力：图片", frozen)
        self.assertIn("当前模型不支持直接理解视频", frozen)
        self.assertNotIn("【工作流进行中】", frozen)
        metadata = owner.saved()["metadata"]
        self.assertEqual(metadata[SYSTEM_PROMPT_KEY], frozen)
        self.assertFalse(set(LEGACY_PROMPT_KEYS) & metadata.keys())
        owner.load_prompt = Mock(side_effect=AssertionError("frozen template reread"))
        owner.api_client._build_responses_instructions = Mock(side_effect=AssertionError("provider prefix rebuilt"))
        owner.permission = "changed permission"
        again = owner.build_messages(owner.context(), "question")
        self.assertEqual(again[0]["content"], frozen)
        owner.context_manager.conversation_metadata = deepcopy(metadata)
        self.assertEqual(owner.build_messages(owner.context(), "question")[0]["content"], frozen)

    def test_different_new_conversations_have_identical_static_prefix(self):
        first, second = Owner(self.root / "first"), Owner(self.root / "second")
        second.context_manager.custom_system_prompt = "different custom instruction"
        second.permission = "different mode"
        a = first.build_messages(first.context(), "a")[0]["content"]
        b = second.build_messages(second.context(), "b")[0]["content"]
        prefix = "provider base\n\nbridge\n\n" + first.load_prompt("main_agent/static").strip()
        self.assertTrue(a.startswith(prefix) and b.startswith(prefix))
        self.assertNotEqual(a, b)

    def test_legacy_migration_preserves_order_and_does_not_use_new_templates(self):
        legacy = {key: f"old-{index}" for index, key in enumerate(LEGACY_PROMPT_KEYS)}
        owner = Owner(self.root, metadata=legacy)
        path = owner.manager._get_conversation_file_path(owner.context_manager.current_conversation_id)
        before = path.read_bytes()
        owner.manager.read_conversation(owner.context_manager.current_conversation_id)
        self.assertEqual(path.read_bytes(), before, "pure reads must not migrate")
        owner.load_prompt = Mock(side_effect=AssertionError("legacy templates regenerated"))
        actual = owner.build_messages(owner.context(), "question")[0]["content"]
        ordered = [legacy[key] for key in LEGACY_PROMPT_KEYS[:-1]]
        expected = "provider base\n\nbridge\n\n" + "\n\n".join([
            *ordered, LEGACY_COLLABORATION_RULES, legacy[LEGACY_PROMPT_KEYS[-1]],
        ])
        self.assertEqual(actual, expected)
        self.assertFalse(set(LEGACY_PROMPT_KEYS) & owner.saved()["metadata"].keys())
        self.assertEqual(owner.saved()["messages"], [])

    def test_compression_discards_legacy_and_rebuilds_current_layout(self):
        owner = Owner(self.root, metadata={"frozen_main_system_prompt": "old main"})
        self.assertIn("old main", owner.build_messages(owner.context(), "q")[0]["content"])
        updates = {**reset_system_prompt_updates(), "compression_count": 1, "workspace_memory_enabled": False}
        owner.manager.update_conversation_metadata(owner.context_manager.current_conversation_id, updates)
        owner.context_manager.conversation_metadata.update(updates)
        rebuilt = owner.build_messages(owner.context(), "q")[0]["content"]
        self.assertNotIn("old main", rebuilt)
        self.assertIn(owner.load_prompt("main_agent/static").strip(), rebuilt)
        self.assertFalse(set(LEGACY_PROMPT_KEYS) & owner.saved()["metadata"].keys())

    def test_snapshot_write_failure_is_not_cached_or_sent(self):
        owner = Owner(self.root)
        old_metadata = deepcopy(owner.context_manager.conversation_metadata)
        with patch.object(owner.manager, "_atomic_write_json", side_effect=OSError("synthetic disk failure")):
            with self.assertRaises(OSError):
                owner.build_messages(owner.context(), "q")
        self.assertEqual(owner.context_manager.conversation_metadata, old_metadata)
        self.assertIsNone(owner.saved()["metadata"].get(SYSTEM_PROMPT_KEY))

    def test_racing_initializers_commit_only_one_snapshot(self):
        owner = Owner(self.root)
        with patch.object(composer, "build_system_prompt", wraps=composer.build_system_prompt) as builder:
            with ThreadPoolExecutor(max_workers=2) as workers:
                results = list(workers.map(lambda _: owner.build_messages(owner.context(), "q")[0]["content"], range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(builder.call_count, 1)

    def test_corrupt_snapshot_is_not_silently_regenerated(self):
        owner = Owner(self.root, metadata={SYSTEM_PROMPT_KEY: ""})
        with self.assertRaisesRegex(ValueError, "Invalid frozen"):
            owner.build_messages(owner.context(), "q")

    def test_image_video_and_text_capability_branches(self):
        owner = Owner(self.root)
        with patch.object(composer, "model_supports_video", return_value=True):
            text, _ = composer.build_system_prompt(owner, owner.context())
        self.assertIn("当前模型视觉能力：图片＋视频", text)
        self.assertIn("`view_image`/`view_video`", text)
        with patch.object(composer, "model_supports_image", return_value=False):
            text, _ = composer.build_system_prompt(owner, owner.context())
        self.assertIn("当前模型视觉能力：无", text)

    def test_initialization_keeps_private_memory_in_the_single_field(self):
        owner = Owner(self.root)
        with patch("modules.workspace_memory_context.prepare_main_prompt", return_value=(
            "private memory full text", {"workspace_memory_snapshot": {"path": "/memory", "version": "test-version"}}
        )):
            frozen = owner.build_messages(owner.context(), "q")[0]["content"]
        metadata = owner.saved()["metadata"]
        self.assertIn("private memory full text", frozen)
        self.assertEqual(metadata["workspace_memory_snapshot"]["version"], "test-version")
        self.assertNotIn("frozen_workspace_memory_prompt", metadata)

    def test_later_system_notifications_keep_their_existing_role_and_content(self):
        owner = Owner(self.root)
        context = owner.context()
        context["conversation"].append({"role": "system", "content": "runtime notification", "metadata": {}})
        messages = owner.build_messages(context, "q")
        self.assertEqual(messages[-1], {"role": "system", "content": "runtime notification"})
        self.assertNotIn("runtime notification", owner.saved()["metadata"][SYSTEM_PROMPT_KEY])


    def test_optional_dynamic_sections_preserve_their_literal_contents(self):
        owner = Owner(self.root)
        owner.context_manager.custom_personalization_config["recent_conversations_prompt_enabled"] = True
        owner._build_recent_conversations_message = lambda **_: "recent conversation {literal-id}"
        owner._format_disabled_tool_notice = lambda: "disabled tools {literal-list}"
        owner.context_manager.custom_system_prompt = "custom instruction {literal-body}"
        frozen = owner.build_messages(owner.context(), "q")[0]["content"]
        self.assertIn("recent conversation {literal-id}", frozen)
        self.assertIn("disabled tools {literal-list}", frozen)
        self.assertIn("custom instruction {literal-body}", frozen)


if __name__ == "__main__":
    unittest.main()
