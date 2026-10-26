"""Offline document/branch regression; run with ASTRION_IGNORE_DOTENV=1."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from modules.chat_mode.storage import ChatStorage
from modules.chat_mode.tree import MessageTree, TreeConflict
from utils.conversation_manager import ConversationManager


def user(text, mid="u1"):
    return {"role": "user", "content": text, "message_id": mid}


def assistant(text, mid="a1", **kwargs):
    return {"role": "assistant", "content": text, "message_id": mid, **kwargs}


class ChatStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "chat"
        with patch.object(ConversationManager, "_load_host_workspaces",
                          side_effect=AssertionError("Chat read workspace catalog")), \
             patch.object(ConversationManager, "_migrate_legacy_conversation_files",
                          side_effect=AssertionError("Chat ran Agent migration")):
            self.store = ChatStorage(self.root)
        self.cid = self.store.create()

    def start(self):
        return self.store.begin(self.cid, "send1", message=user("question"))["value"]["id"]

    def complete(self):
        turn = self.start()
        self.store.append(self.cid, turn, assistant("answer"))
        self.store.finish(self.cid, turn)
        return turn

    def test_no_environment_metadata_and_shared_format(self):
        doc = self.store.read(self.cid)
        self.assertEqual(doc["messages"], [])
        self.assertIsNone(doc["metadata"]["project_path"])
        self.assertNotIn("permission_mode", doc["metadata"])
        self.assertTrue((self.root / "conversations" / f"{self.cid}.json").is_file())

    def test_edit_does_not_fork_or_drop_descendants(self):
        self.complete()
        before = self.store.read(self.cid)
        self.store.edit(self.cid, "edit1", "u1", "corrected")
        self.store.edit(self.cid, "edit2", "a1", "edited answer")
        after = self.store.read(self.cid)
        self.assertEqual(len(before["messages"]), len(after["messages"]))
        self.assertEqual(before["metadata"]["chat"]["turns"], after["metadata"]["chat"]["turns"])
        self.assertEqual([m["content"] for m in self.store.project(self.cid)["messages"]],
                         ["corrected", "edited answer"])

    def test_edit_send_creates_sibling_latest_immediately(self):
        self.complete()
        self.store.begin(self.cid, "edit-send", edit_message_id="u1",
                         message=user("new question", "unused"))
        doc = self.store.read(self.cid)
        self.assertEqual(len(doc["messages"]), 3)
        self.assertEqual([m["content"] for m in MessageTree(doc).path()], ["new question"])
        self.assertEqual(len(MessageTree(doc).siblings("u1")), 2)

    def test_regeneration_is_whole_turn(self):
        turn = self.start()
        calls = [{"id": "call1", "type": "function", "function": {"name": "web_search", "arguments": "{}"}}]
        self.store.append(self.cid, turn, assistant("searching", tool_calls=calls))
        self.store.append(self.cid, turn, {"message_id": "t1", "role": "tool", "content": "result", "tool_call_id": "call1"})
        self.store.append(self.cid, turn, assistant("finished", "a2"))
        self.store.finish(self.cid, turn)
        next_turn = self.store.begin(self.cid, "regen", regenerate_turn_id=turn)["value"]["id"]
        self.store.append(self.cid, next_turn, assistant("new version", "a3"))
        self.assertEqual([m["message_id"] for m in self.store.project(self.cid)["messages"]], ["u1", "a3"])
        self.assertEqual(len(self.store.read(self.cid)["messages"]), 5)

    def test_view_is_detached_and_does_not_move_latest(self):
        first = self.complete()
        second = self.store.begin(self.cid, "regen", regenerate_turn_id=first)["value"]["id"]
        self.store.append(self.cid, second, assistant("new answer", "a2"))
        old = self.store.project(self.cid, "a1", latest=False)
        old["messages"][-1]["content"] = "local mutation"
        self.assertEqual(self.store.project(self.cid)["messages"][-1]["content"], "new answer")
        self.assertEqual(self.store.project(self.cid, "a1", latest=False)["messages"][-1]["content"], "answer")

    def test_continue_old_branch_moves_latest_at_acceptance(self):
        first = self.complete()
        second = self.store.begin(self.cid, "regen", regenerate_turn_id=first)["value"]["id"]
        self.store.append(self.cid, second, assistant("new answer", "a2"))
        self.store.finish(self.cid, second)
        self.store.begin(self.cid, "send-old", parent_id="a1", message=user("follow up", "u2"))
        self.assertEqual([m["message_id"] for m in self.store.project(self.cid)["messages"]], ["u1", "a1", "u2"])

    def test_busy_turn_rejected_without_partial_write(self):
        self.start()
        before = self.store.read(self.cid)
        with self.assertRaises(TreeConflict):
            self.store.begin(self.cid, "send2", message=user("oops", "u2"))
        self.assertEqual(self.store.read(self.cid)["messages"], before["messages"])

    def test_idempotency_precedes_revision_check(self):
        receipt = self.store.begin(self.cid, "send1", message=user("question"), expected_revision=0)
        retry = self.store.begin(self.cid, "send1", message=user("question"), expected_revision=0)
        self.assertEqual(receipt, retry)
        with self.assertRaises(TreeConflict):
            self.store.begin(self.cid, "send1", message=user("different"))
        self.assertEqual(len(self.store.read(self.cid)["messages"]), 1)

    def test_stale_revision_edit_rejected(self):
        self.complete()
        rev = self.store.read(self.cid)["metadata"]["chat"]["revision"]
        self.store.edit(self.cid, "edit1", "a1", "one", expected_revision=rev)
        with self.assertRaises(TreeConflict):
            self.store.edit(self.cid, "edit2", "a1", "two", expected_revision=rev)

    def test_two_managers_serialize_acceptance(self):
        other = ChatStorage(self.root)
        def send(pair):
            store, op = pair
            try:
                store.begin(self.cid, op, message=user(op, op))
                return True
            except TreeConflict:
                return False
        with ThreadPoolExecutor(2) as pool:
            accepted = list(pool.map(send, [(self.store, "one"), (other, "two")]))
        self.assertEqual(sum(accepted), 1)
        self.assertEqual(len(self.store.read(self.cid)["messages"]), 1)

    def test_edit_preserves_tool_protocol_fields(self):
        turn = self.start()
        calls = [{"id": "c1", "function": {"name": "web_search", "arguments": "{}"}}]
        self.store.append(self.cid, turn, assistant("working", tool_calls=calls))
        self.store.edit(self.cid, "edit", "a1", "edited while tool running")
        stored = self.store.read(self.cid)["messages"][-1]
        self.assertEqual(stored["tool_calls"], calls)
        self.assertEqual(stored["message_id"], "a1")

    def test_late_result_after_stop_cannot_write(self):
        turn = self.start()
        self.store.finish(self.cid, turn, "stopped")
        with self.assertRaises(TreeConflict):
            self.store.append(self.cid, turn, assistant("late"))
        self.assertEqual(len(self.store.read(self.cid)["messages"]), 1)

    def test_linear_save_cannot_merge_branch_projection(self):
        self.complete()
        self.assertFalse(self.store.manager.save_conversation(self.cid, [user("bad")], allow_shrink=True))
        self.assertEqual(self.store.project(self.cid)["messages"][-1]["content"], "answer")

    def test_corrupt_document_does_not_get_overwritten(self):
        path = self.root / "conversations" / f"{self.cid}.json"
        path.write_text("{bad", encoding="utf-8")
        with self.assertRaises(json.JSONDecodeError):
            self.store.set_network(self.cid, "network1", False)
        self.assertEqual(path.read_text(), "{bad")

    def test_search_latest_only_and_shared_ancestor(self):
        first = self.complete()
        second = self.store.begin(self.cid, "regen", regenerate_turn_id=first)["value"]["id"]
        self.store.append(self.cid, second, assistant("alternative", "a2"))
        self.assertEqual(self.store.search("answer"), [])
        self.assertEqual(len(self.store.search("alternative")), 1)
        self.assertEqual(len(self.store.search("question")), 1)

    def test_network_switch_does_not_fork_or_generate(self):
        self.complete()
        before = self.store.read(self.cid)
        self.store.set_network(self.cid, "off", False)
        self.store.set_network(self.cid, "off", False)
        after = self.store.read(self.cid)
        self.assertEqual(after["messages"], before["messages"])
        self.assertEqual(after["metadata"]["pending_network_notices"], [{"revision": 1, "enabled": False}])

    def test_tree_validation_aborts_transaction(self):
        self.complete()
        before = self.store.read(self.cid)
        def corrupt(tree):
            tree.messages[0]["metadata"]["chat"]["parent_id"] = "a1"
        with self.assertRaises(ValueError):
            self.store.command(self.cid, "bad", {}, corrupt)
        self.assertEqual(self.store.read(self.cid)["messages"], before["messages"])

    def test_shared_ancestor_edit_invalidates_summary(self):
        self.complete()
        def summary(doc):
            doc["metadata"]["chat"]["summaries"] = [{"valid": True, "message_ids": ["u1", "a1"]}]
        self.store.manager.mutate_conversation(self.cid, summary)
        self.store.edit(self.cid, "edit", "u1", "changed")
        self.assertFalse(self.store.read(self.cid)["metadata"]["chat"]["summaries"][0]["valid"])


if __name__ == "__main__":
    unittest.main()
