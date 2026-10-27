"""Persisted decisions and frozen exports; never call a real model/workspace."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from modules.chat_mode.storage import ChatStorage
from modules.chat_mode.handoff import register, read, select, decline
from modules.chat_mode.compression import select_summary
from server.runtime import TrustedPrincipal
from server.chat_mode.handoff_execution import freeze, export_materials


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.storage = ChatStorage(self.root / "chat")
        self.cid = self.storage.create(model_key="synthetic")
        from modules.chat_mode.handoff import presentation
        presentation(self.storage, self.cid, "fixture-view")
        self.turn = self.storage.begin(self.cid, "send", message={
            "role": "user", "message_id": "u1", "content": "Build an app"})["value"]["id"]
        self.storage.append(self.cid, self.turn, {"role": "assistant", "message_id": "a1",
            "content": 'Ready <suggest_agent reason="需要修改实际文件" />'})
        self.storage.finish(self.cid, self.turn)
        self.principal = TrustedPrincipal("synthetic", None, conversation_kind="chat")
        self.state = register(self.storage, self.cid, self.principal, schedule=False,
                              presentation_id="fixture-view")

    def test_registration_does_not_reset_deadline(self):
        repeated = register(self.storage, self.cid, self.principal, schedule=False)
        self.assertEqual(repeated, self.state)
        self.assertEqual(len(self.storage.read(self.cid)["messages"]), 2)

    def test_select_pauses_timeout_and_decline_is_once(self):
        select(self.storage, self.cid, "a1")
        with patch("modules.chat_mode.handoff.time.time", return_value=self.state["deadline"] + 100), \
             patch("server.runtime.runtime_service.create_task", return_value=SimpleNamespace(task_id="synthetic")) as create:
            unchanged = decline(self.storage, self.cid, self.principal, "a1", expired=True)
            self.assertEqual(unchanged["status"], "selecting")
            create.assert_not_called()
            self.assertEqual(decline(self.storage, self.cid, self.principal, "a1")["status"], "declined")
            decline(self.storage, self.cid, self.principal, "a1")
            self.assertEqual(create.call_count, 1)
            params = create.call_args.args[0].params
            self.assertEqual(params.chat_parent_id, "a1")
            self.assertIn("不切换", params.message)

    def test_timeout_on_changed_branch_does_not_send_to_old_path(self):
        self.storage.begin(self.cid, "later", parent_id="a1", message={
            "role": "user", "message_id": "u2", "content": "Another task"})
        with patch("server.runtime.runtime_service.create_task") as create, \
             patch("modules.chat_mode.handoff.time.time", return_value=self.state["deadline"] + 1):
            result = decline(self.storage, self.cid, self.principal, "a1", expired=True)
        self.assertEqual(result["status"], "declined")
        create.assert_not_called()

    def test_export_and_summary_use_same_frozen_path(self):
        select(self.storage, self.cid, "a1")
        snapshot = freeze(self.storage, self.cid, "a1")
        self.storage.edit(self.cid, "edit", "u1", "changed afterwards")
        self.assertEqual(freeze(self.storage, self.cid, "a1"), snapshot)
        project = self.root / "project"
        project.mkdir()
        target = export_materials(self.storage, snapshot, self.state,
                                  SimpleNamespace(project_path=project), "Frozen summary")
        review = (target / "conversation.md").read_text()
        self.assertIn("Build an app", review)
        self.assertNotIn("changed afterwards", review)
        self.assertNotIn("suggest_agent", review)
        self.assertEqual((target / "summary.md").read_text(), "Frozen summary\n")
        self.assertTrue(self.storage.read(self.cid))

    def test_media_export_accepts_shared_hash_identity(self):
        from utils.media_store import MediaStore
        store = MediaStore(self.storage.root)
        reference = store.store_bytes(b"synthetic-media", kind="image", mime_type="image/png")
        def attach(document):
            document["messages"][0]["media_refs"] = [reference]
        self.storage.manager.mutate_conversation(self.cid, attach)
        snapshot = freeze(self.storage, self.cid, "a1")
        project = self.root / "media-project"
        project.mkdir()
        target = export_materials(self.storage, snapshot, self.state,
                                  SimpleNamespace(project_path=project), "summary")
        self.assertEqual(next((target / "media").iterdir()).read_bytes(), b"synthetic-media")
        self.assertIn("sha256-", (target / "conversation.md").read_text())

    def test_cancel_summary_never_creates_agent(self):
        import asyncio
        import threading
        from server.chat_mode.handoff_execution import start, cancel_for_conversation, is_running
        from modules.chat_mode.handoff import update
        entered = threading.Event()
        async def blocked(*args, **kwargs):
            entered.set()
            await asyncio.Future()
        update(self.storage, self.cid, "a1", lambda state, _: state.update(status="preparing"))
        principal = TrustedPrincipal("synthetic", "workspace", host_mode=True)
        snapshot = freeze(self.storage, self.cid, "a1")
        with patch("modules.chat_mode.summarize.summarize", side_effect=blocked), \
             patch("server.chat_mode.handoff_execution.create_agent") as create:
            start(self.storage, self.cid, "a1", principal, SimpleNamespace(), snapshot)
            self.assertTrue(entered.wait(3))
            self.assertTrue(cancel_for_conversation(self.storage, self.cid))
            self.assertFalse(is_running(self.storage, self.cid, "a1"))
            create.assert_not_called()
        self.assertEqual(read(self.storage, self.cid, "a1")["status"], "failed")

    def test_successful_export_starts_linked_agent_once_and_retry_reuses_it(self):
        from server.chat_mode.handoff_execution import execute, HandoffWorker
        from modules.chat_mode.handoff import update
        project = self.root / "agent-project"
        project.mkdir()
        workspace = SimpleNamespace(project_path=project, data_dir=self.root / "agent-data", workspace_id="project")
        principal = TrustedPrincipal("synthetic", "project", host_mode=True)
        snapshot = freeze(self.storage, self.cid, "a1")
        async def summary(*args, **kwargs):
            return "A verified handoff summary"
        def admit(context):
            materials = project / ".astrion" / "handoff" / self.state["id"]
            self.assertTrue((materials / "summary.md").is_file())
            self.assertTrue((materials / "conversation.md").is_file())
            self.assertEqual(context.principal.conversation_kind, "agent")
            self.assertEqual(context.principal.workspace_id, "project")
            return SimpleNamespace(task_id="agent-task")
        with patch("modules.chat_mode.summarize.summarize", side_effect=summary), \
             patch("modules.personalization_manager.load_personalization_config", return_value={"default_model": "synthetic"}), \
             patch("server.runtime.runtime_service.create_task", side_effect=admit) as task:
            for _ in range(2):
                update(self.storage, self.cid, "a1", lambda state, _: state.update(status="preparing"))
                execute(self.storage, self.cid, "a1", principal, workspace, snapshot, HandoffWorker())
                self.assertEqual(read(self.storage, self.cid, "a1")["status"], "completed")
            self.assertEqual(task.call_count, 2)
        self.assertEqual(len(list((workspace.data_dir / "conversations").rglob("conv_*.json"))), 1)
        self.assertTrue(self.storage.read(self.cid))

    def test_estimate_does_not_encode_media(self):
        from modules.chat_mode.compression import estimate_tokens
        message = {"content": "测试", "media_refs": [{"kind": "image", "media_id": "id"}]}
        estimate = estimate_tokens([message])
        self.assertGreater(estimate, 2048)
        self.assertLess(estimate, 2200)

    def test_branch_summaries_match_ancestors_only_and_edit_invalidates(self):
        original = self.storage.read(self.cid)
        def add(document):
            document["metadata"]["chat"]["summaries"].append({
                "valid": True, "message_ids": ["u1", "a1"], "content": "old summary"})
        self.storage.manager.mutate_conversation(self.cid, add)
        document = self.storage.read(self.cid)
        self.assertIsNotNone(select_summary(document, document["messages"]))
        self.assertIsNone(select_summary(document, [{"message_id": "different"}]))
        self.storage.edit(self.cid, "edit", "u1", "edited")
        document = self.storage.read(self.cid)
        self.assertIsNone(select_summary(document, document["messages"]))
        self.assertEqual(len(original["messages"]), len(document["messages"]))


if __name__ == "__main__":
    unittest.main()
