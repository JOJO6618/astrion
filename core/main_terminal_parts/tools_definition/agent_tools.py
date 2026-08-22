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



class ToolsDefinitionAgentToolsMixin:
    def _build_agent_tools(self) -> List[Dict]:
        # 多智能体模式下，主智能体不再使用旧版的 4 个子智能体工具
        # 而是用 modules/multi_agent/tools.build_master_tools_for_conversation() 返回的新集
        if getattr(self, "multi_agent_mode", False):
            try:
                from modules.multi_agent.tools import build_master_tools_for_conversation
                return build_master_tools_for_conversation()
            except Exception as exc:
                logger.warning(f"[tools] 加载多智能体工具失败，回退旧版: {exc}")
        return [
                                    {
                        "type": "function",
                        "function": {
                            "name": "close_sub_agent",
                            "description": "强制关闭指定子智能体，适用于长时间无响应、超时或卡死的任务。使用前请确认必要的日志/文件已保留，操作会立即终止该任务。",
                            "parameters": {
                                "type": "object",
                                "properties": self._inject_intent({
                                    "task_id": {"type": "string", "description": "子智能体任务ID"},
                                    "agent_id": {"type": "integer", "description": "子智能体编号（1~5），若缺少 task_id 可用"}
                                })
                            }
                        }
                    },

                                    {
                        "type": "function",
                        "function": {
                            "name": "create_sub_agent",
                            "description": "创建一个子智能体来处理独立任务。子智能体拥有完整的工具能力（读写文件、执行命令、搜索网页等），与主智能体共享工作区。\n\n适用场景：\n1. 可以独立完成的任务（生成文档、代码分析、测试执行）\n2. 需要大量工具调用的任务（批量文件处理、数据收集）\n3. 可以并行处理的子任务（多模块开发、多方面分析）\n4. 刚开始修改一个项目、需要先找到某个功能模块的修改位置时，可优先创建子智能体快速了解代码定位与相关上下文\n\n何时使用后台运行：\n- 任务耗时较长（预计超过5分钟）\n- 你可以继续处理其他工作，不需要立即使用结果\n- 多个独立任务可以并行执行\n\n何时使用阻塞运行（默认）：\n- 任务较快（几分钟内完成）\n- 后续工作依赖子智能体的结果\n- 需要立即查看和使用输出\n\n重要限制：\n- 最多同时运行 5 个子智能体\n- 禁止多个子智能体操作相同的文件或目录（会导致冲突）\n- 禁止子智能体间的工作有重叠（如同时修改同一模块、同时测试同一功能）\n- 每个子智能体应该有明确独立的职责范围",
                            "parameters": {
                                "type": "object",
                                "properties": self._inject_intent({
                                    "agent_id": {
                                        "type": "integer",
                                        "description": "子智能体编号（1-99），用于标识和管理。同一对话中每个编号只能使用一次。建议按顺序分配：1、2、3..."
                                    },
                                    "summary": {
                                        "type": "string",
                                        "description": "任务简短摘要（10-30字），用于显示和跟踪。例如：'生成API文档'、'分析性能瓶颈'、'编写单元测试'。"
                                    },
                                    "task": {
                                        "type": "string",
                                        "description": "详细的任务描述，必须包括：\n1. 任务目标：要完成什么\n2. 具体要求：如何完成、注意事项\n3. 交付内容：在交付目录生成哪些文件\n4. 工作范围：明确指定操作的文件/目录范围，避免与其他子智能体冲突\n\n示例：'分析 src/api/ 目录下的所有 Python 文件，检查代码质量问题（复杂度、重复代码、潜在bug），生成分析报告 analysis.md 到交付目录。'"
                                    },
                                    "deliverables_dir": {
                                        "type": "string",
                                        "description": "交付文件夹的相对路径（相对于项目根目录）。子智能体会将所有结果文件放在此目录。\n\n要求：必须是不存在的新目录；若目录不存在会自动创建；若目录已存在（无论是否为空）将报错。\n\n留空则使用默认路径：sub_agent_results/agent_{agent_id}\n\n示例：'docs/api'、'reports/performance'、'tests/generated'"
                                    },
                                    "run_in_background": {
                                        "type": "boolean",
                                        "description": "是否后台运行。\n\ntrue（后台）：立即返回，子智能体在后台执行，完成后会通知你。适合耗时任务或可以并行处理的任务。\n\nfalse（阻塞，默认）：等待子智能体完成后返回结果。适合快速任务或后续工作依赖结果的情况。"
                                    },
                                    "timeout_seconds": {
                                        "type": "integer",
                                        "description": "超时时间（秒），范围 60-3600。超时后子智能体会被强制终止，已生成的部分结果会保留。默认 600 秒（10分钟）。"
                                    },
                                    "thinking_mode": {
                                        "type": "string",
                                        "enum": ["fast", "thinking"],
                                        "description": "子智能体思考模式，根据任务复杂度选择：\n\nfast（快速模式）- 适合简单明确的任务：\n- 网络信息搜集和整理\n- 批量文件读取和简单处理\n- 执行已知的命令序列\n- 生成简单的文档或报告\n- 数据格式转换\n\nthinking（思考模式）- 适合复杂任务：\n- 代码架构分析和重构设计\n- 复杂算法实现和优化\n- 多步骤问题诊断和调试\n- 技术方案选型和对比\n- 需要深度推理的代码审查\n\n不填则使用默认模式。"
                                    }
                                }),
                                "required": ["agent_id", "summary", "task", "deliverables_dir", "thinking_mode"]
                            }
                        }
                    },

                                    {
                        "type": "function",
                        "function": {
                            "name": "terminate_sub_agent",
                            "description": "强制终止正在运行的子智能体。用于：\n1. 任务不再需要\n2. 子智能体陷入死循环或执行错误\n3. 用户要求停止\n\n终止后无法恢复，但已生成的部分结果会保留在交付目录。",
                            "parameters": {
                                "type": "object",
                                "properties": self._inject_intent({
                                    "agent_id": {
                                        "type": "integer",
                                        "description": "要终止的子智能体编号。"
                                    }
                                }),
                                "required": ["agent_id"]
                            }
                        }
                    },

                                    {
                        "type": "function",
                        "function": {
                            "name": "get_sub_agent_status",
                            "description": "查询一个或多个子智能体的当前状态和工作进度。用于检查后台运行的子智能体是否完成、当前在做什么、使用了哪些工具。",
                            "parameters": {
                                "type": "object",
                                "properties": self._inject_intent({
                                    "agent_ids": {
                                        "type": "array",
                                        "items": {"type": "integer"},
                                        "description": "要查询的子智能体编号列表。必须指定至少一个编号。例如：[1] 或 [1, 2, 3]。"
                                    }
                                }),
                                "required": ["agent_ids"]
                            }
                        }
                    },

        ]
