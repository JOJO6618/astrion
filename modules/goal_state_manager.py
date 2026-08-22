"""目标模式（Goal Mode）的对话级状态管理。

每个对话各自持有独立的目标状态，互不影响。状态落盘到
`{data_dir}/goal_states/<conversation_id>.json`，在切换对话、对话压缩、
进程重启后仍能维持（压缩不会改变 conversation_id，压缩 handoff 重入时
由主循环入口按当前 conversation_id 重新加载本对话状态并续注提示词）。

设计要点：
- 不依赖 web_terminal，只接受 data_dir 与 conversation_id，便于单测与复用。
- token 基线对齐工作区级累计 `{data_dir}/token_totals.json`（input/output/total）。
  注意：该累计是整个工作区所有对话共享的，多对话并发运行时 max_tokens
  边界判定是近似值（可能因其他对话的消耗而提前触发）。
- review_history 保存历轮 {main_output, review_reply}，用于构建交叉结构审核文本。
"""

from __future__ import annotations

import json
import re
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

GOAL_STATES_DIRNAME = "goal_states"

# 状态机
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_STOPPED = "stopped"

# 停止原因
REASON_IDLE_NO_TOOL = "idle_no_tool"
REASON_MAX_TURNS = "max_turns"
REASON_MAX_TOKENS = "max_tokens"
REASON_USER_CANCEL = "user_cancel"

# 审核模式
REVIEW_MODE_READONLY = "readonly"
REVIEW_MODE_ACTIVE = "active"

PathLike = Union[str, Path]

_SAFE_CONVERSATION_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def _validate_conversation_id(conversation_id: str) -> str:
    """conversation_id 用作状态文件名，必须是安全字符，防止路径穿越。"""
    cid = str(conversation_id or "").strip()
    if not cid or not _SAFE_CONVERSATION_ID.match(cid):
        raise ValueError(f"非法 conversation_id: {conversation_id!r}")
    return cid


def _empty_state() -> Dict[str, Any]:
    return {
        "active": False,
        "status": STATUS_STOPPED,
        "goal": "",
        "review_mode": REVIEW_MODE_READONLY,
        "max_turns": 5,
        "max_tokens": None,
        "started_at": None,
        "turn_count": 0,
        "token_baseline": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        "tool_call_count_baseline": 0,
        "tool_calls_used": 0,
        "review_history": [],
        "final_summary": None,
        "stopped_reason": None,
    }


