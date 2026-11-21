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
                from modules.mcp_access import list_mcp_tool

                self.mcp_tool_alias_map = {}
                manager = getattr(self, "mcp_client_manager", None)
                if manager is None or not manager.available_servers():
                    return []
                try:
                    tools, self.mcp_tool_alias_map = manager.build_llm_tools(discovery_timeout_seconds=10)
                except Exception as exc:
                    logger.warning("构建 MCP 工具列表失败: %s", exc)
                    tools = []
                if manager.available_servers():
                    tools.append(list_mcp_tool(self._inject_intent))
                return tools
