"""Offline mode/switch matrix and real tool-loop approval dispatch.

Only model calls, tool bodies and unrelated batch-boundary hooks are stubbed.
Approval requests, review service, stale-input checks and one-use grants are real.
"""
from __future__ import annotations

import ast
from contextlib import ExitStack
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any, Dict, Optional
import unittest
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from modules.execution_scope import current_execution_scope, validate_full_access_request
from modules.tool_approval_manager import ToolApprovalManager, resolve_approval_requirements
from server.chat_flow_tool_loop import execute_tool_calls


def permission_evaluator():
    """Load the actual policy method without constructing a runtime terminal."""
    path = ROOT / "core/main_terminal_parts/tools_execution.py"
    tree = ast.parse(path.read_text())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef)
               and node.name == "MainTerminalToolsExecutionMixin")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef)
                  and node.name == "evaluate_tool_permission")
    namespace = dict(Dict=Dict, Any=Any, Optional=Optional, Path=Path,
                     current_execution_scope=current_execution_scope,
                     validate_full_access_request=validate_full_access_request,
                     tr=lambda key: key)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])),
                 str(path), "exec"), namespace)
    return namespace["evaluate_tool_permission"]


class RequirementTests(unittest.TestCase):
    def test_full_access_mode_and_switch_matrix(self):
        cases = [
            ("unrestricted", False, False, True),
            ("unrestricted", True, True, False),
            ("auto_approval", False, True, True),
            ("auto_approval", True, True, False),
            ("approval", False, False, True),
            ("approval", True, False, True),
        ]
        for mode, switch, automatic, human in cases:
            with self.subTest(mode=mode, switch=switch):
                self.assertEqual(resolve_approval_requirements(
                    mode, "full_access", allow_auto_approve_full_access=switch,
                ), {"auto_review_required": automatic, "human_approval_required": human})
        self.assertEqual(resolve_approval_requirements("unrestricted", "full_access"),
                         {"auto_review_required": False, "human_approval_required": True})

    def test_switch_does_not_change_other_approval_types(self):
        for mode in ("approval", "auto_approval", "unrestricted"):
            for kind in ("sandbox_write", "direct_execution"):
                with self.subTest(mode=mode, kind=kind):
                    self.assertEqual(resolve_approval_requirements(mode, kind),
                                     resolve_approval_requirements(
                                         mode, kind, allow_auto_approve_full_access=True))


