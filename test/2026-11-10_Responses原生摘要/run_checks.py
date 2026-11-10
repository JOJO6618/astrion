"""Responses 原生思考摘要离线回归（不发起任何模型请求）。

覆盖：
1. translate 层：每个 summary 小节完成时，chunk 带 ``_reasoning_summary``，
   正文 reasoning_content 仍是完整拼装文本（多段之间空行分隔）。
2. completed 兜底冲刷同样带标记。
3. NativeSummaryBlock：只发布原生小节（忽略思考正文）、去重、revision 递增、
   多行文本折成单行、取消后不再发布。
4. stream_loop 内部字段在透传前端前已 pop。
"""
import asyncio
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from utils.api_client.responses.translate import responses_sse_to_chat_chunks  # noqa: E402
from modules.thinking_summary import NativeSummaryBlock  # noqa: E402


def sse(*events):
    async def gen():
        for event in events:
            yield "data: " + json.dumps(event, ensure_ascii=False)
    return gen()


def collect(gen):
    async def run():
        return [chunk async for chunk in gen]
    return asyncio.run(run())


class TranslateSummaryTest(unittest.TestCase):
    def test_each_part_marked_and_body_joined(self):
        events = [
            {"type": "response.created"},
            {
                "type": "response.reasoning_summary_text.delta",
                "item_id": "r1", "summary_index": 0, "delta": "Reviewing new ",
            },
            {
                "type": "response.reasoning_summary_text.delta",
                "item_id": "r1", "summary_index": 0, "delta": "fixture changes",
            },
            {
                "type": "response.reasoning_summary_text.done",
                "item_id": "r1", "summary_index": 0,
                "text": "**Reviewing new fixture changes**",
            },
            {
                "type": "response.reasoning_summary_text.delta",
                "item_id": "r1", "summary_index": 1, "delta": "Checking outputs",
            },
            {
                "type": "response.reasoning_summary_text.done",
                "item_id": "r1", "summary_index": 1, "text": "Checking outputs",
            },
            {"type": "response.completed", "response": {"output": []}},
        ]
        chunks = collect(responses_sse_to_chat_chunks(sse(*events)))
        marked = [c["_reasoning_summary"] for c in chunks if "_reasoning_summary" in c]
        self.assertEqual(marked, ["Reviewing new fixture changes", "Checking outputs"])
        bodies = [
            c["choices"][0]["delta"].get("reasoning_content")
            for c in chunks if c.get("choices")
        ]
        self.assertIn("Reviewing new fixture changes", bodies)
        self.assertIn("\n\nChecking outputs", bodies)

    def test_completed_flush_marks_buffered_part(self):
        events = [
            {
                "type": "response.reasoning_summary_text.delta",
                "item_id": "r2", "summary_index": 0, "delta": "Buffered only",
            },
            {"type": "response.completed", "response": {"output": []}},
        ]
        chunks = collect(responses_sse_to_chat_chunks(sse(*events)))
        marked = [c["_reasoning_summary"] for c in chunks if "_reasoning_summary" in c]
        self.assertEqual(marked, ["Buffered only"])


class NativeSummaryBlockTest(unittest.TestCase):
    def _block(self, events):
        work = types.SimpleNamespace(published=[])
        return NativeSummaryBlock(
            work, object(), "conv", "msg", lambda event, data: events.append((event, data))
        )

    def test_publishes_only_native_parts(self):
        events = []
        block = self._block(events)
        with patch("modules.thinking_summary.save_summary"):
            block.feed("完整思考正文不应触发摘要")
            block.feed_native("Reviewing new fixture changes")
            block.feed_native("Reviewing new fixture changes")
            block.feed_native("Checking outputs\nwith more detail")
        published = [data for event, data in events if event == "thinking_summary"]
        self.assertEqual(
            [item["summary"] for item in published],
            ["Reviewing new fixture changes", "Checking outputs with more detail"],
        )
        self.assertEqual([item["revision"] for item in published], [1, 2])
        self.assertEqual(block.text, "Checking outputs with more detail")

    def test_cancel_blocks_later_parts(self):
        events = []
        block = self._block(events)
        block.cancel()
        with patch("modules.thinking_summary.save_summary"):
            block.feed_native("later")
        self.assertEqual(events, [])


class StreamLoopFieldTest(unittest.TestCase):
    def test_hidden_field_popped_before_forward(self):
        source = (ROOT / "server/chat_flow_stream_loop.py").read_text(encoding="utf-8")
        pop_at = source.index('data.pop("_native_summary", None)')
        forward_at = source.index("original_sender(event, data)")
        self.assertLess(pop_at, forward_at)


if __name__ == "__main__":
    unittest.main()
