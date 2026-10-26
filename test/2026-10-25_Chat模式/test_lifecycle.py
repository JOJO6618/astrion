"""Workspace-free navigation, quota sharing and all-branch media ownership."""
from datetime import datetime, timedelta
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

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
        service = DesktopNavigation(str(self.root / "navigation.json"))
        payload = {"renderer_id": "renderer", "conversation_kind": "chat", "workspace_id": ""}
        state = service.command("user", "attach1", "attach", payload)["state"]
        key = state["tabs"][0]["key"]
        for i in range(3):
            state = service.command("user", f"attach{i+2}", "attach", {
                **payload, "seen_navigation_id": state["navigation"]["id"]})["state"]
            self.assertEqual(len(state["tabs"]), 1)
            self.assertEqual(state["tabs"][0]["key"], key)
            self.assertEqual(state["tabs"][0]["workspace_id"], "")

    def test_pure_chat_page_resume_and_empty_bind(self):
        service = DesktopNavigation(str(self.root / "navigation.json"))
        service.command("user", "page", "attach", {"renderer_id": "renderer", "page": "/settings/chat"})
        state = service.command("user", "resume", "resume", {"conversation_kind": "chat", "workspace_id": ""})["state"]
        key = state["tabs"][0]["key"]
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

    def test_chat_and_agent_share_account_quota_without_workspace(self):
        from modules.chat_mode.quota import tracker_for
        from server.context.usage import get_or_create_usage_tracker
        from server import state
        personal = self.root / "user" / "personal"
        manager = SimpleNamespace(get_user=lambda username: SimpleNamespace(username=username, role="user"),
                                  _personal_root=lambda username: personal)
        with patch.object(state, "user_manager", manager), patch.object(state, "usage_trackers", {}):
            chat = tracker_for(personal / "chat", "user")
            agent = get_or_create_usage_tracker("user")
            self.assertIs(chat, agent)
            chat.check_and_increment("fast")
            self.assertEqual(agent.get_stats()["quotas"]["fast"]["count"], 1)
        self.assertFalse((self.root / "user" / "projects").exists())


if __name__ == "__main__":
    unittest.main()
