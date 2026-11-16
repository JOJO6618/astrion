"""store.py 通知投递：available 认领、ack token 隔离、失败记录与 2 分钟后重领。"""
from __future__ import annotations

import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

import schedule_fixtures as fx
from modules.scheduled_tasks import clock
from modules.scheduled_tasks.store import ScheduleStore

UTC = fx.UTC
T0 = fx.T0


def iso(dt) -> str:
    return clock.iso(dt)


class NotificationCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "schedules.sqlite3"
        self.store = ScheduleStore(self.path)

    def plan(self, username="alice", plan_id="p1", name="s"):
        definition = fx.plan_definition(name=name, enabled=False)
        return self.store.put(username, definition | {"id": plan_id}, fx.PRINCIPAL, fx.SOURCE, now=T0)

    def complete(self, username="alice", plan_id="p1", request="req"):
        run = self.store.manual_run(username, plan_id, request)
        self.assertTrue(self.store.update_run(run["id"], state="success", finished_at=iso(T0)))
        return self.store.run(run["id"])

    def available(self, run):
        self.assertTrue(self.store.update_run(run["id"], notification_status="available"))
        return self.store.run(run["id"])

    def claim(self, username="alice", now=None):
        return self.store.claim_notifications(username, now=now or T0)


class PendingTest(NotificationCase):
    def test_pending_notifications_only_lists_terminal_pending(self):
        self.plan()
        queued = self.store.manual_run("alice", "p1", "queued")
        done = self.complete(request="done")
        pending_ids = {item["id"] for item in self.store.pending_notifications()}
        self.assertIn(done["id"], pending_ids)
        self.assertNotIn(queued["id"], pending_ids)

    def test_available_runs_are_not_pending(self):
        self.plan()
        run = self.available(self.complete())
        self.assertEqual(self.store.pending_notifications(), [])

    def test_claim_marks_delivering_and_returns_token(self):
        self.plan()
        run = self.available(self.complete())
        claimed = self.claim()
        self.assertEqual([item["id"] for item in claimed], [run["id"]])
        self.assertTrue(claimed[0]["notification_token"])
        stored = self.store.run(run["id"])
        self.assertEqual(stored["notification_status"], "delivering")
        self.assertEqual(stored["notification_token"], claimed[0]["notification_token"])
        self.assertEqual(stored["notification_claimed_at"], iso(T0))

    def test_claim_twice_before_redelivery_window_is_empty(self):
        self.plan()
        self.available(self.complete())
        self.assertEqual(len(self.claim()), 1)
        self.assertEqual(self.claim(now=T0 + timedelta(seconds=119)), [])

    def test_claim_is_scoped_to_user(self):
        self.plan("alice", "pa")
        self.plan("bob", "pb")
        alice_run = self.available(self.complete("alice", "pa", "a1"))
        bob_run = self.available(self.complete("bob", "pb", "b1"))
        claimed = self.claim("alice")
        self.assertEqual([item["id"] for item in claimed], [alice_run["id"]])
        self.assertEqual(self.store.run(bob_run["id"])["notification_status"], "available")


class AckTest(NotificationCase):
    def test_ack_with_token_delivers(self):
        self.plan()
        run = self.available(self.complete())
        token = self.claim()[0]["notification_token"]
        self.assertTrue(self.store.ack_notification("alice", run["id"], token))
        stored = self.store.run(run["id"])
        self.assertEqual(stored["notification_status"], "delivered")
        self.assertIsNone(stored["notification_token"])
        self.assertIsNone(stored["notification_error"])

    def test_ack_with_error_records_failure(self):
        self.plan()
        run = self.available(self.complete())
        token = self.claim()[0]["notification_token"]
        self.assertTrue(self.store.ack_notification("alice", run["id"], token, error="delivery-boom"))
        stored = self.store.run(run["id"])
        self.assertEqual(stored["notification_status"], "failed")
        self.assertEqual(stored["notification_error"], "delivery-boom")

    def test_ack_token_isolation(self):
        self.plan("alice", "pa")
        self.plan("bob", "pb")
        alice_run = self.available(self.complete("alice", "pa", "a1"))
        bob_run = self.available(self.complete("bob", "pb", "b1"))
        alice_token = self.claim("alice")[0]["notification_token"]
        bob_token = self.claim("bob")[0]["notification_token"]
        self.assertFalse(self.store.ack_notification("bob", alice_run["id"], alice_token))
        self.assertFalse(self.store.ack_notification("alice", bob_run["id"], alice_token))
        self.assertFalse(self.store.ack_notification("alice", alice_run["id"], bob_token))
        self.assertFalse(self.store.ack_notification("alice", alice_run["id"], "forged"))
        self.assertEqual(self.store.run(alice_run["id"])["notification_status"], "delivering")
        self.assertTrue(self.store.ack_notification("alice", alice_run["id"], alice_token))
        self.assertTrue(self.store.ack_notification("bob", bob_run["id"], bob_token))

    def test_ack_requires_delivering_state(self):
        self.plan()
        run = self.complete()
        self.assertFalse(self.store.ack_notification("alice", run["id"], "anything"))
        self.available(self.store.run(run["id"]))
        token = self.claim()[0]["notification_token"]
        self.assertTrue(self.store.ack_notification("alice", run["id"], token))
        self.assertFalse(self.store.ack_notification("alice", run["id"], token))

    def test_stale_delivery_is_reclaimable_with_new_token(self):
        self.plan()
        run = self.available(self.complete())
        first = self.claim(now=T0)
        first_token = first[0]["notification_token"]
        second = self.claim(now=T0 + timedelta(minutes=2, seconds=1))
        self.assertEqual([item["id"] for item in second], [run["id"]])
        second_token = second[0]["notification_token"]
        self.assertNotEqual(first_token, second_token)
        self.assertFalse(self.store.ack_notification("alice", run["id"], first_token))
        self.assertTrue(self.store.ack_notification("alice", run["id"], second_token))
        self.assertEqual(self.store.run(run["id"])["notification_status"], "delivered")

    def test_claim_batch_limit_is_ten(self):
        self.plan()
        runs = [self.available(self.complete(request=f"r{index}")) for index in range(12)]
        assert len(runs) == 12
        first = self.claim()
        second = self.claim(now=T0 + timedelta(seconds=1))
        self.assertEqual(len(first), 10)
        self.assertEqual(len(second), 2)
        tokens = {item["notification_token"] for item in first + second}
        self.assertEqual(len(tokens), 12)

    def test_update_run_rejects_token_fields(self):
        self.plan()
        run = self.complete()
        with self.assertRaises(ValueError):
            self.store.update_run(run["id"], notification_token="x")
        with self.assertRaises(ValueError):
            self.store.update_run(run["id"], notification_claimed_at=iso(T0))


if __name__ == "__main__":
    unittest.main()
