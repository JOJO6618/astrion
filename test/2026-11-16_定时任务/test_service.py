"""service.py 回归：FakeAdapter 驱动的 busy skip/merge、停止、审批超时、通知与启动恢复。"""
from __future__ import annotations

import logging
import tempfile
import time
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

import schedule_fixtures as fx
from modules.scheduled_tasks import clock
from modules.scheduled_tasks.service import BusyError, SchedulerService
from modules.scheduled_tasks.store import ScheduleStore

UTC = fx.UTC
T0 = fx.T0


def iso(dt) -> str:
    return clock.iso(dt)


class FakeAdapter:
    """记录调用的执行端替身；never 触碰真实 runtime/config。"""

    def __init__(self):
        self.started: list[str] = []
        self.cancelled: list[str] = []
        self.observed: dict[str, dict] = {}
        self.busy: set[str] = set()
        self.fail_start: dict[str, str] = {}
        self.notify_calls: list[str] = []
        self.notify_status = "available"
        self.notify_exc: Exception | None = None
        # 永久性观察失败（资源已失效）：service 应 cancel 并置 interrupted。
        self.observe_exc: Exception | None = None

    def validate(self, definition, principal):
        return {"username": principal.get("username") or principal.get("user"), "role": "user"}

    def start(self, run):
        self.started.append(run["id"])
        if run["id"] in self.fail_start:
            raise RuntimeError(self.fail_start[run["id"]])
        if run["id"] in self.busy:
            raise BusyError("conversation_busy")
        return {"task_id": f"task-{run['id']}", "conversation_id": "conv-1"}

    def observe(self, run):
        if self.observe_exc is not None:
            raise self.observe_exc
        return self.observed.get(run["id"], {"state": "running"})

    def cancel(self, run):
        self.cancelled.append(run["id"])

    def notify(self, run):
        self.notify_calls.append(run["id"])
        if self.notify_exc is not None:
            raise self.notify_exc
        return self.notify_status


class ServiceCase(unittest.TestCase):
    def setUp(self):
        # 预期内的异常路径会走 logger.exception，这里不污染测试输出。
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "schedules.sqlite3"
        self.store = ScheduleStore(self.path)
        self.adapter = FakeAdapter()
        self.service = SchedulerService(self.store, self.adapter)
        self.addCleanup(self.service.stop)

    def plan(self, *, enabled=False, policies=None, notifications=False, name="s", schedule=None):
        definition = fx.plan_definition(name=name, enabled=enabled, schedule=schedule, policies=policies)
        definition["notifications"] = {"enabled": notifications}
        return self.store.put("alice", definition, fx.PRINCIPAL, fx.SOURCE, now=T0)

    def queued(self, plan, request="req"):
        return self.store.manual_run("alice", plan["id"], request)

    def run_state(self, run):
        return self.store.run(run["id"])


