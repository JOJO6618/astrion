import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

try:
    from config import (
        OUTPUT_FORMATS, DATA_DIR, PROMPTS_DIR, NEED_CONFIRMATION,
        MAX_TERMINALS, TERMINAL_BUFFER_SIZE, TERMINAL_DISPLAY_SIZE,
        MAX_READ_FILE_CHARS, READ_TOOL_DEFAULT_MAX_CHARS,
        READ_TOOL_DEFAULT_CONTEXT_BEFORE, READ_TOOL_DEFAULT_CONTEXT_AFTER,
        READ_TOOL_MAX_CONTEXT_BEFORE, READ_TOOL_MAX_CONTEXT_AFTER,
        READ_TOOL_DEFAULT_MAX_MATCHES, READ_TOOL_MAX_MATCHES,
        READ_TOOL_MAX_FILE_SIZE,
        TERMINAL_SANDBOX_MOUNT_PATH,
        TERMINAL_SANDBOX_MODE,
        TERMINAL_SANDBOX_CPUS,
        TERMINAL_SANDBOX_MEMORY,
        PROJECT_MAX_STORAGE_MB,
        CUSTOM_TOOLS_ENABLED,
    )
except ImportError:
    import sys
    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from config import (
        OUTPUT_FORMATS, DATA_DIR, PROMPTS_DIR, NEED_CONFIRMATION,
        MAX_TERMINALS, TERMINAL_BUFFER_SIZE, TERMINAL_DISPLAY_SIZE,
        MAX_READ_FILE_CHARS, READ_TOOL_DEFAULT_MAX_CHARS,
        READ_TOOL_DEFAULT_CONTEXT_BEFORE, READ_TOOL_DEFAULT_CONTEXT_AFTER,
        READ_TOOL_MAX_CONTEXT_BEFORE, READ_TOOL_MAX_CONTEXT_AFTER,
        READ_TOOL_DEFAULT_MAX_MATCHES, READ_TOOL_MAX_MATCHES,
        READ_TOOL_MAX_FILE_SIZE,
        TERMINAL_SANDBOX_MOUNT_PATH,
        TERMINAL_SANDBOX_MODE,
        TERMINAL_SANDBOX_CPUS,
        TERMINAL_SANDBOX_MEMORY,
        PROJECT_MAX_STORAGE_MB,
        CUSTOM_TOOLS_ENABLED,
    )

from modules.file_manager import FileManager
from modules.search_engine import SearchEngine
from modules.terminal_ops import TerminalOperator
from modules.memory_manager import MemoryManager
from modules.terminal_manager import TerminalManager
from modules.todo_manager import TodoManager
from modules.sub_agent import SubAgentManager
from modules.webpage_extractor import extract_webpage_content, tavily_extract
from modules.ocr_client import OCRClient
from modules.easter_egg_manager import EasterEggManager
from modules.personalization_manager import (
    load_personalization_config,
    build_personalization_prompt,
)
from modules.skills_manager import (
    get_skills_catalog,
    build_skills_list,
    merge_enabled_skills,
    build_skills_prompt,
)
from modules.custom_tool_registry import CustomToolRegistry, build_default_tool_category
from modules.custom_tool_executor import CustomToolExecutor
from modules.mcp_server_registry import MCPServerRegistry, build_default_mcp_category


from modules.container_monitor import collect_stats, inspect_state
from core.tool_config import TOOL_CATEGORIES
from utils.api_client import APIClient
from utils.context_manager import ContextManager
from utils.tool_result_formatter import format_tool_result_for_context
from utils.logger import setup_logger
from config.model_profiles import (
    get_model_profile,
    get_model_prompt_replacements,
    get_model_context_window,
    model_supports_image,
    model_supports_video,
)

logger = setup_logger(__name__)
DISABLE_LENGTH_CHECK = True



