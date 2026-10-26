"""Task acceptance, worker cancellation and snapshots with isolated Chat data."""
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch

from test_shared_loop import FakeAPI, PROFILE
from modules.chat_mode.runtime import ChatRuntime
from modules.chat_mode.storage import ChatStorage
from server.chat_mode_scope import ChatScope
from server.runtime.context import RuntimeContext, TaskParams, TrustedPrincipal
from server.tasks.models import TaskManager, task_public_payload


class BlockingAPI(FakeAPI):
    def __init__(self, deltas):
        super().__init__([])
        self.deltas = deltas
        self.received = threading.Event()

    async def chat(self, messages, tools, stream=True):
        for delta in self.deltas:
            yield {"choices": [{"delta": delta, "finish_reason": None}]}
        self.received.set()
        await asyncio.Event().wait()


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.scope = ChatScope("synthetic", self.root / "chat", self.root)
        self.store = ChatStorage(self.scope.root)
        self.cid = self.store.create(model_key="synthetic")
        self.manager = TaskManager()
        self.api = FakeAPI([[{"content": "hello"}]])
        patches = [
            patch("server.tasks.chat_mode.scope_for_principal", return_value=self.scope),
            patch("server.chat_mode_scope.scope_for_principal", return_value=self.scope),
            patch("modules.chat_mode.runtime.get_model_profile", return_value=PROFILE),
            patch("server.chat_flow_task_main.get_model_profile", return_value=PROFILE),
            patch("server.chat_flow_task_main.get_model_context_window", return_value=128000),
            patch("modules.api_pricing.capture_price", return_value=None),
            patch("modules.exchange_rates.get_rate", return_value=None),
            patch("modules.chat_mode.prompts.load_personalization_config", return_value={}),
            patch("server.context.get_user_resources", side_effect=AssertionError("Created Agent resources")),
            patch("modules.chat_mode.runtime.ChatRuntime", side_effect=self.runtime),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def runtime(self, *args, **kwargs):
        return ChatRuntime(*args, **kwargs, api_client=self.api)

    def context(self, operation="send"):
        return RuntimeContext(TrustedPrincipal("synthetic", None, conversation_kind="chat"),
                              TaskParams(message="hello", conversation_id=self.cid,
                                         chat_operation_id=operation))

    def start(self, operation="send"):
        return self.manager.create_chat_task(self.context(operation), conversation_id=self.cid)

    def join(self, record):
        record.thread.join(8)
        self.assertFalse(record.thread.is_alive(), "Chat worker did not finish")
        self.assertIn(record.status, {"succeeded", "stopped", "failed"})

    def test_real_task_uses_shared_driver_without_workspace(self):
        record = self.start()
        self.join(record)
        self.assertEqual(record.status, "succeeded", record.error)
        self.assertEqual([m["role"] for m in self.store.project(self.cid)["messages"]],
                         ["user", "assistant"])
        payload = task_public_payload(record)
        self.assertEqual(payload["conversation_kind"], "chat")
        self.assertIsNone(payload["workspace_label"])
        snapshot = self.manager.capture_display("synthetic", self.cid, self.store.manager,
                                                workspace_id=None)
        self.assertEqual(snapshot["display"]["task"]["status"], "succeeded")
        self.assertEqual(snapshot["display"]["next_event_idx"], record.next_event_idx)
        self.assertEqual(self.start().task_id, record.task_id)

    def test_hard_cancel_keeps_received_text_and_thinking(self):
        self.api = BlockingAPI([{"reasoning_content": "thought"}, {"content": "received"}])
        record = self.start()
        self.assertTrue(self.api.received.wait(8))
        self.manager.cancel_task("synthetic", record.task_id)
        self.join(record)
        self.assertEqual(record.status, "stopped", record.error)
        messages = self.store.project(self.cid)["messages"]
        self.assertEqual(messages[-1]["content"], "received")
        self.assertEqual(messages[-1]["reasoning_content"], "thought")
        self.assertTrue(messages[-1]["metadata"]["stopped"])
        self.assertIsNone(self.store.read(self.cid)["metadata"]["chat"]["active_turn_id"])

    def test_concurrent_send_is_rejected_and_partial_tools_discarded(self):
        self.api = BlockingAPI([{"content": "prefix", "tool_calls": [
            {"index": 0, "id": "c1", "type": "function", "function": {
                "name": "update_memory", "arguments": '{"operation":'}}]}])
        record = self.start()
        self.assertTrue(self.api.received.wait(8))
        with self.assertRaises(RuntimeError):
            self.start("other-send")
        self.manager.cancel_task("synthetic", record.task_id)
        self.join(record)
        self.assertEqual([m["role"] for m in self.store.project(self.cid)["messages"]], ["user"])

    def test_cancel_complete_tool_calls_fills_every_missing_result(self):
        self.api = FakeAPI([[{"content": "Using tools", "tool_calls": [
            {"index": index, "id": f"call{index}", "type": "function", "function": {
                "name": "update_memory", "arguments": '{"operation":"append","content":"test"}'}}
            for index in range(2)]}]])
        entered = threading.Event()
        async def blocking_tool(*args, **kwargs):
            entered.set()
            await asyncio.Future()
        with patch.object(ChatRuntime, "handle_tool_call", side_effect=blocking_tool):
            record = self.start()
            self.assertTrue(entered.wait(8), record.error)
            self.manager.cancel_task("synthetic", record.task_id)
            self.join(record)
        messages = self.store.project(self.cid)["messages"]
        calls = [call["id"] for message in messages for call in message.get("tool_calls", [])]
        results = [message["tool_call_id"] for message in messages if message["role"] == "tool"]
        self.assertEqual(set(calls), {"call0", "call1"})
        self.assertCountEqual(results, calls)
        self.assertEqual(record.status, "stopped")

    def test_persistence_failure_still_finishes_record(self):
        with patch("modules.chat_mode.context.ChatContext.auto_save_conversation",
                   side_effect=OSError("synthetic disk failure")):
            record = self.start()
            self.join(record)
        self.assertEqual(record.status, "failed")
        self.assertIn("disk failure", record.error)


if __name__ == "__main__":
    unittest.main()
