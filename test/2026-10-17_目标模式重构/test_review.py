import asyncio
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from support import RUNTIME
from modules.goal_state_manager import GoalStateManager
from modules.goal_review_agent import GoalReviewAgent
from modules.execution_scope import current_execution_scope
from server import goal_flow


class ReviewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=RUNTIME)
        self.addCleanup(self.tmp.cleanup)
        self.gsm = GoalStateManager(self.tmp.name, "conv_A")
        self.gsm.start(goal="Write a short poem", max_turns=1, task_id="first")
        self.terminal = SimpleNamespace(data_dir=self.tmp.name)
        self.kwargs = dict(web_terminal=self.terminal, workspace=SimpleNamespace(data_dir=self.tmp.name),
            messages=[{"role": "user", "content": "Write a poem"},
                      {"role": "assistant", "content": "The finished poem"}],
            sender=lambda *args: None, conversation_id="conv_A",
            assistant_content="The finished poem", made_tool_call=False, total_tool_calls=0)

    async def test_text_without_tools_can_complete_at_limit(self):
        with patch.object(goal_flow, "_review_with_recovery", AsyncMock(return_value={"status": "done", "message": "Poem delivered"})):
            result = await goal_flow.handle_goal_after_turn(**self.kwargs)
        self.assertEqual(result["action"], "done")
        self.assertEqual(self.gsm.load()["status"], "done")

    async def test_limit_pauses_only_after_review_says_continue(self):
        with patch.object(goal_flow, "_review_with_recovery", AsyncMock(return_value={"status": "continue", "message": "Need revision"})):
            result = await goal_flow.handle_goal_after_turn(**self.kwargs)
        self.assertEqual(result["reason"], "max_turns")
        self.assertEqual(self.gsm.load()["status"], "paused")

    async def test_close_during_review_cannot_reactivate(self):
        async def review(**kwargs):
            self.gsm.mark_stopped("user_closed")
            return {"status": "continue", "message": "Do more"}
        with patch.object(goal_flow, "_review_with_recovery", side_effect=review), patch.object(goal_flow, "inject_runtime_user_message") as inject:
            await goal_flow.handle_goal_after_turn(**self.kwargs)
        inject.assert_not_called()
        self.assertEqual(self.gsm.load()["status"], "stopped")

    async def test_review_network_failure_retries_review_not_main_task(self):
        replies = [{"status": "error", "error_type": "ReadTimeout", "message": "timeout"},
                   {"status": "done", "message": "verified"}]
        fake_review = SimpleNamespace(review=AsyncMock(side_effect=replies))
        # Skip only the cancellable wait; no real network or 30-second sleep.
        clock = [0, 100]
        with patch.object(goal_flow, "GoalReviewAgent", return_value=fake_review), patch.object(goal_flow.time, "monotonic", side_effect=lambda: clock.pop(0) if clock else 100):
            result = await goal_flow._review_with_recovery(gsm=self.gsm, run_id=self.gsm.state["run_id"],
                web_terminal=self.terminal, payload_text="evidence", sender=lambda *a: None, conversation_id="conv_A")
        self.assertEqual(result["status"], "done")
        self.assertEqual(fake_review.review.await_count, 2)

    async def test_quota_during_review_blocks_immediately(self):
        fake_review = SimpleNamespace(review=AsyncMock(return_value={"status": "error", "code": "usage_limit", "message": "usage exhausted"}))
        with patch.object(goal_flow, "GoalReviewAgent", return_value=fake_review):
            result = await goal_flow._review_with_recovery(gsm=self.gsm, run_id=self.gsm.state["run_id"],
                web_terminal=self.terminal, payload_text="evidence", sender=lambda *a: None, conversation_id="conv_A")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "quota_exhausted")
        self.assertEqual(fake_review.review.await_count, 1)

    async def test_evidence_command_forces_sandbox_without_mutating_parent(self):
        scopes = []
        async def command(**kwargs):
            scopes.append(current_execution_scope())
            self.assertFalse(kwargs["sandbox_write_access"])
            return {"success": True}
        reviewer = object.__new__(GoalReviewAgent)
        reviewer.cfg = {}
        reviewer.web_terminal = SimpleNamespace(
            context_manager=SimpleNamespace(project_path=self.tmp.name),
            terminal_ops=SimpleNamespace(host_execution_mode="direct", run_command=command))
        result = await reviewer._run_readonly_command("ls")
        self.assertTrue(result["success"])
        self.assertEqual(scopes[0].execution_mode, "sandbox")
        self.assertEqual(reviewer.web_terminal.terminal_ops.host_execution_mode, "direct")
        self.assertIsNone(current_execution_scope())


if __name__ == "__main__":
    unittest.main()