class ToolsDefinitionCustomMcpMixin:
    def _build_custom_tools(self) -> List[Dict]:
                if not (self.custom_tools_enabled and getattr(self, "user_role", "user") == "admin"):
                    return []
                try:
                    definitions = self.custom_tool_registry.reload()
                except Exception:
                    definitions = self.custom_tool_registry.list_tools()
                if not definitions:
                    # 更新分类为空列表，避免旧缓存
                    if "custom" in self.tool_categories_map:
                        self.tool_categories_map["custom"].tools = []
                    self._refresh_disabled_tools()
                    return []

                tools: List[Dict] = []
                tool_ids: List[str] = []
                for item in definitions:
                    tool_id = item.get("id")
                    if not tool_id:
                        continue
                    if item.get("invalid_id"):
                        # 跳过不合法的工具 ID，避免供应商严格校验时报错
                        continue
                    tool_ids.append(tool_id)
                    params = item.get("parameters") or {"type": "object", "properties": {}}
                    if isinstance(params, dict) and params.get("type") != "object":
                        params = {"type": "object", "properties": {}}
                    required = item.get("required")
                    if isinstance(required, list):
                        params = dict(params)
                        params["required"] = required

                    tools.append({
                        "type": "function",
                        "function": {
                            "name": tool_id,
                            "description": item.get("description") or f"自定义工具: {tool_id}",
                            "parameters": params
                        }
                    })

                # 覆盖 custom 分类的工具列表
                if "custom" in self.tool_categories_map:
                    self.tool_categories_map["custom"].tools = tool_ids
                self._refresh_disabled_tools()

                return tools

    def _build_mcp_tools(self) -> List[Dict]:
                if not getattr(self, "mcp_tools_enabled", False):
                    if hasattr(self, "tool_categories_map") and "mcp" in self.tool_categories_map:
                        self.tool_categories_map["mcp"].tools = ["list_mcp_servers"]
                    stale_keys = [
                        key for key in list(getattr(self, "tool_categories_map", {}).keys())
                        if isinstance(key, str) and key.startswith("mcp_server__")
                    ]
                    for key in stale_keys:
                        self.tool_categories_map.pop(key, None)
                        self.tool_category_states.pop(key, None)
                    self._refresh_disabled_tools()
                    self.mcp_tool_alias_map = {}
                    return []
                if self._is_mcp_disabled_in_docker_mode():
                    self.mcp_tool_alias_map = {}
                    if "mcp" in self.tool_categories_map:
                        self.tool_categories_map["mcp"].tools = ["list_mcp_servers"]
                    elif getattr(self, "mcp_tools_enabled", False):
                        default_mcp_cat = build_default_mcp_category()
                        self.tool_categories_map["mcp"] = type(next(iter(TOOL_CATEGORIES.values())))(
                            label=default_mcp_cat["label"],
                            tools=["list_mcp_servers"],
                            default_enabled=True,
                            silent_when_disabled=False,
                        )
                    stale_keys = [
                        key for key in list(getattr(self, "tool_categories_map", {}).keys())
                        if isinstance(key, str) and key.startswith("mcp_server__")
                    ]
                    for key in stale_keys:
                        self.tool_categories_map.pop(key, None)
                        self.tool_category_states.pop(key, None)
                    self._refresh_disabled_tools()
                    return []
                # 类别层面的禁用优先生效（即使策略里尚未填充具体工具列表）
                mcp_cat = getattr(self, "tool_categories_map", {}).get("mcp")
                if mcp_cat is not None:
                    mcp_enabled = self.tool_category_states.get("mcp", getattr(mcp_cat, "default_enabled", True))
                    forced = getattr(self, "admin_forced_category_states", {}).get("mcp")
                    if isinstance(forced, bool):
                        mcp_enabled = forced
                    if not mcp_enabled:
                        self.mcp_tool_alias_map = {}
                        self.tool_categories_map["mcp"].tools = ["list_mcp_servers"]
                        stale_keys = [
                            key for key in list(getattr(self, "tool_categories_map", {}).keys())
                            if isinstance(key, str) and key.startswith("mcp_server__")
                        ]
                        for key in stale_keys:
                            self.tool_categories_map.pop(key, None)
                            self.tool_category_states.pop(key, None)
                        self._refresh_disabled_tools()
                        return []
                manager = getattr(self, "mcp_client_manager", None)
                if manager is None:
                    self.mcp_tool_alias_map = {}
                    return []
                try:
                    tool_defs, alias_map = manager.build_llm_tools(
                        ensure_discovery=True,
                        discovery_timeout_seconds=10,
                    )
                except Exception as exc:
                    logger.warning("构建 MCP 工具列表失败: %s", exc)
                    tool_defs, alias_map = [], {}

                # 保存映射，供执行层按 alias 分发到具体 server/tool
                alias_map = alias_map or {}
                if "mcp" in self.tool_categories_map:
                    self.tool_categories_map["mcp"].tools = ["list_mcp_servers"]
                elif getattr(self, "mcp_tools_enabled", False):
                    default_mcp_cat = build_default_mcp_category()
                    self.tool_categories_map["mcp"] = type(next(iter(TOOL_CATEGORIES.values())))(
                        label=default_mcp_cat["label"],
                        tools=["list_mcp_servers"],
                        default_enabled=True,
                        silent_when_disabled=False,
                    )
                server_aliases: Dict[str, List[str]] = {}
                for alias, binding in alias_map.items():
                    sid = str(getattr(binding, "server_id", "") or "").strip()
                    if not sid:
                        continue
                    server_aliases.setdefault(sid, []).append(alias)

                enabled_servers = []
                try:
                    enabled_servers = list(self.mcp_server_registry.list_enabled_servers())
                except Exception:
                    enabled_servers = []

                active_server_category_ids = set()
                for server in enabled_servers:
                    sid = str(server.get("id") or "").strip()
                    if not sid:
                        continue
                    cat_id = MCPServerRegistry.make_server_category_id(sid)
                    active_server_category_ids.add(cat_id)
                    aliases = list(server_aliases.get(sid, []))
                    existing = self.tool_categories_map.get(cat_id)
                    label = MCPServerRegistry.make_server_category_label(server)
                    if existing is not None:
                        existing.label = label
                        existing.tools = aliases
                    else:
                        self.tool_categories_map[cat_id] = type(next(iter(TOOL_CATEGORIES.values())))(
                            label=label,
                            tools=aliases,
                            default_enabled=True,
                            silent_when_disabled=False,
                        )
                    if cat_id not in self.tool_category_states:
                        self.tool_category_states[cat_id] = True

                stale_keys = [
                    key for key in list(self.tool_categories_map.keys())
                    if isinstance(key, str)
                    and key.startswith("mcp_server__")
                    and key not in active_server_category_ids
                ]
                for key in stale_keys:
                    self.tool_categories_map.pop(key, None)
                    self.tool_category_states.pop(key, None)

                if hasattr(self, "_prune_runtime_tool_overrides"):
                    self._prune_runtime_tool_overrides()
                if hasattr(self, "_apply_runtime_tool_overrides"):
                    self._apply_runtime_tool_overrides()

                filtered_tools: List[Dict[str, Any]] = []
                filtered_alias_map: Dict[str, Any] = {}
                for tool_def in tool_defs:
                    alias = str(((tool_def or {}).get("function") or {}).get("name") or "").strip()
                    if not alias:
                        continue
                    binding = alias_map.get(alias)
                    if binding is None:
                        continue
                    sid = str(getattr(binding, "server_id", "") or "").strip()
                    if not sid:
                        continue
                    cat_id = MCPServerRegistry.make_server_category_id(sid)
                    cat = self.tool_categories_map.get(cat_id)
                    cat_enabled = self.tool_category_states.get(
                        cat_id,
                        getattr(cat, "default_enabled", True) if cat is not None else True,
                    )
                    forced = getattr(self, "admin_forced_category_states", {}).get(cat_id)
                    if isinstance(forced, bool):
                        cat_enabled = forced
                    if not cat_enabled:
                        continue
                    filtered_tools.append(tool_def)
                    filtered_alias_map[alias] = binding

                self.mcp_tool_alias_map = filtered_alias_map
                self._refresh_disabled_tools()
                return filtered_tools
