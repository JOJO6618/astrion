"""store.py 回归：版本 CAS、owner 隔离、并发认领、missed 策略、暂停/删除、重启校准、manual 幂等。"""
from __future__ import annotations

import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import schedule_fixtures as fx
from modules.scheduled_tasks import clock
from modules.scheduled_tasks.store import ConflictError, ScheduleStore

UTC = fx.UTC
T0 = fx.T0


def iso(dt: datetime) -> str:
    return clock.iso(dt)


def tick(minutes: int = 0, seconds: int = 0) -> str:
    return iso(T0 + timedelta(minutes=minutes, seconds=seconds))


def day_at(day: int, hour: int = 0, minute: int = 0) -> str:
    return iso(datetime(2026, 11, day, hour, minute, tzinfo=UTC))


class StoreCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "schedules.sqlite3"
        self.store = ScheduleStore(self.path)

    def put(self, username="alice", definition=None, *, overwrite=False, now=T0, **kwargs):
        return self.store.put(
            username,
            definition if definition is not None else fx.plan_definition(**kwargs),
            fx.PRINCIPAL,
            fx.SOURCE,
            overwrite=overwrite,
            now=now,
        )


class PutAndVersionTest(StoreCase):
    def test_put_creates_revision_one_and_next_at(self):
        plan = self.put()
        self.assertEqual(plan["revision"], 1)
        self.assertTrue(plan["id"].startswith("schedule_"))
        self.assertIs(plan["enabled"], True)
        self.assertEqual(plan["next_at"], tick(minutes=1))
        self.assertEqual(plan["created_at"], iso(T0))
        self.assertEqual(plan["principal"], fx.PRINCIPAL)
        self.assertEqual(plan["source"], fx.SOURCE)

    def test_disabled_plan_has_no_next_at(self):
        plan = self.put(enabled=False)
        self.assertIsNone(plan["next_at"])
        self.assertIs(plan["enabled"], False)

    def test_explicit_id_is_kept(self):
        plan = self.put(definition=fx.plan_definition() | {"id": "plan_fixed"})
        self.assertEqual(plan["id"], "plan_fixed")

    def test_overwrite_requires_flag_and_current_revision(self):
        plan = self.put(name="old")
        with self.assertRaises(ConflictError):
            self.put(definition={**plan["definition"], "name": "new"})
        with self.assertRaises(ConflictError):
            self.put(definition={**plan["definition"], "name": "new", "revision": 99}, overwrite=True)
        without_revision = {key: value for key, value in plan["definition"].items() if key != "revision"}
        with self.assertRaises(ConflictError):
            # 覆盖时必须显式带上当前 revision，缺省不算。
            self.put(definition={**without_revision, "name": "new"}, overwrite=True)
        unchanged = self.store.get("alice", plan["id"])
        self.assertEqual(unchanged["revision"], 1)
        self.assertEqual(unchanged["definition"]["name"], "old")
        self.assertEqual(unchanged["next_at"], tick(minutes=1))

    def test_overwrite_with_current_revision_bumps_and_recomputes(self):
        plan = self.put()
        updated = self.put(
            definition={
                **plan["definition"],
                "name": "new",
                "schedule": fx.interval_schedule(every=2),
            },
            overwrite=True,
        )
        self.assertEqual(updated["revision"], 2)
        self.assertEqual(updated["definition"]["name"], "new")
        self.assertEqual(updated["next_at"], tick(minutes=2))

    def test_overwrite_missing_id_raises_keyerror(self):
        with self.assertRaises(KeyError):
            self.put(definition=fx.plan_definition() | {"id": "nope"}, overwrite=True)

    def test_enabled_once_in_past_is_rejected_without_record(self):
        past = fx.plan_definition(schedule=fx.once_schedule(date_text="2020-01-01"))
        with self.assertRaises(ValueError):
            self.put(definition=past)
        self.assertEqual(self.store.list("alice"), [])

    def test_disabled_once_in_past_is_allowed(self):
        past = fx.plan_definition(schedule=fx.once_schedule(date_text="2020-01-01"), enabled=False)
        plan = self.put(definition=past)
        self.assertIsNone(plan["next_at"])

    def test_definition_snapshot_contains_id_and_revision(self):
        plan = self.put()
        self.assertEqual(plan["definition"]["id"], plan["id"])
        self.assertEqual(plan["definition"]["revision"], 1)


