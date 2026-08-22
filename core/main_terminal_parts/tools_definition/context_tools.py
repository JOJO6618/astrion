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



class ToolsDefinitionContextToolsMixin:
    def _build_context_tools(self) -> List[Dict]:
        return [
                                    {
                        "type": "function",
                        "function": {
                            "name": "update_memory",
                            "description": "按条目管理总体长期记忆（自动编号，跨项目通用）。append/replace/delete。当用户提到个人信息、偏好、跨项目习惯，或用户主动要求「记住xxx」时，应积极主动地调用本工具。不记录密钥、隐私、未确认猜测。与特定项目绑定的技术约定用 update_project_memory。",
                            "parameters": {
                                "type": "object",
                                "properties": self._inject_intent({
                                    "content": {"type": "string", "description": "条目内容。append/replace 时必填"},
                                    "operation": {"type": "string", "enum": ["append", "replace", "delete"], "description": "操作类型"},
                                    "index": {"type": "integer", "description": "要替换/删除的序号（从1开始）"}
                                }),
                                "required": ["operation"]
                            }
                        }
                    },

                                    {
                        "type": "function",
                        "function": {
                            "name": "recall_project_memory",
                            "description": "读取指定的项目记忆文件，返回完整内容（含 frontmatter）。项目记忆存储在 .astrion/memory/ 目录下。通常在 search_project_memory 检索定位后使用；若不确定记忆名称，先检索再读取。",
                            "parameters": {
                                "type": "object",
                                "properties": self._inject_intent({
                                    "name": {
                                        "type": "string",
                                        "description": "记忆名称，对应 .astrion/memory/{name}.md 文件名"
                                    }
                                }),
                                "required": ["name"]
                            }
                        }
                    },

                                    {
                        "type": "function",
                        "function": {
                            "name": "search_project_memory",
                            "description": "在项目记忆文件（.astrion/memory/*.md）的正文和描述中全文检索，返回匹配的记忆文件与片段。【硬性要求】开始处理与本项目相关的任务前（修改代码、调试报错、配置部署，或任务可能涉及项目约定/历史决策/已知问题时），必须先调用本工具检索；根据任务内容、用户要求和项目记忆索引中的描述，提取2~5个清晰精确的关键词（优先使用项目术语、模块名、文件名、报错文本，中英文均可）。纯闲聊、与项目无关的通用问题不要调用。未命中时会明确返回无结果，此时不要更换同义词重复尝试；命中后如需完整内容，用 recall_project_memory 读取。",
                            "parameters": {
                                "type": "object",
                                "properties": self._inject_intent({
                                    "keywords": {
                                        "type": "array",
                                        "description": "搜索关键词列表，建议2~5个；任一关键词命中即算匹配，命中数越多排名越靠前",
                                        "items": {"type": "string"},
                                        "minItems": 1,
                                        "maxItems": 5
                                    },
                                    "max_results": {
                                        "type": "integer",
                                        "description": "最多返回多少条匹配记忆，默认5，最大10"
                                    }
                                }),
                                "required": ["keywords"]
                            }
                        }
                    },

                                    {
                        "type": "function",
                        "function": {
                            "name": "update_project_memory",
                            "description": "创建或覆盖项目记忆文件。当你发现有关当前项目的重要约定/决策/坑、用户表现出对项目的偏好，或用户主动要求「在当前项目里，下次要xxx/记住xxx」时，应积极主动地调用本工具。记忆名称用英文下划线，描述格式为'当xxxx时，应该索引本记忆'。不记录可从代码直接推断的或一次性信息。",
                            "parameters": {
                                "type": "object",
                                "properties": self._inject_intent({
                                    "name": {
                                        "type": "string",
                                        "description": "记忆名称，同时作为文件名 .astrion/memory/{name}.md。建议英文+下划线，如 docker_compose"
                                    },
                                    "description": {
                                        "type": "string",
                                        "description": "记忆简述，格式为'当xxxx时，应该索引本记忆'，用于在 prompt 中作为索引展示（≤100字）"
                                    },
                                    "content": {
                                        "type": "string",
                                        "description": "记忆正文（Markdown），记录具体内容"
                                    }
                                }),
                                "required": ["name", "description", "content"]
                            }
                        }
                    },

                                    {
                        "type": "function",
                        "function": {
                            "name": "conversation_search",
                            "description": "在当前工作区的历史对话中搜索或列出对话，返回对话标题与 id。最多提供 3 个关键词，命中任一关键词即可；如果不提供关键词，则按时间列出最近的历史对话。可提供日期范围和最大返回数量。不能跨工作区搜索，也不会搜索当前对话。",
                            "parameters": {
                                "type": "object",
                                "properties": self._inject_intent({
                                    "keywords": {
                                        "type": "array",
                                        "description": "搜索关键词列表，最多3个；仅搜索标题和首条用户消息。可为空。",
                                        "items": {"type": "string"},
                                        "maxItems": 3
                                    },
                                    "query": {"type": "string", "description": "兼容字段：单个搜索关键词，可为空；优先使用 keywords"},
                                    "start_date": {"type": "string", "description": "开始日期 YYYY-MM-DD，可选"},
                                    "end_date": {"type": "string", "description": "结束日期 YYYY-MM-DD，可选"},
                                    "limit": {"type": "integer", "description": "最大返回数量，默认10，最大100"}
                                }),
                                "required": []
                            }
                        }
                    },

                                    {
                        "type": "function",
                        "function": {
                            "name": "conversation_review",
                            "description": "按 id 回顾当前工作区内的历史对话。mode=read 时直接返回回顾内容；若内容超过 50000 字符，将自动保存到 .astrion/review/ 并提示分段或查找阅读。mode=save 时保存 Markdown 文件到 .astrion/review/ 并返回路径。若 id 不属于当前工作区，将返回不存在或不属于当前工作区。",
                            "parameters": {
                                "type": "object",
                                "properties": self._inject_intent({
                                    "conversation_id": {"type": "string", "description": "要回顾的对话 id，例如 conv_20250924_210942_114"},
                                    "mode": {
                                        "type": "string",
                                        "enum": ["read", "save"],
                                        "description": "必要参数。read=直接返回回顾内容；save=保存为 .astrion/review/ 下的 Markdown 文件并返回路径。"
                                    }
                                }),
                                "required": ["conversation_id", "mode"]
                            }
                        }
                    },

                                    {
                        "type": "function",
                        "function": {
                            "name": "todo_create",
                            "description": "创建待办列表，最多 8 条任务；若已有列表将被覆盖。当用户提出稍微复杂的要求、预计需要超过 3 个步骤时，应积极创建待办事项。",
                            "parameters": {
                                "type": "object",
                                "properties": self._inject_intent({
                                    "overview": {"type": "string", "description": "一句话概述待办清单要完成的目标，50 字以内。"},
                                    "tasks": {
                                        "type": "array",
                                        "description": "任务列表，1~8 条，每条写清“动词+对象+目标”。",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "title": {"type": "string", "description": "单个任务描述，写成可执行的步骤"}
                                            },
                                            "required": ["title"]
                                        },
                                        "minItems": 1,
                                        "maxItems": 8
                                    }
                                }),
                                "required": ["overview", "tasks"]
                            }
                        }
                    },

                                    {
                        "type": "function",
                        "function": {
                            "name": "todo_update_task",
                            "description": "批量勾选或取消任务（支持单个或多个任务）；全部勾选时提示所有任务已完成。",
                            "parameters": {
                                "type": "object",
                                "properties": self._inject_intent({
                                    "task_index": {"type": "integer", "description": "任务序号（1-8），兼容旧参数"},
                                    "task_indices": {
                                        "type": "array",
                                        "items": {"type": "integer"},
                                        "minItems": 1,
                                        "maxItems": 8,
                                        "description": "要更新的任务序号列表（1-8），可一次勾选多个"
                                    },
                                    "completed": {"type": "boolean", "description": "true=打勾，false=取消"}
                                }),
                                "required": ["completed"]
                            }
                        }
                    },

                                    {
                        "type": "function",
                        "function": {
                            "name": "manage_personalization",
                            "description": "管理用户个性化设置。支持读取所有配置，或更新单个字段。可修改字段：self_identify（AI自称，最多20字）、user_name（AI如何称呼用户，最多20字）、profession（用户职业，最多20字）、tone（交流语气，最多20字）、considerations（回答时必须考虑的信息，字符串，最多2000字）、theme（主题配色：classic-经典/light-明亮/dark-暗黑）、communication_style（交流风格：default-标准AI风格/human_like-拟人聊天风格/auto-自动选择交流风格）、conversation_continuity（对话连续性：high-高/medium-中/low-低）。更新时会自动验证格式，验证通过后立即保存并生效，新主题会立即应用到界面。",
                            "parameters": {
                                "type": "object",
                                "properties": self._inject_intent({
                                    "action": {
                                        "type": "string",
                                        "enum": ["read", "update"],
                                        "description": "操作类型：read读取所有配置，update更新单个字段"
                                    },
                                    "field": {
                                        "type": "string",
                                        "enum": ["self_identify", "user_name", "profession", "tone", "considerations", "theme", "communication_style", "conversation_continuity"],
                                        "description": "要更新的字段名（仅action=update时需要）"
                                    },
                                    "value": {
                                        "description": "新值（仅action=update时需要）。注意事项提供字符串，其他字段提供字符串"
                                    }
                                }),
                                "required": ["action"]
                            }
                        }
                    }

        ]
