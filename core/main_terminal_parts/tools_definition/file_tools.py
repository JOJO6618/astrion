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



class ToolsDefinitionFileToolsMixin:
    def _build_file_tools(self) -> List[Dict]:
        return [
                                    {
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "description": "将内容写入本地文件系统；append 为 False 时覆盖原文件，True 时追加到末尾。",
                            "parameters": {
                                "type": "object",
                                "properties": self._inject_intent({
                                    "file_path": {
                                        "type": "string",
                                        "description": "要写入的相对路径"
                                    },
                                    "content": {
                                        "type": "string",
                                        "description": "要写入文件的内容"
                                    },
                                    "append": {
                                        "type": "boolean",
                                        "description": "是否追加到文件而不是覆盖它",
                                        "default": False
                                    }
                                }),
                                "required": ["file_path", "content"]
                            }
                        }
                    },

                                    {
                        "type": "function",
                        "function": {
                            "name": "read_skill",
                            "description": "按 skill 名称读取 SKILL.md 内容（.astrion/skills/<name>/ 或 .agents/skills/<name>/）；内部等价于 read_file 的 read 模式，并返回解析后的 path。若技能在 .astrion/skills/ 与 .agents/skills/ 同名重复，会报错并提示改用 read_file 按具体路径读取。",
                            "parameters": {
                                "type": "object",
                                "properties": self._inject_intent({
                                    "skill_name": {
                                        "type": "string",
                                        "description": "skill 名称（支持 skill id 或技能名称）"
                                    }
                                }),
                                "required": ["skill_name"]
                            }
                        }
                    },

                                    {
                        "type": "function",
                        "function": {
                            "name": "create_skill",
                            "description": "验证并归档一个已创建好的 skill 文件夹。当你觉得本次工作的流程、经验或踩坑值得沉淀为 skill，便于以后面对类似问题时高效工作，或用户主动要求“把今天/本次经历转化为 skill”时，应先阅读 skill-creator，按规范创建对应 skill 文件夹与 SKILL.md，再调用本工具验证并归档。source_dir 可为当前工作区相对路径、工作区内绝对路径，宿主机模式也支持电脑上其它位置的绝对路径。验证通过后移动到正式 skills 库；不覆盖已存在 skill。",
                            "parameters": {
                                "type": "object",
                                "properties": self._inject_intent({
                                    "source_dir": {
                                        "type": "string",
                                        "description": "要归档的 skill 文件夹路径；skill 名称使用该文件夹名称。"
                                    }
                                }),
                                "required": ["source_dir"]
                            }
                        }
                    },

                                    {
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "description": "读取/搜索/抽取 UTF-8 文本文件内容。通过 type 参数选择 read（阅读）、search（搜索）、extract（具体行段），支持限制返回字符数。若文件非 UTF-8 或过大，请改用 run_command 调用合适的解析工具或 Python 解释器。",
                            "parameters": {
                                "type": "object",
                                "properties": self._inject_intent({
                                    "path": {"type": "string", "description": "文件路径"},
                                    "type": {
                                        "type": "string",
                                        "enum": ["read", "search", "extract"],
                                        "description": "读取模式：read=阅读、search=搜索、extract=按行抽取"
                                    },
                                    "max_chars": {
                                        "type": "integer",
                                        "description": "返回内容的最大字符数，默认与 config 一致"
                                    },
                                    "start_line": {
                                        "type": "integer",
                                        "description": "[read] 可选的起始行号（1开始）"
                                    },
                                    "end_line": {
                                        "type": "integer",
                                        "description": "[read] 可选的结束行号（>=start_line）"
                                    },
                                    "query": {
                                        "type": "string",
                                        "description": "[search] 搜索关键词"
                                    },
                                    "max_matches": {
                                        "type": "integer",
                                        "description": "[search] 最多返回多少条命中（默认5，最大50）"
                                    },
                                    "context_before": {
                                        "type": "integer",
                                        "description": "[search] 命中行向上追加的行数（默认1，最大3）"
                                    },
                                    "context_after": {
                                        "type": "integer",
                                        "description": "[search] 命中行向下追加的行数（默认1，最大5）"
                                    },
                                    "case_sensitive": {
                                        "type": "boolean",
                                        "description": "[search] 是否区分大小写，默认 false"
                                    },
                                    "segments": {
                                        "type": "array",
                                        "description": "[extract] 需要抽取的行区间",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "label": {
                                                    "type": "string",
                                                    "description": "该片段的标签（可选）"
                                                },
                                                "start_line": {
                                                    "type": "integer",
                                                    "description": "起始行号（>=1）"
                                                },
                                                "end_line": {
                                                    "type": "integer",
                                                    "description": "结束行号（>=start_line）"
                                                }
                                            },
                                            "required": ["start_line", "end_line"]
                                        },
                                        "minItems": 1
                                    }
                                }),
                                "required": ["path", "type"]
                            }
                        }
                    },

                                    {
                        "type": "function",
                        "function": {
                            "name": "edit_file",
                            "description": "在文件中按顺序执行一组或多组精确字符串替换；建议先使用 read_file 获取最新内容以确保精确匹配。任意一组失败时不会写入文件。",
                            "parameters": {
                                "type": "object",
                                "properties": self._inject_intent({
                                    "file_path": {
                                        "type": "string",
                                        "description": "要修改文件的相对路径"
                                    },
                                    "replacements": {
                                        "type": "array",
                                        "description": "替换项数组，按数组顺序依次执行。每一项包含 old_string/new_string，可选 replace_all；replace_all 未提供时默认为 false。",
                                        "minItems": 1,
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "old_string": {
                                                    "type": "string",
                                                    "description": "要替换的文本（需与文件内容精确匹配，保留缩进；建议提供至少3行提升定位稳定性。需要批量替换的场景可以单行或不足一行）"
                                                },
                                                "new_string": {
                                                    "type": "string",
                                                    "description": "用于替换的新文本（必须不同于 old_string）"
                                                },
                                                "replace_all": {
                                                    "type": "boolean",
                                                    "enum": [True, False],
                                                    "description": "是否替换该 old_string 的所有匹配内容。false=仅替换首个匹配，true=替换全部匹配；默认 false。"
                                                }
                                            },
                                            "required": ["old_string", "new_string"]
                                        }
                                    }
                                }),
                                "required": ["file_path", "replacements"]
                            }
                        }
                    },

                                    {
                        "type": "function",
                        "function": {
                            "name": "save_webpage",
                            "description": "提取网页内容并保存为纯文本文件，适合需要长期留存的长文档。请提供网址与目标路径（含 .txt 后缀），落地后请通过终端命令查看。",
                            "parameters": {
                                "type": "object",
                                "properties": self._inject_intent({
                                    "url": {"type": "string", "description": "要保存的网页URL"},
                                    "target_path": {"type": "string", "description": "保存位置，包含文件名，相对于项目根目录"}
                                }),
                                "required": ["url", "target_path"]
                            }
                        }
                    },

        ]