class BusyTest(ServiceCase):
    def test_busy_skip_marks_latest_skipped_and_does_not_start(self):
        plan = self.plan()
        active = self.queued(plan, "active")
        self.store.update_run(active["id"], state="running", task_id="task-active")
        latest = self.queued(plan, "latest")
        self.service.tick(T0)
        self.assertEqual(self.run_state(active)["state"], "running")
        skipped = self.run_state(latest)
        self.assertEqual(skipped["state"], "skipped")
        self.assertEqual(skipped["reason"], "previous_occurrence_running")
        self.assertIsNotNone(skipped["finished_at"])
        self.assertEqual(self.adapter.started, [])

    def test_busy_merge_keeps_only_latest_queued(self):
        plan = self.plan(policies={"busy": "merge", "missed": "skip", "approval_timeout_seconds": 3600})
        active = self.queued(plan, "active")
        self.store.update_run(active["id"], state="running", task_id="task-active")
        older = self.queued(plan, "older")
        time.sleep(0.003)
        newer = self.queued(plan, "newer")
        self.service.tick(T0)
        self.assertEqual(self.run_state(older)["state"], "skipped")
        self.assertEqual(self.run_state(older)["reason"], "merged_into_latest")
        merged = self.run_state(newer)
        self.assertEqual(merged["state"], "queued")
        self.assertEqual(merged["reason"], "previous_occurrence_running")
        self.assertIsNone(merged["started_at"])
        self.assertEqual(self.adapter.started, [])

    def test_merge_latest_starts_after_previous_finishes(self):
        plan = self.plan(policies={"busy": "merge", "missed": "skip", "approval_timeout_seconds": 3600})
        active = self.queued(plan, "active")
        self.store.update_run(active["id"], state="running", task_id="task-active")
        older = self.queued(plan, "older")
        time.sleep(0.003)
        newer = self.queued(plan, "newer")
        self.service.tick(T0)
        self.adapter.observed[active["id"]] = {"state": "success", "summary": "done"}
        self.service.tick(T0 + timedelta(seconds=1))
        self.assertEqual(self.run_state(active)["state"], "success")
        started = self.run_state(newer)
        self.assertEqual(started["state"], "running")
        self.assertEqual(started["task_id"], f"task-{newer['id']}")
        self.assertEqual(started["conversation_id"], "conv-1")
        self.assertEqual(self.run_state(older)["state"], "skipped")

    def test_adapter_busy_is_skipped_with_reason(self):
        plan = self.plan()
        run = self.queued(plan)
        self.adapter.busy.add(run["id"])
        self.service.tick(T0)
        stored = self.run_state(run)
        self.assertEqual(stored["state"], "skipped")
        self.assertEqual(stored["reason"], "conversation_busy")
        self.assertEqual(self.adapter.started, [run["id"]])

    def test_admission_error_marks_failed(self):
        plan = self.plan()
        run = self.queued(plan)
        self.adapter.fail_start[run["id"]] = "admission-boom"
        self.service.tick(T0)
        stored = self.run_state(run)
        self.assertEqual(stored["state"], "failed")
        self.assertEqual(stored["reason"], "admission-boom")
        self.assertIsNotNone(stored["finished_at"])
        self.service.tick(T0 + timedelta(seconds=1))
        self.assertEqual(len(self.adapter.started), 1)


class StopTest(ServiceCase):
    def test_stop_queued_is_terminal_without_adapter_cancel(self):
        plan = self.plan()
        run = self.queued(plan)
        result = self.service.manage("alice", plan["id"], "stop", run_id=run["id"])
        self.assertEqual(result["state"], "stopped")
        stored = self.run_state(run)
        self.assertEqual(stored["state"], "stopped")
        self.assertEqual(stored["reason"], "user_stopped")
        self.assertIs(stored["stop_requested"], True)
        self.assertIsNotNone(stored["finished_at"])
        self.assertEqual(self.adapter.cancelled, [])

    def test_stop_running_requests_cancel_until_terminal(self):
        plan = self.plan()
        run = self.queued(plan)
        self.service.tick(T0)
        self.assertEqual(self.run_state(run)["state"], "running")
        result = self.service.manage("alice", plan["id"], "stop", run_id=run["id"])
        self.assertIs(result["stop_requested"], True)
        self.assertEqual(self.run_state(run)["state"], "running")
        self.assertIn(run["id"], self.adapter.cancelled)
        self.adapter.observed[run["id"]] = {"state": "stopped"}
        self.service.tick(T0 + timedelta(seconds=1))
        stored = self.run_state(run)
        self.assertEqual(stored["state"], "stopped")
        self.assertEqual(stored["reason"], "user_stopped")
        self.assertIsNotNone(stored["finished_at"])

    def test_stop_terminal_run_is_noop(self):
        plan = self.plan()
        run = self.queued(plan)
        self.store.update_run(run["id"], state="success", finished_at=iso(T0))
        result = self.service.manage("alice", plan["id"], "stop", run_id=run["id"])
        self.assertEqual(result["state"], "success")
        self.assertEqual(self.adapter.cancelled, [])

    def test_stop_validation(self):
        plan = self.plan()
        other = self.plan(name="other")
        run = self.queued(plan)
        with self.assertRaises(ValueError):
            self.service.manage("alice", plan["id"], "stop")
        with self.assertRaises(ValueError):
            self.service.manage("alice", other["id"], "stop", run_id=run["id"])
        with self.assertRaises(ValueError):
            self.service.manage("alice", plan["id"], "bogus")


