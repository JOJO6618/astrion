from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from support import ROOT
from utils.api_client.responses.speed import ResponsesOutputTimer, build_responses_speed_sample
from utils.api_client.responses.translate import responses_sse_to_chat_chunks
from utils.token_usage import extract_usage_payload, normalize_usage_payload


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


class OutputTimerTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.timer = ResponsesOutputTimer(self.clock)

    def event(self, at, event, **data):
        self.clock.now = at
        self.timer.observe({"type": "response." + event, **data})

    def test_thinking_wait_and_completed_tail_are_excluded(self):
        self.event(0, "created")
        self.event(1, "output_item.added", item={"id": "r", "type": "reasoning"})
        self.event(80, "reasoning_summary_text.done", item_id="r", text="summary")
        self.event(100, "output_text.delta", item_id="m", delta="hello")
        self.event(102, "output_text.done", item_id="m", text="hello")
        self.event(103, "output_item.done", item={"id": "m", "type": "message"})
        self.event(120, "completed")
        self.assertEqual(self.timer.summary(), {"duration_ms": 2000})

    def test_buffered_function_counts_name_and_argument_generation(self):
        self.event(20, "output_item.added", output_index=1,
                   item={"id": "f", "type": "function_call", "name": "read_file"})
        self.event(21, "function_call_arguments.delta", item_id="f", delta='{"path":')
        self.event(25, "function_call_arguments.delta", item_id="f", delta='"a"}')
        self.event(26, "function_call_arguments.done", item_id="f", arguments='{"path":"a"}')
        self.event(27, "output_item.done", output_index=1,
                   item={"id": "f", "type": "function_call"})
        self.event(30, "completed")
        self.assertEqual(self.timer.summary(), {"duration_ms": 6000})

    def test_parallel_outputs_use_interval_union_and_exclude_between_round_thinking(self):
        self.event(0, "output_item.added", item={"id": "a", "type": "function_call", "name": "a"})
        self.event(1, "output_item.added", item={"id": "b", "type": "function_call", "name": "b"})
        self.event(2, "output_item.done", item={"id": "a", "type": "function_call"})
        self.event(3, "output_item.done", item={"id": "b", "type": "function_call"})
        self.event(4, "output_item.added", item={"id": "r", "type": "reasoning"})
        self.event(100, "output_text.delta", item_id="m", delta="answer")
        self.event(102, "output_text.done", item_id="m")
        self.event(110, "completed")
        self.assertEqual(self.timer.summary(), {"duration_ms": 5000})

    def test_reasoning_event_pauses_an_unclosed_output_interval(self):
        self.event(0, "output_text.delta", item_id="m", delta="first")
        self.event(2, "reasoning_text.delta", item_id="r", delta="thinking")
        self.event(100, "output_text.delta", item_id="m", delta="last")
        self.event(103, "output_item.done", item={"id": "m", "type": "message"})
        self.event(110, "completed")
        self.assertEqual(self.timer.summary(), {"duration_ms": 5000})

    def test_completion_closes_an_output_without_a_done_event(self):
        self.event(4, "function_call_arguments.delta", output_index=0, delta="{}")
        self.event(6, "completed")
        self.assertEqual(self.timer.summary(), {"duration_ms": 2000})

    def test_incomplete_or_thinking_only_stream_has_no_sample(self):
        self.event(1, "output_text.delta", item_id="m", delta="partial")
        self.event(5, "output_text.done", item_id="m")
        self.assertEqual(self.timer.summary(), {})
        timer = ResponsesOutputTimer(self.clock)
        timer.observe({"type": "response.reasoning_summary_text.done", "text": "summary"})
        self.clock.now = 10
        timer.observe({"type": "response.completed"})
        self.assertEqual(timer.summary(), {})


