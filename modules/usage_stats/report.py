"""把多个来源的小时桶聚合为用量统计页载荷（纯函数，不做 IO）。

时区：桶按 UTC 整点存储，这里按调用方时区换算到本地日期与小时。非整点时区
（如 +05:30）会把半小时内的请求归到相邻小时，按天统计的偏差只出现在午夜附近，属已知近似。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, tzinfo
from decimal import Decimal
from typing import Dict, Iterable, List, Optional, Tuple

from .rollup import COST, empty_bucket, merge_bucket

HEATMAP_DAYS = 371  # 53 周
RANGES = (("7", 7), ("30", 30), ("all", None))


@dataclass
class SourceBuckets:
    """一个统计来源（工作区 / Chat / 未归属对话）及其账本小时桶。"""

    id: str
    label: str
    mode: str  # agent | chat
    conversations: Dict[str, Dict[str, list]] = field(default_factory=dict)


def _tokens(bucket: list) -> int:
    return bucket[0] + bucket[2]


class _Localizer:
    def __init__(self, tz: tzinfo):
        self.tz = tz
        self.memo: Dict[int, Tuple[date, int, int]] = {}

    def __call__(self, hour: int) -> Tuple[date, int, int]:
        hit = self.memo.get(hour)
        if hit is None:
            moment = datetime.fromtimestamp(hour * 3600, self.tz)
            hit = (moment.date(), moment.weekday(), moment.hour)
            self.memo[hour] = hit
        return hit


def _new_breakdown() -> dict:
    return {"models": {}, "kinds": {}, "modes": {}, "sources": {}, "punch": [[0] * 24 for _ in range(7)]}


def _finish_breakdown(raw: dict) -> dict:
    models = []
    for key, bucket in raw["models"].items():
        models.append({
            "model_key": key,
            "tokens": _tokens(bucket), "input_tokens": bucket[0], "cached_tokens": bucket[1],
            "output_tokens": bucket[2], "cost_usd": bucket[COST], "requests": bucket[4],
            "priced_requests": bucket[5], "unpriced_requests": bucket[6],
            "speed_tokens": bucket[8], "speed_ms": bucket[9], "ttft_ms": bucket[10], "ttft_n": bucket[11],
        })
    models.sort(key=lambda item: item["tokens"], reverse=True)
    sources = [{"id": key, "tokens": value} for key, value in raw["sources"].items()]
    sources.sort(key=lambda item: item["tokens"], reverse=True)
    return {"models": models, "kinds": raw["kinds"], "modes": raw["modes"],
            "sources": sources, "punch": raw["punch"]}


def build_report(sources: Iterable[SourceBuckets], *, tz: tzinfo, today: Optional[date] = None) -> dict:
    localize = _Localizer(tz)
    today = today or datetime.now(tz).date()
    window_start = today - timedelta(days=HEATMAP_DAYS - 1)
    range_starts = {name: (today - timedelta(days=n - 1) if n else None) for name, n in RANGES}

    days: Dict[date, list] = {}
    day_models: Dict[date, Dict[str, int]] = {}
    ranges = {name: _new_breakdown() for name, _ in RANGES}

    for source in sources:
        for buckets in source.conversations.values():
            for key, bucket in buckets.items():
                try:
                    hour_text, model, kind = key.split("|", 2)
                    hour = int(hour_text)
                except ValueError:
                    continue
                local_day, weekday, local_hour = localize(hour)
                if local_day > today:  # 时钟回拨/未来记录不进入统计
                    continue
                merge_bucket(days.setdefault(local_day, empty_bucket()), bucket)
                per_model = day_models.setdefault(local_day, {})
                per_model[model] = per_model.get(model, 0) + _tokens(bucket)
                tokens = _tokens(bucket)
                for name, start in range_starts.items():
                    if start is not None and local_day < start:
                        continue
                    raw = ranges[name]
                    merge_bucket(raw["models"].setdefault(model, empty_bucket()), bucket)
                    raw["kinds"][kind] = raw["kinds"].get(kind, 0) + tokens
                    raw["modes"][source.mode] = raw["modes"].get(source.mode, 0) + tokens
                    raw["sources"][source.id] = raw["sources"].get(source.id, 0) + tokens
                    raw["punch"][weekday][local_hour] += tokens

    overview = _overview(days, today)
    day_rows, prior = [], {"tokens": 0, "work_tokens": 0, "cost_usd": "0", "requests": 0}
    for day in sorted(days):
        bucket = days[day]
        if day < window_start:
            prior["tokens"] += _tokens(bucket)
            prior["work_tokens"] += bucket[0] - bucket[1] + bucket[2]
            prior["cost_usd"] = str(Decimal(prior["cost_usd"]) + Decimal(bucket[COST]))
            prior["requests"] += bucket[4]
            continue
        models = day_models.get(day) or {}
        day_rows.append({
            "date": day.isoformat(),
            "input_tokens": bucket[0], "cached_tokens": bucket[1], "output_tokens": bucket[2],
            "cost_usd": bucket[COST], "requests": bucket[4],
            "unpriced_requests": bucket[6], "unpriced_tokens": bucket[7],
            "top_model": max(models, key=models.get) if models else None,
        })
    return {
        "today": today.isoformat(),
        "window_start": window_start.isoformat(),
        "first_date": min(days).isoformat() if days else None,
        "overview": overview,
        "days": day_rows,
        "prior": prior,
        "ranges": {name: _finish_breakdown(raw) for name, raw in ranges.items()},
    }


def _overview(days: Dict[date, list], today: date) -> dict:
    total = empty_bucket()
    for bucket in days.values():
        merge_bucket(total, bucket)
    active = sorted(day for day, bucket in days.items() if bucket[4] > 0)
    longest = run = 0
    previous = None
    for day in active:
        run = run + 1 if previous is not None and day - previous == timedelta(days=1) else 1
        longest = max(longest, run)
        previous = day
    active_set = set(active)
    cursor = today if today in active_set else today - timedelta(days=1)
    current = 0
    while cursor in active_set:
        current += 1
        cursor -= timedelta(days=1)
    best_day = max(active, key=lambda day: _tokens(days[day]), default=None)
    return {
        "total_tokens": _tokens(total), "input_tokens": total[0], "cached_tokens": total[1],
        "output_tokens": total[2], "cost_usd": total[COST], "requests": total[4],
        "priced_requests": total[5], "unpriced_requests": total[6], "unpriced_tokens": total[7],
        "active_days": len(active), "current_streak": current, "longest_streak": longest,
        "best_day": ({"date": best_day.isoformat(), "tokens": _tokens(days[best_day])}
                     if best_day else None),
    }
