#!/usr/bin/env python3
"""probe_transport.py —— 跨进程 JSON-RPC 测试夹具（子进程侧 / 被控端）。

定位
----
把 ``LocalExecutionBackend`` + ``ExecutionResources`` 放进一个**独立子进程**，
由控制侧（test_transport.py）通过 stdio 上的 NDJSON 驱动。

这不是公共协议、不是远程后端：
  * 只走 stdio（不监听任何端口，不联网，不启动模型）；
  * 没有 Relay、没有加密、没有认证、没有多租户；
  * 目的只有一个：验证「执行发生在被控进程自己的工作区」这一条可行性。

传输与序列化约束（严格）
------------------------
* 一行一个 JSON 对象，UTF-8，``\\n`` 结尾，禁止多行美化输出；
* 每条消息都必须带 ``"jsonrpc": "2.0"``；请求必须带 ``id``（int/str）；
* 响应必须回显同一 ``id``；服务端主动消息（如 ``ready``）不带 ``id``；
* 禁止 NaN/Infinity（解析与序列化两侧都拒绝）；
* 二进制一律显式 base64：``{"encoding": "base64", "data": "..."}``，
  不存在“猜是不是 base64”的隐式路径；
* 方法分派是 **dict 白名单**（``HANDLERS``），不是 ``eval``/``getattr``，
  未知方法一律 -32601。

stdout 保护
-----------
被代理的 ``FileManager`` 会在部分分支 ``print()`` 警告到 stdout。
协议流占用了 stdout，因此进程启动后立刻：
  1) ``os.dup(1)`` 保留真正的协议写端；
  2) ``os.dup2(2, 1)`` 把 fd 1 重定向到 stderr；
  3) ``sys.stdout = sys.stderr``。
这样任何库内 print 都只会污染 stderr，不会撕碎 NDJSON 帧。

用法
----
    python3 probe_transport.py --workspace <被控工作区> --data-dir <数据目录>

控制侧请勿直接调用本文件，统一走 test_transport.py。
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import platform
import sys
import threading
import traceback
from typing import Any, Callable, Dict, Optional

from probe_transport_io import (
    BACKEND_UNAVAILABLE,
    CONTRACT_MISSING,
    INVALID_PARAMS,
    INVALID_REQUEST,
    INTERNAL_ERROR,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    PROTOCOL_VERSION,
    SERVER_NAME,
    BackendUnavailable,
    _error,
    _install_stdout_guard,
    _reject_constant,
    _require,
    _result,
    _send,
    _validate_request,
)

class Probe:
    """持有后端实例与请求级任务表（用于取消）。"""

    def __init__(self, workspace: str, data_dir: str, backend_mode: str, execution_mode: str = "sandbox") -> None:
        self.workspace = os.path.abspath(workspace)
        self.data_dir = os.path.abspath(data_dir)
        self.backend_mode = backend_mode
        self.execution_mode = execution_mode
        self.nonce = base64.b16encode(os.urandom(8)).decode("ascii")
        self.pid = os.getpid()
        self.backend = None
        self.resources = None
        self.backend_error: Optional[str] = None
        self.backend_traceback: Optional[str] = None
        self._tasks: Dict[str, asyncio.Task] = {}
        self._build_backend()

    # ---- 后端构造：严格按已确认签名，不做 introspection 兜底 ----
    def _build_backend(self) -> None:
        if self.backend_mode == "absent":
            self.backend_error = "backend mode 'absent' (transport-only run)"
            return
        try:
            # 严格固定导入：工厂在 resources.py，后端在 local.py（不做别名兜底）
            from modules.execution_plane.local import LocalExecutionBackend
            from modules.execution_plane.resources import create_local_resources
        except Exception as exc:  # ImportError / SyntaxError / 依赖缺失
            self.backend_error = "import execution_plane local/resources failed: %r" % (exc,)
            self.backend_traceback = traceback.format_exc()
            return

        try:
            resources = create_local_resources(
                project_path=self.workspace,
                data_dir=self.data_dir,
                execution_mode=self.execution_mode,
                network_permission="restricted",
                readonly=False,
                container_session=None,
                command_validator=None,
            )
            backend = LocalExecutionBackend(resources)
        except Exception as exc:
            self.backend_error = "create_local_resources/LocalExecutionBackend failed: %r" % (exc,)
            self.backend_traceback = traceback.format_exc()
            return

        self.resources = resources
        self.backend = backend

    @property
    def available(self) -> bool:
        return self.backend is not None

    def require_backend(self) -> None:
        if not self.available:
            raise BackendUnavailable(self.backend_error or "backend unavailable")

    # ---- 方法实现 ----
    def env_info(self, params: Dict[str, Any]) -> Dict[str, Any]:
        info: Dict[str, Any] = {
            "protocol_version": PROTOCOL_VERSION,
            "server": {
                "name": SERVER_NAME,
                "pid": self.pid,
                "nonce": self.nonce,
                "python": platform.python_version(),
                "platform": platform.platform(),
                "machine": platform.machine(),
            },
            "cwd": os.getcwd(),
            "argv_workspace": self.workspace,
            "argv_data_dir": self.data_dir,
            "backend": {
                "available": self.available,
                "kind": "LocalExecutionBackend" if self.available else "unavailable",
                "error": self.backend_error,
                "traceback": self.backend_traceback,
            },
            "capabilities": [
                "env.info",
                "ping",
                "fs.read_text",
                "fs.write_text",
                "fs.read_binary",
                "fs.read_segment",
                "fs.search",
                "fs.extract_segments",
                "fs.inspect",
                "exec.run",
                "cancel",
                "shutdown",
            ],
        }
        if self.available:
            # 只读属性：workspace_root / cwd 由 ExecutionResources 提供
            info["workspace_root"] = str(self.resources.workspace_root)
            info["resources_cwd"] = str(self.resources.cwd)
            info["data_dir"] = str(self.resources.data_dir)
            info["execution_mode"] = str(self.resources.execution_mode)
            info["network_permission"] = str(self.resources.network_permission)
            info["readonly"] = bool(self.resources.readonly)
        return info

    def fs_write_text(self, params: Dict[str, Any]) -> Dict[str, Any]:
        _require(params, "path", "content")
        path = params["path"]
        content = params["content"]
        mode = params.get("mode", "w")
        if not isinstance(content, str):
            raise TypeError("content must be str; binary payloads must use fs.write_binary")
        self.require_backend()
        # 严格按 FileManager.write_file(path, content, mode="w")
        return self.backend.files.write_file(path, content, mode)

    def fs_read_text(self, params: Dict[str, Any]) -> Dict[str, Any]:
        _require(params, "path")
        self.require_backend()
        result = self.backend.files.read_file(params["path"])
        if isinstance(result, dict):
            result = dict(result)
            result.setdefault("encoding", "utf-8")
        return result

    def fs_read_binary(self, params: Dict[str, Any]) -> Dict[str, Any]:
        _require(params, "path")
        self.require_backend()
        raw = self.backend.files.read_binary(params["path"])
        # 契约：read_binary 返回 bytes；返回其他类型即契约偏差
        if not isinstance(raw, (bytes, bytearray)):
            raise TypeError(
                "read_binary returned %s, expected bytes" % type(raw).__name__
            )
        return {
            "encoding": "base64",
            "data": base64.b64encode(bytes(raw)).decode("ascii"),
            "size": len(raw),
        }

    def fs_read_segment(self, params: Dict[str, Any]) -> Dict[str, Any]:
        _require(params, "path")
        self.require_backend()
        # 严格按 FileManager.read_text_segment(path, *, start_line, end_line, size_limit)
        return self.backend.files.read_text_segment(
            params["path"],
            start_line=params.get("start_line"),
            end_line=params.get("end_line"),
            size_limit=params.get("size_limit"),
        )

    def fs_search(self, params: Dict[str, Any]) -> Dict[str, Any]:
        _require(params, "path", "query")
        self.require_backend()
        return self.backend.files.search_text(
            params["path"],
            query=params["query"],
            max_matches=params.get("max_matches", 20),
            context_before=params.get("context_before", 0),
            context_after=params.get("context_after", 0),
            case_sensitive=params.get("case_sensitive", False),
        )

    def fs_extract_segments(self, params: Dict[str, Any]) -> Dict[str, Any]:
        _require(params, "path", "segments")
        self.require_backend()
        return self.backend.files.extract_segments(
            params["path"],
            segments=params["segments"],
            size_limit=params.get("size_limit"),
        )

    def fs_inspect(self, params: Dict[str, Any]) -> Dict[str, Any]:
        _require(params, "path")
        self.require_backend()
        files = self.backend.files
        if not hasattr(files, "inspect_path"):
            raise AttributeError(
                "backend.files has no inspect_path (contract deviation)"
            )
        return files.inspect_path(params["path"])

    async def exec_run(self, params: Dict[str, Any]) -> Dict[str, Any]:
        _require(params, "command")
        command = params["command"]
        if not isinstance(command, str) or not command.strip():
            raise ValueError("command must be a non-empty str")
        self.require_backend()
        return await self.backend.run_command(
            command,
            timeout=float(params.get("timeout", 30.0)),
            sandbox_write_access=bool(params.get("sandbox_write_access", True)),
            network_permission=params.get("network_permission", "restricted"),
            working_dir=params.get("working_dir"),
        )

    async def cancel(self, params: Dict[str, Any]) -> Dict[str, Any]:
        key = str(params["request_id"])
        task = self._tasks.get(key)
        if task is None or task.done():
            return {"cancelled": False, "reason": "no_such_running_request"}
        task.cancel()
        return {"cancelled": True, "request_id": key}


# 方法白名单：字符串 -> 处理函数名。没有 getattr/__import__/eval。
HANDLERS: Dict[str, str] = {
    "env.info": "env_info",
    "fs.write_text": "fs_write_text",
    "fs.read_text": "fs_read_text",
    "fs.read_binary": "fs_read_binary",
    "fs.read_segment": "fs_read_segment",
    "fs.search": "fs_search",
    "fs.extract_segments": "fs_extract_segments",
    "fs.inspect": "fs_inspect",
    "exec.run": "exec_run",
    "cancel": "cancel",
}

ASYNC_METHODS = {"exec.run", "cancel"}


async def _run_request(probe: Probe, req: Dict[str, Any]) -> None:
    req_id = req["id"]
    method = req["method"]
    params = req.get("params") or {}

    handler_name = HANDLERS.get(method)
    if handler_name is None:
        _error(req_id, METHOD_NOT_FOUND, "method not found: %s" % method, {"method": method})
        return

    handler: Callable[..., Any] = getattr(probe, handler_name)

    try:
        if method in ASYNC_METHODS:
            result = await handler(params)
        else:
            result = handler(params)
    except BackendUnavailable as exc:
        _error(req_id, BACKEND_UNAVAILABLE, "backend unavailable", {"detail": str(exc)})
        return
    except AttributeError as exc:
        _error(req_id, CONTRACT_MISSING, "contract missing", {"detail": str(exc)})
        return
    except KeyError as exc:
        _error(req_id, INVALID_PARAMS, "missing param: %s" % exc, {"missing": str(exc)})
        return
    except (TypeError, ValueError) as exc:
        _error(req_id, INVALID_PARAMS, str(exc))
        return
    except asyncio.CancelledError:
        # 后端若把取消继续抛上来，这里合成一条 cancelled 结果，保证请求仍有响应。
        _result(
            req_id,
            {
                "success": False,
                "status": "cancelled",
                "output": "",
                "return_code": None,
                "truncated": False,
                "elapsed_ms": None,
            },
        )
        raise
    except Exception as exc:  # 兜底：仍然要回一条结构化错误，不能吞成静默
        _error(
            req_id,
            INTERNAL_ERROR,
            "%s: %s" % (type(exc).__name__, exc),
            {"traceback": traceback.format_exc()},
        )
        return

    if not isinstance(result, dict):
        _error(
            req_id,
            INTERNAL_ERROR,
            "handler returned %s, expected dict" % type(result).__name__,
        )
        return
    _result(req_id, result)


async def _handle_line(probe: Probe, raw: bytes) -> None:
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return
    try:
        obj = json.loads(text, parse_constant=_reject_constant)
    except Exception as exc:
        _error(None, PARSE_ERROR, "parse error: %s" % exc)
        return

    problem = _validate_request(obj)
    if problem:
        req_id = obj.get("id") if isinstance(obj, dict) else None
        _error(req_id, INVALID_REQUEST, problem)
        return

    method = obj["method"]
    if method == "ping":
        _result(obj["id"], {"pong": True, "nonce": probe.nonce})
        return
    if method == "shutdown":
        _result(obj["id"], {"bye": True})
        raise SystemExit(0)

    task = asyncio.ensure_future(_run_request(probe, obj))
    if method in ASYNC_METHODS:
        probe._tasks[str(obj["id"])] = task
        task.add_done_callback(lambda _t, k=str(obj["id"]): probe._tasks.pop(k, None))


async def _main_loop(probe: Probe) -> None:
    loop = asyncio.get_running_loop()
    queue: "asyncio.Queue[Optional[bytes]]" = asyncio.Queue()

    def reader() -> None:
        try:
            for line in sys.stdin.buffer:
                loop.call_soon_threadsafe(queue.put_nowait, line)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    thread = threading.Thread(target=reader, name="stdin-reader", daemon=True)
    thread.start()

    while True:
        raw = await queue.get()
        if raw is None:
            break
        await _handle_line(probe, raw)


def main() -> int:
    parser = argparse.ArgumentParser(description="LocalExecutionBackend stdio probe")
    parser.add_argument("--workspace", required=True, help="被控工作区（绝对或相对路径）")
    parser.add_argument("--data-dir", required=True, help="数据目录")
    parser.add_argument(
        "--backend",
        choices=("auto", "absent"),
        default="auto",
        help="auto=尝试构造 LocalExecutionBackend；absent=仅测传输层",
    )
    parser.add_argument(
        "--execution-mode",
        choices=("sandbox", "direct"),
        default="sandbox",
        help="传给 create_local_resources 的执行模式（direct 不做 seatbelt/bwrap）",
    )
    args = parser.parse_args()

    _install_stdout_guard()
    probe = Probe(args.workspace, args.data_dir, args.backend, args.execution_mode)
    _send(
        {
            "jsonrpc": "2.0",
            "method": "ready",
            "params": {
                "protocol_version": PROTOCOL_VERSION,
                "pid": probe.pid,
                "nonce": probe.nonce,
                "backend_available": probe.available,
                "backend_error": probe.backend_error,
                "execution_mode": probe.execution_mode,
            },
        }
    )

    try:
        asyncio.get_event_loop().run_until_complete(_main_loop(probe))
    except SystemExit:
        return 0
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
