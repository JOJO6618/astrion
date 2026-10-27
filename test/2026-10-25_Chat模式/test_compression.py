"""Exercise summary commits across real tree branches without a model service."""
import asyncio
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from modules.chat_mode.storage import ChatStorage
from modules.chat_mode.tree import MessageTree
from modules.chat_mode.compression import maybe_compress, api_history


class CompressionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        preferences = patch("modules.chat_mode.compression.load_personalization_config",
                            return_value={"chat_deep_compress_trigger_tokens": 16000})
        preferences.start()
        self.addCleanup(preferences.stop)
        self.store = ChatStorage(Path(self.temp.name))
        self.cid = self.store.create()
        turn = self.store.begin(self.cid, "first", message={"message_id": "u1", "role": "user", "content": "中" * 20000})["value"]["id"]
        self.store.append(self.cid, turn, {"message_id": "a1", "role": "assistant", "content": "before tools", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "web_search", "arguments": "{}"}}]})
        self.store.append(self.cid, turn, {"message_id": "t1", "role": "tool", "tool_call_id": "c1", "content": "found"})
        self.store.append(self.cid, turn, {"message_id": "a2", "role": "assistant", "content": "first answer"})
        self.store.finish(self.cid, turn)
        self.turn = self.store.begin(self.cid, "second", parent_id="a2", message={"message_id": "u2", "role": "user", "content": "continue"})["value"]["id"]
        context = SimpleNamespace(storage=self.store, current_conversation_id=self.cid)
        context.refresh = lambda: setattr(context, "conversation_history", MessageTree(self.store.read(self.cid)).path())
        context.build_api_history = lambda: [{"role": m["role"], "content": m["content"]} for m in context.conversation_history]
        self.runtime = SimpleNamespace(storage=self.store, conversation_id=self.cid, turn_id=self.turn,
                                       context_manager=context, model_profile={"context_window": 20000},
                                       preferences_dir=Path(self.temp.name), api_client=object(),
                                       thinking_mode=False, record_model_call=lambda _: (True, None))
        self.events = []

    async def test_summary_preserves_tree_and_protocol_batch_and_is_reused(self):
        captured = []
        async def summarize(_, __, messages, **kwargs):
            captured.append(deepcopy(messages))
            return "short summary"
        before = self.store.read(self.cid)["messages"]
        with patch("modules.chat_mode.summarize.summarize", summarize):
            await maybe_compress(self.runtime, lambda *event: self.events.append(event))
            await maybe_compress(self.runtime, lambda *event: self.events.append(event))
        self.assertEqual(len(captured), 1)
        self.assertEqual([m["message_id"] for m in captured[0]], ["u1", "a1", "t1", "a2"])
        self.assertEqual(self.store.read(self.cid)["messages"], before)
        history = api_history(self.runtime.context_manager)
        self.assertEqual(history[0]["role"], "system")
        self.assertIn("short summary", history[0]["content"])
        self.assertEqual(history[-1]["content"], "continue")
        self.assertFalse(self.events[-1][1]["in_progress"])

    async def test_concurrent_prefix_edit_prevents_stale_summary_commit(self):
        async def summarize(*_, **kwargs):
            self.store.edit(self.cid, "edit-during-summary", "u1", "corrected")
            return "outdated summary"
        with patch("modules.chat_mode.summarize.summarize", summarize):
            with self.assertRaisesRegex(RuntimeError, "changed while summarizing"):
                await maybe_compress(self.runtime, lambda *event: self.events.append(event))
        self.assertEqual(self.store.read(self.cid)["metadata"]["chat"]["summaries"], [])
        self.assertEqual(self.store.read(self.cid)["messages"][0]["content"], "corrected")
        self.assertFalse(self.events[-1][1]["in_progress"])

    async def test_cancellation_keeps_original_history_and_clears_compression_state(self):
        async def summarize(*_, **kwargs):
            raise asyncio.CancelledError()
        with patch("modules.chat_mode.summarize.summarize", summarize):
            with self.assertRaises(asyncio.CancelledError):
                await maybe_compress(self.runtime, lambda *event: self.events.append(event))
        self.assertEqual(self.store.read(self.cid)["metadata"]["chat"]["summaries"], [])
        self.assertEqual(len(self.store.read(self.cid)["messages"]), 5)
        self.assertFalse(self.events[-1][1]["in_progress"])
