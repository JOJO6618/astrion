"""自动审核上下文用例的公共导入与 fixture 构造工具。

被 test_history.py / test_history_details.py 共用；本身不含测试用例，
因此不会被 unittest discover 的 test_*.py 模式加载。

只依赖 modules/approval_history.py 与 modules/approval_context_text.py，
不读取真实对话、不请求模型、不写任何文件。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()


def _find_project_root() -> Path:
    """从本文件目录与 cwd 逐级上溯，找到含 modules/approval_history.py 的项目根。

    正式位置 test/2026-11-06_自动审核上下文/ 与本工作区嵌套目录都能命中；
    也支持用 ASTRION_PROJECT_ROOT 显式指定。
    """
    import os

    override = str(os.environ.get("ASTRION_PROJECT_ROOT") or "").strip()
    candidates = [Path(override)] if override else []
    candidates += [_HERE, *_HERE.parents, Path.cwd().resolve(), *Path.cwd().resolve().parents]
    for base in candidates:
        try:
            if (base / "modules" / "approval_history.py").exists():
                return base
        except OSError:
            continue
    return Path.cwd().resolve()


ROOT = _find_project_root()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# 同目录直接导入 fixture 模块（discover 与脚本直跑都成立）
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

from modules.approval_context_text import (  # noqa: E402
    attachment_text,
    parse_arguments,
    result_text,
    scalar_text,
    status_text,
    text_content,
    tool_payload,
    user_text,
    value_text,
)
from modules.approval_history import (  # noqa: E402
    TOOL_WINDOW,
    USER_WINDOW,
    ApprovalHistory,
)


# ---------------------------------------------------------------- fixtures

def user_msg(text: str, *, mid: str = "", source: str | None = "user", **meta) -> dict:
    metadata = dict(meta)
    if source is not None:
        metadata["message_source"] = source
    message = {"role": "user", "content": text}
    if mid:
        message["message_id"] = mid
    if metadata:
        message["metadata"] = metadata
    return message


def chat_msg(text: str, *, mid: str = "") -> dict:
    """当前 Chat 受理的 user 消息：只有 visibility/starts_work，没有 message_source。"""
    return user_msg(text, mid=mid, source=None, visibility="chat", starts_work=True)


def guidance_msg(text: str, *, mid: str = "", original: str | None = None) -> dict:
    meta = {
        "message_source": "guidance",
        "is_auto_generated": True,
        "runtime_injected": True,
        "runtime_guidance": True,
    }
    if original is not None:
        meta["runtime_guidance_original"] = original
    return user_msg(f"[系统通知|guidance]\n{text}", mid=mid, source=None, **meta)


def assistant_calls(*calls: tuple[str, str, dict]) -> dict:
    """calls: (call_id, tool_name, arguments)"""
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(arguments, ensure_ascii=False),
                },
            }
            for call_id, name, arguments in calls
        ],
    }


def tool_result(call_id: str, name: str, payload: dict | str, *, mid: str = "") -> dict:
    message = {
        "role": "tool",
        "tool_call_id": call_id,
        "name": name,
        "content": payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False),
    }
    if mid:
        message["message_id"] = mid
    return message


def bodies(history: ApprovalHistory, kind: str | None = None) -> list[str]:
    rows = history.entries if kind is None else [row for row in history.entries if row.kind == kind]
    return [row.body for row in rows]


def all_rendered(history: ApprovalHistory, *, count: int = 20) -> str:
    """initial_text + 全量 read，便于断言"某文本是否出现在审核可见上下文里"。"""
    return history.initial_text() + "\n" + history.read(offset=0, count=min(count, 20))


def rendered_kinds(text: str) -> list[str]:
    """initial_text 中每个条目头部形如 `3 用户`；返回条目类型序列。"""
    return [kind for _, kind in re.findall(r"^(\d+) (.+)$", text, flags=re.M)]
