"""Exercise the actual Agent task/tool loop using a Chat port and a fake API."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from modules.chat_mode.storage import ChatStorage
from modules.chat_mode.runtime import ChatRuntime
from server.chat_flow_task_main import handle_task_with_sender

PROFILE = {"key": "synthetic", "name": "Synthetic", "api_protocol": "chat",
           "context_window": 128000, "fast": {"model_id": "synthetic"}}


class FakeAPI:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.api_protocol = "chat"
        self.last_responses_reasoning_items = None
        self.thinking_mode = True

    def apply_profile(self, profile):
        pass

    def get_current_thinking_mode(self):
        return self.thinking_mode

    def update_context_budget(self, *args):
        pass

    def _safe_tool_arguments_parse(self, arguments, function_name):
        return True, json.loads(arguments), None

    async def chat(self, messages, tools, stream=True):
        self.requests.append(deepcopy(messages))
        response = self.responses.pop(0)
        if callable(response):
            response = response()
        for delta in response:
            yield {"choices": [{"delta": delta, "finish_reason": None}]}


class SharedLoopTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = ChatStorage(self.root / "chat")
        self.cid = self.store.create(model_key="synthetic")
        self.turn_id = self.store.begin(self.cid, "send", message={
            "role": "user", "message_id": "user1", "content": "hello",
        })["value"]["id"]
        self.events = []

    def runtime(self, responses):
        api = FakeAPI(responses)
        with patch("modules.chat_mode.runtime.get_model_profile", return_value=PROFILE):
            runtime = ChatRuntime(self.store, self.cid, self.turn_id,
                                  preferences_dir=self.root, username="synthetic", api_client=api)
        return runtime, api

    async def run_loop(self, runtime):
        with patch("server.chat_flow_task_main.get_model_profile", return_value=PROFILE), \
             patch("server.chat_flow_task_main.get_model_context_window", return_value=128000), \
             patch("modules.api_pricing.capture_price", return_value=None), \
             patch("modules.exchange_rates.get_rate", return_value=None), \
             patch("modules.chat_mode.prompts.load_personalization_config", return_value={}), \
             patch("server.chat_flow_task_main._prepare_hidden_versioning_baseline_for_first_input", side_effect=AssertionError("Chat versioned project")), \
             patch("server.chat_flow_task_main._record_hidden_versioning_checkpoint_after_run", side_effect=AssertionError("Chat checkpointed project")):
            await handle_task_with_sender(runtime, None, "hello", [],
                                          lambda e, d: self.events.append((e, d)),
                                          "synthetic-task", "synthetic")

    async def test_plain_response_uses_shared_loop(self):
        runtime, api = self.runtime([[{"content": "Hello "}, {"content": "there"}]])
        await self.run_loop(runtime)
        messages = self.store.project(self.cid)["messages"]
        self.assertEqual([m["role"] for m in messages], ["user", "assistant"])
        self.assertEqual(messages[-1]["content"], "Hello there")
        self.assertEqual(sum(m["role"] == "user" for m in api.requests[0]), 1)
        self.assertTrue(any(e == "task_complete" for e, _ in self.events))
        self.assertIsNone(runtime.context_manager.project_path)
        self.assertFalse(hasattr(runtime, "file_manager"))
        self.assertFalse(hasattr(runtime, "terminal_manager"))

    async def test_tool_batch_uses_shared_tool_loop(self):
        runtime, api = self.runtime([
            [{"content": "Remembering", "tool_calls": [{"index": 0, "id": "call1", "type": "function",
               "function": {"name": "update_memory", "arguments": '{"operation":"append","content":"likes tea"}'}}]}],
            [{"content": "Saved"}],
        ])
        await self.run_loop(runtime)
        messages = self.store.project(self.cid)["messages"]
        self.assertEqual([m["role"] for m in messages], ["user", "assistant", "tool", "assistant"])
        self.assertEqual(messages[2]["tool_call_id"], "call1")
        self.assertEqual(runtime.memory_manager.snapshot()["global"], ["likes tea"])
        self.assertEqual(len(api.requests), 2)
        self.assertIn("likes tea", api.requests[1][0]["content"])

    async def test_edits_and_network_notice_refresh_before_next_request(self):
        runtime, api = self.runtime([
            [{"content": "before tool", "tool_calls": [{"index": 0, "id": "call1", "type": "function",
                "function": {"name": "read_chat_memory", "arguments": '{"name":"missing"}'}}]}],
            [{"content": "done"}],
        ])
        execute = runtime.handle_tool_call
        async def tool_with_edit(name, args):
            doc = self.store.project(self.cid)
            aid = next(m["message_id"] for m in doc["messages"] if m["role"] == "assistant")
            self.store.edit(self.cid, "edit", aid, "edited during tool")
            self.store.set_network(self.cid, "off", False)
            return await execute(name, args)
        runtime.handle_tool_call = tool_with_edit
        await self.run_loop(runtime)
        second = api.requests[1]
        self.assertEqual(next(m["content"] for m in second if m["role"] == "assistant"), "edited during tool")
        roles = [m["role"] for m in second]
        self.assertEqual(roles[-3:], ["assistant", "tool", "user"])
        self.assertIn("联网已关闭", second[-1]["content"])
        self.assertFalse(self.store.read(self.cid)["metadata"]["pending_network_notices"])


if __name__ == "__main__":
    unittest.main()
