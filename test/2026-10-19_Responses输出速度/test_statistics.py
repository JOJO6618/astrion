from __future__ import annotations

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from support import ROOT, RUNTIME
from utils.context_manager.token_mixin import TokenMixin
from utils.token_usage import extract_usage_payload

PRICE = {"model_key": "synthetic/speed", "rates": {"input": 2, "cache_read": 0.2, "output": 8}}
USAGE = {"prompt_tokens": 1000, "completion_tokens": 1000, "reasoning_tokens": 900,
         "total_tokens": 2000, "cached_input_tokens": 0}


class Context(TokenMixin):
    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.current_conversation_id = "conv_speed"
        self.counts = dict(total_input_tokens=0, total_output_tokens=0,
                           total_tokens=0, total_cached_input_tokens=0)
        self.conversation_manager = SimpleNamespace(
            get_token_statistics=lambda _: dict(self.counts),
            update_token_statistics=self.update_statistics,
        )
        self.reset_work_segment_speed_samples()

    def update_statistics(self, _, inp, out, total, **kwargs):
        self.counts["total_input_tokens"] += inp
        self.counts["total_output_tokens"] += out
        self.counts["total_tokens"] += total
        self.counts["total_cached_input_tokens"] += kwargs["cached_input_tokens"]
        return True

    def _increment_workspace_token_totals(self, *args):
        pass

    def safe_broadcast_token_update(self, **kwargs):
        pass


class StatisticsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=RUNTIME)
        self.addCleanup(self.directory.cleanup)
        self.context = Context(self.directory.name)

    def test_responses_speed_excludes_reasoning_but_ledger_and_totals_include_it(self):
        sample = {"output_tokens": 100, "duration_ms": 2000}
        self.assertTrue(self.context.apply_usage_statistics(
            USAGE, price=PRICE, request_id="r1", duration_ms=90000, ttft_ms=1000,
            speed_sample=sample))
        summary = self.context.consume_work_segment_speed_summary()
        self.assertEqual(summary["avg_tps"], 50)
        self.assertEqual(summary["output_tokens"], 100)
        self.assertEqual(self.context.counts["total_output_tokens"], 1000)
        ledger = json.loads((Path(self.directory.name) / "api_costs/conv_speed.json").read_text())
        entry = ledger["requests"]["r1"]
        self.assertEqual(entry["output_tokens"], 1000)
        self.assertEqual(entry["cost_usd"], "0.01")
        self.assertEqual(entry["speed_sample"], sample)
        self.assertEqual(entry["duration_ms"], 90000)
        self.assertEqual(entry["ttft_ms"], 1000)

    def test_explicit_empty_sample_skips_speed_without_skipping_usage(self):
        self.assertTrue(self.context.apply_usage_statistics(USAGE, duration_ms=2000, speed_sample={}))
        self.assertIsNone(self.context.consume_work_segment_speed_summary())
        self.assertEqual(self.context.counts["total_output_tokens"], 1000)

    def test_chat_keeps_full_output_and_original_duration(self):
        self.assertTrue(self.context.apply_usage_statistics(USAGE, duration_ms=2000))
        summary = self.context.consume_work_segment_speed_summary()
        self.assertEqual(summary["avg_tps"], 500)
        self.assertEqual(summary["output_tokens"], 1000)

    def test_multiple_requests_use_weighted_average_and_consume_once(self):
        self.context.apply_usage_statistics(USAGE, duration_ms=90000,
            speed_sample={"output_tokens": 100, "duration_ms": 1000})
        self.context.apply_usage_statistics(USAGE, duration_ms=90000,
            speed_sample={"output_tokens": 100, "duration_ms": 3000})
        summary = self.context.consume_work_segment_speed_summary()
        self.assertEqual(summary["avg_tps"], 50)
        self.assertEqual(summary["requests"], 2)
        self.assertEqual(summary["duration_ms"], 4000)
        self.assertIsNone(self.context.consume_work_segment_speed_summary())

    def test_duplicate_request_does_not_duplicate_speed_sample(self):
        for _ in range(2):
            self.context.apply_usage_statistics(USAGE, price=PRICE, request_id="same",
                duration_ms=90000, speed_sample={"output_tokens": 100, "duration_ms": 2000})
        self.assertEqual(self.context.consume_work_segment_speed_summary()["requests"], 1)
        self.assertEqual(self.context.counts["total_output_tokens"], 1000)


