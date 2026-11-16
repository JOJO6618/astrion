"""共享测试辅助：路径、常量与计划定义构造器。

测试全部使用 tempfile 目录中的临时 SQLite，不读取任何真实 .env、用户数据或凭据。
"""
from __future__ import annotations

import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]  # <worktree>/test/2026-11-16_定时任务 -> <worktree>
for entry in (str(ROOT), str(HERE)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from datetime import datetime, timezone  # noqa: E402

UTC = timezone.utc
T0 = datetime(2026, 11, 16, 0, 0, tzinfo=UTC)
PRINCIPAL = {"kind": "user", "user": "alice"}
SOURCE = {"type": "agent", "conversation_id": "conv-source"}


def interval_schedule(every: int = 1, unit: str = "minutes", anchor: str = "2026-11-16T00:00:00Z") -> dict:
    return {"type": "interval", "timezone": "UTC", "every": every, "unit": unit, "anchor": anchor}


def daily_schedule(times: list[str] | None = None, timezone_name: str = "UTC") -> dict:
    return {"type": "daily", "timezone": timezone_name, "times": times or ["00:30"]}


def once_schedule(date_text: str = "2026-11-16", time_text: str = "01:00") -> dict:
    return {"type": "once", "timezone": "UTC", "date": date_text, "times": [time_text]}


def plan_definition(
    *,
    name: str = "巡检",
    message: str = "检查项目状态",
    enabled: bool = True,
    schedule: dict | None = None,
    policies: dict | None = None,
    workspace: str = "ws-1",
    conversation: dict | None = None,
) -> dict:
    """与 normalize_definition 输出形状一致的完整计划定义。"""
    return {
        "schema_version": 1,
        "name": name,
        "message": message,
        "enabled": enabled,
        "schedule": dict(schedule if schedule is not None else interval_schedule()),
        "target": {
            "workspace_id": workspace,
            "conversation": dict(conversation if conversation is not None else {"mode": "new"}),
        },
        "configuration": {"mode": "inherit"},
        "goal": {"mode": "off"},
        "policies": dict(
            policies
            if policies is not None
            else {"busy": "skip", "missed": "skip", "approval_timeout_seconds": 3600}
        ),
        "notifications": {"enabled": False},
    }


def at(day: int, hour: int = 0, minute: int = 0, second: int = 0, month: int = 11, year: int = 2026) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=UTC)
