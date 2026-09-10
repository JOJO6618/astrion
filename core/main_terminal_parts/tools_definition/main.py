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
from core.tool_loading import (
    build_load_tools_definition,
    get_tool_loading_state,
)
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



class ToolsDefinitionMainMixin:
    def define_tools(self, include_deferred: bool = False) -> List[Dict]:
                """定义可用工具（添加确认工具）

                include_deferred=True 时跳过延迟工具过滤（供 load_tools 取
                完整定义用），正常请求走默认 False。
                """
                tools: List[Dict] = []
                tools.extend(self._build_core_tools())
                tools.extend(self._build_file_tools())
                tools.extend(self._build_terminal_tools())
                tools.extend(self._build_search_web_tools())
                tools.extend(self._build_agent_tools())
                tools.extend(self._build_context_tools())
                tools.extend(self._build_misc_tools())
                tools.extend(self._build_workflow_tools())
                # 多模态模型自带能力，不再暴露 vlm_analyze，改为 view_image / view_video
                model_key = getattr(self, "model_key", None)
                if model_key and (model_supports_image(model_key) or model_supports_video(model_key)):
                    tools = [
                        tool for tool in tools
                        if (tool.get("function") or {}).get("name") != "vlm_analyze"
                    ]
                    if model_supports_image(model_key):
                        tools.append({
                            "type": "function",
                            "function": {
                                "name": "view_image",
                                "description": "将指定本地图片附加到工具结果中（tool 消息携带 image_url），便于模型主动查看图片内容。",
                                "parameters": {
                                    "type": "object",
                                    "properties": self._inject_intent({
                                        "path": {
                                            "type": "string",
                                            "description": "项目内的图片相对路径（不要以 /workspace 开头）；宿主机模式可用绝对路径。支持 png/jpg/webp/gif/bmp。"
                                        }
                                    }),
                                    "required": ["path"]
                                }
                            }
                        })
                    if model_supports_video(model_key):
                        tools.append({
                            "type": "function",
                            "function": {
                                "name": "view_video",
                                "description": "将指定本地视频附加到工具结果中（tool 消息携带 video_url），便于模型查看视频内容。",
                                "parameters": {
                                    "type": "object",
                                    "properties": self._inject_intent({
                                        "path": {
                                            "type": "string",
                                            "description": "项目内的视频相对路径（不要以 /workspace 开头）；宿主机模式可用绝对路径。支持 mp4/mov/mkv/avi/webm。"
                                        }
                                    }),
                                    "required": ["path"]
                                }
                            }
                        })
                # submit_plan 全模式注入：它是沟通/提交类工具而非危险写操作，
                # 过滤会让非 plan 模式的模型不知道该能力存在（如 ask 模式下用户
                # 要求「整理好计划提交给我审核」时模型无法响应）。非 plan 调用由
                # _handle_submit_plan 运行时兜延返回引导，无需工具层硬限制。
                # 附加自定义工具（仅管理员可见）
                custom_tools = self._build_custom_tools()
                if custom_tools:
                    tools.extend(custom_tools)
                # 附加 MCP 工具（由管理员统一配置）
                mcp_tools = self._build_mcp_tools()
                if mcp_tools:
                    tools.extend(mcp_tools)
                if self.disabled_tools:
                    tools = [
                        tool for tool in tools
                        if tool.get("function", {}).get("name") not in self.disabled_tools
                    ]

                # 工具动态加载：按对话快照过滤延迟工具。快照创建即钉死，
                # tools 数组对话级稳定（不破坏前缀缓存）；老对话无字段=全量。
                # load_tools 取完整定义走 include_deferred=True，跳过本段。
                tl_state = None
                if not include_deferred:
                    try:
                        _tl_cm = getattr(self, "context_manager", None)
                        _tl_meta = getattr(_tl_cm, "conversation_metadata", None) if _tl_cm else None
                        tl_state = get_tool_loading_state(_tl_meta)
                    except Exception:
                        tl_state = None
                if tl_state:
                    _deferred = set(tl_state["deferred_set"])
                    tools = [
                        tool for tool in tools
                        if (tool.get("function") or {}).get("name") not in _deferred
                    ]
                    if _deferred:
                        tools.append(build_load_tools_definition())
                    # 懒记录 initial_exposed：首个请求写入一次。该字段仅作审计
                    # 快照，过滤语义恒为「构建集 − deferred_set」，不依赖它。
                    if not tl_state["initial_exposed"]:
                        try:
                            _tl_conv_id = getattr(_tl_cm, "current_conversation_id", None) if _tl_cm else None
                            if _tl_conv_id:
                                _exposed = [
                                    name for name in (
                                        (t.get("function") or {}).get("name") for t in tools
                                    ) if name
                                ]
                                _new_state = {**tl_state, "initial_exposed": _exposed}
                                _mgr = (
                                    _tl_cm._get_conversation_manager_for_id(_tl_conv_id)
                                    if hasattr(_tl_cm, "_get_conversation_manager_for_id")
                                    else _tl_cm.conversation_manager
                                )
                                _mgr.update_conversation_metadata(_tl_conv_id, {"tool_loading": _new_state})
                                if isinstance(_tl_cm.conversation_metadata, dict):
                                    _tl_cm.conversation_metadata["tool_loading"] = _new_state
                        except Exception:
                            pass

                # 调试日志：记录工具列表（DEBUG 级，不上终端）
                tool_names = [t.get("function", {}).get("name") for t in tools]
                logger.debug("[define_tools] 可用工具列表: %s", tool_names)

                if "manage_personalization" in tool_names:
                    logger.debug("[define_tools] manage_personalization 工具已启用")
                else:
                    logger.debug("[define_tools] manage_personalization 未启用: disabled_tools=%s", self.disabled_tools)
                
                return self._apply_intent_to_tools(tools)
