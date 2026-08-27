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



class ToolsDefinitionCoreToolsMixin:

    def _build_sleep_tool_definition(self) -> Dict:
        """根据运行模式构建 sleep 工具定义。

        多智能体模式下子智能体不会调用 finish_task 结束，因此不提供
        wait_sub_agent_ids 参数；常规模式保留完整功能。
        """
        is_multi_agent = getattr(self, "multi_agent_mode", False)
        if is_multi_agent:
            return {
                "type": "function",
                "function": {
                    "name": "sleep",
                    "description": "等待工具。三种模式三选一：1) seconds：短暂延迟；2) wait_runcommand_id：等待指定后台 run_command 结束并直接返回结果；3) wait_sub_agent_output：等待指定子智能体下一次输出并直接返回该消息（多智能体模式专用）。若同时提供多个等待参数会报错。",
                    "parameters": {
                        "type": "object",
                        "properties": self._inject_intent({
                            "seconds": {
                                "type": "number",
                                "description": "等待的秒数，可以是小数（如0.2秒）。建议范围：0.1-10秒"
                            },
                            "wait_runcommand_id": {
                                "type": "string",
                                "description": "等待指定后台 run_command 的 command_id 结束后返回。"
                            },
                            "wait_sub_agent_output": {
                                "type": "string",
                                "description": "等待指定子智能体下一次输出并直接返回该消息。多智能体模式专用，传子智能体显示名（如 UI Operator_1）。"
                            },
                            "reason": {
                                "type": "string",
                                "description": "等待的原因说明（可选）"
                            }
                        }),
                        "required": []
                    }
                }
            }
        return {
            "type": "function",
            "function": {
                "name": "sleep",
                "description": "等待工具。三种模式三选一：1) seconds：短暂延迟；2) wait_sub_agent_ids：等待指定子智能体全部结束并直接返回结果；3) wait_runcommand_id：等待指定后台 run_command 结束并直接返回结果。若同时提供多个等待参数会报错。",
                "parameters": {
                    "type": "object",
                    "properties": self._inject_intent({
                        "seconds": {
                            "type": "number",
                            "description": "等待的秒数，可以是小数（如0.2秒）。建议范围：0.1-10秒"
                        },
                        "wait_sub_agent_ids": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "description": "等待这些子智能体全部结束后返回（可提供一个或多个编号）。"
                        },
                        "wait_runcommand_id": {
                            "type": "string",
                            "description": "等待指定后台 run_command 的 command_id 结束后返回。"
                        },
                        "reason": {
                            "type": "string",
                            "description": "等待的原因说明（可选）"
                        }
                    }),
                    "required": []
                }
            }
        }

    def _build_core_tools(self) -> List[Dict]:
        return [
                        	                        self._build_sleep_tool_definition(),

                                    {
                        "type": "function",
                        "function": {
                            "name": "ask_user",
                            "description": "向用户提问并阻塞等待回答。适用于工作中途需要向用户确认、询问的情况：当开发/执行任务中遇到会影响实现方向、产品行为、数据安全或用户偏好的关键不确定性时使用。不适用于要开启下一阶段工作前的询问——此时应直接输出内容告知用户，而不是调用本工具。优先把问题设计成可选择题：默认应提供 2-4 个清晰选项，并把推荐项放第一位；只有用户必须提供具体文本、路径、命名等无法合理枚举的开放问题时，才不要提供 options。前端会弹出问题窗口，用户可以选择预设选项，也始终可以直接打字补充或改写回答；拿到回答后工具才返回，模型才能继续下一步。调用 ask_user 时不要同时调用其他执行工具；如需多个相互独立的问题，可以在同一轮并行调用多个 ask_user。",
                            "parameters": {
                                "type": "object",
                                "properties": self._inject_intent({
                                    "question": {
                                        "type": "string",
                                        "description": "要问用户的核心问题。必须具体、简短、可回答；优先配合 options 让用户点选，而不是要求用户从零输入。"
                                    },
                                    "context": {
                                        "type": "string",
                                        "description": "可选。说明为什么需要确认，以及不同选择会影响什么。"
                                    },
                                    "options": {
                                        "type": "array",
                                        "description": "强烈建议提供。给用户的预设选项，通常 2-4 个；如果有推荐项，放第一位并在 label 中标注“推荐”。每个选项应互斥、短、能直接决策，并用 description 说明影响或取舍。除非问题必须让用户输入具体文本/路径/名称，否则不要省略 options。用户始终可以不选预设项而直接打字回答。",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "id": {
                                                    "type": "string",
                                                    "description": "选项稳定 ID，例如 modal / sidebar / skip / keep_current。"
                                                },
                                                "label": {
                                                    "type": "string",
                                                    "description": "展示给用户的短标签。"
                                                },
                                                "description": {
                                                    "type": "string",
                                                    "description": "一句话说明该选项的影响或取舍。"
                                                }
                                            },
                                            "required": ["id", "label"]
                                        }
                                    }
                                }),
                                "required": ["question"]
                            }
                        }
                    },

                    {
                        "type": "function",
                        "function": {
                            "name": "submit_plan",
                            "description": "【仅计划模式可用】计划已完成并写入计划文档后，调用本工具提请用户批准。前端会弹出批准窗口展示计划文档内容，用户可以批准（附或不附意见）或拒绝（附意见）。批准后系统会自动切换到执行模式，你应随即按计划开始实施；被拒绝时根据用户意见修订计划文档后重新调用本工具提交。不要在计划未定稿、或仍有待澄清的问题时调用；也不要用本工具询问开放式问题——讨论请直接在回复正文中进行。调用本工具时不要同时调用其他工具。",
                            "parameters": {
                                "type": "object",
                                "properties": self._inject_intent({
                                    "plan_file": {
                                        "type": "string",
                                        "description": "计划文档路径，必须位于工作区 .astrion/plan/ 目录下（.md 文件）。"
                                    },
                                    "summary": {
                                        "type": "string",
                                        "description": "一句话概述这份计划要解决什么，展示在批准窗口标题区。"
                                    }
                                }),
                                "required": ["plan_file"]
                            }
                        }
                    },

        ]
