"""Sidebar copies preserve every branch but cannot inherit active operations."""
import tempfile
import unittest
from pathlib import Path

from modules.chat_mode.storage import ChatStorage
from modules.chat_mode.duplicate import duplicate
from modules.chat_mode.tree import TreeConflict


class DuplicateTests(unittest.TestCase):
    def test_copy_is_idempotent_preserves_branches_and_does_not_replay_handoffs(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = ChatStorage(Path(directory))
            source = storage.create()
            receipt = storage.begin(source, "first", message={"message_id": "u", "role": "user", "content": "question"})
            turn = receipt["value"]["id"]
            storage.append(source, turn, {"message_id": "a", "role": "assistant", "content": "first"})
            storage.finish(source, turn, "complete")
            second = storage.begin(source, "regenerate", regenerate_turn_id=turn)["value"]["id"]
            storage.append(source, second, {"message_id": "b", "role": "assistant", "content": "second"})
            storage.finish(source, second, "complete")
            storage.manager.mutate_conversation(source, lambda doc: doc["metadata"]["chat"].update(handoffs={"a": {"status": "pending"}}))
            copied = duplicate(storage, source, "copy-once")
            self.assertNotEqual(copied, source)
            self.assertEqual(duplicate(storage, source, "copy-once"), copied)
            document = storage.read(copied)
            self.assertEqual({m["message_id"] for m in document["messages"]}, {"u", "a", "b"})
            self.assertEqual(document["metadata"]["chat"]["handoffs"], {})
            self.assertEqual(document["metadata"]["chat"]["commands"], {})
            storage.edit(copied, "edit-copy", "a", "copy only")
            self.assertEqual(storage.read(source)["messages"][1]["content"], "first")

    def test_running_source_is_not_copied(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = ChatStorage(Path(directory))
            source = storage.create()
            storage.begin(source, "first", message={"message_id": "u", "role": "user", "content": "question"})
            with self.assertRaises(TreeConflict):
                duplicate(storage, source, "copy-once")
            self.assertEqual(len(list(storage.manager.conversations_dir.glob("conv_*.json"))), 1)