class ApprovalTimeoutTest(ServiceCase):
    def test_waiting_approval_times_out_to_failed(self):
        plan = self.plan(policies={"busy": "skip", "missed": "skip", "approval_timeout_seconds": 60})
        run = self.queued(plan)
        self.service.tick(T0)
        self.adapter.observed[run["id"]] = {"state": "waiting"}
        self.service.tick(T0 + timedelta(seconds=10))
        waiting = self.run_state(run)
        self.assertEqual(waiting["state"], "waiting")
        self.assertEqual(waiting["waiting_since"], iso(T0 + timedelta(seconds=10)))
        self.service.tick(T0 + timedelta(seconds=30))
        self.assertEqual(self.run_state(run)["waiting_since"], iso(T0 + timedelta(seconds=10)))
        self.service.tick(T0 + timedelta(seconds=70))
        timed_out = self.run_state(run)
        self.assertEqual(timed_out["state"], "stopping")
        self.assertIs(timed_out["stop_requested"], True)
        self.assertEqual(timed_out["reason"], "approval_timeout")
        self.assertIn(run["id"], self.adapter.cancelled)
        self.adapter.observed[run["id"]] = {"state": "stopped"}
        self.service.tick(T0 + timedelta(seconds=71))
        final = self.run_state(run)
        self.assertEqual(final["state"], "failed")
        self.assertEqual(final["reason"], "approval_timeout")
        self.assertIsNotNone(final["finished_at"])

    def test_waiting_since_clears_when_running_again(self):
        plan = self.plan(policies={"busy": "skip", "missed": "skip", "approval_timeout_seconds": 60})
        run = self.queued(plan)
        self.service.tick(T0)
        self.adapter.observed[run["id"]] = {"state": "waiting"}
        self.service.tick(T0 + timedelta(seconds=5))
        self.assertIsNotNone(self.run_state(run)["waiting_since"])
        self.adapter.observed[run["id"]] = {"state": "running"}
        self.service.tick(T0 + timedelta(seconds=10))
        stored = self.run_state(run)
        self.assertEqual(stored["state"], "running")
        self.assertIsNone(stored["waiting_since"])


class ServiceNotificationTest(ServiceCase):
    def complete(self, plan, request="req"):
        run = self.queued(plan, request)
        self.service.tick(T0)
        self.adapter.observed[run["id"]] = {"state": "success", "summary": "ok"}
        self.service.tick(T0 + timedelta(seconds=1))
        return self.run_state(run)

    def test_enabled_notification_becomes_available(self):
        plan = self.plan(notifications=True)
        run = self.complete(plan)
        self.assertEqual(run["state"], "success")
        self.assertEqual(run["notification_status"], "available")
        self.assertEqual(self.adapter.notify_calls, [run["id"]])
        claimed = self.store.claim_notifications("alice", now=T0 + timedelta(seconds=2))
        self.assertEqual([item["id"] for item in claimed], [run["id"]])
        self.assertTrue(self.store.ack_notification("alice", run["id"], claimed[0]["notification_token"]))
        self.assertEqual(self.run_state(run)["notification_status"], "delivered")

    def test_disabled_notification_skips_adapter(self):
        plan = self.plan(notifications=False)
        run = self.complete(plan)
        self.assertEqual(run["notification_status"], "disabled")
        self.assertEqual(self.adapter.notify_calls, [])

    def test_notification_exception_records_failure(self):
        plan = self.plan(notifications=True)
        self.adapter.notify_exc = RuntimeError("notify-boom")
        run = self.complete(plan)
        self.assertEqual(run["notification_status"], "failed")
        self.assertEqual(run["notification_error"], "notify-boom")
        self.assertEqual(self.store.claim_notifications("alice"), [])
class TickQueuedMergeTest(ServiceCase):
    """无 active 时多个 queued + merge：只启动最新，旧的同轮 skip，不留给下轮。"""

    def test_multiple_queued_merge_starts_only_newest_in_same_tick(self):
        plan = self.plan(policies={"busy": "merge", "missed": "skip", "approval_timeout_seconds": 3600})
        runs = [self.queued(plan, f"req-{index}") for index in range(3)]
        with self.store.transaction() as db:
            for index, run in enumerate(runs):
                db.execute(
                    "UPDATE runs SET created_at=? WHERE id=?",
                    (iso(fx.at(16, 0, 0, second=index)), run["id"]),
                )

        self.service.tick(T0)
        newest = self.run_state(runs[-1])
        self.assertEqual(newest["state"], "running")
        self.assertEqual(newest["task_id"], f"task-{runs[-1]['id']}")
        self.assertEqual(self.adapter.started, [runs[-1]["id"]])

        for older in runs[:-1]:
            stored = self.run_state(older)
            self.assertEqual(stored["state"], "skipped")
            self.assertEqual(stored["reason"], "merged_into_latest")
            self.assertIsNotNone(stored["finished_at"])
        self.assertEqual(self.store.runs("alice", plan["id"], states={"queued"}), [])

        # 旧 run 已在同一轮结束，下一轮不会再次启动它们。
        self.service.tick(T0 + timedelta(seconds=1))
        self.assertEqual(self.adapter.started, [runs[-1]["id"]])


