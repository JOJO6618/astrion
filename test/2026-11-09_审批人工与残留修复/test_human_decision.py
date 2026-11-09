import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from modules.tool_approval_manager import ToolApprovalManager  # noqa: E402


ARGS = {"command": "echo hi", "request_full_access": True, "full_access_reason": "need host"}


def _create(manager, *, human, auto=True):
    return manager.create_request(
        username="user",
        conversation_id="conv",
        task_id="task",
        tool_call_id="tool",
        tool_name="run_command",
        arguments=ARGS,
        preview={},
        approval_type="full_access",
        auto_review_required=auto,
        executor_id=1,
        workspace_root="/tmp",
        execution_mode="sandbox",
        human_approval_required=human,
    )


class FullAccessHumanDecisionTests(unittest.TestCase):
    def setUp(self):
        self.manager = ToolApprovalManager()

    def test_automatic_only_request_rejects_human_decision(self):
        item = _create(self.manager, human=False, auto=True)
        with self.assertRaises(ValueError):
            self.manager.decide(item["approval_id"], "user", "approved")
        row = self.manager.get(item["approval_id"])
        self.assertEqual(row["status"], "pending")
        self.assertIsNone(row["human_decision"])

    def test_automatic_only_request_is_approved_by_review_only(self):
        item = _create(self.manager, human=False, auto=True)
        decided = self.manager.record_auto_review(item["approval_id"], "approved", reason="ok")
        self.assertEqual(decided["status"], "approved")
        claimed = self.manager.claim_execution(
            item["approval_id"],
            {
                "username": "user",
                "conversation_id": "conv",
                "task_id": "task",
                "tool_call_id": "tool",
                "tool_name": "run_command",
                "arguments": ARGS,
                "approval_type": "full_access",
                "executor_id": 1,
                "workspace_root": "/tmp",
                "execution_mode": "sandbox",
                "human_approval_required": False,
                "auto_review_required": True,
            },
        )
        self.assertEqual(claimed["status"], "approved")

    def test_dual_gate_still_accepts_human_decision(self):
        item = _create(self.manager, human=True, auto=True)
        row = self.manager.decide(item["approval_id"], "user", "approved")
        self.assertEqual(row["status"], "pending")
        self.assertEqual(row["human_decision"], "approved")
        final = self.manager.record_auto_review(item["approval_id"], "approved", reason="ok")
        self.assertEqual(final["status"], "approved")


if __name__ == "__main__":
    unittest.main()