class StreamingTests(unittest.IsolatedAsyncioTestCase):
    async def run_attempts(self, attempts, protocol="responses", retries=0):
        from server.chat_flow_stream_loop import run_streaming_attempts
        client = SimpleNamespace(api_protocol=protocol, calls=0)
        recorded = []

        async def chat(*args, **kwargs):
            chunks = attempts[client.calls]
            client.calls += 1
            for chunk in chunks:
                yield chunk

        client.chat = chat
        terminal = SimpleNamespace(api_client=client, model_key="synthetic/speed",
            context_manager=SimpleNamespace(apply_usage_statistics=lambda *args, **kwargs:
                recorded.append((args, kwargs))), apply_model_profile=lambda _: None)
        events = []
        prefix = "server.chat_flow_stream_loop."
        with patch(prefix + "get_stop_flag", return_value=None), \
             patch(prefix + "debug_log"), patch(prefix + "log_backend_chunk"), \
             patch(prefix + "cancel_pending_tools"), \
             patch(prefix + "wait_retry_delay", new=AsyncMock(return_value=False)), \
             patch(prefix + "get_model_profile", return_value={}), \
             patch("modules.goal_policy.goal_for_terminal", return_value=None), \
             patch("modules.api_pricing.capture_price", return_value=PRICE), \
             patch("asyncio.sleep", new=AsyncMock()):
            result = await run_streaming_attempts(
                web_terminal=terminal, messages=[], tools=[],
                sender=lambda name, data: events.append((name, data)),
                client_sid="client", username="synthetic", conversation_id="conv_speed",
                current_iteration=1, max_api_retries=retries, retry_delay_seconds=0,
                detected_tool_intent={}, full_response="", tool_calls=[], current_thinking="",
                detected_tools={}, last_usage_payload=None, in_thinking=False,
                thinking_started=False, thinking_ended=False, text_started=False,
                text_has_content=False, text_streaming=False, text_chunk_index=0,
                last_text_chunk_time=None, chunk_count=0, reasoning_chunks=0,
                content_chunks=0, tool_chunks=0, last_finish_reason=None, accumulated_response="")
        return result, recorded, events

    @staticmethod
    def complete(usage=None, timing=None):
        chunk = {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": usage or USAGE}
        if timing is not None:
            chunk["_response_output_timing"] = timing
        return chunk

    async def test_main_loop_passes_full_usage_and_separate_response_sample(self):
        result, recorded, _ = await self.run_attempts([[
            {"choices": [{"delta": {"reasoning_content": "summary"}}]},
            {"choices": [{"delta": {"content": "answer"}}]},
            self.complete(timing={"duration_ms": 2000}),
        ]])
        self.assertFalse(result["stopped"])
        self.assertEqual(len(recorded), 1)
        args, kwargs = recorded[0]
        self.assertEqual(args[0]["completion_tokens"], 1000)
        self.assertEqual(kwargs["speed_sample"], {"output_tokens": 100, "duration_ms": 2000})

    async def test_missing_timing_or_reasoning_never_falls_back_to_full_output(self):
        for chunk in (self.complete(), self.complete(
            usage={"completion_tokens": 1000}, timing={"duration_ms": 2000})):
            _, recorded, _ = await self.run_attempts([[chunk]])
            self.assertEqual(recorded[0][1]["speed_sample"], {})
            self.assertEqual(recorded[0][0][0]["completion_tokens"], 1000)

    async def test_chat_passes_no_override(self):
        _, recorded, _ = await self.run_attempts([[self.complete()]], protocol="chat_completions")
        self.assertIsNone(recorded[0][1]["speed_sample"])

    async def test_retry_discards_failed_attempt_usage_and_timing(self):
        _, recorded, events = await self.run_attempts([
            [self.complete(timing={"duration_ms": 2000}), {"error": {"message": "disconnected"}}],
            [self.complete()],
        ], retries=1)
        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0][1]["speed_sample"], {})
        self.assertTrue(any(name == "error" and data["retry"] for name, data in events))


if __name__ == "__main__":
    unittest.main()
