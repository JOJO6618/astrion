from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from support import RUNTIME
from modules.goal_state_manager import GoalStateManager
from server import goal_runtime as runtime


class Timer:
    created = []

    def __init__(self, delay, callback):
        self.delay, self.callback = delay, callback
        self.cancelled = False
        self.created.append(self)

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=RUNTIME)
        self.addCleanup(self.tmp.cleanup)
        self.gsm = GoalStateManager(self.tmp.name, "conv_A")
        self.gsm.start(goal="Finish report", task_id="first")
        self.rec = SimpleNamespace(username="tester", workspace_id="ws", conversation_id="conv_A",
            principal=SimpleNamespace(), task_id="first", goal_run_id=self.gsm.state["run_id"],
            stop_requested=False, goal_error=None, task_type="chat",
            task_params=SimpleNamespace(goal_action=None),
            directives=SimpleNamespace(auto_user_message_event=False))
        self.events = []
        self.manager = SimpleNamespace(_append_event=lambda r, k, d: self.events.append((k, d)))
        self.resolver = patch.object(runtime, "manager_for_record", side_effect=lambda r: GoalStateManager(self.tmp.name, r.conversation_id))
        self.resolver.start()
        self.addCleanup(self.resolver.stop)
        Timer.created.clear()
        timers = patch.object(runtime.threading, "Timer", Timer)
        timers.start()
        self.addCleanup(timers.stop)
        self.addCleanup(runtime._TIMERS.clear)

    def test_network_exhaustion_schedules_new_user_same_conversation(self):
        self.rec.goal_error = {"status_code": 503, "message": "unavailable", "attempt": 5}
        runtime.finish_goal_task(self.manager, self.rec)
        self.assertEqual(self.gsm.load()["status"], "retrying")
        timer = Timer.created[-1]
        self.assertGreater(timer.delay, 20)
        # A late child usage commit changes revision, but must not lose recovery.
        self.gsm.record_usage("child", 4, run_id=self.rec.goal_run_id)
        from server.runtime import runtime_service
        with patch.object(runtime_service, "list_runs", return_value=[]), patch.object(runtime_service, "create_task") as create:
            timer.callback()
        ctx = create.call_args.args[0]
        self.assertEqual(ctx.params.conversation_id, "conv_A")
        self.assertEqual(ctx.params.message_source, "goal_recovery")
        self.assertTrue(ctx.directives.auto_user_message_event)
        self.assertIn("Finish report", ctx.params.message)

    def test_quota_is_blocked_without_timer(self):
        self.rec.goal_error = {"status_code": 429, "error_code": "insufficient_quota"}
        runtime.finish_goal_task(self.manager, self.rec)
        self.assertEqual(self.gsm.load()["status"], "blocked")
        self.assertEqual(self.gsm.state["stopped_reason"], "quota_exhausted")
        self.assertFalse(Timer.created)

    def test_pause_and_close_invalidate_delayed_callbacks(self):
        from server.runtime import runtime_service
        for action in (lambda: self.gsm.pause("user_cancel"), lambda: self.gsm.mark_stopped("user_closed")):
            self.gsm.update(status="running", task_id="first")
            self.rec.goal_error = {"error_type": "ReadTimeout"}
            runtime.finish_goal_task(self.manager, self.rec)
            action()
            with patch.object(runtime_service, "create_task") as create:
                Timer.created[-1].callback()
            create.assert_not_called()

    def test_manual_continuation_retires_old_retry(self):
        from server.runtime import runtime_service
        self.rec.goal_error = {"message": "network error"}
        runtime.finish_goal_task(self.manager, self.rec)
        old = Timer.created[-1]
        self.gsm.pause("user_cancel")
        self.gsm.resume(task_id="manual")
        self.gsm.update(status="retrying", next_retry_at=1)
        with patch.object(runtime_service, "create_task") as create:
            old.callback()
        create.assert_not_called()

    def test_cancel_finalization_cannot_reschedule(self):
        self.rec.stop_requested = True
        self.rec.goal_error = {"message": "connection reset"}
        runtime.finish_goal_task(self.manager, self.rec)
        self.assertEqual(self.gsm.load()["status"], "paused")
        self.assertFalse(Timer.created)

    def test_late_task_cannot_change_new_task_state(self):
        self.gsm.update(task_id="newer")
        self.rec.goal_error = {"message": "connection reset"}
        runtime.finish_goal_task(self.manager, self.rec)
        self.assertEqual(self.gsm.load()["task_id"], "newer")
        self.assertEqual(self.gsm.state["status"], "running")
        self.assertFalse(Timer.created)

    def test_paused_notifications_rejected_but_user_message_not_resume(self):
        self.gsm.pause("user_cancel")
        self.rec.directives.auto_user_message_event = True
        with self.assertRaises(RuntimeError):
            runtime.prepare_goal_task(self.rec)
        self.rec.directives.auto_user_message_event = False
        self.rec.goal_run_id = None
        runtime.prepare_goal_task(self.rec)
        self.assertIsNone(self.rec.goal_run_id)
        self.assertEqual(self.gsm.load()["status"], "paused")

    def test_compression_does_not_adopt_goal(self):
        self.rec.task_type = "compression"
        self.rec.goal_run_id = None
        runtime.prepare_goal_task(self.rec)
        self.assertIsNone(self.rec.goal_run_id)

    def test_error_categories(self):
        cases = [({"status_code": "401"}, "authentication"),
                 ({"status_code": 400}, "configuration"),
                 ({"status_code": 429}, "retryable"),
                 ({"status_code": 502}, "retryable"),
                 ({"error_type": "RemoteProtocolError"}, "retryable"),
                 ({"message": "outof usage"}, "quota_exhausted"),
                 ({"message": "opaque failure"}, "unknown_error")]
        for error, category in cases:
            self.assertEqual(runtime.classify_error(error), category)


if __name__ == "__main__":
    unittest.main()