class OwnerIsolationTest(StoreCase):
    def test_same_id_different_users_are_independent(self):
        alice = self.put(name="alice-plan")
        bob = self.put(username="bob", definition=fx.plan_definition(name="bob-plan") | {"id": alice["id"]})
        self.assertEqual(bob["revision"], 1)
        self.assertEqual(bob["definition"]["name"], "bob-plan")
        self.assertEqual(self.store.get("alice", alice["id"])["definition"]["name"], "alice-plan")
        self.assertEqual(len(self.store.list("alice")), 1)
        self.assertEqual(len(self.store.list("bob")), 1)
        self.assertEqual(self.store.list("bob")[0]["definition"]["name"], "bob-plan")

    def test_cross_user_access_raises(self):
        plan = self.put()
        with self.assertRaises(KeyError):
            self.store.get("bob", plan["id"])
        with self.assertRaises(KeyError):
            self.store.enable("bob", plan["id"], False)
        with self.assertRaises(KeyError):
            self.store.delete("bob", plan["id"])
        with self.assertRaises(KeyError):
            self.store.manual_run("bob", plan["id"], "r1")

    def test_runs_are_scoped_by_owner(self):
        plan = self.put()
        run = self.store.manual_run("alice", plan["id"], "req-1")
        self.assertEqual(self.store.runs("bob"), [])
        self.assertEqual([row["id"] for row in self.store.runs("alice")], [run["id"]])
        with self.assertRaises(KeyError):
            self.store.run(run["id"], "bob")
        self.assertEqual(self.store.run(run["id"], "alice")["id"], run["id"])


