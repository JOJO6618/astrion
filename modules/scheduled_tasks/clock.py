"""Timezone-aware schedule arithmetic. UTC instants are the only storage clock."""
from __future__ import annotations

import calendar
import math
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc
TYPES = {"once", "interval", "daily", "weekly", "monthly", "cron"}


def utc_now() -> datetime:
    return datetime.now(UTC)


def instant(value: str | datetime) -> datetime:
    if not isinstance(value, (str, datetime)):
        raise ValueError('时间点应为包含时区的 ISO 日期时间')
    result = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("时间点必须包含时区")
    return result.astimezone(UTC)


def iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z") if value else None


def integer(value: Any, minimum: int, maximum: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{label}：应为 {minimum}–{maximum} 的整数")
    return value


def cron_field(text: str, low: int, high: int) -> set[int]:
    values: set[int] = set()
    for part in text.split(","):
        pieces = part.split("/")
        if len(pieces) > 2:
            raise ValueError("cron：步长格式错误")
        base = pieces[0]
        if len(pieces) == 2 and not pieces[1].isdigit():
            raise ValueError("cron：步长必须为正整数")
        step = int(pieces[1]) if len(pieces) == 2 else 1
        if not 1 <= step <= high - low + 1:
            raise ValueError("cron：步长超出范围")
        if base == "*":
            start, end = low, high
        elif re.fullmatch(r"\d+-\d+", base):
            start, end = map(int, base.split("-"))
        elif base.isdigit():
            start = int(base)
            end = high if len(pieces) == 2 else start
        else:
            raise ValueError("cron：只支持数字、*、列表、范围和步长")
        if not low <= start <= end <= high:
            raise ValueError("cron：字段数值超出范围")
        values.update(range(start, end + 1, step))
    return values


def parse_cron(expression: str) -> tuple[list[set[int]], bool, bool]:
    parts = expression.split()
    if len(parts) != 5:
        raise ValueError("cron：需要标准五字段（分 时 日 月 周）")
    fields = [cron_field(part, *bounds) for part, bounds in zip(parts, [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)])]
    fields[4] = {day % 7 for day in fields[4]}
    return fields, parts[2].startswith("*"), parts[4].startswith("*")


