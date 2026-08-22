"""MCP 客户端管理：工具发现、调用与 OpenAI 工具映射。"""

from __future__ import annotations

import hashlib
import json
import os
import select
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, TYPE_CHECKING

import httpx

from config import MCP_PROTOCOL_VERSION, MCP_DEFAULT_TIMEOUT_SECONDS
from modules.mcp_server_registry import MCPServerRegistry

if TYPE_CHECKING:
    from modules.user_container_manager import ContainerHandle


class _StreamableHTTPMCPClient:
    """最小可用 streamable HTTP MCP 客户端。"""

    def __init__(self, server: Dict[str, Any], timeout_seconds: int, protocol_version: str):
        self.server = server
        self.url = str(server.get("url") or "").strip()
        self.timeout_seconds = max(3, int(timeout_seconds or MCP_DEFAULT_TIMEOUT_SECONDS))
        self.protocol_version = protocol_version
        self._session_id: Optional[str] = None
        self._seq = 0
        self._client = httpx.Client(timeout=self.timeout_seconds)
        self._initialized = False
        self._initialize_result: Dict[str, Any] = {}
        self._negotiated_protocol_version: Optional[str] = None

    def _next_id(self) -> int:
        self._seq += 1
        return self._seq

    @staticmethod
    def _header_pick(headers: httpx.Headers, key: str) -> Optional[str]:
        for hk, hv in headers.items():
            if hk.lower() == key.lower():
                return hv
        return None

    @staticmethod
    def _infer_mcp_name(method: str, params: Optional[Dict[str, Any]]) -> Optional[str]:
        if not isinstance(params, dict):
            params = {}
        method = str(method or "").strip()
        if method == "tools/call":
            value = params.get("name")
            return str(value).strip() if value is not None else None
        if method == "resources/read":
            value = params.get("uri")
            return str(value).strip() if value is not None else None
        if method == "prompts/get":
            value = params.get("name")
            return str(value).strip() if value is not None else None
        return None

    def _build_headers(self, *, method: Optional[str] = None, params: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": self._negotiated_protocol_version or self.protocol_version,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        if method:
            headers["Mcp-Method"] = str(method)
            name = self._infer_mcp_name(str(method), params)
            if name:
                headers["Mcp-Name"] = name
        headers.update(self.server.get("headers") or {})
        return headers

    @staticmethod
    def _parse_sse_json_events(raw_text: str) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        blocks = raw_text.split("\n\n")
        for block in blocks:
            data_lines: List[str] = []
            for line in block.splitlines():
                if line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())
            if not data_lines:
                continue
            payload = "\n".join(data_lines).strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                parsed = json.loads(payload)
            except Exception:
                continue
            if isinstance(parsed, dict):
                events.append(parsed)
        return events

    @staticmethod
    def _build_server_request_result(method: str, params: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
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

    def _post_jsonrpc_message(self, payload: Dict[str, Any]) -> None:
        try:
            resp = self._client.post(
                self.url,
                headers=self._build_headers(),
                json=payload,
            )
        except Exception as exc:
            raise MCPClientError(f"HTTP MCP 回写失败: {exc}") from exc
        if resp.status_code >= 400:
            raise MCPClientError(f"HTTP MCP 回写状态异常: {resp.status_code} {resp.text[:400]}")

    def _handle_server_request_message(self, message: Dict[str, Any]) -> None:
        msg_id = message.get("id")
        method = str(message.get("method") or "").strip()
        params = message.get("params")
        if not isinstance(params, dict):
            params = {}
        try:
            ok, payload = self._build_server_request_result(method, params)
            if ok:
                self._post_jsonrpc_message({"jsonrpc": "2.0", "id": msg_id, "result": payload})
            else:
                self._post_jsonrpc_message({"jsonrpc": "2.0", "id": msg_id, "error": payload})
        except Exception as exc:
            self._post_jsonrpc_message(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32603, "message": f"Client internal error: {exc}"},
                }
            )

    def _request(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        notification: bool = False,
        *,
        _allow_session_retry: bool = True,
    ) -> Dict[str, Any]:
        if not self.url:
            raise MCPClientError("streamable_http 服务缺少 url")
        req_id = None if notification else self._next_id()
        payload: Dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if req_id is not None:
            payload["id"] = req_id
        try:
            resp = self._client.post(
                self.url,
                headers=self._build_headers(method=method, params=params or {}),
                json=payload,
            )
        except Exception as exc:
            raise MCPClientError(f"HTTP MCP 请求失败: {exc}") from exc

        session_id = self._header_pick(resp.headers, "Mcp-Session-Id")
        if session_id:
            self._session_id = session_id

        # 会话失效（服务端返回 404）时，按规范重新建立 session 并重试一次
        if (
            resp.status_code == 404
            and self._session_id
            and method != "initialize"
            and _allow_session_retry
        ):
            self._session_id = None
            self._initialized = False
            self._initialize_result = {}
            self._negotiated_protocol_version = None
            self.initialize()
            return self._request(
                method,
                params,
                notification=notification,
                _allow_session_retry=False,
            )

        if resp.status_code >= 400:
            raise MCPClientError(f"HTTP MCP 状态异常: {resp.status_code} {resp.text[:400]}")

        if notification:
            return {}

        content_type = (resp.headers.get("content-type") or "").lower()
        message: Optional[Dict[str, Any]] = None
        if "application/json" in content_type:
            parsed = resp.json()
            if isinstance(parsed, dict):
                message = parsed
        elif "text/event-stream" in content_type:
            for event in self._parse_sse_json_events(resp.text):
                if not isinstance(event, dict):
                    continue
                if "method" in event:
                    if "id" in event:
                        self._handle_server_request_message(event)
                    # 通知直接消费
                    continue
                if event.get("id") == req_id:
                    message = event
                    break
        else:
            try:
                parsed = resp.json()
                if isinstance(parsed, dict):
                    message = parsed
            except Exception:
                pass

        if not message:
            raise MCPClientError(f"HTTP MCP 未返回可解析响应（method={method}）")
        if "error" in message:
            raise MCPClientError(f"{method} 调用失败: {message.get('error')}")
        return message.get("result") or {}

    def initialize(self) -> Dict[str, Any]:
        if self._initialized:
            return dict(self._initialize_result)
        result = self._request(
            "initialize",
            {
                "protocolVersion": self.protocol_version,
                "capabilities": {
                    "roots": {"listChanged": False},
                },
                "clientInfo": {"name": "agents-mcp-bridge", "version": "1.0.0"},
            },
        )
        self._request("notifications/initialized", {}, notification=True)
        self._initialize_result = dict(result or {})
        self._initialized = True
        negotiated = result.get("protocolVersion") if isinstance(result, dict) else None
        if negotiated:
            self._negotiated_protocol_version = str(negotiated)
        return dict(self._initialize_result)

    def request(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self._request(method, params, notification=False)

    def close(self) -> None:
        try:
            if self._session_id and self.url:
                self._client.delete(self.url, headers=self._build_headers())
        except Exception:
            pass
        try:
            self._client.close()
        except Exception:
            pass
        self._session_id = None
        self._initialized = False
        self._initialize_result = {}
        self._negotiated_protocol_version = None
