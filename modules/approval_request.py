"""Capture and render one approval's context; model payloads are plain text."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from typing import Any

from modules.approval_context_text import metadata, value_text
from modules.approval_history import ApprovalHistory


def _runtime_inputs(terminal: Any, username: str, item: dict) -> dict:
    if not username or not item.get("task_id") or not item.get("conversation_id"):
        return {}
    from server.tasks import task_manager
    reader = getattr(task_manager, "snapshot_review_inputs", None)
    return reader(username, item["task_id"], item["conversation_id"]) if reader else {}


def _goal(terminal: Any, conversation_id: str | None) -> str:
    if not conversation_id or not getattr(terminal, "data_dir", None):
        return ""
    from modules.goal_state_manager import GoalStateManager
    manager = GoalStateManager(terminal.data_dir, conversation_id)
    state = manager.load()
    return str(state.get("goal") or "") if state.get("active") else ""


def _mode(terminal: Any, getter: str, default: str) -> str:
    return str(getattr(terminal, getter, lambda: default)())


def capture_inputs(terminal: Any, username: str, item: dict) -> dict:
    cm = terminal.context_manager
    history = deepcopy(getattr(cm, "conversation_history", None) or [])
    runtime = _runtime_inputs(terminal, username, item)
    trigger = str(runtime.get("trigger_message_id") or getattr(cm, "current_shallow_message_id", None) or "")
    # Compression preserves the original work anchor rather than its handoff text.
    anchor = str(getattr(cm, "current_shallow_message_id", None) or "")
    if anchor and any(row.get("message_id") == anchor for row in history if isinstance(row, dict)):
        trigger = anchor
    return {
        "history": history, "pending": deepcopy(runtime.get("guidance") or []),
        "trigger": trigger, "goal": _goal(terminal, item.get("conversation_id")),
        "execution": _mode(terminal, "get_execution_mode", "sandbox"),
        "permission": _mode(terminal, "get_permission_mode", "auto_approval"),
        "network": _mode(terminal, "get_network_permission", "restricted"),
        "workspace": str(getattr(cm, "project_path", "")),
    }


def input_fingerprint(inputs: dict) -> str:
    # Internal JSON is only used for hashing, never sent to the reviewer.
    # Include call parameters and returned evidence, not only authorization text.
    # Thinking, counters and compression-only marks do not change review evidence.
    history = []
    for row in inputs["history"]:
        if not isinstance(row, dict):
            continue
        meta = metadata(row)
        history.append({
            key: row.get(key) for key in (
                "role", "content", "message_id", "tool_calls", "tool_call_id", "name", "media_refs", "images", "videos",
            )
        } | {"metadata": {key: meta.get(key) for key in (
            "message_source", "source", "is_auto_generated", "runtime_injected", "hidden",
            "visibility", "multi_agent_message", "runtime_guidance_original", "files", "tool_payload",
        )}})
    values = {key: value for key, value in inputs.items() if key != "history"}
    values["history"] = history
    encoded = json.dumps(values, ensure_ascii=False, sort_keys=True, default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass
class ApprovalRequest:
    terminal: Any
    username: str
    item: dict
    inputs: dict
    history: ApprovalHistory
    fingerprint: str

    @classmethod
    def capture(cls, terminal: Any, username: str, item: dict) -> "ApprovalRequest":
        inputs = capture_inputs(terminal, username, item)
        history = ApprovalHistory(inputs["history"], trigger_id=inputs["trigger"], pending=inputs["pending"])
        return cls(terminal, username, deepcopy(item), inputs, history, input_fingerprint(inputs))

    def is_current(self) -> bool:
        return self.fingerprint == input_fingerprint(capture_inputs(self.terminal, self.username, self.item))

    def render(self, function_name: str, arguments: dict, risk_markers: list | None = None) -> str:
        execution = self.inputs["execution"]
        full = self.item.get("approval_type") == "full_access"
        mode_text = "完全访问" if execution == "direct" else "沙箱"
        if full:
            mode_text += "，本次申请单次完全访问"
        lines = ["环境：" + mode_text]
        if execution == "direct" or full:
            lines.append("本次将在宿主机脱离沙箱，以运行Astrion的系统账户权限执行。")
        else:
            lines.append("网络权限：" + self.inputs["network"])
        if full and self.item.get("human_approval_required", True):
            lines.append("自动审核通过后仍需人工批准。")
        else:
            lines.append("自动审核通过即可执行。")
        directory = arguments.get("working_dir") or self.inputs["workspace"]
        lines.append("待审工作目录：" + str(directory))
        if self.item.get("approval_type") == "browser_action":
            url = (self.item.get("preview") or {}).get("browser_url") or ""
            lines.append(f"浏览器操作站点：{self.item.get('browser_site')}" + (f"（当前网址 {url}）" if url else "") + "；在用户已登录的真实网页上执行，批准仅限本次。")
        if self.inputs["goal"]:
            lines.extend(["当前用户目标：", self.inputs["goal"]])
        lines.append(self.history.initial_text())
        if risk_markers:
            lines.append("规则提示（不是裁决）：" + "、".join(str(value) for value in risk_markers))
        # The same current directory appears once; histories retain their own directories.
        params = {key: value for key, value in arguments.items() if key != "working_dir"}
        lines.extend(["待审：" + function_name, value_text(params)])
        return "\n".join(lines)
