from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from support import RUNTIME
from flask import Flask
from modules.goal_state_manager import GoalStateManager
from server.tasks import api
from server import goal_runtime


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=RUNTIME)
        self.addCleanup(self.tmp.cleanup)
        self.gsm = GoalStateManager(self.tmp.name, "conv_A")
        self.gsm.start(goal="Deliver", task_id="task1")
        self.rec = SimpleNamespace(conversation_id="conv_A", task_id="task1", status="running")
        self.data = SimpleNamespace(workspace_id="ws", manager_for=lambda cid: SimpleNamespace(read_conversation=lambda cid: {}))
        self.workspace = SimpleNamespace(data_dir=self.tmp.name)
        self.endpoint = api.control_goal_api
        while hasattr(self.endpoint, "__wrapped__"):
            self.endpoint = self.endpoint.__wrapped__
        self.app = Flask(__name__)

    def control(self, payload):
        with self.app.test_request_context(json=payload), patch.object(api.task_manager, "list_tasks", return_value=[self.rec]), patch.object(api.task_manager, "_append_event") as emit, patch.object(api.task_manager, "cancel_task", side_effect=AssertionError("Goal close must not cancel task")):
            response = self.endpoint("conv_A", self.data, self.workspace, "tester")
            return response, emit

    def test_close_persists_and_publishes_without_cancel(self):
        response, emit = self.control({"action": "close", "run_id": self.gsm.state["run_id"]})
        self.assertTrue(response.json["success"])
        self.assertEqual(response.json["data"]["status"], "stopped")
        self.assertEqual(self.rec.status, "running")
        self.assertEqual(self.gsm.load()["status"], "stopped")
        self.assertEqual(emit.call_args.args[1], "goal_progress")

    def test_old_goal_control_cannot_close_new_goal(self):
        (response, status), emit = self.control({"action": "close", "run_id": "old-goal"})
        self.assertEqual(status, 409)
        self.assertEqual(self.gsm.load()["status"], "running")
        emit.assert_not_called()

    def test_pause_without_task_cancel_can_be_manually_continued(self):
        self.control({"action": "pause", "run_id": self.gsm.state["run_id"]})
        self.assertEqual(self.gsm.load()["status"], "paused")
        rec = SimpleNamespace(task_type="chat", task_id="continued", message="Continue", conversation_id="conv_A",
            task_params=SimpleNamespace(goal_action="continue", goal_options=None), goal_run_id=None)
        with patch.object(goal_runtime, "manager_for_record", return_value=self.gsm):
            goal_runtime.prepare_goal_task(rec)
        self.assertEqual(rec.goal_run_id, self.gsm.state["run_id"])
        self.assertEqual(self.gsm.state["task_id"], "continued")
        self.assertEqual(self.gsm.state["status"], "running")


if __name__ == "__main__":
    unittest.main()
