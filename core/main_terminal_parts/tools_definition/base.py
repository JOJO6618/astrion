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



class ToolsDefinitionBaseMixin:
    @staticmethod
    def _mcp_disabled_message() -> str:
                return "当前为docker模式，MCP仅支持宿主机模式"

    def _is_mcp_disabled_in_docker_mode(self) -> bool:
                session = getattr(self, "container_session", None)
                return bool(session and getattr(session, "mode", None) == "docker")

    def _inject_intent(self, properties: Dict[str, Any]) -> Dict[str, Any]:
                """在工具参数中注入 intent（简短意图说明），仅当开关启用时。

                字段含义：要求模型用不超过15个中文字符对即将执行的动作做简要说明，供前端展示。
                """
                if not self.tool_intent_enabled:
                    return properties
                if not isinstance(properties, dict):
                    return properties
                intent_field = {
                    "intent": {
                        "type": "string",
                        "description": "用不超过15个字向用户说明你要做什么，例如：等待下载完成/创建日志文件"
                    }
                }
                # 将 intent 放在最前面以提高模型关注度
                return {**intent_field, **properties}

    def _apply_intent_to_tools(self, tools: List[Dict]) -> List[Dict]:
                """遍历工具列表，为缺少 intent 的工具补充字段（开关启用时生效）。"""
                if not self.tool_intent_enabled:
                    return tools
                intent_field = {
                    "intent": {
                        "type": "string",
                        "description": "用不超过15个字向用户说明你要做什么，例如：等待下载完成/创建日志文件/搜索最新新闻"
                    }
                }
                for tool in tools:
                    func = tool.get("function") or {}
                    tool_name = str(func.get("name") or "").strip()
                    # MCP 扩展工具参数需与远端 schema 严格一致，不注入 intent
                    if tool_name.startswith("mcp__"):
                        continue
                    params = func.get("parameters") or {}
                    if not isinstance(params, dict):
                        continue
                    if params.get("type") != "object":
                        continue
                    props = params.get("properties")
                    if not isinstance(props, dict):
                        continue
                    # 补充 intent 属性
                    if "intent" not in props:
                        params["properties"] = {**intent_field, **props}
                    # 将 intent 加入必填
                    required_list = params.get("required")
                    if isinstance(required_list, list):
                        if "intent" not in required_list:
                            required_list.insert(0, "intent")
                            params["required"] = required_list
                    else:
                        params["required"] = ["intent"]
                return tools
