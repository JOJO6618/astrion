"""Offline tests: real task/projection/tool loop, synthetic identities, no model calls."""
from __future__ import annotations

import ast
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from server.runtime.context import RuntimeContext, TaskParams, TrustedPrincipal
from server.tasks.models import TaskManager, TaskRecord
from server.tasks.compression_requests import run_requested_compression
from modules.tool_approval_manager import ToolApprovalManager
from server.chat_flow_tool_loop import execute_tool_calls
from server.chat_flow_stream_loop import run_streaming_attempts


def make_record(manager, task_id="synthetic-task", workspace_id="synthetic-workspace"):
    ctx = RuntimeContext(
        principal=TrustedPrincipal(username="synthetic-user", workspace_id=workspace_id,
                                   role="user", is_api_user=False, host_mode=True),
        params=TaskParams(message="synthetic", conversation_id="conv_synthetic"),
    )
    rec = TaskRecord(task_id, ctx, "conv_synthetic")
    rec.status = "running"
    manager._tasks[task_id] = rec
    return rec


class RequestTests(unittest.TestCase):
    def setUp(self):
        self.manager = TaskManager()
        self.rec = make_record(self.manager)

    def request(self):
        return self.manager.request_compression(self.rec.username, self.rec.workspace_id,
                                                self.rec.conversation_id)

    def test_queue_deduplicates_and_is_visible_in_snapshot(self):
        first = self.request()
        self.assertTrue(first["queued"])
        self.assertEqual(self.request(), first)
        self.assertEqual(len(self.rec.events), 1)
        state = self.rec.display_projection.snapshot()["state"]["compression"]
        self.assertEqual(state["stage"], "waiting_for_tools")
        self.assertTrue(self.manager.take_compression_request(self.rec.username, self.rec.task_id))
        self.assertFalse(self.manager.take_compression_request(self.rec.username, self.rec.task_id))
        self.assertEqual(self.request()["compression"]["stage"], "generating_summary")
        self.assertEqual(len(self.rec.events), 1)

    def test_request_is_scoped_to_user_workspace_and_conversation(self):
        for user, workspace, conversation in (("other", self.rec.workspace_id, self.rec.conversation_id),
                                              (self.rec.username, "other", self.rec.conversation_id),
                                              (self.rec.username, self.rec.workspace_id, "conv_other")):
            self.assertIsNone(self.manager.request_compression(user, workspace, conversation))
        self.assertEqual(self.rec.compression_request, "")

    def test_stop_does_not_consume_or_revive_request(self):
        self.request()
        self.rec.stop_requested = True
        self.assertFalse(self.manager.take_compression_request(self.rec.username, self.rec.task_id))
        with self.assertRaises(RuntimeError):
            self.request()
        self.manager._append_event(self.rec, "task_stopped", {})
        self.assertEqual(self.rec.compression_request, "")
        self.assertIsNone(self.rec.display_projection.snapshot()["state"]["compression"])
        new = make_record(self.manager, "new-task")
        self.assertEqual(new.compression_request, "")

    def test_automatic_compression_satisfies_pending_request(self):
        self.request()
        self.manager._append_event(self.rec, "compression_state", {
            "in_progress": True, "mode": "auto", "stage": "generating_summary"})
        self.assertFalse(self.manager.take_compression_request(self.rec.username, self.rec.task_id))
        self.request()
        self.assertEqual(len(self.rec.events), 2)
        self.manager._append_event(self.rec, "compression_finished", {"in_progress": False})
        self.assertEqual(self.rec.compression_request, "")

    def test_final_admission_and_request_are_atomic(self):
        def close():
            return self.manager.take_compression_request(self.rec.username, self.rec.task_id,
                                                        close_if_empty=True)
        def request():
            try:
                self.request()
                return True
            except RuntimeError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            accepted = pool.submit(request)
            consumed = pool.submit(close)
            self.assertEqual(accepted.result(), consumed.result())
        # If admitted, exactly one pending request was consumed. Otherwise admission is closed.
        self.assertNotEqual(self.rec.compression_request, "pending")


class AsyncBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_requested_compression_uses_current_writer_and_continue_mode(self):
        manager = TaskManager()
        rec = make_record(manager)
        manager.request_compression(rec.username, rec.workspace_id, rec.conversation_id)
        result = {"success": True, "guide_message": "continue synthetic task"}
        with patch("server.tasks.task_manager", manager), \
             patch("server.deep_compression.run_deep_compression", AsyncMock(return_value=result)) as compress:
            actual = await run_requested_compression(
                web_terminal=object(), workspace=object(), conversation_id=rec.conversation_id,
                username=rec.username, task_id=rec.task_id, sender=Mock())
        self.assertEqual(actual, result)
        self.assertEqual(compress.await_args.kwargs["mode"], "requested")
        self.assertEqual(rec.status, "running")

    async def run_rejected_batch(self, approval_type, *, compress=False, stopped=False):
        history, events = [], []
        cm = SimpleNamespace(project_path=ROOT, add_conversation=lambda *a, **k: history.append((a, k)))
        terminal = SimpleNamespace(context_manager=cm, define_tools=lambda: [],
                                   evaluate_tool_permission=lambda name, args: (
                                       {"allowed": True, "requires_approval": True,
                                        "mode": "unrestricted", "approval_type": approval_type}
                                       if name == "run_command" else {"allowed": False}),
                                   get_work_mode=lambda: "execute")
        calls = [{"id": "first", "function": {"name": "run_command", "arguments": "{}"}},
                 {"id": "second", "function": {"name": "read_file", "arguments": "{}"}}]
        messages = [{"role": "assistant", "tool_calls": calls, "content": ""}]
        async def boundary(**kwargs):
            self.assertEqual([m["tool_call_id"] for m in messages[1:]], ["first", "second"])
            self.assertEqual([item[1]["tool_call_id"] for item in history], ["first", "second"])
            return {"success": True, "guide_message": "continue"} if compress else None
        with patch("server.chat_flow_tool_loop.tool_approval_manager", ToolApprovalManager()), \
             patch("server.chat_flow_tool_loop._wait_for_tool_approval", AsyncMock(return_value={"decision": "rejected"})), \
             patch("server.chat_flow_tool_loop._build_tool_approval_preview", return_value={}), \
             patch("server.chat_flow_tool_loop.TOOL_CALL_COOLDOWN", 0), \
             patch("server.tasks.compression_requests.run_requested_compression", side_effect=boundary) as consume:
            result = await execute_tool_calls(
                web_terminal=terminal, tool_calls=calls, sender=lambda e, d: events.append((e, d)),
                messages=messages, client_sid="synthetic-task", username="synthetic-user", iteration=1,
                conversation_id="conv_synthetic", last_tool_call_time=0,
                process_background_command_updates=AsyncMock(), process_multi_agent_master_messages=AsyncMock(),
                get_stop_flag=lambda *a, **k: {"stop": stopped}, clear_stop_flag=Mock(),
                workspace=SimpleNamespace(data_dir=ROOT))
        return result, messages, events, consume

    async def test_full_access_rejection_fills_entire_batch_and_continues(self):
        result, messages, events, _ = await self.run_rejected_batch("full_access")
        self.assertFalse(result["stopped"])
        self.assertFalse(result.get("approval_rejected"))
        self.assertEqual(len(messages), 3)
        self.assertFalse(any(e == "tool_start" for e, _ in events))

    async def test_compression_runs_after_all_batch_results_even_when_rejected(self):
        result, _, _, consume = await self.run_rejected_batch("full_access", compress=True)
        self.assertTrue(result["deep_compressed"])
        self.assertEqual(consume.call_count, 1)

    async def test_ordinary_manual_approval_rejection_keeps_existing_behavior(self):
        result, messages, _, consume = await self.run_rejected_batch("sandbox_write")
        self.assertTrue(result["approval_rejected"])
        self.assertEqual(len(messages), 2)
        consume.assert_not_called()

    async def test_real_stop_still_cancels_batch(self):
        result, messages, _, consume = await self.run_rejected_batch("full_access", stopped=True)
        self.assertTrue(result["stopped"])
        self.assertEqual(len(messages), 3)
        consume.assert_not_called()

    async def test_running_tool_request_waits_for_complete_batch(self):
        manager = TaskManager()
        rec = make_record(manager)
        history, executed = [], []
        calls = [{"id": name, "function": {"name": name, "arguments": "{}"}}
                 for name in ("synthetic_first", "synthetic_second")]
        messages = [{"role": "assistant", "tool_calls": calls, "content": ""}]
        cm = SimpleNamespace(project_path=ROOT, conversation_history=[],
                             add_conversation=lambda *a, **k: history.append(k["tool_call_id"]),
                             get_current_context_tokens=lambda *a: 0,
                             on_tool_call_finished=lambda *a, **k: 0)
        async def execute(name, args):
            executed.append(name)
            if name == "synthetic_first":
                manager.request_compression(rec.username, rec.workspace_id, rec.conversation_id)
            self.assertEqual(rec.compression_request, "pending")
            return '{"success": true, "output": "synthetic result"}'
        terminal = SimpleNamespace(context_manager=cm, define_tools=lambda: [],
                                   evaluate_tool_permission=lambda *a: {"allowed": True},
                                   handle_tool_call=execute)
        async def compress(**kwargs):
            self.assertEqual(history, ["synthetic_first", "synthetic_second"])
            self.assertEqual([m["tool_call_id"] for m in messages[1:]], history)
            return {"success": True, "guide_message": "continue"}
        with patch("server.tasks.task_manager", manager), \
             patch("server.deep_compression.run_deep_compression", side_effect=compress), \
             patch("server.chat_flow_tool_loop.TOOL_CALL_COOLDOWN", 0), \
             patch("server.chat_flow_tool_loop.load_personalization_config", return_value={}):
            result = await execute_tool_calls(
                web_terminal=terminal, tool_calls=calls,
                sender=lambda e, d: manager._append_event(rec, e, d), messages=messages,
                client_sid=rec.task_id, username=rec.username, conversation_id=rec.conversation_id,
                iteration=1, last_tool_call_time=0, process_background_command_updates=AsyncMock(),
                process_multi_agent_master_messages=AsyncMock(), get_stop_flag=lambda *a, **k: None,
                clear_stop_flag=Mock(), workspace=SimpleNamespace(data_dir=ROOT))
        self.assertEqual(executed, ["synthetic_first", "synthetic_second"])
        self.assertTrue(result["deep_compressed"])

    async def test_thinking_ends_before_tool_parameters_finish_streaming(self):
        events = []
        async def chat(*a, **k):
            yield {"choices": [{"delta": {"reasoning_content": "synthetic thought"}}]}
            yield {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "synthetic-tool",
                    "function": {"name": "read_file", "arguments": '{"path":'}}]}}]}
            kinds = [e for e, _ in events]
            self.assertLess(kinds.index("thinking_end"), kinds.index("tool_preparing"))
            yield {"choices": [{"delta": {"tool_calls": [{"index": 0,
                    "function": {"arguments": '"synthetic.txt"}'}}]}, "finish_reason": "tool_calls"}]}
        terminal = SimpleNamespace(api_client=SimpleNamespace(chat=chat), model_key="synthetic")
        kwargs = dict(web_terminal=terminal, messages=[], tools=[], sender=lambda e, d: events.append((e, d)),
                      client_sid="synthetic-task", username="synthetic-user", conversation_id="conv_synthetic",
                      current_iteration=1, max_api_retries=0, retry_delay_seconds=0, detected_tool_intent={},
                      full_response="", tool_calls=[], current_thinking="", detected_tools={}, last_usage_payload=None,
                      in_thinking=False, thinking_started=False, thinking_ended=False, text_started=False,
                      text_has_content=False, text_streaming=False, text_chunk_index=0, last_text_chunk_time=None,
                      chunk_count=0, reasoning_chunks=0, content_chunks=0, tool_chunks=0,
                      last_finish_reason=None, accumulated_response="")
        with patch("modules.api_pricing.capture_price", return_value=None):
            result = await run_streaming_attempts(**kwargs)
        self.assertTrue(result["thinking_ended"])
        self.assertFalse(result["in_thinking"])
        self.assertEqual(sum(e == "thinking_end" for e, _ in events), 1)

    async def test_compression_continuation_preserves_work_anchor(self):
        tree = ast.parse((ROOT / "server/chat_flow_task_main.py").read_text())
        node = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)
                    and n.name == "continue_after_compression")
        continuation, finalize = AsyncMock(), Mock()
        terminal = SimpleNamespace()
        scope = dict(handle_task_with_sender=continuation, web_terminal=terminal, workspace=object(),
                     sender=Mock(), client_sid="synthetic-task", username="synthetic-user",
                     current_work_anchor_message_id="original-anchor", finalize_user_work_timer=finalize,
                     finalize_run_versioning_checkpoint=Mock())
        exec(compile(ast.Module(body=[node], type_ignores=[]), "<actual-continuation>", "exec"), scope)
        await scope["continue_after_compression"]({"success": True, "guide_message": "continue"})
        self.assertEqual(continuation.await_args.kwargs["work_anchor_message_id"], "original-anchor")
        self.assertEqual(continuation.await_args.args[5], "synthetic-task")
        finalize.assert_not_called()


if __name__ == "__main__":
    unittest.main()
