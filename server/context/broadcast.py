"""终端事件广播与回调包装（用户房间广播 + conversation_id 注入）。"""
from __future__ import annotations

from typing import Optional

from core.web_terminal import WebTerminal
from server.utils_common import debug_log


def make_terminal_callback(username: str):
    """生成面向指定用户的广播函数"""
    from server.extensions import socketio
    def _callback(event_type, data):
        try:
            socketio.emit(event_type, data, room=f"user_{username}")
        except Exception as exc:
            debug_log(f"广播事件失败 ({username}): {event_type} - {exc}")
    return _callback


def attach_user_broadcast(terminal: WebTerminal, username: str):
    """确保终端的广播函数指向当前用户的房间。

    对话级 terminal 的回调会额外包装注入 conversation_id（见 _wrap_callback_with_conversation_id）。
    """
    callback = make_terminal_callback(username)
    callback = _wrap_callback_with_conversation_id(
        callback, getattr(terminal, "_bound_conversation_id", None)
    )
    terminal.message_callback = callback
    if terminal.terminal_manager:
        terminal.terminal_manager.broadcast = callback


def _wrap_callback_with_conversation_id(callback, conversation_id: Optional[str]):
    """包装广播回调，为 dict 类型的事件数据注入 conversation_id（setdefault，不覆盖已有值）。

    对话级 terminal 的广播（shell 输出、terminal 列表等）仍发到用户房间，
    前端按 conversation_id 过滤，避免同工作区多个对话的终端事件互相串扰。
    """
    if not callback or not conversation_id:
        return callback

    def _wrapped(event_type, data):
        if isinstance(data, dict):
            data = dict(data)
            data.setdefault("conversation_id", conversation_id)
        return callback(event_type, data)

    return _wrapped
