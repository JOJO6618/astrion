"""Weekly subscription quota value tracker.

每小时采样一次 ChatGPT 订阅的周窗口用量，把本周期已消耗的 API 价值（按本地
账本折算）与推算的「满额度价值」写入状态文件；当用量归零或窗口重置时间变化
时，把上一条记录归档为「上一周期」，从而形成一个可回看的历史列表。

设计约束：
- 账本是唯一金额来源（<DATA_DIR>/api_costs/*.json），本模块不累加计数，只存
  周期边界与最近一次采样结果，避免进程重启/崩溃导致数字漂移。
- 采样失败保留旧值并标记 stale，不用 0 覆盖。
- 只有真正拿到周窗口才写入，避免把 5 小时窗口误当周窗口。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from config import DATA_DIR

logger = logging.getLogger(__name__)

STATE_FILENAME = "weekly_quota.json"
WEEK_SECONDS = 7 * 24 * 60 * 60
WEEK_TOLERANCE = 0.15          # 窗口长度容差（±15%），避免把 5 小时窗口当周窗口
SAMPLE_INTERVAL_SECONDS = 60 * 60
MIN_SAMPLE_GAP_SECONDS = 300   # 页面刷新触发的补采最小间隔
HISTORY_LIMIT = 26             # 保留最近约半年
DECREASE_EPSILON = 0.01        # 用量下降超过该值视为周期重置

_lock = threading.RLock()
_wake = threading.Event()
_thread: Optional[threading.Thread] = None
_started = False


# ── 状态文件 ──────────────────────────────────────────────────────────

def _state_path():
    from pathlib import Path
    return Path(DATA_DIR) / STATE_FILENAME


def _empty_state() -> dict:
    return {"version": 1, "accounts": {}, "last_attempt_at": None, "last_error": ""}


def _load_state() -> dict:
    path = _state_path()
    if not path.exists():
        return _empty_state()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        logger.warning("weekly_quota state unreadable, starting fresh", exc_info=True)
        return _empty_state()
    if not isinstance(data, dict) or not isinstance(data.get("accounts"), dict):
        return _empty_state()
    return data


def _save_state(state: dict) -> None:
    path = _state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        logger.warning("weekly_quota state write failed", exc_info=True)


# ── 账本金额 ──────────────────────────────────────────────────────────

def _ledger_dirs():
    from pathlib import Path
    base = Path(DATA_DIR)
    dirs = {base / "api_costs", base / "chat" / "api_costs"}
    try:
        for child in base.iterdir():
            if child.is_dir():
                candidate = child / "api_costs"
                if candidate.is_dir():
                    dirs.add(candidate)
    except OSError:
        pass
    return [d for d in dirs if d.is_dir()]


def _parse_ts(raw: Any) -> Optional[float]:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw).timestamp()
    except ValueError:
        return None


def gpt_value_between(start_ts: float, end_ts: float) -> Decimal:
    """累加账本中 [start, end) 区间内 GPT 请求的折算金额（按请求 UUID 去重）。"""
    total = Decimal("0")
    seen: set[str] = set()
    for directory in _ledger_dirs():
        for path in directory.glob("*.json"):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    payload = json.load(fh)
            except Exception:
                continue
            requests = payload.get("requests")
            if not isinstance(requests, dict):
                continue
            for request_id, record in requests.items():
                if not isinstance(record, dict) or request_id in seen:
                    continue
                model_key = str((record.get("price") or {}).get("model_key") or "").lower()
                if "gpt" not in model_key:
                    continue
                recorded = _parse_ts(record.get("recorded_at"))
                if recorded is None or not (start_ts <= recorded < end_ts):
                    continue
                seen.add(request_id)
                try:
                    total += Decimal(str(record.get("cost_usd") or "0"))
                except Exception:
                    continue
    return total


# ── 采样 ──────────────────────────────────────────────────────────────

def _pick_weekly_window(usage: dict) -> Optional[dict]:
    """从 primary/secondary 中挑出最接近 7 天的窗口，拿不到返回 None。"""
    best: Optional[dict] = None
    best_diff: Optional[float] = None
    for _name in ("primary", "secondary"):
        window = (usage or {}).get(_name)
        if not isinstance(window, dict):
            continue
        used = window.get("usedPercent")
        minutes = window.get("windowDurationMins")
        resets_at = window.get("resetsAt")
        if not isinstance(used, (int, float)) or not isinstance(minutes, (int, float)):
            continue
        if not isinstance(resets_at, (int, float)) or resets_at <= 0:
            continue
        seconds = float(minutes) * 60
        if abs(seconds - WEEK_SECONDS) > WEEK_SECONDS * WEEK_TOLERANCE:
            continue
        diff = abs(seconds - WEEK_SECONDS)
        if best_diff is None or diff < best_diff:
            best_diff = diff
            best = {
                "used_percent": float(used),
                "window_seconds": int(round(seconds)),
                "resets_at": int(resets_at),
            }
    return best


def _sample_account(account_id: str, state: dict) -> None:
    from utils.api_client.codex.accounts import get_codex_account_store
    from modules.crosspet.quota import fetch_usage

    store = get_codex_account_store()
    auth = store.get_auth_manager(account_id)
    usage = fetch_usage(auth)
    window = _pick_weekly_window(usage)
    if window is None:
        raise ValueError("weekly_window_unavailable")

    resets_at = int(window["resets_at"])
    window_seconds = int(window["window_seconds"])
    used_percent = float(window["used_percent"])
    period_start = resets_at - window_seconds
    now = time.time()

    value = gpt_value_between(float(period_start), float(min(now, resets_at)))
    max_value = None
    if used_percent > 0:
        max_value = value / (Decimal(str(used_percent)) / Decimal(100))

    entry = {
        "period_start": period_start,
        "period_end": resets_at,
        "resets_at": resets_at,
        "window_seconds": window_seconds,
        "used_percent": round(used_percent, 2),
        "api_value_usd": str(value.quantize(Decimal("0.000001"))),
        "max_value_usd": None if max_value is None else str(max_value.quantize(Decimal("0.000001"))),
        "sampled_at": datetime.now(timezone.utc).isoformat(),
        "stale": False,
    }

    accounts = state.setdefault("accounts", {})
    account = accounts.setdefault(account_id, {"current": None, "history": []})
    previous = account.get("current")

    if isinstance(previous, dict):
        prev_resets = int(previous.get("resets_at") or 0)
        prev_used = float(previous.get("used_percent") or 0)
        rolled = prev_resets != resets_at or used_percent < prev_used - DECREASE_EPSILON
        if rolled:
            archived = dict(previous)
            archived["archived_at"] = datetime.now(timezone.utc).isoformat()
            account.setdefault("history", []).insert(0, archived)
            del account["history"][HISTORY_LIMIT:]

    account["current"] = entry


def sample_all() -> None:
    """对所有已连接账号采样一次；单账号失败不影响其他账号。"""
    from utils.api_client.codex.accounts import get_codex_account_store

    try:
        store = get_codex_account_store()
        accounts = [a for a in store.list_accounts() if a.get("connected")]
    except Exception as exc:
        logger.warning("weekly_quota account discovery failed: %s", exc)
        return

    with _lock:
        state = _load_state()
        now = time.time()
        last_attempt = _parse_ts(state.get("last_attempt_at"))
        if (last_attempt is not None and state.get("accounts")
                and now - last_attempt < MIN_SAMPLE_GAP_SECONDS):
            return
        state["last_attempt_at"] = datetime.now(timezone.utc).isoformat()
        errors = []
        for account in accounts:
            account_id = str(account.get("id") or "")
            if not account_id:
                continue
            try:
                _sample_account(account_id, state)
            except Exception as exc:
                errors.append(f"{account_id}: {exc}")
                logger.info("weekly_quota sample skipped for %s: %s", account_id, exc)
        state["last_error"] = "; ".join(errors)[:500]
        _save_state(state)


def _loop() -> None:
    # 启动后先让服务完成初始化，再采第一次。
    _wake.wait(20)
    while True:
        try:
            sample_all()
        except Exception:
            logger.warning("weekly_quota sample cycle failed", exc_info=True)
        _wake.wait(SAMPLE_INTERVAL_SECONDS)
        _wake.clear()


def start_tracker() -> None:
    global _thread, _started
    with _lock:
        if _started:
            return
        _started = True
        _thread = threading.Thread(target=_loop, name="weekly-quota-tracker", daemon=True)
        _thread.start()


def request_sample() -> None:
    """唤醒后台线程立即采样一次（非阻塞）。"""
    _wake.set()


# ── 读取 ──────────────────────────────────────────────────────────────

def get_snapshot(account_id: str) -> dict:
    """返回某账号的本周期记录与历史周期（history 按结束时间倒序）。"""
    with _lock:
        state = _load_state()
    account = (state.get("accounts") or {}).get(account_id) or {}
    current = account.get("current")
    history = [h for h in (account.get("history") or []) if isinstance(h, dict)]
    history.sort(key=lambda item: int(item.get("period_end") or 0), reverse=True)
    return {
        "current": current if isinstance(current, dict) else None,
        "history": history,
        "last_attempt_at": state.get("last_attempt_at"),
        "last_error": state.get("last_error") or "",
    }
