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



class ToolsDefinitionSearchWebToolsMixin:
    def _build_search_web_tools(self) -> List[Dict]:
        return [
                                    {
                        "type": "function",
                        "function": {
                            "name": "web_search",
                            "description": "当现有资料不足时搜索外部信息。调用前说明目的，精准撰写 query，并合理设置时间/主题参数；避免重复或无意义的搜索。",
                            "parameters": {
                                "type": "object",
                                "properties": self._inject_intent({
                                    "query": {
                                        "type": "string",
                                        "description": "搜索查询内容（不要包含日期或时间范围）"
                                    },
                                    "max_results": {
                                        "type": "integer",
                                        "description": "最大结果数，可选"
                                    },
                                    "topic": {
                                        "type": "string",
                                        "description": "搜索主题，可选值：general（默认）/news/finance"
                                    },
                                    "time_range": {
                                        "type": "string",
                                        "description": "相对时间范围，可选 day/week/month/year，支持缩写 d/w/m/y；与 days 和 start_date/end_date 互斥"
                                    },
                                    "days": {
                                        "type": "integer",
                                        "description": "最近 N 天，仅当 topic=news 时可用；与 time_range、start_date/end_date 互斥"
                                    },
                                    "start_date": {
                                        "type": "string",
                                        "description": "开始日期，YYYY-MM-DD；必须与 end_date 同时提供，与 time_range、days 互斥"
                                    },
                                    "end_date": {
                                        "type": "string",
                                        "description": "结束日期，YYYY-MM-DD；必须与 start_date 同时提供，与 time_range、days 互斥"
                                    },
                                    "country": {
                                        "type": "string",
                                        "description": "国家过滤，仅 topic=general 可用，使用英文小写国名"
                                    },
                                    "include_domains": {
                                        "type": "array",
                                        "description": "仅包含这些域名（可选，最多300个）",
                                        "items": {
                                            "type": "string"
                                        }
                                    }
                                }),
                                "required": ["query"]
                            }
                        }
                    },

                                    {
                        "type": "function",
                        "function": {
                            "name": "extract_webpage",
                            "description": "在 web_search 结果不够详细时提取网页正文。调用前说明用途，注意提取内容会消耗大量 token，超过80000字符将被拒绝。",
                            "parameters": {
                                "type": "object",
                                "properties": self._inject_intent({
                                    "url": {"type": "string", "description": "要提取内容的网页URL"}
                                }),
                                "required": ["url"]
                            }
                        }
                    },

        ]
