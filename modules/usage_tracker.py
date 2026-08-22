"""Per-user usage tracking utilities with rolling quota windows."""

import json
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional, Literal, Tuple


QuotaKey = Literal["fast", "thinking", "search"]


QUOTA_DEFAULTS = {
    "default": {
        "fast": {"limit": 200, "window_hours": 5},
        "thinking": {"limit": 200, "window_hours": 5},
        "search": {"limit": 20, "window_hours": 24},
    },
    "search_daily": {"limit": 20, "window_hours": 24},
    "admin": {
        "fast": {"limit": 999, "window_hours": 5},
        "thinking": {"limit": 999, "window_hours": 5},
        "search": {"limit": 999, "window_hours": 24},
    },
}


class UsageTracker:
    """Record per-user model/search usage statistics and enforce quotas."""

    def __init__(self, data_dir: str, role: str = "user"):
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.stats_file = self.data_dir / "usage_stats.json"
        self.role = role or "user"
        self._lock = threading.Lock()
        self._state = {
            "started_at": self._now_iso(),
            "updated_at": self._now_iso(),
            "windows": {
                "fast": {"count": 0, "window_start": None},
                "thinking": {"count": 0, "window_start": None},
                "search": {"count": 0, "window_start": None},
            },
        }
        self._load()

    def _now_iso(self) -> str:
        return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

    def _load(self):
        if not self.stats_file.exists():
            return
        try:
            data = json.loads(self.stats_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._merge_state(data)
        except Exception:
            pass

    def _merge_state(self, data: Dict):
        windows = data.get("windows")
        if windows:
            for key in ("fast", "thinking", "search"):
                win = windows.get(key) or {}
                self._state["windows"][key] = {
                    "count": int(win.get("count", 0)),
                    "window_start": win.get("window_start"),
                }
        else:
            legacy_model = data.get("model_calls") or {}
            legacy_search = data.get("search_calls") or {}
            self._state["windows"]["fast"]["count"] = int(legacy_model.get("fast", 0))
            self._state["windows"]["thinking"]["count"] = int(legacy_model.get("thinking", 0))
            self._state["windows"]["search"]["count"] = int(legacy_search.get("total", 0))
            for key in ("fast", "thinking", "search"):
                if not self._state["windows"][key].get("window_start"):
                    self._state["windows"][key]["window_start"] = None
        self._state["started_at"] = data.get("started_at") or self._state["started_at"]
        self._state["updated_at"] = data.get("updated_at") or self._state["updated_at"]

    def _save(self):
        with self.stats_file.open("w", encoding="utf-8") as f:
            json.dump(self._state, f, ensure_ascii=False, indent=2)

    def _get_quota_config(self, metric: QuotaKey) -> Dict[str, int]:
        if self.role == "admin":
            return QUOTA_DEFAULTS["admin"][metric]
        if metric == "search":
            return QUOTA_DEFAULTS["search_daily"]
        return QUOTA_DEFAULTS["default"][metric]

    def _ensure_window(
        self,
        metric: QuotaKey,
        init_if_missing: bool = False,
    ) -> Tuple[int, Optional[str], Optional[datetime], Optional[datetime]]:
        """Returns tuple(count, window_start_iso, window_start_dt, reset_at_dt)."""
        config = self._get_quota_config(metric)
        window_hours = config["window_hours"]
        window_delta = timedelta(hours=window_hours)
        window_data = self._state["windows"].setdefault(metric, {"count": 0, "window_start": None})
        count = int(window_data.get("count", 0))
        window_start_iso = window_data.get("window_start")
        now = datetime.utcnow()
        window_start_dt: Optional[datetime] = None

        if window_start_iso:
            try:
                parsed = datetime.fromisoformat(window_start_iso.replace("Z", ""))
                window_start_dt = floor_to_hour(parsed)
            except ValueError:
                window_start_dt = None

        reset_at_dt: Optional[datetime] = None
        if window_start_dt:
            reset_at_dt = window_start_dt + window_delta
            if now >= reset_at_dt:
                window_data["count"] = 0
                count = 0
                if init_if_missing:
                    window_start_dt = floor_to_hour(now)
                    window_data["window_start"] = frame_iso(window_start_dt)
                    reset_at_dt = window_start_dt + window_delta
                else:
                    window_start_dt = None
                    window_data["window_start"] = None
                    reset_at_dt = None

        if window_start_dt is None and init_if_missing:
            window_start_dt = floor_to_hour(now)
            window_data["window_start"] = frame_iso(window_start_dt)
            reset_at_dt = window_start_dt + window_delta
        elif count == 0 and window_data.get("window_start") and not init_if_missing:
            # 没有真实用量时清理遗留的窗口起点，使下一次调用时重新计窗。
            window_data["window_start"] = None
            window_start_iso = None

        if window_start_dt and not reset_at_dt:
            reset_at_dt = window_start_dt + window_delta

        return (
            count,
            window_data.get("window_start"),
            window_start_dt,
            reset_at_dt,
        )

    def check_and_increment(self, metric: QuotaKey) -> Tuple[bool, Dict[str, str]]:
        """Check quota and increment if allowed. Returns (allowed, info)."""
        with self._lock:
            count, window_start_iso, window_start_dt, reset_at_dt = self._ensure_window(
                metric, init_if_missing=True
            )
            quota = self._get_quota_config(metric)["limit"]
            if count >= quota:
                reset_at_iso = frame_iso(reset_at_dt)
                return False, {
                    "limit": quota,
                    "count": count,
                    "reset_at": reset_at_iso,
                    "window_start": window_start_iso or "",
                }

            new_count = count + 1
            self._state["windows"][metric]["count"] = new_count
            self._state["updated_at"] = self._now_iso()
            self._save()

            return True, {
                "limit": quota,
                "count": new_count,
                "reset_at": frame_iso(reset_at_dt),
                "window_start": self._state["windows"][metric]["window_start"],
            }

    def get_quota_snapshot(self) -> Dict[str, Dict[str, str]]:
        snapshot = {}
        for key in ("fast", "thinking", "search"):
            count, window_start_iso, _, reset_dt = self._ensure_window(key)
            limit = self._get_quota_config(key)["limit"]
            if not count:
                window_start_iso = None
                reset_value = None
            else:
                reset_value = frame_iso(reset_dt)
            snapshot[key] = {
                "count": count,
                "limit": limit,
                "window_start": window_start_iso,
                "reset_at": reset_value,
            }
        snapshot["role"] = self.role
        return snapshot

    def get_stats(self) -> Dict:
        with self._lock:
            data = json.loads(json.dumps(self._state))
            quotas = self.get_quota_snapshot()
            for key in ("fast", "thinking", "search"):
                data["windows"][key]["reset_at"] = quotas[key]["reset_at"]
            data["role"] = self.role
            data["quotas"] = quotas
            self._save()
            return data


def frame_iso(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat() + "Z"


def floor_to_hour(value: datetime) -> datetime:
    return value.replace(minute=0, second=0, microsecond=0)


__all__ = ["UsageTracker", "QUOTA_DEFAULTS"]