class UsageTests(unittest.TestCase):
    def test_reasoning_detail_survives_extraction_and_repeated_normalization(self):
        for value in (0, 90):
            usage = extract_usage_payload({"usage": {"input_tokens": 10, "output_tokens": 100,
                "output_tokens_details": {"reasoning_tokens": value}}})
            self.assertEqual(usage["reasoning_tokens"], value)
            self.assertEqual(normalize_usage_payload(usage), usage)
            self.assertEqual(build_responses_speed_sample(usage, {"duration_ms": 2000}),
                             {"output_tokens": 100 - value, "duration_ms": 2000})

    def test_unknown_or_invalid_reasoning_detail_is_skipped(self):
        for details in ({}, {"reasoning_tokens": None}, {"reasoning_tokens": -1},
                        {"reasoning_tokens": True}, {"reasoning_tokens": "invalid"},
                        {"reasoning_tokens": 101}, {"reasoning_tokens": 100}):
            usage = {"output_tokens": 100, "output_tokens_details": details}
            self.assertEqual(build_responses_speed_sample(usage, {"duration_ms": 2000}), {})

    def test_missing_or_invalid_duration_is_skipped(self):
        usage = {"output_tokens": 100, "output_tokens_details": {"reasoning_tokens": 90}}
        for timing in ({}, {"duration_ms": 0}, {"duration_ms": -1},
                       {"duration_ms": None}, {"duration_ms": True}):
            self.assertEqual(build_responses_speed_sample(usage, timing), {})


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def collect(self, events):
        clock = Clock()

        async def lines():
            for at, data in events:
                clock.now = at
                yield "data: " + json.dumps(data)

        timer = ResponsesOutputTimer(clock)
        with patch("utils.api_client.responses.translate.ResponsesOutputTimer", return_value=timer):
            return [chunk async for chunk in responses_sse_to_chat_chunks(
                lines(), required_map={"read_file": {"path"}},
            )]

    async def test_real_conversion_preserves_buffering_but_times_original_arguments(self):
        item = {"id": "f", "type": "function_call", "name": "read_file", "call_id": "call"}
        chunks = await self.collect([
            (1, {"type": "response.created"}),
            (20, {"type": "response.reasoning_summary_text.delta", "item_id": "r", "delta": "summary"}),
            (50, {"type": "response.reasoning_summary_text.done", "item_id": "r", "text": "summary"}),
            (60, {"type": "response.output_item.added", "item": item, "output_index": 1}),
            (61, {"type": "response.function_call_arguments.delta", "item_id": "f", "delta": '{"path":'}),
            (65, {"type": "response.function_call_arguments.delta", "item_id": "f", "delta": '"a","optional":null}'}),
            (66, {"type": "response.function_call_arguments.done", "item_id": "f"}),
            (67, {"type": "response.output_item.done", "item": {
                **item, "arguments": '{"path":"a","optional":null}'}}),
            (90, {"type": "response.completed", "response": {"usage": {
                "input_tokens": 10, "output_tokens": 1000,
                "output_tokens_details": {"reasoning_tokens": 940}}}}),
        ])
        calls = [call for chunk in chunks for choice in chunk["choices"]
                 for call in choice.get("delta", {}).get("tool_calls", [])]
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["function"]["arguments"], "")
        self.assertEqual(json.loads(calls[1]["function"]["arguments"]), {"path": "a"})
        self.assertEqual(chunks[-1]["_response_output_timing"], {"duration_ms": 6000})
        usage = extract_usage_payload(chunks[-1])
        self.assertEqual(usage["completion_tokens"], 1000)
        self.assertEqual(build_responses_speed_sample(usage, chunks[-1]["_response_output_timing"]),
                         {"output_tokens": 60, "duration_ms": 6000})

    async def test_text_then_thinking_then_tool_sums_output_intervals(self):
        chunks = await self.collect([
            (10, {"type": "response.output_text.delta", "item_id": "m", "delta": "hello"}),
            (12, {"type": "response.output_text.done", "item_id": "m"}),
            (13, {"type": "response.reasoning_text.delta", "item_id": "r", "delta": "thinking"}),
            (50, {"type": "response.output_item.added", "item": {
                "id": "f", "type": "function_call", "name": "read_file", "call_id": "call"}}),
            (53, {"type": "response.output_item.done", "item": {
                "id": "f", "type": "function_call", "name": "read_file", "arguments": "{}"}}),
            (60, {"type": "response.completed", "response": {"usage": {
                "input_tokens": 10, "output_tokens": 100,
                "output_tokens_details": {"reasoning_tokens": 75}}}}),
        ])
        self.assertEqual(chunks[-1]["_response_output_timing"], {"duration_ms": 5000})


if __name__ == "__main__":
    unittest.main()
