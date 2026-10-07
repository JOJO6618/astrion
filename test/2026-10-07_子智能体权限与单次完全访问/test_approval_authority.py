from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from modules.execution_scope import (
    ExecutionScope, bind_execution_scope, command_fingerprint,
    current_execution_scope, validate_child_access, validate_full_access_request,
)
from modules.full_access_review import run_full_access_review
from modules.tool_approval_manager import ToolApprovalManager
from server.tool_execution_authority import approval_execution_scope, execute_with_authority


class ApprovalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.manager = ToolApprovalManager()
        self.args = {"command": "echo approved", "timeout": 10, "request_full_access": True,
                     "full_access_reason": "需要本次宿主机访问", "working_dir": None}
        self.terminal = SimpleNamespace(
            project_path=str(self.root),
            context_manager=SimpleNamespace(project_path=self.root, current_conversation_id="conversation"),
            get_permission_mode=lambda: "auto_approval", get_work_mode=lambda: "execute",
            get_execution_mode=lambda: "sandbox", container_session=SimpleNamespace(mode="host"),
        )
        self.item = self.manager.create_request(
            username="user", conversation_id="conversation", task_id="task", tool_call_id="tool",
            tool_name="run_command", arguments=self.args, preview={},
            approval_type="full_access", auto_review_required=True,
            executor_id=id(self.terminal), workspace_root=str(self.root),
        )
        self.id = self.item["approval_id"]

    def approved(self):
        self.manager.decide(self.id, "user", "approved")
        return self.manager.record_auto_review(self.id, "approved")

    def scope(self, item=None, args=None, **overrides):
        options = dict(task_id="task", tool_call_id="tool", expected_root=self.root,
                       manager=self.manager, tool_name="run_command", fingerprint=command_fingerprint(self.args))
        options.update(overrides)
        return approval_execution_scope(self.terminal, args or self.args, item or self.manager.get(self.id), **options)

    async def test_manual_takeover_is_not_reported_as_auto_approval(self):
        from modules.auto_approval_service import run_auto_approval
        item = self.manager.create_request(
            username="user", conversation_id="conversation", task_id="task", tool_call_id="ordinary",
            tool_name="run_command", arguments={}, preview={},
        )
        aid = item["approval_id"]
        async def review(**kwargs):
            self.manager.decide(aid, "user", "approved")
            self.assertEqual(kwargs["cancel_check"]()["source"], "manual")
            return {"decision": "approved"}
        events = []
        with patch("modules.auto_approval_service.tool_approval_manager", self.manager), \
             patch("modules.auto_approval_service.ApprovalAgent", return_value=SimpleNamespace(review=review)):
            result = await run_auto_approval(
                web_terminal=self.terminal, username="user", approval_id=aid,
                conversation_id="conversation", recent_tool_actions=[], function_name="run_command",
                arguments={}, risk_markers=[], sender=lambda event, data: events.append((event, data)),
            )
        self.assertEqual(result["source"], "manual")
        self.assertEqual(events[-1][1]["progress"]["source"], "manual")
        self.assertIsNone(events[-1][1]["progress"]["decision"])

    def test_both_orders_require_both_decisions(self):
        for first in ("human", "auto"):
            manager = ToolApprovalManager()
            item = manager.create_request(username="u", conversation_id="c", task_id="t", tool_call_id="x",
                                          tool_name="run_command", arguments={}, preview={},
                                          approval_type="full_access", auto_review_required=True)
            aid = item["approval_id"]
            if first == "human":
                self.assertEqual(manager.decide(aid, "u", "approved")["status"], "pending")
                self.assertEqual(manager.record_auto_review(aid, "approved")["status"], "approved")
            else:
                self.assertEqual(manager.record_auto_review(aid, "approved")["status"], "pending")
                self.assertEqual(manager.decide(aid, "u", "approved")["status"], "approved")

    def test_rejection_and_expiration_are_terminal(self):
        self.manager.decide(self.id, "user", "approved")
        self.assertEqual(self.manager.record_auto_review(self.id, "rejected")["status"], "rejected")
        self.assertEqual(self.manager.decide(self.id, "user", "approved")["status"], "rejected")
        with self.assertRaises(ValueError):
            self.scope()

    def test_expired_approval_cannot_be_revived(self):
        self.manager.mark_expired(self.id)
        self.assertEqual(self.manager.decide(self.id, "user", "approved")["status"], "expired")
        self.assertEqual(self.manager.record_auto_review(self.id, "approved")["status"], "expired")
        with self.assertRaises(ValueError):
            self.scope()

    def test_another_executor_cannot_claim_the_same_approval(self):
        item = self.approved()
        alien = SimpleNamespace(**vars(self.terminal))
        with self.assertRaises(ValueError):
            approval_execution_scope(alien, self.args, item, task_id="task", tool_call_id="tool",
                                     expected_root=self.root, manager=self.manager, tool_name="run_command",
                                     fingerprint=command_fingerprint(self.args))
        self.scope(item)

    def test_one_approval_cannot_mint_multiple_grants(self):
        item = self.approved()
        scope = self.scope(item)
        with self.assertRaises(ValueError):
            self.scope(item)
        with bind_execution_scope(scope):
            self.assertTrue(scope.full_access_grant.consume(self.terminal, self.args))
            self.assertFalse(scope.full_access_grant.consume(self.terminal, self.args))

    def test_atomic_claim_has_single_winner(self):
        item = self.approved()
        def claim(_):
            try:
                self.manager.claim_execution(self.id, item)
                return 1
            except ValueError:
                return 0
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(claim, range(20))), 1)

    def test_authority_rejects_changed_command_and_identity(self):
        item = self.approved()
        for kwargs in ({"task_id": "other"}, {"tool_call_id": "other"}, {"tool_name": "write_file"}):
            with self.assertRaises(ValueError):
                self.scope(item, **kwargs)
        changed = dict(self.args, command="echo substituted")
        with self.assertRaises(ValueError):
            self.scope(item, changed)
        scope = self.scope(item)
        with bind_execution_scope(scope):
            self.assertFalse(scope.full_access_grant.consume(self.terminal, changed))
            self.assertTrue(scope.full_access_grant.consume(self.terminal, self.args))

    async def test_stop_and_wrong_tool_do_not_enter_executor(self):
        async def forbidden(*_):
            self.fail("executor was entered")
        self.terminal.handle_tool_call = forbidden
        scope = self.scope(self.approved())
        result = await execute_with_authority(self.terminal, "run_command", self.args, scope, lambda: True)
        self.assertEqual(json.loads(result)["code"], "task_cancelled")
        result = await execute_with_authority(self.terminal, "write_file", self.args, scope)
        self.assertEqual(json.loads(result)["code"], "full_access_denied")

    async def test_human_approval_does_not_cancel_auto_review(self):
        started = asyncio.Event()
        finish = asyncio.Event()
        async def reviewer(**_):
            started.set()
            await finish.wait()
            return {"decision": "approved", "reason": "审核通过"}
        events = []
        task = asyncio.create_task(run_full_access_review(
            manager=self.manager, approval_id=self.id, username="user", reviewer=reviewer,
            sender=lambda event, data: events.append((event, data)), timeout_seconds=2,
        ))
        await started.wait()
        self.assertEqual(self.manager.decide(self.id, "user", "approved")["status"], "pending")
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        finish.set()
        self.assertEqual((await task)["decision"], "approved")
        self.assertTrue(any(event == "auto_approval_progress" for event, _ in events))

    async def test_auto_approval_still_waits_for_human(self):
        async def reviewer(**_):
            return {"decision": "approved", "reason": "ok"}
        result = await run_full_access_review(manager=self.manager, approval_id=self.id, username="user",
                                              reviewer=reviewer, sender=lambda *_: None)
        self.assertEqual(result["decision"], "pending")
        with self.assertRaises(ValueError):
            self.scope(result["item"])

    async def test_review_timeout_and_cancel(self):
        async def reviewer(**_):
            await asyncio.Event().wait()
        result = await run_full_access_review(manager=self.manager, approval_id=self.id, username="user",
                                              reviewer=reviewer, sender=lambda *_: None, timeout_seconds=0.01)
        self.assertEqual(result["decision"], "rejected")
        self.assertEqual(self.manager.get(self.id)["auto_review_status"], "rejected")

    def test_child_access_and_request_validation(self):
        self.terminal.get_permission_mode = lambda: "readonly"
        self.terminal.get_work_mode = lambda: "plan"
        self.assertEqual(validate_child_access(self.terminal, "workspace_write"), "workspace_write")
        for level in ("sandbox_write", "full_access", [], {}, None):
            with self.assertRaises(ValueError):
                validate_child_access(self.terminal, level)
        with self.assertRaises(ValueError):
            validate_full_access_request(self.terminal, self.args)
        self.terminal.get_permission_mode = lambda: "unrestricted"
        self.terminal.get_work_mode = lambda: "execute"
        child = ExecutionScope("sub_agent", "child", self.root, "workspace_write")
        with bind_execution_scope(child), self.assertRaises(ValueError):
            validate_full_access_request(self.terminal, self.args)

    async def test_concurrent_contexts_do_not_leak(self):
        scopes = [ExecutionScope("sub_agent", str(i), self.root, level) for i, level in enumerate(
            ("workspace_write", "sandbox_write", "full_access"))]
        async def observe(scope):
            with bind_execution_scope(scope):
                await asyncio.sleep(0)
                self.assertIs(current_execution_scope(), scope)
        await asyncio.gather(*(observe(scope) for scope in scopes))
        self.assertIsNone(current_execution_scope())


if __name__ == "__main__":
    unittest.main()