class AdmissionCancellationTest(ServiceCase):
    def test_store_update_failure_after_admission_cancels_task(self):
        plan = self.plan()
        run = self.queued(plan)
        real_update = self.store.update_run

        def flaky(run_id, *, expected=None, **values):
            if values.get("state") == "running":
                return False
            return real_update(run_id, expected=expected, **values)

        with mock.patch.object(self.store, "update_run", side_effect=flaky):
            self.service.tick(T0)

        self.assertEqual(self.adapter.started, [run["id"]])
        self.assertEqual(self.adapter.cancelled, [run["id"]])
        stored = self.run_state(run)
        self.assertEqual(stored["state"], "failed")
        self.assertEqual(stored["reason"], "受理后的运行记录已失效")
        self.assertIsNotNone(stored["finished_at"])
        self.assertIsNone(stored["task_id"])

        # 已取消受理记录不会再被下一轮当成孤儿继续。
        self.service.tick(T0 + timedelta(seconds=1))
        self.assertEqual(self.adapter.started, [run["id"]])


class ObserveResourceFailureTest(ServiceCase):
    def test_permanent_observe_errors_cancel_and_interrupt(self):
        errors = (PermissionError("resource-gone"), KeyError("resource-gone"), ValueError("resource-gone"))
        for index, error in enumerate(errors):
            with self.subTest(error=type(error).__name__):
                plan = self.plan(name=f"p{index}")
                run = self.queued(plan, f"observe-{index}")
                self.service.tick(T0)
                self.assertEqual(self.run_state(run)["state"], "running")

                self.adapter.observe_exc = error
                self.service.tick(T0 + timedelta(seconds=1))
                stored = self.run_state(run)
                self.assertEqual(stored["state"], "interrupted")
                self.assertEqual(stored["reason"], str(error))
                self.assertIsNotNone(stored["finished_at"])
                self.assertIn(run["id"], self.adapter.cancelled)
                self.adapter.observe_exc = None


class StopBackgroundStoppingTest(ServiceCase):
    def test_stop_while_background_active_reports_stopping(self):
        plan = self.plan()
        run = self.queued(plan)
        self.service.tick(T0)
        self.assertEqual(self.run_state(run)["state"], "running")

        requested = self.service.manage("alice", plan["id"], "stop", run_id=run["id"])
        self.assertIs(requested["stop_requested"], True)
        self.assertEqual(requested["state"], "running")

        # 后台仍 active：观察结果还是 running，但不能把停止请求当成已停止。
        self.service.tick(T0 + timedelta(seconds=1))
        stored = self.run_state(run)
        self.assertEqual(stored["state"], "stopping")
        self.assertIs(stored["stop_requested"], True)
        self.assertIsNone(stored["finished_at"])

        self.adapter.observed[run["id"]] = {"state": "stopped"}
        self.service.tick(T0 + timedelta(seconds=2))
        final = self.run_state(run)
        self.assertEqual(final["state"], "stopped")
        self.assertEqual(final["reason"], "user_stopped")
        self.assertIsNotNone(final["finished_at"])


class StartupTest(ServiceCase):
    def test_start_recovers_runs_and_rejects_second_owner(self):
        plan = self.plan()
        run = self.queued(plan)
        self.store.update_run(run["id"], state="running", task_id="task-x")
        second = SchedulerService(self.store, self.adapter)
        self.addCleanup(second.stop)
        self.assertTrue(self.service.start())
        self.assertEqual(self.run_state(run)["state"], "interrupted")
        self.assertEqual(self.run_state(run)["reason"], "process_restarted")
        self.assertFalse(second.start())
        self.assertTrue(second.error)
        self.service.stop()
        third = SchedulerService(self.store, self.adapter)
        self.addCleanup(third.stop)
        self.assertTrue(third.start())
        third.stop()


if __name__ == "__main__":
    unittest.main()
