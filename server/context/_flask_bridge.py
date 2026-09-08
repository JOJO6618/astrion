"""Flask 请求上下文的延迟桥接（任务核心层解耦用，2026-09-08）。

背景：server.context 子包中仅「兼容模式」（未传 RuntimeIdentity）的代码路径
需要读写 Flask session。本模块把 flask 依赖收敛为使用点延迟导入：
flask 包不可用时按「无请求上下文」处理（读返回默认值、写静默跳过），
使 server.tasks 等核心层在无 flask 环境中也能独立加载（G4 拆解）。

函数命名与 flask 原符号保持一致（has_request_context），调用点改动最小；
调用方通过 `from server.context._flask_bridge import ...` 引入。
"""
from __future__ import annotations

from typing import Any


def has_request_context() -> bool:
    """探测 Flask 请求上下文；flask 包不可用时返回 False。"""
    try:
        from flask import has_request_context as _flask_has_request_context
    except ImportError:
        return False
    return _flask_has_request_context()


def session_get(key: str, default: Any = None) -> Any:
    """读取 Flask session；无 flask 包或无请求上下文时返回 default。"""
    if not has_request_context():
        return default
    from flask import session

    return session.get(key, default)


def session_set(key: str, value: Any) -> None:
    """回写 Flask session；无 flask 包或无请求上下文时静默跳过。"""
    if not has_request_context():
        return
    from flask import session

    session[key] = value
