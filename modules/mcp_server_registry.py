"""MCP 服务器配置与缓存管理。"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config import (
    MCP_SERVERS_FILE,
    MCP_TOOLS_ENABLED,
    MCP_DEFAULT_TIMEOUT_SECONDS,
)


class MCPServerRegistry:
    """文件式 MCP 服务器注册表。"""

    ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
    TRANSPORTS = {"stdio", "streamable_http"}

    def __init__(self, config_path: str = MCP_SERVERS_FILE, enabled: bool = MCP_TOOLS_ENABLED):
        self.config_path = Path(config_path).expanduser().resolve()
        self.enabled = bool(enabled)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache: List[Dict[str, Any]] = []
        if self.enabled:
            self.reload()

    # ------------------------------------------------------------------
    # 基础方法
    # ------------------------------------------------------------------
    @classmethod
    def _is_valid_id(cls, server_id: str) -> bool:
        return bool(server_id and cls.ID_PATTERN.match(server_id))

    @staticmethod
    def _normalize_transport(raw: Any) -> str:
        value = str(raw or "").strip().lower().replace("-", "_")
        if value in {"http", "https", "streamablehttp"}:
            return "streamable_http"
        return value

    @staticmethod
    def _normalize_str_list(value: Any) -> List[str]:
        if not isinstance(value, list):
            return []
        output: List[str] = []
        for item in value:
            text = str(item).strip() if item is not None else ""
            if text:
                output.append(text)
        return output

    @staticmethod
    def _normalize_str_dict(value: Any) -> Dict[str, str]:
        if not isinstance(value, dict):
            return {}
        output: Dict[str, str] = {}
        for k, v in value.items():
            key = str(k).strip() if k is not None else ""
            if not key:
                continue
            output[key] = str(v) if v is not None else ""
        return output

    @staticmethod
    def _normalize_bool(value: Any, default: bool = True) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
        return default

    @staticmethod
    def _normalize_timeout(value: Any) -> int:
        try:
            parsed = int(float(value))
        except Exception:
            parsed = MCP_DEFAULT_TIMEOUT_SECONDS
        if parsed <= 0:
            parsed = MCP_DEFAULT_TIMEOUT_SECONDS
        return min(parsed, 300)

    @staticmethod
    def _safe_slug(value: Any, fallback: str = "x") -> str:
        raw = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or "")).strip("_")
        if not raw:
            raw = fallback
        if not re.match(r"^[A-Za-z]", raw):
            raw = f"x_{raw}"
        return raw

    @classmethod
    def make_tool_alias(cls, server_id: str, remote_tool_name: str, *, max_len: int = 64) -> str:
        sid = cls._safe_slug(server_id, "server")
        tid = cls._safe_slug(remote_tool_name, "tool")
        prefix = f"mcp__{sid}__"
        remain = max(8, max_len - len(prefix))
        return f"{prefix}{tid[:remain]}"

    @classmethod
    def make_server_category_id(cls, server_id: str, *, max_len: int = 64) -> str:
        sid = cls._safe_slug(server_id, "server")
        prefix = "mcp_server__"
        remain = max(8, max_len - len(prefix))
        return f"{prefix}{sid[:remain]}"

    @classmethod
    def make_server_category_label(cls, server: Any) -> str:
        if isinstance(server, dict):
            name = str(server.get("name") or server.get("id") or "").strip()
            sid = str(server.get("id") or "").strip()
        else:
            name = str(server or "").strip()
            sid = ""
        target = name or sid or "未命名服务"
        return f"MCP · {target}"

    @staticmethod
    def build_default_mcp_category() -> Dict[str, Any]:
        return {
            "id": "mcp",
            "label": "MCP 工具",
            "tools": ["list_mcp_servers"],
            "default_enabled": True,
            "silent_when_disabled": False,
        }

    def _ensure_file(self) -> None:
        if self.config_path.exists():
            return
        payload = {"servers": []}
        self.config_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _read_file(self) -> Dict[str, Any]:
        self._ensure_file()
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        return {"servers": []}

    def _write_file(self, payload: Dict[str, Any]) -> None:
        self.config_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _normalize_server(self, item: Dict[str, Any]) -> Dict[str, Any]:
        data = dict(item or {})
        server_id = str(data.get("id") or "").strip()
        transport = self._normalize_transport(data.get("transport"))
        timeout_seconds = self._normalize_timeout(data.get("timeout_seconds"))
        include_tools = self._normalize_str_list(data.get("include_tools"))
        exclude_tools = self._normalize_str_list(data.get("exclude_tools"))

        normalized = {
            "id": server_id,
            "name": str(data.get("name") or server_id),
            "description": str(data.get("description") or ""),
            "enabled": self._normalize_bool(data.get("enabled"), True),
            "transport": transport,
            "command": str(data.get("command") or "").strip(),
            "args": self._normalize_str_list(data.get("args")),
            "cwd": str(data.get("cwd") or "").strip(),
            "env": self._normalize_str_dict(data.get("env")),
            "url": str(data.get("url") or "").strip(),
            "headers": self._normalize_str_dict(data.get("headers")),
            "timeout_seconds": timeout_seconds,
            "include_tools": include_tools,
            "exclude_tools": exclude_tools,
            "tools_cache": data.get("tools_cache") if isinstance(data.get("tools_cache"), list) else [],
            "tools_cache_updated_at": str(data.get("tools_cache_updated_at") or ""),
            "last_error": str(data.get("last_error") or ""),
        }

        return normalized

    def _validate_server(self, item: Dict[str, Any]) -> Tuple[bool, str]:
        server_id = item.get("id")
        if not self._is_valid_id(server_id):
            return False, "服务 ID 不合法：需以字母开头，可包含字母、数字、下划线、短横线"
        transport = item.get("transport")
        if transport not in self.TRANSPORTS:
            return False, f"不支持的 transport: {transport}（支持: {sorted(self.TRANSPORTS)}）"
        if transport == "stdio" and not item.get("command"):
            return False, "stdio 传输必须提供 command"
        if transport == "streamable_http" and not item.get("url"):
            return False, "streamable_http 传输必须提供 url"
        return True, ""

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    def reload(self) -> List[Dict[str, Any]]:
        data = self._read_file()
        servers = data.get("servers")
        output: List[Dict[str, Any]] = []
        if isinstance(servers, list):
            for item in servers:
                if not isinstance(item, dict):
                    continue
                normalized = self._normalize_server(item)
                if not normalized.get("id"):
                    continue
                output.append(normalized)
        self._cache = output
        return list(self._cache)

    def list_servers(self, include_disabled: bool = True) -> List[Dict[str, Any]]:
        if include_disabled:
            return [dict(item) for item in self._cache]
        return [dict(item) for item in self._cache if item.get("enabled")]

    def list_enabled_servers(self) -> List[Dict[str, Any]]:
        return self.list_servers(include_disabled=False)

    def get_server(self, server_id: str) -> Optional[Dict[str, Any]]:
        target = str(server_id or "").strip()
        for item in self._cache:
            if item.get("id") == target:
                return dict(item)
        return None

    def upsert_server(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        normalized = self._normalize_server(payload or {})
        ok, error = self._validate_server(normalized)
        if not ok:
            raise ValueError(error)

        current = {item["id"]: item for item in self._cache if item.get("id")}
        existing = current.get(normalized["id"]) or {}
        if existing:
            # 保留历史缓存字段，除非显式覆盖
            if not normalized.get("tools_cache"):
                normalized["tools_cache"] = existing.get("tools_cache") or []
            if not normalized.get("tools_cache_updated_at"):
                normalized["tools_cache_updated_at"] = existing.get("tools_cache_updated_at") or ""
            if not normalized.get("last_error"):
                normalized["last_error"] = existing.get("last_error") or ""
        current[normalized["id"]] = normalized
        updated = sorted(current.values(), key=lambda x: str(x.get("id")))
        self._cache = updated
        self._write_file({"servers": updated})
        return dict(normalized)

    def delete_server(self, server_id: str) -> bool:
        target = str(server_id or "").strip()
        if not target:
            return False
        before = len(self._cache)
        after = [item for item in self._cache if item.get("id") != target]
        if len(after) == before:
            return False
        self._cache = after
        self._write_file({"servers": after})
        return True

    def update_tools_cache(
        self,
        server_id: str,
        *,
        tools: Optional[List[Dict[str, Any]]] = None,
        error: Optional[str] = None,
        touched: bool = True,
    ) -> Optional[Dict[str, Any]]:
        target = str(server_id or "").strip()
        if not target:
            return None
        changed = False
        updated: List[Dict[str, Any]] = []
        matched: Optional[Dict[str, Any]] = None
        for item in self._cache:
            if item.get("id") != target:
                updated.append(item)
                continue
            next_item = dict(item)
            if tools is not None:
                next_item["tools_cache"] = tools
                changed = True
            if error is not None:
                next_item["last_error"] = str(error)
                changed = True
            if touched:
                next_item["tools_cache_updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                changed = True
            updated.append(next_item)
            matched = next_item
        if changed:
            self._cache = updated
            self._write_file({"servers": updated})
        return dict(matched) if matched else None

    def build_cached_alias_list(self, server_id: Optional[str] = None) -> List[str]:
        aliases: List[str] = []
        seen = set()
        targets = self._cache
        if server_id:
            targets = [item for item in self._cache if item.get("id") == server_id]
        for server in targets:
            if not server.get("enabled", True):
                continue
            sid = server.get("id")
            if not sid:
                continue
            include_tools = set(server.get("include_tools") or [])
            exclude_tools = set(server.get("exclude_tools") or [])
            tools = server.get("tools_cache") or []
            for tool in tools:
                name = str((tool or {}).get("name") or "").strip()
                if not name:
                    continue
                if include_tools and name not in include_tools:
                    continue
                if name in exclude_tools:
                    continue
                alias = self.make_tool_alias(sid, name)
                if alias in seen:
                    continue
                seen.add(alias)
                aliases.append(alias)
        return aliases


def build_default_mcp_category() -> Dict[str, Any]:
    return MCPServerRegistry.build_default_mcp_category()