class ClaimTest(StoreCase):
    def test_due_plan_claimed_once(self):
        plan = self.put()
        claimed = self.store.claim_due(T0 + timedelta(minutes=1))
        self.assertEqual(len(claimed), 1)
        run = claimed[0]
        self.assertEqual(run["state"], "queued")
        self.assertEqual(run["trigger"], "schedule")
        self.assertEqual(run["scheduled_for"], tick(minutes=1))
        self.assertEqual(
            run["occurrence_key"],
            f"scheduled:alice:{plan['id']}:1:{tick(minutes=1)}",
        )
        self.assertEqual(run["definition"]["id"], plan["id"])
        self.assertEqual(run["definition"]["revision"], 1)
        self.assertEqual(self.store.get("alice", plan["id"])["next_at"], tick(minutes=2))

    def test_second_claim_same_instant_is_noop(self):
        self.put()
        self.assertEqual(len(self.store.claim_due(T0 + timedelta(minutes=1))), 1)
        self.assertEqual(self.store.claim_due(T0 + timedelta(minutes=1)), [])
        self.assertEqual(len(self.store.runs("alice")), 1)

    def test_not_due_plan_is_not_claimed(self):
        self.put()
        self.assertEqual(self.store.claim_due(T0), [])
        self.assertEqual(self.store.claim_due(T0 + timedelta(seconds=59)), [])

    def test_paused_and_deleted_plans_are_not_claimed(self):
        paused = self.put(definition=fx.plan_definition(name="paused") | {"id": "p1"})
        self.store.enable("alice", paused["id"], False)
        deleted = self.put(definition=fx.plan_definition(name="deleted") | {"id": "p2"})
        self.store.delete("alice", deleted["id"])
        self.assertEqual(self.store.claim_due(T0 + timedelta(minutes=5)), [])

    def test_claim_within_grace_keeps_original_occurrence(self):
        self.put(schedule=fx.daily_schedule(["09:00"]))
        claimed = self.store.claim_due(datetime(2026, 11, 16, 9, 0, 30, tzinfo=UTC))
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0]["state"], "queued")
        self.assertEqual(claimed[0]["scheduled_for"], day_at(16, 9, 0))

    def test_claim_after_grace_with_skip_records_single_skipped_run(self):
        self.put(schedule=fx.daily_schedule(["09:00"]))
        claimed = self.store.claim_due(datetime(2026, 11, 16, 9, 0, 31, tzinfo=UTC))
        self.assertEqual(len(claimed), 1)
        run = claimed[0]
        self.assertEqual(run["state"], "skipped")
        self.assertEqual(run["reason"], "missed")
        self.assertEqual(run["scheduled_for"], day_at(16, 9, 0))
        self.assertIsNotNone(run["finished_at"])
        self.assertEqual(self.store.get("alice", run["schedule_id"])["next_at"], day_at(17, 9, 0))

    def test_missed_skip_does_not_replay_every_tick(self):
        self.put(schedule=fx.interval_schedule(every=10))
        claimed = self.store.claim_due(T0 + timedelta(minutes=45))
        self.assertEqual(len(claimed), 1)
        run = claimed[0]
        self.assertEqual(run["state"], "skipped")
        self.assertEqual(run["reason"], "missed")
        # 只记录最近一次（00:40），不逐条重放 00:10/00:20/00:30。
        self.assertEqual(run["scheduled_for"], tick(minutes=40))
        self.assertEqual(len(self.store.runs("alice")), 1)
        self.assertEqual(self.store.get("alice", run["schedule_id"])["next_at"], tick(minutes=50))

    def test_missed_latest_records_single_queued_run_at_latest_tick(self):
        self.put(
            schedule=fx.interval_schedule(every=10),
            policies={"busy": "skip", "missed": "latest", "approval_timeout_seconds": 3600},
        )
        claimed = self.store.claim_due(T0 + timedelta(minutes=45))
        self.assertEqual(len(claimed), 1)
        run = claimed[0]
        self.assertEqual(run["state"], "queued")
        self.assertIsNone(run["reason"])
        self.assertEqual(run["scheduled_for"], tick(minutes=40))
        self.assertEqual(len(self.store.runs("alice")), 1)
        self.assertEqual(self.store.get("alice", run["schedule_id"])["next_at"], tick(minutes=50))

    def test_missed_latest_daily_queues_current_day_only(self):
        self.put(
            schedule=fx.daily_schedule(["09:00"]),
            policies={"busy": "skip", "missed": "latest", "approval_timeout_seconds": 3600},
        )
        claimed = self.store.claim_due(datetime(2026, 11, 18, 10, 0, tzinfo=UTC))
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0]["state"], "queued")
        self.assertEqual(claimed[0]["scheduled_for"], day_at(18, 9, 0))
        self.assertEqual(self.store.get("alice", claimed[0]["schedule_id"])["next_at"], day_at(19, 9, 0))

    def test_once_plan_stops_after_claim(self):
        plan = self.put(schedule=fx.once_schedule(date_text="2026-11-16", time_text="01:00"))
        claimed = self.store.claim_due(datetime(2026, 11, 16, 1, 0, 5, tzinfo=UTC))
        self.assertEqual([run["state"] for run in claimed], ["queued"])
        after = self.store.get("alice", plan["id"])
        self.assertIs(after["enabled"], False)
        self.assertIsNone(after["next_at"])
        self.assertEqual(self.store.claim_due(datetime(2026, 11, 16, 1, 0, 10, tzinfo=UTC)), [])

    def test_claimed_snapshot_isolated_from_later_update(self):
        plan = self.put(name="old")
        run = self.store.claim_due(T0 + timedelta(minutes=1))[0]
        self.put(
            definition={**plan["definition"], "name": "new", "schedule": fx.interval_schedule(every=7)},
            overwrite=True,
        )
        stored = self.store.run(run["id"])
        self.assertEqual(stored["definition"]["name"], "old")
        self.assertEqual(stored["definition"]["schedule"]["every"], 1)
        self.assertEqual(stored["revision"], 1)

    def test_multiple_users_claimed_with_owner_scoped_keys(self):
        alice = self.put(name="a")
        bob = self.put(username="bob", definition=fx.plan_definition(name="b") | {"id": "bob-1"})
        claimed = self.store.claim_due(T0 + timedelta(minutes=1))
        self.assertEqual(len(claimed), 2)
        keys = {run["occurrence_key"] for run in claimed}
        self.assertIn(f"scheduled:alice:{alice['id']}:1:{tick(minutes=1)}", keys)
        self.assertIn(f"scheduled:bob:{bob['id']}:1:{tick(minutes=1)}", keys)
        self.assertEqual(len(self.store.runs("alice")), 1)
        self.assertEqual(len(self.store.runs("bob")), 1)


