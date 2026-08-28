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
from modules.mcp_client_manager.exceptions import MCPClientError
from modules.mcp_client_manager.http_client import _StreamableHTTPMCPClient
from modules.mcp_client_manager.models import MCPClientPoolEntry, MCPToolBinding
from modules.mcp_client_manager.stdio_client import _StdioMCPClient
from modules.mcp_server_registry import MCPServerRegistry

from modules.i18n import tr

if TYPE_CHECKING:
    from modules.user_container_manager import ContainerHandle


class MCPClientManager:
    """MCP 客户端主协调器。"""

    def __init__(
        self,
        registry: MCPServerRegistry,
        protocol_version: str = MCP_PROTOCOL_VERSION,
        container_session: Optional["ContainerHandle"] = None,
    ):
        self.registry = registry
        self.protocol_version = str(protocol_version or MCP_PROTOCOL_VERSION)
        self.container_session = container_session
        self._container_session_signature = self._compute_session_signature(container_session)
        self._latest_alias_map: Dict[str, MCPToolBinding] = {}
        self._client_pool: Dict[str, MCPClientPoolEntry] = {}
        self._pool_lock = threading.RLock()

    def __del__(self):
        try:
            self.close_all_clients()
        except Exception:
            pass

    @staticmethod
    def _compute_session_signature(session: Optional["ContainerHandle"]) -> Optional[Tuple[str, str, str, str, str]]:
        if not session:
            return None
        return (
            str(getattr(session, "mode", "") or ""),
            str(getattr(session, "workspace_path", "") or ""),
            str(getattr(session, "mount_path", "") or ""),
            str(getattr(session, "container_name", "") or ""),
            str(getattr(session, "sandbox_bin", "") or ""),
        )

    def set_container_session(self, session: Optional["ContainerHandle"]) -> None:
        next_signature = self._compute_session_signature(session)
        if next_signature == self._container_session_signature:
            # 引用对象可能相同（甚至被原地修改），这里仍刷新引用，便于后续读取最新字段。
            self.container_session = session
            return
        self.close_all_clients()
        self.container_session = session
        self._container_session_signature = next_signature

    @staticmethod
    def _server_signature(server: Dict[str, Any]) -> str:
        payload = {
            "id": server.get("id"),
            "transport": server.get("transport"),
            "command": server.get("command"),
            "args": server.get("args") or [],
            "cwd": server.get("cwd"),
            "env": server.get("env") or {},
            "url": server.get("url"),
            "headers": server.get("headers") or {},
            "timeout_seconds": server.get("timeout_seconds"),
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _is_client_alive(client: Any) -> bool:
        process = getattr(client, "process", None)
        if process is not None:
            return bool(process.poll() is None)
        # HTTP 客户端无法直接探活，按可用处理，失败时走重连兜底
        return True

    @staticmethod
    def _safe_close_client(client: Any) -> None:
        try:
            if client:
                client.close()
        except Exception:
            pass

    def _close_pool_entry_locked(self, server_id: str) -> None:
        entry = self._client_pool.pop(server_id, None)
        if entry:
            self._safe_close_client(entry.client)

    def _ensure_client_locked(self, server: Dict[str, Any], timeout_seconds: int, *, force_recreate: bool = False):
        sid = str(server.get("id") or "").strip()
        if not sid:
            raise MCPClientError(tr("mcp_mgr.server_missing_id"))
        signature = self._server_signature(server)
        entry = self._client_pool.get(sid)
        needs_recreate = force_recreate or (entry is None)
        if entry and not needs_recreate:
            if entry.signature != signature:
                needs_recreate = True
            elif not self._is_client_alive(entry.client):
                needs_recreate = True

        if needs_recreate:
            if entry:
                self._safe_close_client(entry.client)
            client = self._make_client(server, timeout_seconds)
            if hasattr(client, "start"):
                client.start()
            init_result = client.initialize()
            now = time.time()
            entry = MCPClientPoolEntry(
                server_id=sid,
                signature=signature,
                client=client,
                timeout_seconds=timeout_seconds,
                created_at=now,
                last_used_at=now,
            )
            self._client_pool[sid] = entry
            return client, init_result

        # 复用连接：动态刷新超时，并确保处于 initialized 状态
        entry.timeout_seconds = timeout_seconds
        try:
            if hasattr(entry.client, "timeout_seconds"):
                entry.client.timeout_seconds = max(3, int(timeout_seconds))
            if hasattr(entry.client, "_client"):
                entry.client._client.timeout = httpx.Timeout(max(3, int(timeout_seconds)))
        except Exception:
            pass
        init_result = {}
        if hasattr(entry.client, "initialize"):
            init_result = entry.client.initialize() or {}
        entry.last_used_at = time.time()
        return entry.client, init_result

    def _run_with_client(
        self,
        server: Dict[str, Any],
        timeout_seconds: int,
        operation: Callable[[Any], Any],
    ) -> Tuple[Any, Dict[str, Any]]:
        last_exc: Optional[Exception] = None
        for attempt in range(2):
            with self._pool_lock:
                client, init_result = self._ensure_client_locked(
                    server,
                    timeout_seconds,
                    force_recreate=attempt > 0,
                )
                sid = str(server.get("id") or "").strip()
                try:
                    output = operation(client)
                    entry = self._client_pool.get(sid)
                    if entry:
                        entry.last_used_at = time.time()
                    return output, init_result
                except Exception as exc:  # pragma: no cover - 仅异常链路
                    last_exc = exc
                    self._close_pool_entry_locked(sid)
                    continue
        if last_exc:
            raise last_exc
        raise MCPClientError(tr("mcp_mgr.client_exec_failed"))

    def close_all_clients(self) -> None:
        with self._pool_lock:
            entries = list(self._client_pool.values())
            self._client_pool.clear()
        for entry in entries:
            self._safe_close_client(entry.client)

    @staticmethod
    def _sanitize_input_schema(schema: Any) -> Dict[str, Any]:
        if not isinstance(schema, dict):
            return {"type": "object", "properties": {}}
        output = dict(schema)
        if output.get("type") != "object":
            output["type"] = "object"
        if not isinstance(output.get("properties"), dict):
            output["properties"] = {}
        if "required" in output and not isinstance(output.get("required"), list):
            output.pop("required", None)
        return output

    @staticmethod
    def _compact_tool_entry(tool: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "name": str(tool.get("name") or "").strip(),
            "description": str(tool.get("description") or ""),
            "title": str(tool.get("title") or ""),
            "inputSchema": tool.get("inputSchema") if isinstance(tool.get("inputSchema"), dict) else {"type": "object", "properties": {}},
        }

    @staticmethod
    def _build_call_message(result: Dict[str, Any]) -> str:
        content = result.get("content")
        if isinstance(content, list):
            lines: List[str] = []
            for idx, item in enumerate(content, start=1):
                if isinstance(item, dict) and item.get("type") == "text":
                    text = str(item.get("text") or "").strip()
                    if text:
                        lines.append(text)
                        continue
                if not isinstance(item, dict):
                    continue
                ctype = str(item.get("type") or "").strip().lower()
                if ctype in {"image", "audio"}:
                    mime = str(item.get("mimeType") or item.get("mime_type") or "").strip()
                    if not mime:
                        mime = "image/png" if ctype == "image" else "audio/wav"
                    data_len = len(str(item.get("data") or ""))
                    lines.append(f"[MCP {ctype} #{idx}] {mime} ({data_len} base64 chars)")
                    continue
                if ctype in {"resource_link", "resourcelink"}:
                    uri = str(item.get("uri") or item.get("url") or "").strip()
                    name = str(item.get("name") or item.get("title") or "").strip()
                    if uri and name:
                        lines.append(f"[MCP 资源] {name} -> {uri}")
                    elif uri:
                        lines.append(f"[MCP 资源] {uri}")
                    continue
                if ctype == "resource":
                    resource = item.get("resource")
                    if isinstance(resource, dict):
                        text = str(resource.get("text") or "").strip()
                        if text:
                            lines.append(text)
                            continue
                        uri = str(resource.get("uri") or "").strip()
                        if uri:
                            lines.append(f"[MCP 资源] {uri}")
                            continue
                    uri = str(item.get("uri") or item.get("url") or "").strip()
                    if uri:
                        lines.append(f"[MCP 资源] {uri}")
            if lines:
                return "\n".join(lines)[:500]
        structured = result.get("structuredContent")
        if structured is not None:
            try:
                return json.dumps(structured, ensure_ascii=False)[:500]
            except Exception:
                return str(structured)[:500]
        return ""

    @staticmethod
    def _content_preview(content_items: Any) -> str:
        if not isinstance(content_items, list):
            return ""
        previews: List[str] = []
        for item in content_items:
            if not isinstance(item, dict):
                continue
            ctype = item.get("type")
            if ctype == "text":
                text = str(item.get("text") or "").strip()
                if text:
                    previews.append(text[:300])
            elif ctype:
                previews.append(f"[{ctype}]")
            if len(previews) >= 4:
                break
        return "\n".join(previews).strip()

    def _make_client(self, server: Dict[str, Any], timeout_seconds: int):
        transport = server.get("transport")
        if transport == "stdio":
            return _StdioMCPClient(
                server,
                timeout_seconds,
                self.protocol_version,
                container_session=self.container_session,
            )
        if transport == "streamable_http":
            return _StreamableHTTPMCPClient(server, timeout_seconds, self.protocol_version)
        raise MCPClientError(tr("mcp_mgr.unsupported_transport", transport=transport))

    def _paged_list_tools(self, client, max_pages: int = 20) -> List[Dict[str, Any]]:
        cursor: Optional[str] = None
        tools: List[Dict[str, Any]] = []
        for _ in range(max_pages):
            params = {"cursor": cursor} if cursor else {}
            result = client.request("tools/list", params)
            page_tools = result.get("tools") if isinstance(result, dict) else []
            if isinstance(page_tools, list):
                for item in page_tools:
                    if isinstance(item, dict):
                        compact = self._compact_tool_entry(item)
                        if compact.get("name"):
                            tools.append(compact)
            cursor = (result or {}).get("nextCursor") if isinstance(result, dict) else None
            if not cursor:
                break
        return tools

    def discover_tools_for_server(self, server_id: str, *, timeout_seconds: Optional[int] = None) -> Dict[str, Any]:
        server = self.registry.get_server(server_id)
        if not server:
            return {"success": False, "error": tr("mcp.server_not_found", server_id=server_id)}
        timeout = int(timeout_seconds or server.get("timeout_seconds") or MCP_DEFAULT_TIMEOUT_SECONDS)
        started = time.time()
        try:
            tools, init_result = self._run_with_client(
                server,
                timeout,
                lambda client: self._paged_list_tools(client),
            )
            self.registry.update_tools_cache(server_id, tools=tools, error="", touched=True)
            elapsed_ms = int((time.time() - started) * 1000)
            return {
                "success": True,
                "server_id": server_id,
                "protocol_version": init_result.get("protocolVersion") if isinstance(init_result, dict) else None,
                "tools_count": len(tools),
                "tools": tools,
                "elapsed_ms": elapsed_ms,
            }
        except Exception as exc:
            self.registry.update_tools_cache(server_id, error=str(exc), touched=True)
            return {"success": False, "server_id": server_id, "error": str(exc)}

    def sync_servers(self, server_id: Optional[str] = None) -> Dict[str, Any]:
        # 先从磁盘重载一次，避免「文件已改但进程内缓存未刷新」导致同步遗漏，
        # 以及后续 update_tools_cache 把旧缓存反写覆盖新配置。
        self.registry.reload()
        with self._pool_lock:
            enabled_ids = {
                str(item.get("id") or "").strip()
                for item in self.registry.list_enabled_servers()
                if str(item.get("id") or "").strip()
            }
            stale_ids = [sid for sid in list(self._client_pool.keys()) if sid not in enabled_ids]
            for sid in stale_ids:
                self._close_pool_entry_locked(sid)
        if server_id:
            return self.discover_tools_for_server(server_id)
        results = []
        for server in self.registry.list_enabled_servers():
            sid = server.get("id")
            if not sid:
                continue
            results.append(self.discover_tools_for_server(sid))
        ok_count = sum(1 for item in results if item.get("success"))
        overall_success = (not results) or (ok_count == len(results))
        return {
            "success": overall_success,
            "servers_total": len(results),
            "servers_success": ok_count,
            "servers_failed": len(results) - ok_count,
            "results": results,
        }

    def _iter_visible_tools(self, server: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
        include_tools = set(server.get("include_tools") or [])
        exclude_tools = set(server.get("exclude_tools") or [])
        for item in server.get("tools_cache") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            if include_tools and name not in include_tools:
                continue
            if name in exclude_tools:
                continue
            yield item

    def build_llm_tools(
        self,
        *,
        ensure_discovery: bool = True,
        discovery_timeout_seconds: int = 12,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, MCPToolBinding]]:
        tools: List[Dict[str, Any]] = []
        alias_map: Dict[str, MCPToolBinding] = {}

        # 防止“外部直接改配置文件但进程内缓存没刷新”：
        # 这里在常规构建阶段主动重载一次，避免后续 discover/update 把旧缓存反写回磁盘。
        if ensure_discovery:
            self.registry.reload()

        servers = self.registry.list_enabled_servers()
        for server in servers:
            sid = str(server.get("id") or "").strip()
            if not sid:
                continue
            if ensure_discovery and not (server.get("tools_cache") or []):
                self.discover_tools_for_server(sid, timeout_seconds=discovery_timeout_seconds)
                server = self.registry.get_server(sid) or server

            for item in self._iter_visible_tools(server):
                remote_name = str(item.get("name") or "").strip()
                alias_base = self.registry.make_tool_alias(sid, remote_name)
                alias = alias_base
                if alias in alias_map:
                    suffix = hashlib.sha1(f"{sid}:{remote_name}".encode("utf-8")).hexdigest()[:6]
                    max_len = max(8, 64 - len(suffix) - 2)
                    alias = f"{alias_base[:max_len]}__{suffix}"

                description = str(item.get("description") or "").strip()
                if description:
                    description = f"[MCP:{sid}] {description}"
                else:
                    description = f"[MCP:{sid}] 通过 MCP 服务调用远程工具 {remote_name}"

                schema = self._sanitize_input_schema(item.get("inputSchema"))
                tools.append(
                    {
                        "type": "function",
                        "function": {
                            "name": alias,
                            "description": description,
                            "parameters": schema,
                        },
                    }
                )
                alias_map[alias] = MCPToolBinding(
                    alias=alias,
                    server_id=sid,
                    server_name=str(server.get("name") or sid),
                    remote_name=remote_name,
                    transport=str(server.get("transport") or ""),
                    timeout_seconds=int(server.get("timeout_seconds") or MCP_DEFAULT_TIMEOUT_SECONDS),
                )

        self._latest_alias_map = alias_map
        return tools, alias_map

    def _resolve_binding(
        self,
        tool_alias: str,
        alias_map: Optional[Dict[str, MCPToolBinding]] = None,
    ) -> Optional[MCPToolBinding]:
        if alias_map and tool_alias in alias_map:
            return alias_map[tool_alias]
        if tool_alias in self._latest_alias_map:
            return self._latest_alias_map[tool_alias]

        # 容错：从缓存动态重建一遍
        _, rebuilt = self.build_llm_tools(ensure_discovery=False)
        return rebuilt.get(tool_alias)

    def call_tool_by_alias(
        self,
        tool_alias: str,
        arguments: Dict[str, Any],
        *,
        alias_map: Optional[Dict[str, MCPToolBinding]] = None,
    ) -> Dict[str, Any]:
        binding = self._resolve_binding(tool_alias, alias_map)
        if not binding:
            return {"success": False, "error": tr("mcp.tool_mapping_not_found", tool_alias=tool_alias)}
        server = self.registry.get_server(binding.server_id)
        if not server:
            return {"success": False, "error": tr("mcp.server_not_found", server_id=binding.server_id)}
        if not server.get("enabled", True):
            return {"success": False, "error": tr("mcp.server_disabled", server_id=binding.server_id)}

        timeout = int(server.get("timeout_seconds") or binding.timeout_seconds or MCP_DEFAULT_TIMEOUT_SECONDS)
        started = time.time()
        try:
            call_result, _ = self._run_with_client(
                server,
                timeout,
                lambda client: client.request(
                    "tools/call",
                    {"name": binding.remote_name, "arguments": arguments or {}},
                ),
            )
            is_error = bool((call_result or {}).get("isError"))
            content_items = (call_result or {}).get("content") or []
            preview = self._content_preview(content_items)
            elapsed_ms = int((time.time() - started) * 1000)
            message = self._build_call_message(call_result)

            return {
                "success": not is_error,
                "server_id": binding.server_id,
                "server_name": binding.server_name,
                "tool_alias": binding.alias,
                "tool_name": binding.remote_name,
                "transport": binding.transport,
                "elapsed_ms": elapsed_ms,
                "is_error": is_error,
                "message": message,
                "preview": preview,
                "content": content_items,
                "structured_content": (call_result or {}).get("structuredContent"),
                "raw_result": call_result,
            }
        except Exception as exc:
            return {
                "success": False,
                "server_id": binding.server_id,
                "server_name": binding.server_name,
                "tool_alias": binding.alias,
                "tool_name": binding.remote_name,
                "transport": binding.transport,
                "error": str(exc),
            }
