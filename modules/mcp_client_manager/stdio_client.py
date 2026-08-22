"""MCP 客户端管理：工具发现、调用与 OpenAI 工具映射。"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import shutil
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, TYPE_CHECKING

import httpx

from config import MCP_PROTOCOL_VERSION, MCP_DEFAULT_TIMEOUT_SECONDS
from modules.mcp_client_manager.exceptions import MCPClientError
from modules.mcp_server_registry import MCPServerRegistry

if TYPE_CHECKING:
    from modules.user_container_manager import ContainerHandle


class _StdioMCPClient:
    """最小可用 stdio MCP 客户端（JSON-RPC over newline-delimited JSON）。"""

    CONTAINER_PREFIXES = (
        "/workspace",
        "/usr/",
        "/bin/",
        "/sbin/",
        "/lib/",
        "/lib64/",
        "/opt/",
        "/etc/",
        "/tmp/",
        "/var/",
        "/home/",
        "/root/",
    )
    PATH_OPTION_KEYS = {
        "--path",
        "--dir",
        "--directory",
        "--root",
        "--cwd",
        "--workspace",
    }

    def __init__(
        self,
        server: Dict[str, Any],
        timeout_seconds: int,
        protocol_version: str,
        container_session: Optional["ContainerHandle"] = None,
    ):
        self.server = server
        self.timeout_seconds = max(3, int(timeout_seconds or MCP_DEFAULT_TIMEOUT_SECONDS))
        self.protocol_version = protocol_version
        self.container_session = container_session
        self.process: Optional[subprocess.Popen] = None
        self._seq = 0
        self._initialized = False
        self._initialize_result: Dict[str, Any] = {}
        self._negotiated_protocol_version: Optional[str] = None
        # stdout/stderr 后台排空线程状态。Windows 的 select 仅支持套接字、
        # 不支持匿名管道（WinError 10038），因此统一用读线程 + 队列做跨平台超时读取。
        self._stdout_queue: "queue.Queue[Optional[str]]" = queue.Queue()
        self._stdout_eof = False
        self._stderr_lines: "deque[str]" = deque(maxlen=80)

    def _next_id(self) -> int:
        self._seq += 1
        return self._seq

    @staticmethod
    def _is_windows_abs_path(value: str) -> bool:
        return len(value) > 2 and value[1] == ":" and value[0].isalpha()

    @staticmethod
    def _normalize_mount_path(value: str) -> str:
        mount = str(value or "/workspace").strip()
        mount = mount.rstrip("/")
        return mount or "/workspace"

    def _normalize_workspace_path(self, value: Any) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        try:
            return str(Path(raw).expanduser().resolve())
        except Exception:
            return raw

    def _map_workspace_path(self, raw: str, workspace_host: str, mount_path: str) -> str:
        if not raw or not workspace_host:
            return raw
        if raw.startswith(mount_path):
            return raw
        if self._is_windows_abs_path(raw):
            return raw
        if not raw.startswith("/"):
            return raw
        try:
            normalized = str(Path(raw).expanduser().resolve())
        except Exception:
            normalized = raw
        if normalized == workspace_host:
            return mount_path
        prefix = workspace_host + os.sep
        if normalized.startswith(prefix):
            rel = normalized[len(prefix):].replace("\\", "/")
            return f"{mount_path}/{rel}" if rel else mount_path
        return raw

    def _rewrite_path_like_value_for_container(
        self,
        value: str,
        workspace_host: str,
        mount_path: str,
    ) -> str:
        raw = str(value or "").strip()
        if not raw:
            return raw
        mapped = self._map_workspace_path(raw, workspace_host, mount_path)
        return mapped if mapped != raw else raw

    @staticmethod
    def _command_basename(value: str) -> str:
        text = str(value or "").strip().replace("\\", "/").rstrip("/")
        if not text:
            return ""
        if "/" in text:
            return text.split("/")[-1]
        return text

    def _rewrite_command_for_container(self, command: str, workspace_host: str, mount_path: str) -> str:
        raw = str(command or "").strip()
        if not raw:
            return raw
        mapped = self._map_workspace_path(raw, workspace_host, mount_path)
        if mapped != raw:
            return mapped
        # command 若是宿主机绝对路径（如 /opt/homebrew/bin/npx），
        # 在容器内通常应退化为命令名（npx/python3），避免无意义路径校验阻断。
        if raw.startswith("/") or self._is_windows_abs_path(raw) or "\\" in raw:
            candidate = self._command_basename(raw)
            return candidate or raw
        return raw

    def _rewrite_arg_for_container(
        self,
        arg: str,
        *,
        index: int,
        workspace_host: str,
        mount_path: str,
    ) -> str:
        raw = str(arg or "")
        stripped = raw.strip()
        if not stripped:
            return raw
        if "=" in stripped:
            left, right = stripped.split("=", 1)
            if left in self.PATH_OPTION_KEYS and right:
                rewritten = self._rewrite_path_like_value_for_container(
                    right,
                    workspace_host=workspace_host,
                    mount_path=mount_path,
                )
                return f"{left}={rewritten}"
        return self._rewrite_path_like_value_for_container(
            stripped,
            workspace_host=workspace_host,
            mount_path=mount_path,
        )

    def _prepare_launch(self) -> Tuple[List[str], Optional[str], Dict[str, str]]:
        command_raw = str(self.server.get("command") or "").strip()
        args_raw = [str(item) for item in (self.server.get("args") or [])]
        env_raw = dict(self.server.get("env") or {})
        cwd_raw = str(self.server.get("cwd") or "").strip() or None
        if not command_raw:
            raise MCPClientError("stdio 服务缺少 command")

        session = self.container_session
        use_container = bool(session and getattr(session, "mode", None) == "docker")
        if not use_container:
            env = dict(os.environ)
            env.update(env_raw)
            resolved_cwd = cwd_raw
            if not resolved_cwd and session is not None:
                workspace_hint = self._normalize_workspace_path(getattr(session, "workspace_path", ""))
                if workspace_hint:
                    resolved_cwd = workspace_hint
            return [command_raw, *args_raw], resolved_cwd, env

        container_name = str(getattr(session, "container_name", "") or "").strip()
        if not container_name:
            raise MCPClientError("docker 模式下 stdio MCP 缺少 container_name")
        docker_bin = str(getattr(session, "sandbox_bin", "") or "docker").strip() or "docker"
        docker_path = shutil.which(docker_bin) or docker_bin
        mount_path = self._normalize_mount_path(getattr(session, "mount_path", "/workspace"))
        workspace_host = self._normalize_workspace_path(getattr(session, "workspace_path", ""))

        command = self._rewrite_command_for_container(
            command_raw,
            workspace_host=workspace_host,
            mount_path=mount_path,
        )
        args: List[str] = []
        for idx, item in enumerate(args_raw):
            args.append(
                self._rewrite_arg_for_container(
                    item,
                    index=idx,
                    workspace_host=workspace_host,
                    mount_path=mount_path,
                )
            )
        cwd = (
            self._rewrite_path_like_value_for_container(
                cwd_raw,
                workspace_host=workspace_host,
                mount_path=mount_path,
            )
            if cwd_raw
            else None
        )

        cmd: List[str] = [docker_path, "exec", "-i"]
        if cwd:
            cmd += ["-w", cwd]

        exec_env = {
            "PYTHONIOENCODING": "utf-8",
            "TERM": "xterm-256color",
        }
        for key, value in env_raw.items():
            if value is not None:
                exec_env[str(key)] = str(value)
        for key, value in exec_env.items():
            cmd += ["-e", f"{key}={value}"]
        cmd += [container_name, command, *args]

        return cmd, None, dict(os.environ)

    def start(self) -> None:
        if self.process and self.process.poll() is None:
            return
        launch_cmd, cwd, env = self._prepare_launch()
        try:
            # MCP 协议（JSON-RPC over stdio）字节级要求 UTF-8，必须显式指定，
            # 否则 Windows 上 text=True 默认按 locale（cp936）编解码，中文 payload 会损坏
            self.process = subprocess.Popen(
                launch_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                cwd=cwd or None,
                env=env,
            )
        except Exception as exc:
            raise MCPClientError(f"启动 stdio MCP 服务失败: {exc}") from exc
        # 启动后台排空线程：stdout 按行入队（供 _read_message 带超时消费），
        # stderr 持续排空（防止管道缓冲写满阻塞服务进程），仅保留末尾行用于诊断。
        self._stdout_queue = queue.Queue()
        self._stdout_eof = False
        self._stderr_lines = deque(maxlen=80)
        server_id = str(self.server.get("id") or "?")
        threading.Thread(
            target=self._drain_stdout_stream,
            name=f"mcp-stdout-{server_id}",
            daemon=True,
        ).start()
        threading.Thread(
            target=self._drain_stderr_stream,
            name=f"mcp-stderr-{server_id}",
            daemon=True,
        ).start()
        self._initialized = False
        self._initialize_result = {}
        self._negotiated_protocol_version = None

    def _drain_stdout_stream(self) -> None:
        """后台线程：阻塞按行读取 stdout 推入队列，EOF/异常时推 None 哨兵。"""
        stream = self.process.stdout if self.process else None
        try:
            if stream:
                for line in stream:
                    self._stdout_queue.put(line)
        except Exception:
            pass
        finally:
            self._stdout_queue.put(None)

    def _drain_stderr_stream(self) -> None:
        """后台线程：持续排空 stderr，仅保留末尾若干行用于错误诊断。"""
        stream = self.process.stderr if self.process else None
        try:
            if stream:
                for line in stream:
                    self._stderr_lines.append(line)
        except Exception:
            pass

    def _process_exit_error(self, last_text_line: str) -> MCPClientError:
        returncode = self.process.returncode if self.process else None
        stderr_tail = "".join(self._stderr_lines)[-400:] if self._stderr_lines else ""
        detail = (stderr_tail or last_text_line or "")[:400]
        return MCPClientError(f"stdio 进程已退出（code={returncode}）{detail}")

    def _send(self, payload: Dict[str, Any]) -> None:
        if not self.process or not self.process.stdin:
            raise MCPClientError("stdio 进程未启动")
        raw = json.dumps(payload, ensure_ascii=False)
        try:
            self.process.stdin.write(raw + "\n")
            self.process.stdin.flush()
        except Exception as exc:
            raise MCPClientError(f"写入 stdio 消息失败: {exc}") from exc

    def _read_message(self, timeout_seconds: Optional[float] = None) -> Dict[str, Any]:
        if not self.process or not self.process.stdout:
            raise MCPClientError("stdio 进程未启动")
        timeout = self.timeout_seconds if timeout_seconds is None else max(0.1, float(timeout_seconds))
        deadline = time.monotonic() + timeout
        last_text_line = ""
        # 跨平台读取：不依赖 select（Windows 仅支持套接字），
        # 消费后台读线程产出的队列，用 queue.get(timeout) 做超时控制。
        while True:
            remain = deadline - time.monotonic()
            if remain <= 0:
                raise MCPClientError("读取 stdio MCP 响应超时")
            try:
                item = self._stdout_queue.get(timeout=min(0.3, remain))
            except queue.Empty:
                if self._stdout_eof and (not self.process or self.process.poll() is not None):
                    raise self._process_exit_error(last_text_line)
                continue
            if item is None:
                # 读线程到达 EOF：服务进程退出或流被关闭
                self._stdout_eof = True
                if not self.process or self.process.poll() is not None:
                    raise self._process_exit_error(last_text_line)
                continue
            line = item.strip()
            if not line:
                continue
            try:
                return json.loads(line)
            except Exception:
                last_text_line = line
                continue

    @staticmethod
    def _build_server_request_result(method: str, params: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        """处理服务端发起的请求（Server -> Client Request）。"""
        name = str(method or "").strip()
        if name == "ping":
            return True, {}
        if name == "roots/list":
            return True, {"roots": []}
        if name == "completion/complete":
            return False, {"code": -32601, "message": "completion/complete not supported by client"}
        if name == "sampling/createMessage":
            return False, {"code": -32601, "message": "sampling not supported by client"}
        if name == "elicitation/create":
            return False, {"code": -32601, "message": "elicitation not supported by client"}
        return False, {"code": -32601, "message": f"Client method not implemented: {name}"}

    @staticmethod
    def _handle_server_notification(method: str, params: Dict[str, Any]) -> None:
        """处理服务端通知（Server -> Client Notification）。当前策略：静默接收。"""
        _ = method, params

    def _respond_server_request(self, msg: Dict[str, Any]) -> None:
        msg_id = msg.get("id")
        method = str(msg.get("method") or "").strip()
        params = msg.get("params")
        if not isinstance(params, dict):
            params = {}
        try:
            ok, payload = self._build_server_request_result(method, params)
            if ok:
                self._send({"jsonrpc": "2.0", "id": msg_id, "result": payload})
            else:
                self._send({"jsonrpc": "2.0", "id": msg_id, "error": payload})
        except Exception as exc:
            self._send(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32603, "message": f"Client internal error: {exc}"},
                }
            )

    def request(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not self.process or self.process.poll() is not None:
            self.start()
        req_id = self._next_id()
        payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}
        self._send(payload)
        while True:
            msg = self._read_message()

            # 收到目标响应
            if msg.get("id") == req_id and "method" not in msg:
                if "error" in msg:
                    error = msg.get("error") or {}
                    raise MCPClientError(f"{method} 调用失败: {error}")
                return msg.get("result") or {}

            # 服务端请求：回包后继续等待目标响应
            if "id" in msg and "method" in msg:
                self._respond_server_request(msg)
                continue

            # 服务端通知：消费后继续
            if "method" in msg:
                self._handle_server_notification(str(msg.get("method") or ""), msg.get("params") or {})
                continue

    def notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        if not self.process or self.process.poll() is not None:
            self.start()
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def initialize(self) -> Dict[str, Any]:
        if self._initialized:
            return dict(self._initialize_result)
        if not self.process or self.process.poll() is not None:
            self.start()
        result = self.request(
            "initialize",
            {
                "protocolVersion": self.protocol_version,
                "capabilities": {
                    "roots": {"listChanged": False},
                },
                "clientInfo": {"name": "agents-mcp-bridge", "version": "1.0.0"},
            },
        )
        self.notify("notifications/initialized")
        self._initialize_result = dict(result or {})
        self._initialized = True
        negotiated = result.get("protocolVersion") if isinstance(result, dict) else None
        if negotiated:
            self._negotiated_protocol_version = str(negotiated)
        return dict(self._initialize_result)

    def close(self) -> None:
        if not self.process:
            return
        try:
            if self.process.stdin:
                self.process.stdin.close()
        except Exception:
            pass
        try:
            if self.process.poll() is None:
                self.process.terminate()
                self.process.wait(timeout=1.5)
        except Exception:
            try:
                self.process.kill()
            except Exception:
                pass
        finally:
            try:
                if self.process.stdout:
                    self.process.stdout.close()
            except Exception:
                pass
            try:
                if self.process.stderr:
                    self.process.stderr.close()
            except Exception:
                pass
            self.process = None
            self._initialized = False
            self._initialize_result = {}
            self._negotiated_protocol_version = None
