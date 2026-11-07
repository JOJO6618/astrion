"""Offline approval requests, failures, billing identity and stale-input guards."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from modules.approval_agent import ApprovalAgent
from modules.approval_request import ApprovalRequest
from modules.auto_approval_service import build_auto_approval_payload, run_auto_approval
from modules.review_model_caller import ReviewModelCallError
from modules.tool_approval_manager import ToolApprovalManager
from server.tool_execution_authority import (
    DeniedExecution, approval_execution_scope, execute_with_authority, resolve_approval_authority,
)


def terminal():
    return SimpleNamespace(
        context_manager=SimpleNamespace(
            project_path=ROOT, current_conversation_id="c", current_shallow_message_id="u",
            conversation_history=[{"role": "user", "message_id": "u", "content": "检查项目，不能修改系统。",
                                   "metadata": {"message_source": "user"}}],
        ),
        get_execution_mode=lambda: "sandbox", get_permission_mode=lambda: "auto_approval",
        handle_tool_call=AsyncMock(return_value='{"success":true}'),
    )


def decision(value="approved", reason="任务范围内"):
    return {"tool_calls": [{"id": "d", "function": {
        "name": "approve_decision", "arguments": json.dumps({"decision": value, "reason": reason}),
    }}]}


class RequestTests(unittest.TestCase):
    def test_plain_request_keeps_parameters_and_deduplicates_directory(self):
        term = terminal()
        payload = build_auto_approval_payload(
            web_terminal=term, recent_tool_actions=[{"tool_name": "FAKE_CACHE"}],
            function_name="run_command", arguments={"command": "npm test", "timeout": 120,
                                                     "working_dir": "/specific/path", "run_in_background": False},
        )
        self.assertNotIn("FAKE_CACHE", payload)
        self.assertNotIn('{"', payload)
        self.assertEqual(payload.count("/specific/path"), 1)
        self.assertIn("timeout: 120", payload)
        self.assertIn("run_in_background: false", payload)
        self.assertIn("不能修改系统", payload)

    def test_single_full_access_describes_actual_execution_and_both_decisions(self):
        term = terminal()
        request = ApprovalRequest.capture(term, "", {"approval_type": "full_access", "human_approval_required": True})
        text = request.render("run_command", {"command": "npm test"})
        self.assertIn("沙箱，本次申请单次完全访问", text)
        self.assertIn("宿主机脱离沙箱", text)
        self.assertIn("仍需人工批准", text)
        request.item["human_approval_required"] = False
        self.assertIn("自动审核通过即可执行", request.render("run_command", {}))

    def test_goal_original_is_included_once(self):
        with patch("modules.approval_request._goal", return_value="完成发布但不要推送"):
            request = ApprovalRequest.capture(terminal(), "", {})
        text = request.render("run_command", {})
        self.assertEqual(text.count("完成发布但不要推送"), 1)

    def test_pending_guidance_is_read_without_consuming(self):
        pending = [{"id": "g", "source": "guidance", "text": "先别运行下载的代码",
                    "files": ["requirements.txt"], "images": [{"path": "screenshot.png"}]}]
        with patch("modules.approval_request._runtime_inputs", return_value={"guidance": pending}):
            request = ApprovalRequest.capture(terminal(), "u", {"task_id": "t", "conversation_id": "c"})
        self.assertEqual(len(pending), 1)
        self.assertIn("尚未交主模型", request.render("run_command", {}))
        self.assertIn("先别运行下载的代码", request.render("run_command", {}))
        self.assertIn("requirements.txt", request.render("run_command", {}))
        self.assertIn("screenshot.png", request.render("run_command", {}))

    def test_network_change_invalidates_review(self):
        term = terminal()
        term.get_network_permission = lambda: "restricted"
        request = ApprovalRequest.capture(term, "", {})
        term.get_network_permission = lambda: "full"
        self.assertFalse(request.is_current())

    def test_explicit_missing_trigger_is_not_replaced_by_latest_user(self):
        from modules.approval_history import ApprovalHistory
        history = ApprovalHistory(terminal().context_manager.conversation_history, trigger_id="missing")
        self.assertIsNone(history.trigger)
        self.assertIn("触发原文未找到", history.initial_text())

    def test_legacy_qa_keeps_original_but_marks_missing_answer_status(self):
        from modules.approval_history import ApprovalHistory
        history = ApprovalHistory([
            {"role": "assistant", "tool_calls": [{"id": "q", "function": {"name": "ask_user", "arguments": '{"question":"继续吗"}'}}]},
            {"role": "tool", "tool_call_id": "q", "name": "ask_user", "content": json.dumps({
                "status": "answered", "success": True, "answer_text": "问题已过期",
            })},
        ])
        self.assertIn("问题已过期", history.initial_text())
        self.assertIn("不能单独证明用户已回答或授权", history.initial_text())

    def test_task_snapshot_does_not_drain_guidance_or_include_recovery_drafts(self):
        from server.tasks.models import TaskManager
        manager = TaskManager()
        record = SimpleNamespace(username="u", conversation_id="c", trigger_message_id="u",
                                 runtime_guidance_queue=[{"id": "g", "text": "新要求"}],
                                 runtime_pending_queue=[{"text": "待恢复草稿"}])
        manager._tasks["t"] = record
        snapshot = manager.snapshot_review_inputs("u", "t", "c")
        snapshot["guidance"][0]["text"] = "修改快照"
        self.assertEqual(record.runtime_guidance_queue[0]["text"], "新要求")
        self.assertEqual(len(record.runtime_guidance_queue), 1)
        self.assertNotIn("待恢复草稿", str(snapshot))
        self.assertEqual(manager.snapshot_review_inputs("other", "t", "c"), {})
        self.assertEqual(manager.snapshot_review_inputs("u", "t", "other"), {})

    def test_changes_after_capture_invalidate_inputs_but_compression_marks_do_not(self):
        term = terminal()
        request = ApprovalRequest.capture(term, "", {})
        term.context_manager.conversation_history[0]["metadata"]["deep_compacted"] = True
        self.assertTrue(request.is_current())
        term.context_manager.conversation_history[0]["content"] = "不要执行"
        self.assertFalse(request.is_current())


class AgentTests(unittest.IsolatedAsyncioTestCase):
    def agent(self):
        with patch("modules.approval_agent.load_approval_agent_config", return_value={"profile": {}, "max_rounds": 2}), \
             patch("modules.approval_agent.review_billing_for", return_value=None):
            return ApprovalAgent(web_terminal=terminal(), conversation_id="c")

    async def test_service_failure_is_not_a_risk_rejection(self):
        with patch("modules.approval_agent.call_review_model", side_effect=ReviewModelCallError("no model")):
            result = await self.agent().review(payload_text="待审")
        self.assertEqual(result["decision"], "error")
        self.assertEqual(result["code"], "review_request_failed")

    async def test_transient_timeout_retries_once(self):
        # 重试统一在 review_model_caller 内（见 test/2026-11-07_审批与审核修复），
        # 智能体层只调一次，不再自己重试。
        with patch("modules.approval_agent.call_review_model", return_value=decision()) as call:
            result = await self.agent().review(payload_text="待审")
        self.assertEqual(result["decision"], "approved")
        self.assertEqual(call.call_count, 1)

    async def test_repeated_timeout_fails_closed_with_error(self):
        with patch("modules.approval_agent.call_review_model", side_effect=ReviewModelCallError(
            "timeout", details={"error_type": "timeout"},
        )) as call:
            result = await self.agent().review(payload_text="待审")
        self.assertEqual(result["decision"], "error")
        self.assertEqual(call.call_count, 1)

    async def test_invalid_decision_is_not_a_policy_denial(self):
        with patch("modules.approval_agent.call_review_model", return_value=decision("needs_human")):
            result = await self.agent().review(payload_text="待审")
        self.assertEqual(result["code"], "review_invalid_decision")

    async def test_history_tool_reads_frozen_evidence(self):
        agent = self.agent()
        request = ApprovalRequest.capture(terminal(), "", {})
        agent.review_history = request.history
        first = {"tool_calls": [{"id": "r", "function": {
            "name": "read_review_history", "arguments": '{"query":"不能修改系统"}',
        }}]}
        with patch("modules.approval_agent.call_review_model", side_effect=[first, decision()]) as call:
            result = await agent.review(payload_text="待审")
        self.assertEqual(result["decision"], "approved")
        messages = call.call_args.args[1]
        evidence = next(row for row in messages if row.get("role") == "tool")
        self.assertIn("不能修改系统", evidence["content"])
        self.assertEqual(evidence["tool_call_id"], "r")

    async def test_missing_profile_and_debug_transcript_do_not_reference_unbound_messages(self):
        agent = self.agent()
        agent.cfg = {}
        result = await agent.review(payload_text="待审")
        self.assertEqual(result["code"], "review_config_missing")


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.term = terminal()
        self.manager = ToolApprovalManager()
        self.events = []

    def create(self, *, full=False, human=False):
        return self.manager.create_request(
            username="u", conversation_id="c", task_id="t", tool_call_id="call",
            tool_name="run_command", arguments={"command": "npm test"}, preview={},
            approval_type="full_access" if full else "sandbox_write", auto_review_required=True,
            human_approval_required=human, executor_id=id(self.term), workspace_root=str(ROOT),
        )

    async def review(self, item, reviewer):
        with patch("modules.auto_approval_service.tool_approval_manager", self.manager), \
             patch("modules.auto_approval_service.ApprovalAgent", return_value=SimpleNamespace(review=reviewer)) as factory:
            result = await run_auto_approval(
                web_terminal=self.term, username="u", approval_id=item["approval_id"], conversation_id="c",
                recent_tool_actions=[], function_name="run_command", arguments=item["arguments"], risk_markers=[],
                sender=lambda event, data: self.events.append((event, data)),
            )
        for call in factory.call_args_list:
            self.assertEqual(call.kwargs["conversation_id"], "c")
        return result

    async def test_ordinary_error_closes_request_without_manual_fallback(self):
        item = self.create()
        result = await self.review(item, AsyncMock(return_value={"decision": "error", "code": "review_timeout", "reason": "timeout"}))
        self.assertEqual(result["decision"], "error")
        row = self.manager.get(item["approval_id"])
        self.assertEqual(row["status"], "expired")
        self.assertEqual(row["auto_review_status"], "error")
        self.assertEqual(self.manager.list_pending("u"), [])

    async def test_closed_switch_still_waits_for_human_and_open_switch_executes(self):
        closed = self.create(full=True, human=True)
        result = await self.review(closed, AsyncMock(return_value={"decision": "approved", "reason": "ok"}))
        self.assertEqual(result["decision"], "pending")
        row = self.manager.decide(closed["approval_id"], "u", "approved")
        self.assertEqual(row["status"], "approved")
        opened = self.create(full=True, human=False)
        result = await self.review(opened, AsyncMock(return_value={"decision": "approved", "reason": "ok"}))
        self.assertEqual(result["decision"], "approved")

    async def test_full_access_error_is_not_reported_as_rejection(self):
        result = await self.review(self.create(full=True), AsyncMock(return_value={
            "decision": "error", "code": "review_parse_error", "reason": "bad response",
        }))
        self.assertEqual(result["decision"], "error")
        self.assertEqual(result["code"], "review_parse_error")
        self.assertEqual(self.manager.list_pending("u"), [])

    async def test_new_user_input_during_review_causes_automatic_rereview(self):
        item = self.create()
        payloads = []
        async def reviewer(**kwargs):
            payloads.append(kwargs["payload_text"])
            if len(payloads) == 1:
                self.term.context_manager.conversation_history.append({
                    "role": "user", "message_id": "new", "content": "禁止安装", "metadata": {"message_source": "guidance"},
                })
            return {"decision": "approved", "reason": "仅运行已有测试"}
        result = await self.review(item, reviewer)
        self.assertEqual(result["decision"], "approved")
        self.assertEqual(len(payloads), 2)
        self.assertIn("禁止安装", payloads[1])

    async def test_continuous_changes_fail_closed(self):
        item = self.create()
        async def reviewer(**kwargs):
            self.term.context_manager.conversation_history.append({"role": "user", "content": "新要求"})
            return {"decision": "approved"}
        result = await self.review(item, reviewer)
        self.assertEqual(result["decision"], "error")
        self.assertEqual(result["code"], "review_context_changed")

    async def test_new_input_after_approval_cannot_mint_execution_scope(self):
        item = self.create()
        result = await self.review(item, AsyncMock(return_value={"decision": "approved"}))
        self.term.context_manager.conversation_history.append({"role": "user", "content": "不要执行"})
        scope = resolve_approval_authority(
            self.term, item["arguments"], result["item"], task_id="t", tool_call_id="call", expected_root=ROOT,
            manager=self.manager, tool_name="run_command",
        )
        self.assertIsInstance(scope, DeniedExecution)

    async def test_new_input_after_scope_claim_is_checked_again_at_tool_start(self):
        item = self.create()
        result = await self.review(item, AsyncMock(return_value={"decision": "approved"}))
        scope = approval_execution_scope(
            self.term, item["arguments"], result["item"], task_id="t", tool_call_id="call", expected_root=ROOT,
            manager=self.manager, tool_name="run_command",
        )
        self.term.context_manager.conversation_history.append({"role": "user", "content": "不要执行"})
        outcome = json.loads(await execute_with_authority(self.term, "run_command", item["arguments"], scope))
        self.assertEqual(outcome["code"], "review_context_changed")
        self.term.handle_tool_call.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