class ManualRunTest(StoreCase):
    def test_same_request_id_is_idempotent(self):
        plan = self.put()
        first = self.store.manual_run("alice", plan["id"], "req-1")
        second = self.store.manual_run("alice", plan["id"], "req-1")
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.store.runs("alice", plan["id"])), 1)
        self.assertEqual(first["trigger"], "manual")
        self.assertEqual(first["occurrence_key"], f"manual:alice:{plan['id']}:req-1")

    def test_different_request_ids_create_new_runs(self):
        plan = self.put()
        first = self.store.manual_run("alice", plan["id"], "req-1")
        second = self.store.manual_run("alice", plan["id"], "req-2")
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(len(self.store.runs("alice", plan["id"])), 2)

    def test_same_request_id_different_users_are_distinct(self):
        plan = self.put()
        alice_run = self.store.manual_run("alice", plan["id"], "shared")
        self.put(username="bob", definition=fx.plan_definition() | {"id": plan["id"]})
        bob_run = self.store.manual_run("bob", plan["id"], "shared")
        self.assertNotEqual(alice_run["id"], bob_run["id"])
        self.assertEqual(self.store.run(alice_run["id"])["username"], "alice")

    def test_manual_run_deleted_plan_raises(self):
        plan = self.put()
        self.store.delete("alice", plan["id"])
        with self.assertRaises(KeyError):
            self.store.manual_run("alice", plan["id"], "req-1")


class LifecycleTest(StoreCase):
    def test_disable_pauses_and_skips_only_queued_runs(self):
        plan = self.put(name="s")
        active = self.store.manual_run("alice", plan["id"], "active")
        self.store.update_run(active["id"], state="running")
        queued = self.store.manual_run("alice", plan["id"], "queued")
        paused = self.store.enable("alice", plan["id"], False, revision=1)
        self.assertIs(paused["enabled"], False)
        self.assertIsNone(paused["next_at"])
        self.assertEqual(paused["revision"], 2)
        self.assertEqual(self.store.run(active["id"])["state"], "running")
        skipped = self.store.run(queued["id"])
        self.assertEqual(skipped["state"], "skipped")
        self.assertEqual(skipped["reason"], "plan_paused")
        self.assertIsNotNone(skipped["finished_at"])

    def test_enable_resumes_with_new_next_at(self):
        plan = self.put(enabled=False)
        before = clock.utc_now()
        resumed = self.store.enable("alice", plan["id"], True)
        self.assertIs(resumed["enabled"], True)
        self.assertGreater(clock.instant(resumed["next_at"]), before)
        self.assertEqual(resumed["revision"], 2)

    def test_enable_revision_conflict(self):
        plan = self.put()
        with self.assertRaises(ConflictError):
            self.store.enable("alice", plan["id"], False, revision=9)
        self.assertIs(self.store.get("alice", plan["id"])["enabled"], True)

    def test_enable_once_in_past_fails_and_keeps_state(self):
        past = fx.plan_definition(schedule=fx.once_schedule(date_text="2020-01-01"), enabled=False)
        plan = self.put(definition=past)
        with self.assertRaises(ValueError):
            self.store.enable("alice", plan["id"], True)
        after = self.store.get("alice", plan["id"])
        self.assertIs(after["enabled"], False)
        self.assertIsNone(after["next_at"])
        self.assertEqual(after["revision"], 1)

    def test_delete_skips_queued_but_keeps_running(self):
        plan = self.put(name="s")
        running = self.store.manual_run("alice", plan["id"], "running")
        self.store.update_run(running["id"], state="running")
        queued = self.store.manual_run("alice", plan["id"], "queued")
        self.store.delete("alice", plan["id"])
        self.assertEqual(self.store.run(running["id"])["state"], "running")
        skipped = self.store.run(queued["id"])
        self.assertEqual(skipped["state"], "skipped")
        self.assertEqual(skipped["reason"], "plan_deleted")

    def test_delete_hides_plan_but_keeps_history(self):
        plan = self.put()
        run = self.store.manual_run("alice", plan["id"], "req-1")
        self.store.delete("alice", plan["id"])
        with self.assertRaises(KeyError):
            self.store.get("alice", plan["id"])
        self.assertNotIn(plan["id"], [item["id"] for item in self.store.list("alice")])
        self.assertIsNotNone(self.store.get("alice", plan["id"], deleted=True)["deleted_at"])
        skipped = self.store.run(run["id"])
        self.assertEqual(skipped["state"], "skipped")
        self.assertEqual(skipped["reason"], "plan_deleted")

    def test_delete_twice_raises(self):
        plan = self.put()
        self.store.delete("alice", plan["id"])
        with self.assertRaises(KeyError):
            self.store.delete("alice", plan["id"])

    def test_deleted_id_cannot_be_recreated(self):
        plan = self.put()
        self.store.delete("alice", plan["id"])
        with self.assertRaises(ConflictError):
            self.put(definition={**plan["definition"], "revision": 1}, overwrite=True)