class GoalStateManager:
    """对话级目标状态。一个实例对应一个对话的 goal_states/<conversation_id>.json。"""

    def __init__(self, data_dir: PathLike, conversation_id: str):
        self.data_dir = Path(data_dir).expanduser()
        self.conversation_id = _validate_conversation_id(conversation_id)
        self.state: Dict[str, Any] = self.load()

    # ------------------------------------------------------------------ 路径/持久化

    def _path(self) -> Path:
        return self.data_dir / GOAL_STATES_DIRNAME / f"{self.conversation_id}.json"

    @classmethod
    def load_from(cls, data_dir: PathLike, conversation_id: str) -> "GoalStateManager":
        """便捷构造：等价于 GoalStateManager(data_dir, conversation_id)。"""
        return cls(data_dir, conversation_id)

    def load(self) -> Dict[str, Any]:
        path = self._path()
        if not path.exists():
            self.state = _empty_state()
            return self.state
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh) or {}
            merged = _empty_state()
            if isinstance(raw, dict):
                merged.update(raw)
            # 结构兜底
            if not isinstance(merged.get("review_history"), list):
                merged["review_history"] = []
            if not isinstance(merged.get("token_baseline"), dict):
                merged["token_baseline"] = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
            self.state = merged
        except (OSError, json.JSONDecodeError, ValueError):
            self.state = _empty_state()
        return self.state

    def save(self) -> None:
        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.state, fh, ensure_ascii=False, indent=2)
        tmp.replace(path)

    # ------------------------------------------------------------------ 生命周期

    def start(
        self,
        *,
        goal: str,
        review_mode: str,
        max_turns: Optional[int],
        max_tokens: Optional[int],
        token_baseline: Optional[Dict[str, int]] = None,
        tool_call_baseline: int = 0,
    ) -> Dict[str, Any]:
        rm = review_mode if review_mode in (REVIEW_MODE_READONLY, REVIEW_MODE_ACTIVE) else REVIEW_MODE_READONLY
        baseline = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        if isinstance(token_baseline, dict):
            for k in baseline:
                try:
                    baseline[k] = max(0, int(token_baseline.get(k) or 0))
                except (TypeError, ValueError):
                    baseline[k] = 0
        self.state = {
            "active": True,
            "status": STATUS_RUNNING,
            "goal": str(goal or "").strip(),
            "review_mode": rm,
            "max_turns": int(max_turns) if max_turns else None,
            "max_tokens": int(max_tokens) if max_tokens else None,
            "started_at": time.time(),
            "turn_count": 0,
            "token_baseline": baseline,
            "tool_call_count_baseline": max(0, int(tool_call_baseline or 0)),
            "tool_calls_used": 0,
            "review_history": [],
            "final_summary": None,
            "stopped_reason": None,
        }
        self.save()
        return deepcopy(self.state)

    def is_active(self) -> bool:
        return bool(self.state.get("active")) and self.state.get("status") == STATUS_RUNNING

    def get_goal(self) -> str:
        return str(self.state.get("goal") or "")

    def get_review_mode(self) -> str:
        rm = self.state.get("review_mode")
        return rm if rm in (REVIEW_MODE_READONLY, REVIEW_MODE_ACTIVE) else REVIEW_MODE_READONLY

    def get_turn(self) -> int:
        try:
            return int(self.state.get("turn_count") or 0)
        except (TypeError, ValueError):
            return 0

    def increment_turn(self) -> int:
        self.state["turn_count"] = self.get_turn() + 1
        self.save()
        return self.state["turn_count"]

    def append_review(self, main_output: str, review_reply: str) -> None:
        history = self.state.get("review_history")
        if not isinstance(history, list):
            history = []
        history.append(
            {
                "main_output": str(main_output or ""),
                "review_reply": str(review_reply or ""),
            }
        )
        self.state["review_history"] = history
        self.save()

    def mark_done(self, summary: str) -> Dict[str, Any]:
        self.state["active"] = False
        self.state["status"] = STATUS_DONE
        self.state["final_summary"] = str(summary or "")
        self.state["stopped_reason"] = None
        self.save()
        return deepcopy(self.state)

    def mark_stopped(self, reason: str) -> Dict[str, Any]:
        self.state["active"] = False
        self.state["status"] = STATUS_STOPPED
        self.state["stopped_reason"] = reason
        self.save()
        return deepcopy(self.state)

    def clear(self) -> None:
        """完全清除本对话的目标状态。"""
        self.state = _empty_state()
        path = self._path()
        try:
            if path.exists():
                path.unlink()
            return
        except OSError:
            pass
        self.save()

    # ------------------------------------------------------------------ 边界判定

    def reached_max_turns(self) -> bool:
        mt = self.state.get("max_turns")
        if not mt:
            return False
        return self.get_turn() >= int(mt)

    def reached_max_tokens(self, current_total_tokens: int) -> bool:
        """current_total_tokens 为工作区当前累计 total_tokens。"""
        mt = self.state.get("max_tokens")
        if not mt:
            return False
        used = self.tokens_used(current_total_tokens)
        return used >= int(mt)

    def tokens_used(self, current_total_tokens: int) -> int:
        baseline = self.state.get("token_baseline") or {}
        try:
            base_total = int(baseline.get("total_tokens") or 0)
        except (TypeError, ValueError):
            base_total = 0
        return max(0, int(current_total_tokens or 0) - base_total)

    # ------------------------------------------------------------------ 审核输入构建

    def build_review_payload_text(self, current_main_output: str) -> str:
        """生成交叉结构的单条 user 文本：目标 + 历轮(主模型输出/审核反馈) + 当前轮待审。"""
        goal = self.get_goal()
        lines: List[str] = ["【本次目标】", goal or "（目标为空）", ""]
        history = self.state.get("review_history") or []
        for idx, entry in enumerate(history, start=1):
            if not isinstance(entry, dict):
                continue
            lines.append(f"【第{idx}轮】")
            lines.append(f"主执行模型输出：{str(entry.get('main_output') or '').strip()}")
            lines.append(f"你的反馈：{str(entry.get('review_reply') or '').strip()}")
            lines.append("")
        lines.append("【当前轮·待审核】")
        lines.append(f"主执行模型输出：{str(current_main_output or '').strip()}")
        return "\n".join(lines)

    # ------------------------------------------------------------------ 前端快照

    def progress_snapshot(self, *, current_total_tokens: int = 0, current_tool_calls: int = 0) -> Dict[str, Any]:
        started_at = self.state.get("started_at")
        duration = None
        if started_at:
            try:
                duration = max(0.0, time.time() - float(started_at))
            except (TypeError, ValueError):
                duration = None
        try:
            tool_baseline = int(self.state.get("tool_call_count_baseline") or 0)
        except (TypeError, ValueError):
            tool_baseline = 0
        try:
            stored_tool_calls = max(0, int(self.state.get("tool_calls_used") or 0))
        except (TypeError, ValueError):
            stored_tool_calls = 0
        current_tool_calls_used = max(0, int(current_tool_calls or 0) - tool_baseline)
        tool_calls_used = max(stored_tool_calls, current_tool_calls_used)
        if tool_calls_used != stored_tool_calls:
            self.state["tool_calls_used"] = tool_calls_used
            try:
                self.save()
            except Exception:
                pass
        return {
            "conversation_id": self.conversation_id,
            "goal": self.get_goal(),
            "status": self.state.get("status"),
            "turn_count": self.get_turn(),
            "tokens_used": self.tokens_used(current_total_tokens),
            "tool_calls": tool_calls_used,
            "duration_seconds": duration,
            "review_mode": self.get_review_mode(),
            "max_turns": self.state.get("max_turns"),
            "max_tokens": self.state.get("max_tokens"),
            "final_summary": self.state.get("final_summary"),
            "stopped_reason": self.state.get("stopped_reason"),
        }
