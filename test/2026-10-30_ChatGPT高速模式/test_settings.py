"""Use real conversation transactions for Agent and Chat settings."""
import inspect
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask
from modules.chat_mode.configuration import configure_conversation
from modules.chat_mode.runtime import ChatRuntime
from modules.chat_mode.storage import ChatStorage
from server.chat.fast_mode import update_chatgpt_fast_mode
from server.runtime.context import TaskParams
from utils.conversation_manager import ConversationManager

PROFILE = {"responses_auth": "codex_oauth", "supports_chatgpt_fast_mode": True}


class SettingTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manager = ConversationManager(str(self.root / "agent"))
        self.cid = self.manager.create_conversation(
            project_path=str(self.root), model_key="synthetic", initial_messages=[
                {"role": "user", "message_id": "u", "content": "preserve this"},
            ],
        )
        self.context = SimpleNamespace(
            current_conversation_id=self.cid, conversation_manager=self.manager,
            conversation_metadata={},
        )
        self.terminal = SimpleNamespace(context_manager=self.context, api_client=SimpleNamespace())
        self.workspace = SimpleNamespace(workspace_id="ws")
        self.app = Flask(__name__)
        raw = inspect.unwrap(update_chatgpt_fast_mode)
        self.app.add_url_rule("/fast", view_func=lambda: raw(
            self.terminal, self.workspace, "synthetic"), methods=["POST"])
        self.http = self.app.test_client()
        for item in [patch("server.chat.fast_mode.get_model_profile", return_value=PROFILE),
                     patch("server.chat.fast_mode.task_manager._tasks", {}),
                     patch("server.chat.fast_mode.is_main_task_gate_busy", return_value=False)]:
            item.start()
            self.addCleanup(item.stop)

    def post(self, enabled, **kwargs):
        return self.http.post("/fast", json={"enabled": enabled, "conversation_id": self.cid, **kwargs})

    def test_agent_setting_round_trips_without_changing_messages(self):
        before = self.manager.read_conversation(self.cid)["messages"]
        self.assertEqual(self.post(True).status_code, 200)
        document = self.manager.read_conversation(self.cid)
        self.assertTrue(document["metadata"]["chatgpt_fast_mode"])
        self.assertEqual(document["messages"], before)
        self.assertTrue(self.terminal.api_client.chatgpt_fast_mode)
        self.assertTrue(self.context.conversation_metadata["chatgpt_fast_mode"])
        self.assertEqual(self.post(False).status_code, 200)
        self.assertFalse(self.manager.read_conversation(self.cid)["metadata"]["chatgpt_fast_mode"])

    def test_busy_gate_or_pending_task_rejects_setting(self):
        with patch("server.chat.fast_mode.is_main_task_gate_busy", return_value=True):
            self.assertEqual(self.post(True).status_code, 409)
        pending = SimpleNamespace(username="synthetic", conversation_id=self.cid,
                                  workspace_id="ws", status="pending")
        with patch("server.chat.fast_mode.task_manager._tasks", {"task": pending}):
            self.assertEqual(self.post(True).status_code, 409)
        self.assertNotIn("chatgpt_fast_mode", self.manager.read_conversation(self.cid)["metadata"])

    def test_target_and_type_validation(self):
        self.assertEqual(self.post(True, conversation_id="other").status_code, 404)
        for value in ["true", 1, None]:
            self.assertEqual(self.post(value).status_code, 400)

    def test_unsupported_model_cannot_enable_fast(self):
        with patch("server.chat.fast_mode.get_model_profile", return_value={}):
            self.assertEqual(self.post(True).status_code, 400)
            self.assertEqual(self.post(False).status_code, 200)

    def test_failed_disk_write_does_not_change_runtime(self):
        self.app.testing = True
        self.terminal.api_client.chatgpt_fast_mode = False
        with patch.object(self.manager, "mutate_conversation", side_effect=OSError("synthetic disk failure")):
            with self.assertRaises(OSError):
                self.post(True)
        self.assertFalse(self.terminal.api_client.chatgpt_fast_mode)
        self.assertNotIn("chatgpt_fast_mode", self.context.conversation_metadata)

    def test_chat_persistence_runtime_and_boolean_validation(self):
        storage = ChatStorage(self.root / "chat")
        cid = storage.create(model_key="synthetic", chatgpt_fast_mode=True)
        self.assertTrue(storage.read(cid)["metadata"]["chatgpt_fast_mode"])
        with patch("modules.chat_mode.configuration.get_model_profile", return_value=PROFILE):
            configure_conversation(storage, cid, {"chatgpt_fast_mode": False})
            self.assertFalse(storage.read(cid)["metadata"]["chatgpt_fast_mode"])
            configure_conversation(storage, cid, {"chatgpt_fast_mode": True})
            with self.assertRaises(ValueError):
                configure_conversation(storage, cid, {"chatgpt_fast_mode": "false"})
        client = SimpleNamespace(apply_profile=lambda profile: None)
        runtime = SimpleNamespace(model_key="synthetic", api_client=client,
                                  thinking_mode=True, reasoning_effort="high",
                                  context_manager=SimpleNamespace(conversation_metadata={"chatgpt_fast_mode": True}))
        ChatRuntime.apply_model_profile(runtime, PROFILE)
        self.assertTrue(client.chatgpt_fast_mode)
        self.assertEqual(client.reasoning_effort, "high")

    def test_chat_runtime_params_accept_boolean_only(self):
        TaskParams(chat_settings={"chatgpt_fast_mode": True}).validate()
        with self.assertRaises(ValueError):
            TaskParams(chat_settings={"chatgpt_fast_mode": "false"}).validate()