class ToolLoopTests(unittest.IsolatedAsyncioTestCase):
    async def run_batch(self, *, mode="unrestricted", switch=None, environment="sandbox",
                        full=True, review="approved", change_switch=False, stale=False,
                        permission_retry=False):
        manager, events, saved, scopes = ToolApprovalManager(), [], [], []
        prefs = {} if switch is None else {"allow_auto_approve_full_access": switch}
        with TemporaryDirectory() as data_dir, ExitStack() as stack:
            cm = SimpleNamespace(
                project_path=ROOT, current_conversation_id="synthetic-conv",
                current_shallow_message_id="u", conversation_metadata={},
                conversation_history=[{"role": "user", "message_id": "u", "content": "检查项目"}],
                get_current_context_tokens=lambda *a: 0,
                on_tool_call_finished=lambda *a, **k: 0,
                is_compression_in_progress=lambda: False,
            )
            def save(role, content, **metadata):
                row = {"role": role, "content": content, **metadata}
                cm.conversation_history.append(row)
                saved.append(row)
                return row
            cm.add_conversation = save
            term = SimpleNamespace(
                context_manager=cm, project_path=ROOT, data_dir=data_dir,
                define_tools=lambda: [], get_permission_mode=lambda: mode,
                get_execution_mode=lambda: environment, get_work_mode=lambda: "execute",
                _is_readonly_run_command_allowed=lambda cmd: True,
                _extract_command_risk_markers=lambda cmd: [],
                _APPROVAL_REQUIRED_TOOLS={"run_command", "write_file", "edit_file"},
            )
            evaluate = permission_evaluator()
            term.evaluate_tool_permission = lambda name, args: evaluate(term, name, args)
            executions = 0
            async def execute(name, args):
                nonlocal executions
                executions += 1
                scope = current_execution_scope()
                scopes.append(scope)
                if scope and scope.full_access_grant:
                    self.assertTrue(scope.full_access_grant.consume(term, args))
                    self.assertFalse(scope.full_access_grant.consume(term, args))
                if permission_retry and executions == 1:
                    return '{"success":false,"error":"Permission denied"}'
                return '{"success":true,"output":"synthetic result"}'
            term.handle_tool_call = AsyncMock(side_effect=execute)

            async def reviewer(**kwargs):
                if change_switch:
                    prefs["allow_auto_approve_full_access"] = not switch
                if stale:
                    cm.conversation_history.append({"role": "user", "content": "停止执行"})
                result = {"decision": review, "reason": "synthetic review"}
                if review == "error":
                    result["code"] = "review_request_failed"
                return result
            review_call = AsyncMock(side_effect=reviewer)
            factory = stack.enter_context(patch(
                "modules.auto_approval_service.ApprovalAgent",
                return_value=SimpleNamespace(review=review_call),
            ))
            async def human(**kwargs):
                if change_switch:
                    prefs["allow_auto_approve_full_access"] = not switch
                row = manager.decide(kwargs["approval_id"], "synthetic-user", "approved")
                return {"decision": row["status"], "item": row, "source": "manual"}
            manual = stack.enter_context(patch(
                "server.chat_flow_tool_loop._wait_for_tool_approval", AsyncMock(side_effect=human)))
            for target, value in (
                ("server.chat_flow_tool_loop.tool_approval_manager", manager),
                ("modules.auto_approval_service.tool_approval_manager", manager),
                ("server.chat_flow_tool_loop._build_tool_approval_preview", lambda *a: {}),
                ("server.chat_flow_tool_loop.load_personalization_config", lambda *a: dict(prefs)),
                ("server.chat_flow_tool_loop.TOOL_CALL_COOLDOWN", 0),
                ("server.chat_flow_tool_loop.debug_log", lambda *a: None),
                ("modules.goal_policy.interaction_denied", lambda *a: False),
                ("modules.approval_request._goal", lambda *a: ""),
                ("modules.approval_request._runtime_inputs", lambda *a: {}),
                ("server.tasks.compression_requests.run_requested_compression", AsyncMock(return_value=None)),
                ("server.runtime_guidance.inject_pending_guidance", Mock()),
                ("server.chat_flow_tool_loop.process_workflow_updates", AsyncMock()),
            ):
                stack.enter_context(patch(target, value))
            args = {"command": "echo synthetic", "timeout": 10}
            if full:
                args.update(request_full_access=True, full_access_reason="检查项目需要宿主机工具链")
            calls = [
                {"id": "first", "function": {"name": "run_command", "arguments": json.dumps(args)}},
                {"id": "second", "function": {"name": "synthetic_followup", "arguments": "{}"}},
            ]
            cm.conversation_history.append({"role": "assistant", "tool_calls": calls})
            messages = [{"role": "assistant", "tool_calls": calls}]
            result = await execute_tool_calls(
                web_terminal=term, tool_calls=calls, messages=messages,
                sender=lambda event, data: events.append((event, data)),
                client_sid="synthetic-task", username="synthetic-user", iteration=1,
                conversation_id=cm.current_conversation_id, last_tool_call_time=0,
                process_background_command_updates=AsyncMock(),
                process_multi_agent_master_messages=AsyncMock(),
                get_stop_flag=lambda *a, **k: None, clear_stop_flag=Mock(),
            )
            for call in factory.call_args_list:
                self.assertEqual(call.kwargs["conversation_id"], "synthetic-conv")
            requested = [data["approval"] for event, data in events if event == "tool_approval_required"]
            rows = [manager.get(row["approval_id"]) for row in requested]
            self.assertEqual([row["tool_call_id"] for row in saved], ["first", "second"])
            self.assertFalse(result["stopped"])
            self.assertFalse(result.get("approval_rejected"))
            return SimpleNamespace(rows=rows, events=events, review=review_call, manual=manual,
                                   saved=saved, executed=term.handle_tool_call, scopes=scopes)

    async def test_unrestricted_default_or_closed_keeps_manual_entry(self):
        for switch in (None, False):
            with self.subTest(switch=switch):
                out = await self.run_batch(switch=switch)
                out.review.assert_not_awaited()
                out.manual.assert_awaited_once()
                self.assertTrue(out.rows[0]["human_approval_required"])
                self.assertFalse(out.rows[0]["auto_review_required"])
                self.assertIsNotNone(out.scopes[0].full_access_grant)

    async def test_unrestricted_open_reviews_then_claims_one_time_grant(self):
        out = await self.run_batch(switch=True)
        out.review.assert_awaited_once()
        out.manual.assert_not_awaited()
        self.assertEqual(out.executed.await_count, 2)
        self.assertFalse(out.rows[0]["human_approval_required"])
        self.assertEqual(out.rows[0]["auto_review_status"], "approved")
        self.assertIsNotNone(out.scopes[0].full_access_grant)
        self.assertIn("自动审核通过即可执行", out.review.await_args.kwargs["payload_text"])

    async def test_rejection_and_error_never_execute_command_but_continue_batch(self):
        for review in ("rejected", "error"):
            with self.subTest(review=review):
                out = await self.run_batch(switch=True, review=review)
                self.assertEqual(out.executed.await_args.args[0], "synthetic_followup")
                self.assertEqual(out.executed.await_count, 1)
                out.manual.assert_not_awaited()
                payload = out.saved[0]["metadata"]["tool_payload"]
                self.assertEqual(payload["status"], review)
                self.assertEqual(out.rows[0]["status"], "expired" if review == "error" else "rejected")

    async def test_stale_review_never_executes_and_does_not_fall_back_to_human(self):
        out = await self.run_batch(switch=True, stale=True)
        self.assertEqual(out.review.await_count, 2)
        self.assertEqual(out.executed.await_count, 1)
        self.assertEqual(out.saved[0]["metadata"]["tool_payload"]["code"], "review_context_changed")
        out.manual.assert_not_awaited()

    async def test_approval_mode_ignores_open_switch(self):
        out = await self.run_batch(mode="approval", switch=True)
        out.review.assert_not_awaited()
        out.manual.assert_awaited_once()
        self.assertTrue(out.rows[0]["human_approval_required"])

    async def test_auto_mode_retains_closed_dual_and_open_auto_only(self):
        for switch in (False, True):
            with self.subTest(switch=switch):
                out = await self.run_batch(mode="auto_approval", switch=switch)
                out.review.assert_awaited_once()
                self.assertEqual(out.manual.await_count, int(not switch))
                self.assertEqual(out.rows[0]["human_approval_required"], not switch)

    async def test_pending_request_uses_captured_switch_in_both_directions(self):
        for switch in (False, True):
            with self.subTest(switch=switch):
                out = await self.run_batch(switch=switch, change_switch=True)
                self.assertEqual(out.rows[0]["auto_review_required"], switch)
                self.assertEqual(out.rows[0]["human_approval_required"], not switch)
                self.assertEqual(out.manual.await_count, int(not switch))

    async def test_unrestricted_normal_sandbox_and_direct_calls_stay_exempt(self):
        for environment, full in (("sandbox", False), ("direct", False), ("direct", True)):
            with self.subTest(environment=environment, full=full):
                out = await self.run_batch(switch=True, environment=environment, full=full)
                self.assertEqual(out.rows, [])
                out.manual.assert_not_awaited()
                out.review.assert_not_awaited()
                self.assertEqual(out.scopes, [None, None])

    async def test_readonly_failure_retry_retains_ordinary_approval_rules(self):
        for mode in ("approval", "auto_approval"):
            with self.subTest(mode=mode):
                out = await self.run_batch(mode=mode, switch=True, full=False, permission_retry=True)
                self.assertEqual(out.rows[0]["approval_type"], "sandbox_write")
                self.assertEqual(out.review.await_count, int(mode == "auto_approval"))
                self.assertEqual(out.manual.await_count, int(mode == "approval"))
                self.assertEqual(out.executed.await_count, 3)


if __name__ == "__main__":
    unittest.main()
