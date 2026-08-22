"""对话级主任务门闸（单写者防护）。

背景（2026-08-12「平行时空」事故）：socketio 入口的主聊天任务不在
task_manager 注册，`create_chat_task` 的单对话互斥对它们不可见；完成通知
轮询器又只凭 `_tool_loop_active`（仅覆盖工具执行窗口）判断对话是否空闲，
于是在主任务两次工具循环的间隙里派发了通知任务——两个主任务并发交叉写入
同一份 conversation_history，产生 assistant/assistant/tool/tool 乱序段，
下一轮请求重建消息时 tool 配对崩坏（API 400 tool_call_id is not found）；
并发 execute_tool_calls 还把 `_tool_loop_active` 永久卡成 True，通知全部死等。

不变量：**一个对话（= 一个 WebTerminal 实例）同一时刻只允许一个主聊天任务运行。**

用法：
- 所有主任务入口统一收敛在 `process_message_task`（chat_flow.py），在此获取
  门闸并在 finally 释放。
- 通知派发链（完成通知轮询器）先 `try_acquire_main_task_gate` 预占，再通过
  session_data["main_task_gate_token"] 把 token 移交给新任务线程认领；
  派发失败时释放并回滚通知标记。
"""
from __future__ import annotations

import threading
import uuid
from typing import Optional

_LOCK = threading.Lock()
_GATE_ATTR = "_main_task_gate_token"


def try_acquire_main_task_gate(terminal) -> Optional[str]:
    """非阻塞获取门闸。成功返回 token；门闸已被占用返回 None。"""
    if terminal is None:
        return None
    with _LOCK:
        if getattr(terminal, _GATE_ATTR, None):
            return None
        token = uuid.uuid4().hex
        setattr(terminal, _GATE_ATTR, token)
        return token


def acquire_adopted_main_task_gate(terminal, token: Optional[str]) -> Optional[str]:
    """认领派发方预占的门闸 token；认领失败则退化为竞争获取。

    返回 None 表示门闸被其他任务持有，调用方应放弃本次运行。
    """
    if terminal is None:
        return None
    with _LOCK:
        current = getattr(terminal, _GATE_ATTR, None)
        if token and current == token:
            return token  # 认领成功（门闸已由派发方持有）
        if current:
            return None  # 被无关任务占用
        new_token = uuid.uuid4().hex
        setattr(terminal, _GATE_ATTR, new_token)
        return new_token


def release_main_task_gate(terminal, token: Optional[str]) -> None:
    """释放门闸。只有持有者（token 匹配）才能释放，重复/过期调用为无操作。"""
    if terminal is None or not token:
        return
    with _LOCK:
        if getattr(terminal, _GATE_ATTR, None) == token:
            setattr(terminal, _GATE_ATTR, None)


def is_main_task_gate_busy(terminal) -> bool:
    """只读探测：当前是否有主任务持有门闸。"""
    if terminal is None:
        return False
    return bool(getattr(terminal, _GATE_ATTR, None))
