"""Workspace-free navigation, quota sharing and all-branch media ownership."""
from datetime import datetime, timedelta
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from modules.chat_mode.storage import ChatStorage
from modules.chat_mode.media_gc import collect
from modules.desktop_navigation import DesktopNavigation
from utils.media_store import MediaStore


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_chat_new_reload_keeps_one_stable_tab(self):
        # v3：空白 Chat 标签由 create 建立，刷新只是重新 attach，不增标签
        service = DesktopNavigation(str(self.root / "navigation.json"))
        state = service.command("user", "create1", "create", {"conversation_kind": "chat"})["state"]
        key = state["tabs"][0]["key"]
        for i in range(3):
            state = service.command("user", f"attach{i}", "attach", {
                "key": key, "renderer_id": f"renderer{i}"})["state"]
            self.assertEqual(len(state["tabs"]), 1)
            self.assertEqual(state["tabs"][0]["key"], key)
            self.assertEqual(state["tabs"][0]["workspace_id"], "")

    def test_pure_chat_page_resume_and_empty_bind(self):
        # v3：设置页是 page 标签；回到 Chat 即 activate opener；空绑定不生效
        service = DesktopNavigation(str(self.root / "navigation.json"))
        state = service.command("user", "create", "create", {"conversation_kind": "chat"})["state"]
        key = state["tabs"][0]["key"]
        service.command("user", "attach", "attach", {"key": key, "renderer_id": "renderer"})
        state = service.command("user", "page", "page", {"page": "/settings/chat"})["state"]
        self.assertNotEqual(state["active_key"], key)
        state = service.command("user", "back", "activate", {"key": key})["state"]
        self.assertEqual(state["active_key"], key)
        result = service.command("user", "empty", "bind", {"renderer_id": "renderer", "workspace_id": "", "key": key})
        self.assertFalse(result["result"]["bound"])
        bound = service.command("user", "bind", "bind", {"renderer_id": "renderer", "workspace_id": "", "key": key,
                                                               "conversation_id": "conv_test"})["state"]
        self.assertEqual(bound["tabs"][0]["key"], key)
        self.assertEqual(bound["tabs"][0]["conversation_kind"], "chat")

    def test_same_conversation_id_in_chat_and_agent_is_separate(self):
        service = DesktopNavigation(str(self.root / "navigation.json"))
        service.command("user", "chat", "open", {"conversation_kind": "chat", "conversation_id": "conv_test"})
        state = service.command("user", "agent", "open", {"workspace_id": "project", "conversation_id": "conv_test"})["state"]
        self.assertEqual(len(state["tabs"]), 2)
        self.assertEqual({tab["conversation_kind"] for tab in state["tabs"]}, {"chat", "agent"})

    def test_media_gc_preserves_old_branches_and_drafts(self):
        storage = ChatStorage(self.root / "personal" / "chat")
        store = MediaStore(storage.root)
        refs = [store.store_bytes(f"media{i}".encode(), kind="image", mime_type="image/png") for i in range(4)]
        cid = storage.create(model_key="synthetic")
        turn = storage.begin(cid, "send", message={"role": "user", "message_id": "u1", "content": "first",
                                                  "media_refs": [refs[0]]})["value"]["id"]
        storage.append(cid, turn, {"role": "assistant", "message_id": "a1", "content": "reply"})
        storage.finish(cid, turn)
        storage.begin(cid, "fork", edit_message_id="u1", message={"role": "user", "message_id": "u2", "content": "edited"})
        drafts = storage.root / "drafts"
        drafts.mkdir()
        (drafts / "new.json").write_text(json.dumps({"media": [refs[1]]}))
        with store._lock:
            index = store._load_index()
            for reference in refs[:3]:
                index["media"][reference["media_id"]]["updated_at"] = (datetime.now() - timedelta(days=2)).isoformat()
            store._save_index(index)
        removed = collect(storage)
        self.assertEqual(removed, [refs[2]["media_id"]])
        for reference in [refs[0], refs[1], refs[3]]:
            self.assertIsNotNone(store.load_bytes_by_media_id(reference["media_id"]))

    def test_media_stores_share_the_same_index_lock(self):
        first, second = MediaStore(self.root), MediaStore(self.root)
        self.assertIs(first._lock, second._lock)

if __name__ == "__main__":
    unittest.main()
