from __future__ import annotations

import asyncio
from copy import deepcopy
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from server.compression_commit import commit_compression
from server.deep_compression import _clear_compression_state_on_error, _generate_summary
from server.state import stop_flags
from server.tasks.queue_state import finish_queue, load_queue, queue_snapshot, consume_selected_message


class MemoryManager:
    def __init__(self, messages):
        self._io_lock = threading.RLock()
        self.data = {"messages": deepcopy(messages), "metadata": {},
                     "token_statistics": {"cumulative_total_tokens": 123}}

    def load_conversation(self, cid):
        return deepcopy(self.data)

    def _get_conversation_file_path(self, cid):
        return cid

    def _validate_token_statistics(self, data):
        return data

    def _atomic_write_json(self, path, data):
        self.data = deepcopy(data)

    def _update_index(self, cid, data):
        pass


class CompressionLifecycleTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.original = [{"role": "user", "content": "original", "message_id": "m1"}]
        self.manager = MemoryManager(self.original)
        self.cm = SimpleNamespace(conversation_history=deepcopy(self.original), conversation_metadata={})

    def commit(self, **kwargs):
        return commit_compression(self.cm, self.manager, "conv_test", self.original,
                                  {"compression_count": 1}, round_index=1, now="2026-10-04",
                                  guide_message="summary guide", **kwargs)

    async def test_success_marks_only_snapshot_and_commits_guide_together(self):
        self.manager.data["messages"].append({"role": "user", "content": "new input", "message_id": "m2"})
        self.manager.data["metadata"]["runtime_message_queue"] = {"pending": [{"text": "queued"}]}
        self.assertEqual(self.commit(), 1)
        messages = self.manager.data["messages"]
        self.assertTrue(messages[0]["metadata"]["deep_compacted"])
        self.assertNotIn("deep_compacted", messages[1].get("metadata", {}))
        self.assertEqual(messages[2]["content"], "summary guide")
        self.assertEqual(self.manager.data["metadata"]["compression_count"], 1)
        self.assertEqual(self.manager.data["metadata"]["runtime_message_queue"]["pending"][0]["text"], "queued")
        self.assertEqual(self.manager.data["token_statistics"]["cumulative_total_tokens"], 123)
        self.assertEqual(self.manager.data["token_statistics"]["current_context_tokens"], 0)

    async def test_stop_before_commit_leaves_original_messages_unchanged(self):
        stop_flags["compression-test"] = {"task": asyncio.current_task(), "stop": True}
        try:
            with self.assertRaises(asyncio.CancelledError):
                self.commit()
            self.assertEqual(self.manager.data["messages"], self.original)
            self.assertEqual(self.cm.conversation_history, self.original)
            self.assertNotIn("compression_count", self.manager.data["metadata"])
        finally:
            stop_flags.pop("compression-test", None)

    async def test_other_tasks_stop_does_not_cancel_this_compression(self):
        stop_flags["unrelated-test"] = {"task": object(), "stop": True}
        try:
            self.assertEqual(self.commit(), 1)
        finally:
            stop_flags.pop("unrelated-test", None)

    async def test_write_failure_does_not_mark_memory_history(self):
        with patch.object(self.manager, "_atomic_write_json", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                self.commit()
        self.assertEqual(self.cm.conversation_history, self.original)
        self.assertEqual(self.manager.data["messages"], self.original)

    async def test_edited_snapshot_cannot_be_compacted_with_stale_summary(self):
        self.manager.data["messages"][0]["content"] = "edited"
        with self.assertRaises(RuntimeError):
            self.commit()
        self.assertNotIn("deep_compacted", self.manager.data["messages"][0].get("metadata", {}))

    async def test_summary_cancel_closes_stream_and_does_not_retry(self):
        started = asyncio.Event()
        closed = asyncio.Event()
        calls = []

        async def chat(*args, **kwargs):
            calls.append(True)
            try:
                started.set()
                await asyncio.Event().wait()
                yield {}
            finally:
                closed.set()

        terminal = SimpleNamespace(build_context=lambda: {}, build_messages=lambda *a: [],
                                   define_tools=lambda: [], api_client=SimpleNamespace(chat=chat))
        task = asyncio.create_task(_generate_summary(terminal, "summary", retries=5))
        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(closed.is_set())
        self.assertEqual(len(calls), 1)

    async def test_summary_stream_closes_on_success_and_error(self):
        for failure in (False, True):
            with self.subTest(failure=failure):
                calls = []

                class Stream:
                    def __init__(self):
                        self.closed = False
                        self.sent = False

                    def __aiter__(self):
                        return self

                    async def __anext__(self):
                        if failure:
                            raise RuntimeError("stream failure")
                        if self.sent:
                            raise StopAsyncIteration
                        self.sent = True
                        return {"choices": [{"delta": {"content": "summary"}}]}

                    async def aclose(self):
                        self.closed = True

                def chat(*args, **kwargs):
                    stream = Stream()
                    calls.append(stream)
                    return stream

                terminal = SimpleNamespace(
                    build_context=lambda: {}, build_messages=lambda *a: [],
                    define_tools=lambda: [], api_client=SimpleNamespace(chat=chat))
                summary, reason = await _generate_summary(terminal, "summary", retries=2)
                self.assertTrue(all(stream.closed for stream in calls))
                self.assertEqual(len(calls), 2 if failure else 1)
                if failure:
                    self.assertEqual(reason, "stream failure")
                else:
                    self.assertEqual(summary, "summary")
                    self.assertIsNone(reason)

    async def test_summary_stop_flag_closes_suspended_generator(self):
        closed = asyncio.Event()

        async def chat(*args, **kwargs):
            try:
                stop_flags["summary-stop-test"] = {
                    "task": asyncio.current_task(), "stop": True}
                yield {"choices": [{"delta": {"content": "discard"}}]}
            finally:
                closed.set()

        terminal = SimpleNamespace(
            build_context=lambda: {}, build_messages=lambda *a: [],
            define_tools=lambda: [], api_client=SimpleNamespace(chat=chat))
        try:
            with self.assertRaises(asyncio.CancelledError):
                await _generate_summary(terminal, "summary")
            self.assertTrue(closed.is_set())
        finally:
            stop_flags.pop("summary-stop-test", None)

    async def test_cancel_cleanup_clears_compression_status(self):
        states = []
        events = []
        cm = SimpleNamespace(current_conversation_id="conv_test", set_compression_state=lambda **v: states.append(v))

        @_clear_compression_state_on_error
        async def execute(**kwargs):
            raise asyncio.CancelledError()

        with self.assertRaises(asyncio.CancelledError):
            await execute(web_terminal=SimpleNamespace(context_manager=cm), conversation_id="conv_test",
                          mode="manual", sender=lambda *args: events.append(args))
        self.assertEqual(states, [{"in_progress": False}])
        self.assertFalse(events[-1][1]["in_progress"])

    async def test_stopped_queue_survives_reload_with_guidance_attachments(self):
        rec = SimpleNamespace(queue_manager=self.manager, conversation_id="conv_test",
                              runtime_pending_queue=[{"id": "q1", "text": "presend"}],
                              runtime_guidance_queue=[{"id": "g1", "text": "guide", "files": ["a.py"]}],
                              runtime_queue_paused=False, task_params=SimpleNamespace(queued_message_id=None))
        finish_queue(rec, paused=True)
        load_queue(rec)
        self.assertEqual(len(rec.runtime_pending_queue), 2)
        self.assertTrue(rec.runtime_queue_paused)
        self.assertEqual(rec.runtime_pending_queue[1]["files"], ["a.py"])
        self.assertEqual(queue_snapshot(self.manager, "conv_test")["messages"][1]["id"], "g1")
        rec.task_params.queued_message_id = "g1"
        load_queue(rec)
        # 受理后、真正写入用户消息之前仍在队列，提前停止不能丢它。
        self.assertEqual(len(rec.runtime_pending_queue), 2)
        consume_selected_message(rec)
        self.assertEqual([item["id"] for item in rec.runtime_pending_queue], ["q1"])


if __name__ == "__main__":
    unittest.main()
