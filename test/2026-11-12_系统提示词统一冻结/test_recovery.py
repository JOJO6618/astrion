from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from support import Owner, isolated_builders
from modules.system_prompt_snapshot import SYSTEM_PROMPT_KEY
from utils.conversation_manager import ConversationManager


class RecoveryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="system_recovery_case_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        stack = isolated_builders()
        self.addCleanup(stack.close)

    def test_stale_manager_saving_messages_does_not_remove_the_frozen_system(self):
        owner = Owner(self.root)
        frozen = owner.build_messages(owner.context(), "q")[0]["content"]
        other = ConversationManager(base_dir=str(owner.data_dir), project_path=str(owner.project_path))
        self.assertTrue(other.save_conversation(
            conversation_id=owner.context_manager.current_conversation_id,
            messages=[{"role": "user", "content": "saved from another instance"}],
            project_path=str(owner.project_path),
        ))
        self.assertEqual(owner.saved()["metadata"][SYSTEM_PROMPT_KEY], frozen)

    def _restore(self, owner, target_id):
        from server import conversation as api
        source_id = owner.context_manager.current_conversation_id
        checkpoint_manager = SimpleNamespace(
            get_checkpoint=lambda _: {"seq": 1, "message_index": 0},
            get_checkpoint_snapshot=lambda _: {
                "messages": [{"role": "user", "content": "checkpoint question"}],
                "metadata": {SYSTEM_PROMPT_KEY: "obsolete checkpoint system", "frozen_main_system_prompt": "obsolete legacy",
                             "workspace_memory_snapshot": {"path": "/obsolete", "version": "old"}},
            },
            prune_checkpoints_after=lambda _: {"max_seq": 1},
            load_meta=lambda: {},
        )
        terminal = SimpleNamespace(context_manager=SimpleNamespace(conversation_manager=owner.manager))
        workspace = SimpleNamespace(project_path=owner.project_path, data_dir=owner.data_dir)
        with patch.object(api, "_get_conv_versioning_manager", return_value=checkpoint_manager), patch.object(api, "_update_conversation_versioning_meta"):
            api._restore_checkpoint_to_conversation(
                terminal, workspace, source_id, target_id, 1,
                api.ConversationVersioningManager.TRACKING_MODE_CONVERSATION_ONLY,
                False, backup_mode="full",
            )

    def test_checkpoint_restore_keeps_the_current_conversation_snapshot(self):
        owner = Owner(self.root)
        frozen = owner.build_messages(owner.context(), "q")[0]["content"]
        self._restore(owner, owner.context_manager.current_conversation_id)
        saved = owner.saved()
        self.assertEqual(saved["metadata"][SYSTEM_PROMPT_KEY], frozen)
        self.assertNotIn("frozen_main_system_prompt", saved["metadata"])
        self.assertNotIn("workspace_memory_snapshot", saved["metadata"])
        self.assertEqual(saved["messages"][0]["content"], "checkpoint question")

    def test_new_fork_does_not_inherit_the_sources_frozen_system(self):
        owner = Owner(self.root)
        owner.build_messages(owner.context(), "q")
        target_id = owner.manager.create_conversation(project_path=str(owner.project_path))
        self._restore(owner, target_id)
        saved = owner.manager.read_conversation(target_id)
        self.assertIsNone(saved["metadata"][SYSTEM_PROMPT_KEY])
        self.assertNotIn("frozen_main_system_prompt", saved["metadata"])


if __name__ == "__main__":
    unittest.main()
