"""用量统计派生汇总的离线回归：合成账本、临时目录，不读真实数据、不启动服务。

运行：python3 -B -m unittest discover -s test/2026-11-06_用量统计页 -p 'test_*.py'
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from modules.usage_stats import (  # noqa: E402
    CHAT_ID, UNASSIGNED_ID, SourceBuckets, WorkspaceRef, build_report, collect_ledger_dir,
    gather_sources, rollup_ledger,
)
from modules.usage_stats.cache import CACHE_FILENAME  # noqa: E402

CST = timezone(timedelta(hours=8))


def req(at, *, model="p/m", actor="main", inp=1000, cached=400, out=100, cost="0.002",
        status="priced", **extra):
    return actor, {"actor_id": actor, "recorded_at": at, "input_tokens": inp,
                   "cached_input_tokens": cached, "output_tokens": out,
                   "cost_usd": cost if status == "priced" else None, "status": status,
                   "price": {"model_key": model}, **extra}


def ledger(*items):
    actors, requests = {}, {}
    kinds = {"main": "main", "thinking_summary": "thinking_summary"}
    for idx, (actor, record) in enumerate(items):
        actors[actor] = {"id": actor, "kind": kinds.get(actor, "sub_agent")}
        requests[f"r{idx}"] = record
    return {"version": 1, "actors": actors, "requests": requests}


def write_ledger(data_dir: Path, conv: str, payload: dict):
    path = data_dir / "api_costs" / f"{conv}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class RollupTests(unittest.TestCase):
    def test_hour_buckets_by_model_and_kind(self):
        buckets = rollup_ledger(ledger(
            req("2026-11-05T01:10:00+00:00"),
            req("2026-11-05T01:50:00+00:00", duration_ms=2000, ttft_ms=500),
            req("2026-11-05T01:20:00+00:00", actor="task_1"),
        ))
        hour = int(datetime(2026, 11, 5, 1, tzinfo=timezone.utc).timestamp() // 3600)
        main = buckets[f"{hour}|p/m|main"]
        self.assertEqual(main[:3], [2000, 800, 200])
        self.assertEqual(Decimal(main[3]), Decimal("0.004"))
        self.assertEqual(main[4:7], [2, 2, 0])
        self.assertEqual(main[8:12], [100, 2000, 500, 1])
        self.assertEqual(buckets[f"{hour}|p/m|sub_agent"][4], 1)

    def test_unpriced_not_free_and_speed_sample_rules(self):
        buckets = rollup_ledger(ledger(
            req("2026-11-05T01:00:00+00:00", status="unpriced", speed_sample={}),
            req("2026-11-05T01:00:00+00:00", speed_sample={"output_tokens": 60, "duration_ms": 1000}),
        ))
        bucket = next(iter(buckets.values()))
        self.assertEqual(bucket[5:8], [1, 1, 1100])
        self.assertEqual(Decimal(bucket[3]), Decimal("0.002"))
        self.assertEqual(bucket[8:10], [60, 1000])  # 空样本跳过，不回退到 duration

    def test_bad_records_skipped(self):
        payload = ledger(req("not-a-date"), req("2026-11-05T01:00:00+00:00"))
        payload["requests"]["junk"] = "x"
        self.assertEqual(sum(b[4] for b in rollup_ledger(payload).values()), 1)
        self.assertEqual(rollup_ledger(None), {})


class ReportTests(unittest.TestCase):
    def source(self, *items, sid="ws:a", mode="agent"):
        return SourceBuckets(sid, sid, mode, {"conv_1": rollup_ledger(ledger(*items))})

    def test_timezone_moves_day_and_punch_hour(self):
        # UTC 11-05 18:30 = 北京时间 11-06 02:30（周五）
        src = self.source(req("2026-11-05T18:30:00+00:00"))
        utc = build_report([src], tz=timezone.utc, today=date(2026, 11, 6))
        cst = build_report([src], tz=CST, today=date(2026, 11, 6))
        self.assertEqual(utc["days"][0]["date"], "2026-11-05")
        self.assertEqual(cst["days"][0]["date"], "2026-11-06")
        self.assertEqual(cst["ranges"]["7"]["punch"][4][2], 1100)

    def test_overview_streaks_best_day_and_ranges(self):
        items = [req(f"2026-11-0{d}T03:00:00+00:00", inp=1000 * d) for d in (1, 2, 4, 5, 6)]
        items.append(req("2026-09-01T03:00:00+00:00"))
        rep = build_report([self.source(*items)], tz=timezone.utc, today=date(2026, 11, 6))
        ov = rep["overview"]
        self.assertEqual((ov["active_days"], ov["current_streak"], ov["longest_streak"]), (6, 3, 3))
        self.assertEqual(ov["best_day"]["date"], "2026-11-06")
        self.assertEqual(rep["first_date"], "2026-09-01")
        r7 = rep["ranges"]["7"]["models"][0]
        self.assertEqual(r7["requests"], 5)
        self.assertEqual(rep["ranges"]["all"]["models"][0]["requests"], 6)

    def test_streak_survives_inactive_today(self):
        rep = build_report([self.source(req("2026-11-05T03:00:00+00:00"))], tz=timezone.utc,
                           today=date(2026, 11, 6))
        self.assertEqual(rep["overview"]["current_streak"], 1)

    def test_prior_totals_outside_heatmap_window(self):
        rep = build_report([self.source(req("2024-01-01T03:00:00+00:00"), req("2026-11-06T03:00:00+00:00"))],
                           tz=timezone.utc, today=date(2026, 11, 6))
        self.assertEqual(len(rep["days"]), 1)
        self.assertEqual(rep["prior"]["tokens"], 1100)
        self.assertEqual(rep["prior"]["work_tokens"], 700)

    def test_modes_kinds_sources_and_top_model(self):
        a = self.source(req("2026-11-06T03:00:00+00:00", model="p/big", inp=9000),
                        req("2026-11-06T03:00:00+00:00", model="p/small", actor="thinking_summary"))
        chat = self.source(req("2026-11-06T04:00:00+00:00"), sid=CHAT_ID, mode="chat")
        rep = build_report([a, chat], tz=timezone.utc, today=date(2026, 11, 6))
        r = rep["ranges"]["7"]
        self.assertEqual(set(r["modes"]), {"agent", "chat"})
        self.assertIn("thinking_summary", r["kinds"])
        self.assertEqual(r["sources"][0]["id"], "ws:a")
        self.assertEqual(rep["days"][0]["top_model"], "p/big")

    def test_future_records_ignored(self):
        rep = build_report([self.source(req("2026-12-01T03:00:00+00:00"))], tz=timezone.utc,
                           today=date(2026, 11, 6))
        self.assertEqual(rep["days"], [])
        self.assertIsNone(rep["overview"]["best_day"])


class CacheAndSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="usage-stats-"))

    def test_cache_reuses_unchanged_and_drops_deleted(self):
        data = self.tmp / "data"
        write_ledger(data, "conv_a", ledger(req("2026-11-06T03:00:00+00:00")))
        path_b = write_ledger(data, "conv_b", ledger(req("2026-11-06T03:00:00+00:00")))
        self.assertEqual(set(collect_ledger_dir(data / "api_costs")), {"conv_a", "conv_b"})
        cache = json.loads((data / CACHE_FILENAME).read_text())
        # 篡改缓存内容但签名不变：应直接复用缓存（证明没有重新解析）
        cache["files"]["conv_a"]["buckets"] = {"marker|x|main": [0] * 12}
        (data / CACHE_FILENAME).write_text(json.dumps(cache))
        self.assertIn("marker|x|main", collect_ledger_dir(data / "api_costs")["conv_a"])
        path_b.unlink()
        self.assertEqual(set(collect_ledger_dir(data / "api_costs")), {"conv_a"})

    def test_cache_reparses_changed_file(self):
        data = self.tmp / "data"
        path = write_ledger(data, "conv_a", ledger(req("2026-11-06T03:00:00+00:00")))
        collect_ledger_dir(data / "api_costs")
        time.sleep(0.01)
        write_ledger(data, "conv_a", ledger(req("2026-11-06T03:00:00+00:00"), req("2026-11-06T04:00:00+00:00")))
        os.utime(path, ns=(time.time_ns(), time.time_ns()))
        total = sum(b[4] for b in collect_ledger_dir(data / "api_costs")["conv_a"].values())
        self.assertEqual(total, 2)

    def test_shared_ledger_attributed_by_conversation_files(self):
        data = self.tmp / "host"
        for conv in ("conv_a", "conv_b", "conv_gone"):
            write_ledger(data, conv, ledger(req("2026-11-06T03:00:00+00:00")))
        refs = []
        for ws, conv in (("w1", "conv_a"), ("w2", "conv_b")):
            folder = data / "conversations" / ws
            folder.mkdir(parents=True)
            (folder / f"{conv}.json").write_text("{}")
            (folder / "index.json").write_text("{}")
            refs.append(WorkspaceRef(ws, ws.upper(), data, folder))
        chat_root = self.tmp / "chat"
        write_ledger(chat_root, "conv_c", ledger(req("2026-11-06T03:00:00+00:00")))
        sources = {s.id: s for s in gather_sources(refs, chat_root, shared_ledger=True)}
        self.assertEqual(set(sources["ws:w1"].conversations), {"conv_a"})
        self.assertEqual(set(sources["ws:w2"].conversations), {"conv_b"})
        self.assertEqual(set(sources[UNASSIGNED_ID].conversations), {"conv_gone"})
        self.assertEqual(set(sources[CHAT_ID].conversations), {"conv_c"})

    def test_private_ledger_keeps_deleted_conversations_in_workspace(self):
        data = self.tmp / "ws"
        write_ledger(data, "conv_deleted", ledger(req("2026-11-06T03:00:00+00:00")))
        ref = WorkspaceRef("default", "默认", data, data / "conversations" / "default")
        sources = gather_sources([ref], None, shared_ledger=False)
        self.assertEqual(set(sources[0].conversations), {"conv_deleted"})
        self.assertEqual([s.id for s in sources], ["ws:default", CHAT_ID])
        self.assertFalse(sources[-1].conversations)


if __name__ == "__main__":
    unittest.main()
