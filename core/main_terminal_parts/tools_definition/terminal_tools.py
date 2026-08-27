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



class ToolsDefinitionTerminalToolsMixin:
    def _build_terminal_tools(self) -> List[Dict]:
        return [
                                    {
                        "type": "function",
                        "function": {
                            "name": "terminal_session",
                            "description": "管理持久化终端会话，可打开、关闭、列出或切换终端。请在授权工作区内执行命令，禁止启动需要完整 TTY 的程序（python REPL、vim、top 等）。",
                            "parameters": {
                                "type": "object",
                                "properties": self._inject_intent({
                                    "action": {
                                        "type": "string", 
                                        "enum": ["open", "close", "list", "reset"],
                                        "description": "操作类型：open-打开新终端，close-关闭终端，list-列出所有终端，reset-重置终端"
                                    },
                                    "session_name": {
                                        "type": "string",
                                        "description": "终端会话名称（open、close、reset时需要）"
                                    },
                                    "working_dir": {
                                        "type": "string",
                                        "description": "工作目录，相对于项目路径（open时可选）"
                                    }
                                }),
                                "required": ["action"]
                            }
                        }
                    },

                        	            {
        	                "type": "function",
        	                "function": {
                                    "name": "terminal_input",
                                    "description": "向指定终端发送命令或输入。禁止启动会占用终端界面的程序（python/node/nano/vim 等）；如遇卡死请结合 terminal_snapshot 并使用 terminal_session 的 reset 恢复。output_wait 必填：本次收集/等待输出的最大时长（秒，1-300），不会封装命令、不会强杀进程；在窗口内若检测到命令已完成会提前返回，否则到时返回已产生的输出并保持命令继续运行。需要强制超时终止请使用 run_command。\n用法建议：\n1) 短命令多次执行可设 5-10 秒；\n2) 只需确认启动的长任务（下载/clone/pip/启动服务）可设约 30 秒，运行期间可做其他事情，后续用 terminal_snapshot 判断状态/是否完成；\n3) 必须等待结果再继续的任务（大表/文件批量处理、前端构建、批量测试、压缩/解压、数据导出/统计）设足够覆盖实际执行时间；\n4) 可能长时间无输出的任务（遍历海量文件/转码/打包）短窗口不一定有输出，可适当加长或加完成标记；\n5) 长时间无输出且怀疑卡死时，使用 terminal_session 的 reset 重置终端；\n6) 需要仅发送回车时，可传入一个空格字符作为 command（等效按下 Enter）。\n若不确定上一条命令是否结束，先用 terminal_snapshot 确认后再继续输入。",
        	                    "parameters": {
        	                        "type": "object",
        	                        "properties": self._inject_intent({
        	                            "command": {
        	                                "type": "string",
                                        "description": "要执行的命令或发送的输入"
                                    },
                                    "session_name": {
                                        "type": "string",
                                        "description": "目标终端会话名称（必填）"
                                    },
                                    "output_wait": {
                                        "type": "number",
                                        "description": "等待/收集输出的最大秒数（1-300，必填）；非命令超时，到点即返回已产生输出并保持命令运行"
                                    }
                                }),
                                "required": ["command", "output_wait", "session_name"]
                            }
                        }
                    },

                                    {
                        "type": "function",
                        "function": {
                            "name": "terminal_snapshot",
                            "description": "获取指定终端最近的输出快照，用于判断当前状态。默认返回末尾的50行，可通过参数调整。",
                            "parameters": {
                                "type": "object",
                                "properties": self._inject_intent({
                                    "session_name": {
                                        "type": "string",
                                        "description": "目标终端会话名称（可选，默认活动终端）"
                                    },
                                    "lines": {
                                        "type": "integer",
                                        "description": "返回的最大行数（可选）"
                                    },
                                    "max_chars": {
                                        "type": "integer",
                                        "description": "返回的最大字符数（可选）"
                                    }
                                })
                            }
                        }
                    },

                                    {
                        "type": "function",
	                "function": {
	                    "name": "run_command",
	                    "description": "执行一次性终端命令，适合查看文件信息（file/ls/stat/iconv 等）、转换编码或调用 CLI 工具。禁止启动交互式程序。必须提供 timeout。前台模式（run_in_background=false，默认）上限120秒，超时会打断；后台模式（run_in_background=true）上限3600秒，会先等待5秒返回已有输出并继续在后台运行，完成后由系统通知。后台模式会返回所有指令输出结果，禁止用于启动后台服务。",
	                    "parameters": {
	                        "type": "object",
	                        "properties": self._inject_intent({
	                            "command": {"type": "string", "description": "终端命令"},
                                    "timeout": {
                                        "type": "number",
                                        "description": "超时时长（秒），必填。前台最大120；后台最大3600。"
                                    },
                                    "run_in_background": {
                                        "type": "boolean",
                                        "description": "是否后台运行。true 时先等待5秒返回已有输出，并在后台持续执行直至结束。禁止用于启动后台服务（如常驻服务进程、watch 类命令）。"
                                    }
                                }),
                                "required": ["command", "timeout"]
                            }
                        }
                    },

        ]
