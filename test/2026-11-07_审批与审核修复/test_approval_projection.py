"""审核失败终态在显示投影中的行为（2026-11-07 修复）。"""

import unittest

from server.conversation_view.projector import RuntimeProjection


def _event(idx, event_type, data):
    return {"idx": idx, "type": event_type, "data": data, "ts": 0}


def _approval(status="pending", approval_id="approval_1"):
    return {
        "approval_id": approval_id,
        "tool_name": "run_command",
        "status": status,
        "conversation_id": "conv_1",
        "auto_review_required": True,
    }


class ApprovalProjectionTest(unittest.TestCase):
    def setUp(self):
        self.projection = RuntimeProjection("task_1", "conv_1")

    def _apply(self, events):
        for event in events:
            self.projection.apply(event)
        return self.projection.state

    def test_review_error_clears_pending_and_records_terminal(self):
        state = self._apply([
            _event(0, "tool_approval_required", {"approval": _approval(), "conversation_id": "conv_1"}),
            _event(1, "auto_approval_progress", {
                "approval_id": "approval_1", "progress": {"stage": "done", "decision": "error"},
            }),
            _event(2, "tool_approval_resolved", {
                "approval_id": "approval_1", "decision": "error", "reason": "审核请求失败",
                "conversation_id": "conv_1",
            }),
        ])
        self.assertEqual(state["pending_tool_approvals"], [])
        self.assertIn("approval_1", state["resolved_tool_approval_ids"])
        records = [r for r in state["approval_review_records"] if r.get("approval_id") == "approval_1"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["final_decision"], "error")

    def test_terminal_required_does_not_reenter_pending(self):
        # 审核失败后 full_access 的 _finish 会重发带 expired 终态的 required。
        state = self._apply([
            _event(0, "tool_approval_required", {"approval": _approval(status="expired")}),
        ])
        self.assertEqual(state["pending_tool_approvals"], [])

    def test_pending_update_still_updates_existing_row(self):
        state = self._apply([
            _event(0, "tool_approval_required", {"approval": _approval()}),
            _event(1, "tool_approval_required", {
                "approval": {**_approval(), "auto_review_status": "reviewing"},
            }),
        ])
        self.assertEqual(len(state["pending_tool_approvals"]), 1)
        self.assertEqual(state["pending_tool_approvals"][0]["auto_review_status"], "reviewing")

    def test_timeout_still_resolves(self):
        state = self._apply([
            _event(0, "tool_approval_required", {"approval": _approval()}),
            _event(1, "tool_approval_resolved", {"approval_id": "approval_1", "decision": "timeout"}),
        ])
        self.assertEqual(state["pending_tool_approvals"], [])


if __name__ == "__main__":
    unittest.main()
