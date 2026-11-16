"""clock.py 回归：六种时间规则、IANA 时区与 DST、月末、cron OR/合法性、interval 锚点。"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

import schedule_fixtures as fx
from modules.scheduled_tasks import clock

UTC = fx.UTC


def iso(dt: datetime | None) -> str | None:
    """统一比较用的 UTC 文本形式（忽略 iso() 是否输出 .000000）。"""
    value = clock.iso(dt)
    return value.replace(".000000Z", "Z") if value else None


class InstantHelpersTest(unittest.TestCase):
    def test_instant_accepts_z_and_offsets(self):
        self.assertEqual(clock.instant("2026-11-16T08:00:00+08:00"), datetime(2026, 11, 16, 0, 0, tzinfo=UTC))
        self.assertEqual(clock.instant("2026-11-16T00:00:00Z"), datetime(2026, 11, 16, 0, 0, tzinfo=UTC))

    def test_instant_rejects_naive_datetime(self):
        with self.assertRaises(ValueError):
            clock.instant("2026-11-16T00:00:00")
        with self.assertRaises(ValueError):
            clock.instant(datetime(2026, 11, 16, 0, 0))

    def test_iso_normalizes_to_z(self):
        self.assertEqual(iso(datetime(2026, 11, 16, 8, 0, tzinfo=timezone(timedelta(hours=8)))), "2026-11-16T00:00:00Z")
        self.assertIsNone(iso(None))

    def test_integer_rejects_bool_and_out_of_range(self):
        self.assertEqual(clock.integer(5, 1, 10, "x"), 5)
        for bad in (True, 0, 11, 5.0, "5", None):
            with self.assertRaises(ValueError):
                clock.integer(bad, 1, 10, "x")


class NormalizeScheduleTest(unittest.TestCase):
    def test_daily_defaults_and_dedupes_times(self):
        out = clock.normalize_schedule({"type": "daily", "times": ["09:00", "09:00", "08:00"]})
        self.assertEqual(out, {"type": "daily", "timezone": "Asia/Shanghai", "times": ["08:00", "09:00"]})

    def test_once_requires_single_time_and_date(self):
        with self.assertRaises(ValueError):
            clock.normalize_schedule({"type": "once", "date": "2026-12-01", "times": ["09:00", "10:00"]})
        with self.assertRaises(ValueError):
            clock.normalize_schedule({"type": "once", "times": ["09:00"]})
        with self.assertRaises(ValueError):
            clock.normalize_schedule({"type": "once", "date": "2026-02-30", "times": ["09:00"]})

    def test_weekly_days_sorted_and_bounded(self):
        out = clock.normalize_schedule({"type": "weekly", "days": [6, 0], "times": ["00:00"]})
        self.assertEqual(out["days"], [0, 6])
        for bad in ([], [7], [-1], [True], "0", [1.5]):
            with self.assertRaises(ValueError, msg=repr(bad)):
                clock.normalize_schedule({"type": "weekly", "days": bad, "times": ["00:00"]})

    def test_monthly_day_last_and_range(self):
        self.assertEqual(clock.normalize_schedule({"type": "monthly", "day": "last", "times": ["00:00"]})["day"], "last")
        self.assertEqual(clock.normalize_schedule({"type": "monthly", "day": 31, "times": ["00:00"]})["day"], 31)
        for bad in (0, 32, "31", True):
            with self.assertRaises(ValueError, msg=repr(bad)):
                clock.normalize_schedule({"type": "monthly", "day": bad, "times": ["00:00"]})

    def test_interval_every_unit_and_default_anchor(self):
        out = clock.normalize_schedule(
            {"type": "interval", "every": 2, "unit": "hours"}, now=fx.T0
        )
        self.assertEqual(out["every"], 2)
        self.assertEqual(out["unit"], "hours")
        self.assertEqual(out["anchor"], "2026-11-16T00:00:00.000000Z")
        for every in (0, -1, 525601, True, "5", None):
            with self.assertRaises(ValueError, msg=repr(every)):
                clock.normalize_schedule({"type": "interval", "every": every, "unit": "minutes"})
        with self.assertRaises(ValueError):
            clock.normalize_schedule({"type": "interval", "every": 5, "unit": "days"})

    def test_interval_anchor_requires_timezone_and_is_normalized(self):
        with self.assertRaises(ValueError):
            clock.normalize_schedule(
                {"type": "interval", "every": 5, "unit": "minutes", "anchor": "2026-01-01T00:00:00"}
            )
        out = clock.normalize_schedule(
            {"type": "interval", "every": 5, "unit": "minutes", "anchor": "2026-01-01T08:00:00+08:00"}
        )
        self.assertEqual(out["anchor"], "2026-01-01T00:00:00.000000Z")

    def test_interval_anchor_is_preserved_only_for_same_grid(self):
        previous = {"schedule": clock.normalize_schedule(
            {"type": "interval", "every": 10, "unit": "minutes", "anchor": "2026-01-01T00:00:00Z"}
        )}
        kept = clock.normalize_schedule(
            {"type": "interval", "every": 10, "unit": "minutes"}, now=fx.at(1), previous=previous["schedule"]
        )
        self.assertEqual(kept["anchor"], "2026-01-01T00:00:00.000000Z")
        changed = clock.normalize_schedule(
            {"type": "interval", "every": 15, "unit": "minutes"}, now=fx.at(1), previous=previous["schedule"]
        )
        self.assertEqual(changed["anchor"], "2026-11-01T00:00:00.000000Z")

    def test_cron_normalizes_whitespace_and_limits_length(self):
        self.assertEqual(
            clock.normalize_schedule({"type": "cron", "expression": "  0   9 * * 1-5 "})["expression"],
            "0 9 * * 1-5",
        )
        with self.assertRaises(ValueError):
            clock.normalize_schedule({"type": "cron", "expression": "0 0 * * " + "1," * 80})
        with self.assertRaises(ValueError):
            clock.normalize_schedule({"type": "cron", "expression": 9})

    def test_rejects_unknown_fields_and_invalid_combinations(self):
        with self.assertRaises(ValueError):
            clock.normalize_schedule({"type": "daily", "times": ["00:00"], "bogus": 1})
        with self.assertRaises(ValueError):
            clock.normalize_schedule({"type": "daily", "days": [1], "times": ["00:00"]})
        with self.assertRaises(ValueError):
            clock.normalize_schedule({"type": "weekly", "times": ["00:00"]})
        with self.assertRaises(ValueError):
            clock.normalize_schedule({"type": "monthly", "times": ["00:00"]})
        with self.assertRaises(ValueError):
            clock.normalize_schedule({"type": "yearly", "times": ["00:00"]})
        with self.assertRaises(ValueError):
            clock.normalize_schedule([])
        with self.assertRaises(ValueError):
            clock.normalize_schedule({"type": "daily", "timezone": "Mars/Phobos", "times": ["00:00"]})
        with self.assertRaises(ValueError):
            clock.normalize_schedule({"type": "daily", "timezone": 8, "times": ["00:00"]})
        with self.assertRaises(ValueError):
            clock.normalize_schedule({"type": "daily", "times": ["9:00"]})
        with self.assertRaises(ValueError):
            clock.normalize_schedule({"type": "daily", "times": ["24:00"]})
        with self.assertRaises(ValueError):
            clock.normalize_schedule({"type": "daily", "times": ["00:60"]})
        with self.assertRaises(ValueError):
            clock.normalize_schedule({"type": "daily", "times": []})
        with self.assertRaises(ValueError):
            clock.normalize_schedule({"type": "daily", "times": ["00:00"] * 25})


class DailyAndWeeklyTest(unittest.TestCase):
    def test_daily_converts_local_to_utc(self):
        s = {"type": "daily", "timezone": "Asia/Shanghai", "times": ["09:00"]}
        self.assertEqual(iso(clock.next_occurrence(s, fx.T0)), "2026-11-16T01:00:00Z")
        self.assertEqual(iso(clock.next_occurrence(s, fx.at(16, 1))), "2026-11-17T01:00:00Z")

    def test_daily_multiple_times_roll_forward(self):
        s = {"type": "daily", "timezone": "Asia/Shanghai", "times": ["09:00", "18:00"]}
        self.assertEqual(iso(clock.next_occurrence(s, fx.at(16, 1))), "2026-11-16T10:00:00Z")
        self.assertEqual(iso(clock.next_occurrence(s, fx.at(16, 10))), "2026-11-17T01:00:00Z")

    def test_weekly_zero_is_sunday(self):
        sunday = {"type": "weekly", "timezone": "UTC", "days": [0], "times": ["00:00"]}
        self.assertEqual(iso(clock.next_occurrence(sunday, fx.at(16))), "2026-11-22T00:00:00Z")
        monday = {"type": "weekly", "timezone": "UTC", "days": [1], "times": ["00:00"]}
        self.assertEqual(iso(clock.next_occurrence(monday, fx.at(15))), "2026-11-16T00:00:00Z")

    def test_weekly_multiple_days(self):
        s = {"type": "weekly", "timezone": "UTC", "days": [0, 3], "times": ["00:00"]}
        # 2026-11-16 是周一；下一次是周三 11-18。
        self.assertEqual(iso(clock.next_occurrence(s, fx.at(16, 12))), "2026-11-18T00:00:00Z")


class MonthlyTest(unittest.TestCase):
    def test_day_31_skips_months_without_it(self):
        s = {"type": "monthly", "timezone": "UTC", "day": 31, "times": ["00:00"]}
        self.assertEqual(iso(clock.next_occurrence(s, fx.at(1, month=4, year=2026))), "2026-05-31T00:00:00Z")

    def test_day_30_skips_february(self):
        s = {"type": "monthly", "timezone": "UTC", "day": 30, "times": ["00:00"]}
        self.assertEqual(iso(clock.next_occurrence(s, fx.at(1, month=2, year=2026))), "2026-03-30T00:00:00Z")

    def test_last_day_handles_february_and_december(self):
        s = {"type": "monthly", "timezone": "UTC", "day": "last", "times": ["00:00"]}
        self.assertEqual(iso(clock.next_occurrence(s, fx.at(1, month=2, year=2026))), "2026-02-28T00:00:00Z")
        self.assertEqual(iso(clock.next_occurrence(s, fx.at(28, month=2, year=2026))), "2026-03-31T00:00:00Z")
        self.assertEqual(iso(clock.next_occurrence(s, fx.at(31, month=12, year=2026))), "2027-01-31T00:00:00Z")


class IntervalTest(unittest.TestCase):
    def test_fixed_grid_does_not_drift(self):
        s = {"type": "interval", "timezone": "UTC", "every": 90, "unit": "minutes", "anchor": "2026-01-01T00:00:00Z"}
        self.assertEqual(iso(clock.next_occurrence(s, fx.T0)), "2026-11-16T01:30:00Z")
        first = clock.next_occurrence(s, fx.at(16, 1, 30))
        self.assertEqual(iso(first), "2026-11-16T03:00:00Z")
        self.assertEqual(iso(clock.next_occurrence(s, first)), "2026-11-16T04:30:00Z")

    def test_exact_tick_is_strictly_after(self):
        s = {"type": "interval", "timezone": "UTC", "every": 90, "unit": "minutes", "anchor": "2026-01-01T00:00:00Z"}
        self.assertEqual(iso(clock.next_occurrence(s, fx.at(16, 1, 29, 59))), "2026-11-16T01:30:00Z")
        self.assertEqual(iso(clock.next_occurrence(s, fx.at(16, 1, 30))), "2026-11-16T03:00:00Z")

    def test_hours_unit(self):
        s = {"type": "interval", "timezone": "UTC", "every": 6, "unit": "hours", "anchor": "2026-11-16T00:00:00Z"}
        self.assertEqual(iso(clock.next_occurrence(s, fx.T0)), "2026-11-16T06:00:00Z")

    def test_previous_occurrence_uses_grid(self):
        s = {"type": "interval", "timezone": "UTC", "every": 90, "unit": "minutes", "anchor": "2026-01-01T00:00:00Z"}
        self.assertEqual(iso(clock.previous_occurrence(s, fx.at(16, 3))), "2026-11-16T03:00:00Z")
        self.assertEqual(iso(clock.previous_occurrence(s, fx.at(16, 2))), "2026-11-16T01:30:00Z")
        self.assertEqual(iso(clock.previous_occurrence(s, fx.at(16, 0, 30))), "2026-11-16T00:00:00Z")
        self.assertIsNone(clock.previous_occurrence(s, datetime(2025, 12, 31, tzinfo=UTC)))

    def test_future_anchor_starts_one_interval_after_anchor(self):
        """已确认契约：首个触发是 anchor + interval，锚点本身不单独触发。"""
        s = {"type": "interval", "timezone": "UTC", "every": 90, "unit": "minutes", "anchor": "2026-11-16T00:00:00Z"}
        self.assertEqual(iso(clock.next_occurrence(s, fx.at(15, 0))), "2026-11-16T01:30:00Z")
        self.assertIsNone(clock.previous_occurrence(s, fx.at(15, 0)))


class OnceTest(unittest.TestCase):
    def test_once_in_local_zone(self):
        s = {"type": "once", "timezone": "Asia/Shanghai", "date": "2026-12-01", "times": ["09:00"]}
        self.assertEqual(iso(clock.next_occurrence(s, fx.T0)), "2026-12-01T01:00:00Z")
        self.assertIsNone(clock.next_occurrence(s, datetime(2026, 12, 1, 1, 0, tzinfo=UTC)))

    def test_once_nonexistent_local_time_never_fires(self):
        s = {"type": "once", "timezone": "America/New_York", "date": "2026-03-08", "times": ["02:30"]}
        self.assertIsNone(clock.next_occurrence(s, fx.T0))

    def test_once_previous(self):
        s = {"type": "once", "timezone": "UTC", "date": "2026-11-16", "times": ["01:00"]}
        self.assertEqual(iso(clock.previous_occurrence(s, fx.at(16, 2))), "2026-11-16T01:00:00Z")
        self.assertIsNone(clock.previous_occurrence(s, fx.T0))


class DstTest(unittest.TestCase):
    def test_nonexistent_local_time_is_skipped(self):
        s = {"type": "daily", "timezone": "America/New_York", "times": ["02:30"]}
        self.assertEqual(iso(clock.next_occurrence(s, fx.at(7, month=3))), "2026-03-07T07:30:00Z")
        # 2026-03-08 的 02:30 不存在，直接跳到 03-09。
        self.assertEqual(iso(clock.next_occurrence(s, fx.at(8, month=3))), "2026-03-09T06:30:00Z")

    def test_ambiguous_local_time_uses_first_occurrence_only(self):
        s = {"type": "daily", "timezone": "America/New_York", "times": ["01:30"]}
        self.assertEqual(iso(clock.next_occurrence(s, fx.at(31, month=10))), "2026-10-31T05:30:00Z")
        # 2026-11-01 01:30 出现两次（EDT 05:30Z / EST 06:30Z），只取第一次。
        self.assertEqual(iso(clock.next_occurrence(s, fx.at(31, 5, 30, month=10))), "2026-11-01T05:30:00Z")
        self.assertEqual(iso(clock.next_occurrence(s, fx.at(1, 5, 30))), "2026-11-02T06:30:00Z")
        self.assertEqual(iso(clock.previous_occurrence(s, fx.at(1, 6, 0))), "2026-11-01T05:30:00Z")

    def test_weekly_dst_shift_keeps_utc_conversion(self):
        s = {"type": "weekly", "timezone": "America/New_York", "days": [0], "times": ["09:00"]}
        self.assertEqual(iso(clock.next_occurrence(s, fx.at(6, month=3))), "2026-03-08T13:00:00Z")
        self.assertEqual(iso(clock.next_occurrence(s, fx.at(8, 13, month=3))), "2026-03-15T13:00:00Z")


class CronTest(unittest.TestCase):
    def test_steps_and_wildcards(self):
        s = {"type": "cron", "timezone": "UTC", "expression": "*/15 * * * *"}
        self.assertEqual(iso(clock.next_occurrence(s, fx.at(16, 10, 7))), "2026-11-16T10:15:00Z")
        self.assertEqual(iso(clock.next_occurrence(s, fx.at(16, 10, 45))), "2026-11-16T11:00:00Z")

    def test_ranges_weekdays(self):
        s = {"type": "cron", "timezone": "UTC", "expression": "0 9 * * 1-5"}
        self.assertEqual(iso(clock.next_occurrence(s, fx.at(20, 10))), "2026-11-23T09:00:00Z")

    def test_dom_and_dow_both_restricted_use_or(self):
        s = {"type": "cron", "timezone": "UTC", "expression": "0 0 13 * 5"}
        # 2026-12-04 是周五，2026-12-13 是周日（13 号）；两者都应触发。
        self.assertEqual(iso(clock.next_occurrence(s, fx.at(1, month=12))), "2026-12-04T00:00:00Z")
        self.assertEqual(iso(clock.next_occurrence(s, fx.at(4, month=12))), "2026-12-11T00:00:00Z")
        self.assertEqual(iso(clock.next_occurrence(s, fx.at(11, month=12))), "2026-12-13T00:00:00Z")

    def test_single_restricted_field_uses_and(self):
        dom = {"type": "cron", "timezone": "UTC", "expression": "0 0 13 * *"}
        self.assertEqual(iso(clock.next_occurrence(dom, fx.at(1, month=12))), "2026-12-13T00:00:00Z")
        dow = {"type": "cron", "timezone": "UTC", "expression": "0 0 * * 5"}
        self.assertEqual(iso(clock.next_occurrence(dow, fx.at(1, month=12))), "2026-12-04T00:00:00Z")

    def test_dow_seven_is_sunday(self):
        s = {"type": "cron", "timezone": "UTC", "expression": "0 0 * * 7"}
        self.assertEqual(iso(clock.next_occurrence(s, fx.at(16))), "2026-11-22T00:00:00Z")

    def test_lists(self):
        s = {"type": "cron", "timezone": "UTC", "expression": "0 0 * * 0,6"}
        self.assertEqual(iso(clock.next_occurrence(s, fx.at(16))), "2026-11-21T00:00:00Z")

    def test_day_of_month_step(self):
        s = {"type": "cron", "timezone": "UTC", "expression": "0 0 1-31/10 * *"}
        self.assertEqual(iso(clock.next_occurrence(s, fx.at(2, month=12))), "2026-12-11T00:00:00Z")

    def test_month_restriction(self):
        s = {"type": "cron", "timezone": "UTC", "expression": "0 0 1 6 *"}
        self.assertEqual(iso(clock.next_occurrence(s, fx.at(1))), "2027-06-01T00:00:00Z")

    def test_leap_day_cron(self):
        s = {"type": "cron", "timezone": "UTC", "expression": "0 0 29 2 *"}
        self.assertEqual(iso(clock.next_occurrence(s, fx.at(1, month=1, year=2025))), "2028-02-29T00:00:00Z")

    def test_impossible_cron_returns_none(self):
        s = {"type": "cron", "timezone": "UTC", "expression": "0 0 31 2 *"}
        self.assertIsNone(clock.next_occurrence(s, fx.at(1)))
        self.assertEqual(clock.next_dates(s, 3, fx.T0), [])

    def test_previous_occurrence_orders_descending(self):
        s = {"type": "cron", "timezone": "UTC", "expression": "0 9,18 * * *"}
        self.assertEqual(iso(clock.previous_occurrence(s, fx.at(16, 20))), "2026-11-16T18:00:00Z")
        self.assertEqual(iso(clock.previous_occurrence(s, fx.at(16, 17))), "2026-11-16T09:00:00Z")
        self.assertEqual(iso(clock.previous_occurrence(s, fx.at(16, 1))), "2026-11-15T18:00:00Z")

    def test_invalid_cron_expressions(self):
        bad = [
            "0 0 * *",  # 四字段
            "0 0 * * * *",  # 六字段
            "60 0 * * *",
            "0 24 * * *",
            "0 0 0 * *",
            "0 0 * 13 *",
            "0 0 * * 8",
            "*/0 * * * *",
            "*/61 * * * *",
            "1-2-3 * * * *",
            "a * * * *",
            "0 0 * * 1#2",
            "0 0 * * 5-1",
            "",
        ]
        for expression in bad:
            with self.assertRaises(ValueError, msg=expression):
                clock.normalize_schedule({"type": "cron", "expression": expression})


class NextDatesTest(unittest.TestCase):
    def test_returns_three_future_utc_points(self):
        s = {"type": "daily", "timezone": "Asia/Shanghai", "times": ["09:00"]}
        self.assertEqual(
            [item.replace(".000000Z", "Z") for item in clock.next_dates(s, 3, fx.T0)],
            ["2026-11-16T01:00:00Z", "2026-11-17T01:00:00Z", "2026-11-18T01:00:00Z"],
        )

    def test_count_is_capped(self):
        s = {"type": "daily", "timezone": "UTC", "times": ["00:00"]}
        self.assertEqual(len(clock.next_dates(s, 100, fx.T0)), 20)

    def test_naive_after_is_rejected(self):
        s = {"type": "daily", "timezone": "UTC", "times": ["00:00"]}
        with self.assertRaises(ValueError):
            clock.next_occurrence(s, datetime(2026, 11, 16, 0, 0))


if __name__ == "__main__":
    unittest.main()