class UpdateAndRecoverTest(StoreCase):
    def test_update_run_applies_allowed_fields_and_cas(self):
        plan = self.put()
        run = self.store.manual_run("alice", plan["id"], "req-1")
        self.assertTrue(
            self.store.update_run(
                run["id"],
                expected={"queued"},
                state="running",
                started_at=iso(T0),
                task_id="task-1",
                conversation_id="conv-1",
            )
        )
        stored = self.store.run(run["id"])
        self.assertEqual(stored["state"], "running")
        self.assertEqual(stored["task_id"], "task-1")
        self.assertEqual(stored["conversation_id"], "conv-1")
        self.assertFalse(self.store.update_run(run["id"], expected={"queued"}, state="failed"))
        self.assertTrue(self.store.update_run(run["id"], stop_requested=True))
        self.assertIs(self.store.run(run["id"])["stop_requested"], True)

    def test_update_run_rejects_invalid_payloads(self):
        plan = self.put()
        run = self.store.manual_run("alice", plan["id"], "req-1")
        with self.assertRaises(ValueError):
            self.store.update_run(run["id"])
        with self.assertRaises(ValueError):
            self.store.update_run(run["id"], bogus=1)
        with self.assertRaises(ValueError):
            self.store.update_run(run["id"], state="success", username="mallory")

    def test_recover_interrupts_active_states_only(self):
        plan = self.put()
        active_ids = {}
        for state in ("starting", "running", "waiting", "stopping"):
            run = self.store.manual_run("alice", plan["id"], f"req-{state}")
            self.store.update_run(run["id"], state=state)
            active_ids[state] = run["id"]
        done = self.store.manual_run("alice", plan["id"], "req-done")
        self.store.update_run(done["id"], state="success")
        queued = self.store.manual_run("alice", plan["id"], "req-queued")
        self.store.recover()
        for state, run_id in active_ids.items():
            stored = self.store.run(run_id)
            self.assertEqual(stored["state"], "interrupted", state)
            self.assertEqual(stored["reason"], "process_restarted")
            self.assertIsNotNone(stored["finished_at"])
        self.assertEqual(self.store.run(done["id"])["state"], "success")
        # 当前实现只校准已开始的实例；queued 保持原状（见交付报告）。
        self.assertEqual(self.store.run(queued["id"])["state"], "queued")

    def test_runs_filters_and_limit(self):
        plan = self.put()
        other = self.put(name="other")
        first = self.store.manual_run("alice", plan["id"], "req-1")
        second = self.store.manual_run("alice", plan["id"], "req-2")
        self.store.manual_run("alice", other["id"], "req-3")
        self.assertTrue(self.store.update_run(second["id"], state="success"))
        self.assertEqual(len(self.store.runs("alice")), 3)
        self.assertEqual(len(self.store.runs("alice", plan["id"])), 2)
        self.assertEqual(
            [row["id"] for row in self.store.runs("alice", plan["id"], states={"success"})],
            [second["id"]],
        )
        self.assertEqual(len(self.store.runs("alice", plan["id"], limit=1)), 1)
        self.assertIn(first["id"], [row["id"] for row in self.store.runs("alice", plan["id"], limit=2)])


class ConcurrencyTest(StoreCase):
    def test_concurrent_claims_accept_exactly_one(self):
        plan = self.put()
        workers = 6
        stores = [ScheduleStore(self.path) for _ in range(workers)]
        barrier = threading.Barrier(workers)
        claimed: list[list[dict]] = []
        lock = threading.Lock()

        def claim(store: ScheduleStore) -> None:
            barrier.wait()
            result = store.claim_due(T0 + timedelta(minutes=1))
            with lock:
                claimed.append(result)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(claim, store) for store in stores]
            for future in futures:
                future.result()

        flat = [run for batch in claimed for run in batch]
        self.assertEqual(len(flat), 1, flat)
        self.assertEqual(flat[0]["state"], "queued")
        self.assertEqual(len(self.store.runs("alice")), 1)
        self.assertEqual(self.store.runs("alice")[0]["schedule_id"], plan["id"])
        self.assertEqual(self.store.get("alice", plan["id"])["next_at"], tick(minutes=2))


if __name__ == "__main__":
    unittest.main()