def normalize_schedule(value: Any, now: datetime | None = None, previous: dict | None = None) -> dict:
    if not isinstance(value, dict):
        raise ValueError("schedule：需要对象")
    allowed = {"type", "timezone", "times", "days", "day", "every", "unit", "date", "expression", "anchor"}
    if set(value) - allowed:
        raise ValueError(f"schedule：未知字段 {', '.join(sorted(set(value) - allowed))}")
    kind = value.get("type")
    if kind not in TYPES:
        raise ValueError("schedule.type：无效频率")
    zone = value.get("timezone", "Asia/Shanghai")
    if not isinstance(zone, str):
        raise ValueError("schedule.timezone：需要 IANA 时区")
    try:
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("schedule.timezone：时区不存在") from exc
    result = {"type": kind, "timezone": zone}
    permitted = {"type", "timezone"}
    if kind in {"once", "daily", "weekly", "monthly"}:
        times = value.get("times")
        if not isinstance(times, list) or not 1 <= len(times) <= 24 or any(not isinstance(t, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", t) for t in times):
            raise ValueError("schedule.times：需要 1–24 个 HH:mm 时间")
        if kind == "once" and len(times) != 1:
            raise ValueError("单次计划只能设置一个时间")
        result["times"] = sorted(set(times))
        permitted.add("times")
    if kind == "once":
        try:
            result["date"] = date.fromisoformat(value["date"]).isoformat()
        except (KeyError, ValueError, TypeError) as exc:
            raise ValueError("schedule.date：需要 YYYY-MM-DD 日期") from exc
        permitted.add("date")
    if kind == "weekly":
        days = value.get("days")
        if not isinstance(days, list) or not days:
            raise ValueError("schedule.days：至少选择一天")
        result["days"] = sorted({integer(d, 0, 6, "schedule.days") for d in days})
        permitted.add("days")
    if kind == "monthly":
        day = value.get("day")
        result["day"] = "last" if day == "last" else integer(day, 1, 31, "schedule.day")
        permitted.add("day")
    if kind == "interval":
        result["every"] = integer(value.get("every"), 1, 525600, "schedule.every")
        unit = value.get("unit")
        if unit not in {"minutes", "hours"}:
            raise ValueError("schedule.unit：只支持 minutes/hours")
        result["unit"] = unit
        same = previous and all(previous.get(k) == result[k] for k in ("type", "every", "unit"))
        anchor = value.get("anchor") or (previous.get("anchor") if same else None)
        result["anchor"] = iso(instant(anchor) if anchor else (now or utc_now()))
        permitted.update({"every", "unit", "anchor"})
    if kind == "cron":
        expression = value.get("expression")
        if not isinstance(expression, str) or len(expression) > 160:
            raise ValueError("schedule.expression：需要五字段 cron")
        parse_cron(expression)
        result["expression"] = " ".join(expression.split())
        permitted.add("expression")
    if set(value) - permitted:
        raise ValueError("schedule：包含不适用于当前频率的字段")
    return result


def _local_instant(day: date, hhmm: str, zone: ZoneInfo) -> datetime | None:
    hour, minute = map(int, hhmm.split(":"))
    local = datetime.combine(day, time(hour, minute)).replace(tzinfo=zone, fold=0)
    result = local.astimezone(UTC)
    # Nonexistent DST wall times do not survive a round trip. Ambiguity uses fold 0.
    return result if result.astimezone(zone).replace(tzinfo=None) == local.replace(tzinfo=None) else None


def next_occurrence(schedule: dict, after: datetime) -> datetime | None:
    after = instant(after)
    kind = schedule["type"]
    if kind == "interval":
        anchor = instant(schedule["anchor"])
        seconds = schedule["every"] * (60 if schedule["unit"] == "minutes" else 3600)
        count = max(1, math.floor((after - anchor).total_seconds() / seconds) + 1)
        return anchor + timedelta(seconds=count * seconds)
    zone = ZoneInfo(schedule["timezone"])
    if kind == "once":
        point = _local_instant(date.fromisoformat(schedule["date"]), schedule["times"][0], zone)
        return point if point and point > after else None
    start = after.astimezone(zone).date()
    cron = parse_cron(schedule["expression"]) if kind == "cron" else None
    times = ([(h, m) for h in sorted(cron[0][1]) for m in sorted(cron[0][0])] if cron
             else [tuple(map(int, item.split(":"))) for item in schedule["times"]])
    for offset in range(366 * 8 + 1):
        day = start + timedelta(days=offset)
        weekday = (day.weekday() + 1) % 7
        if kind == "weekly" and weekday not in schedule["days"]:
            continue
        if kind == "monthly":
            expected = calendar.monthrange(day.year, day.month)[1] if schedule["day"] == "last" else schedule["day"]
            if day.day != expected:
                continue
        if cron:
            fields, dom_wild, dow_wild = cron
            if day.month not in fields[3]:
                continue
            dom, dow = day.day in fields[2], weekday in fields[4]
            matches = (dom and dow) if (dom_wild or dow_wild) else (dom or dow)
            if not matches:
                continue
        for hour, minute in times:
            point = _local_instant(day, f"{hour:02d}:{minute:02d}", zone)
            if point and point > after:
                return point
    return None


def previous_occurrence(schedule: dict, before: datetime) -> datetime | None:
    """Most recent occurrence at or before before, without replaying missed ticks."""
    before = instant(before)
    kind = schedule["type"]
    if kind == "interval":
        anchor = instant(schedule["anchor"])
        seconds = schedule["every"] * (60 if schedule["unit"] == "minutes" else 3600)
        count = math.floor((before - anchor).total_seconds() / seconds)
        return anchor + timedelta(seconds=count * seconds) if count >= 1 else None
    zone = ZoneInfo(schedule["timezone"])
    if kind == "once":
        point = _local_instant(date.fromisoformat(schedule["date"]), schedule["times"][0], zone)
        return point if point and point <= before else None
    start = before.astimezone(zone).date()
    cron = parse_cron(schedule["expression"]) if kind == "cron" else None
    times = ([(h, m) for h in sorted(cron[0][1], reverse=True) for m in sorted(cron[0][0], reverse=True)] if cron
             else sorted([tuple(map(int, item.split(":"))) for item in schedule["times"]], reverse=True))
    for offset in range(366 * 8 + 1):
        day = start - timedelta(days=offset)
        weekday = (day.weekday() + 1) % 7
        if kind == "weekly" and weekday not in schedule["days"]:
            continue
        if kind == "monthly":
            expected = calendar.monthrange(day.year, day.month)[1] if schedule["day"] == "last" else schedule["day"]
            if day.day != expected:
                continue
        if cron:
            fields, dom_wild, dow_wild = cron
            dom, dow = day.day in fields[2], weekday in fields[4]
            if day.month not in fields[3] or not ((dom and dow) if (dom_wild or dow_wild) else (dom or dow)):
                continue
        for hour, minute in times:
            point = _local_instant(day, f"{hour:02d}:{minute:02d}", zone)
            if point and point <= before:
                return point
    return None


def next_dates(schedule: dict, count: int = 3, after: datetime | None = None) -> list[str]:
    cursor = after or utc_now()
    result = []
    for _ in range(min(count, 20)):
        cursor = next_occurrence(schedule, cursor)
        if cursor is None:
            break
        result.append(iso(cursor))
    return result
