"""probe_transport 的传输层工具：帧写出、错误码、请求校验。

从 probe_transport.py 拆出，纯粹为了让单文件保持在 500 行以内；语义未变：
- stdout 一旦被 `_install_stdout_guard()` 接管，就只写协议帧；
- 序列化拒绝 NaN/Infinity，单行 UTF-8；
- 请求校验只做形状检查，不碰后端。
"""
from __future__ import annotations

import json
import os
import sys
import threading
from typing import Any, Dict, Optional

PROTOCOL_VERSION = "astrion-exec-probe/1"
SERVER_NAME = "astrion-local-exec-probe"

# JSON-RPC 错误码（只用到这些）
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
BACKEND_UNAVAILABLE = -32010
CONTRACT_MISSING = -32011

# 协议写端（启动时从 fd 1 dup 出来，之后 fd 1 指向 stderr）
_protocol_fd: Optional[int] = None
_write_lock = threading.Lock()


def _install_stdout_guard() -> None:
    """把真实 stdout 留给协议，其余 print 全部改道 stderr。"""
    global _protocol_fd
    _protocol_fd = os.dup(1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr


def _send(message: Dict[str, Any]) -> None:
    """序列化并写出一帧。任何不可序列化对象都在这里炸掉（而不是写坏帧）。"""
    line = json.dumps(
        message,
        ensure_ascii=False,
        allow_nan=False,  # NaN/Infinity 直接抛 ValueError
        separators=(",", ":"),
    )
    if "\n" in line or "\r" in line:
        raise ValueError("serialized frame contains a newline")
    data = (line + "\n").encode("utf-8")
    with _write_lock:
        assert _protocol_fd is not None
        os.write(_protocol_fd, data)


def _result(req_id: Any, result: Dict[str, Any]) -> None:
    _send({"jsonrpc": "2.0", "id": req_id, "result": result})


def _error(req_id: Any, code: int, message: str, data: Optional[Dict[str, Any]] = None) -> None:
    payload: Dict[str, Any] = {"code": code, "message": message}
    if data:
        payload["data"] = data
    _send({"jsonrpc": "2.0", "id": req_id, "error": payload})


class BackendUnavailable(RuntimeError):
    """local.py 尚未落地 / 构造失败。传输层仍可测，执行语义不可测。"""


def _require(params: Dict[str, Any], *names: str) -> None:
    """先校验参数形状，再碰后端：错误码不依赖后端是否可用。"""
    for name in names:
        if name not in params:
            raise KeyError(name)


def _validate_request(obj: Any) -> Optional[str]:
    if not isinstance(obj, dict):
        return "request must be a JSON object"
    if obj.get("jsonrpc") != "2.0":
        return 'jsonrpc must be "2.0"'
    if "id" not in obj:
        return "id is required"
    if not isinstance(obj["id"], (int, str)) or isinstance(obj["id"], bool):
        return "id must be int or str"
    if not isinstance(obj.get("method"), str):
        return "method must be a string"
    params = obj.get("params", {})
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return "params must be an object"
    return None


def _reject_constant(value: str) -> Any:
    raise ValueError("non-finite number %s is not allowed" % value)




__all__ = [
    "PROTOCOL_VERSION",
    "SERVER_NAME",
    "PARSE_ERROR",
    "INVALID_REQUEST",
    "METHOD_NOT_FOUND",
    "INVALID_PARAMS",
    "INTERNAL_ERROR",
    "BACKEND_UNAVAILABLE",
    "CONTRACT_MISSING",
    "BackendUnavailable",
    "_install_stdout_guard",
    "_send",
    "_result",
    "_error",
    "_require",
    "_validate_request",
    "_reject_constant",
]
