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



class ToolsDefinitionMiscToolsMixin:
    def _build_misc_tools(self) -> List[Dict]:
        return [
                                    {
                        "type": "function",
                        "function": {
                            "name": "vlm_analyze",
                            "description": "使用大参数视觉语言模型（Qwen3.5）理解图片：文字、物体、布局、表格等，仅支持本地路径。",
                            "parameters": {
                                "type": "object",
                                "properties": self._inject_intent({
                                    "path": {"type": "string", "description": "项目内的图片相对路径"},
                                    "prompt": {"type": "string", "description": "传递给 VLM 的中文提示词，如“请总结这张图的内容”“表格的总金额是多少”“图中是什么车？”。"}
                                }),
                                "required": ["path", "prompt"]
                            }
                        }
                    },

                                    {
                        "type": "function",
                        "function": {
                            "name": "list_mcp_servers",
                            "description": "列出当前已配置的 MCP 服务与工具映射（mcp__... 别名）。可选刷新远程 tools/list 缓存后再返回。",
                            "parameters": {
                                "type": "object",
                                "properties": self._inject_intent({
                                    "refresh": {
                                        "type": "boolean",
                                        "description": "是否先同步 MCP 服务工具缓存（默认 false）"
                                    },
                                    "server_id": {
                                        "type": "string",
                                        "description": "仅查看指定 MCP 服务（可选）"
                                    },
                                    "include_disabled": {
                                        "type": "boolean",
                                        "description": "是否包含已禁用服务（默认 false）"
                                    }
                                }),
                                "required": []
                            }
                        }
                    },

                                    {
                        "type": "function",
                        "function": {
                            "name": "trigger_easter_egg",
                            "description": "触发隐藏彩蛋，用于展示非功能性特效。需指定 effect 参数，例如 flood（灌水）或 snake（贪吃蛇）。",
                            "parameters": {
                                "type": "object",
                                "properties": self._inject_intent({
                                    "effect": {
                                        "type": "string",
                                        "description": "彩蛋标识，目前支持 flood（灌水）与 snake（贪吃蛇）。"
                                    }
                                }),
                                "required": ["effect"]
                            }
                        }
                    },

        ]
