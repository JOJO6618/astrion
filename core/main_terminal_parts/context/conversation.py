import asyncio
import json
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
    RECENT_CONVERSATIONS_PROMPT_LIMIT_MIN,
    RECENT_CONVERSATIONS_PROMPT_LIMIT_MAX,
    RECENT_CONVERSATIONS_PROMPT_LIMIT_DEFAULT,
)
from modules.skills_manager import (
    get_skills_catalog,
    build_skills_list,
    merge_enabled_skills,
    build_skills_prompt,
    infer_private_skills_dir,
)
from modules.custom_tool_registry import CustomToolRegistry, build_default_tool_category
from modules.custom_tool_executor import CustomToolExecutor


from modules.container_monitor import collect_stats, inspect_state
from core.tool_config import TOOL_CATEGORIES
from utils.api_client import APIClient
from utils.context_manager import ContextManager, AUTO_SHALLOW_PLACEHOLDER
from utils.host_workspace_debug import write_host_workspace_debug
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


class ConversationMixin:
    """MainTerminalContextMixin conversation 能力 mixin。"""

    def _build_recent_conversations_message(self, limit: int = 10) -> Optional[str]:
            """构建最近对话提示（仅当前工作区）。"""
            try:
                manager = getattr(self.context_manager, "conversation_manager", None)
                if not manager:
                    return None
                current_id = getattr(self.context_manager, "current_conversation_id", None)
                items = manager.get_recent_conversation_summaries(
                    limit=limit,
                    exclude_conversation_id=current_id,
                    first_message_max_chars=100,
                )
                if not items:
                    return None
                lines: List[str] = []
                for index, item in enumerate(items, start=1):
                    title = str(item.get("title") or "未命名对话").strip()
                    conv_id = str(item.get("id") or "").strip()
                    first_message = str(item.get("first_user_message") or "").strip() or "（无首条用户消息）"
                    updated_at = str(item.get("updated_at") or "").strip()
                    lines.append(f"{index}. [{conv_id}] 标题：{title}")
                    lines.append(f"   首条用户消息：{first_message}")
                    if updated_at:
                        lines.append(f"   更新时间：{updated_at}")
                recent_text = "\n".join(lines)
                template = self.load_prompt("recent_conversations").strip()
                if template and "{recent_conversations}" in template:
                    return template.format(recent_conversations=recent_text)
                if template:
                    return f"{template}\n\n{recent_text}"
                return f"## 最近对话（仅当前工作区）\n\n{recent_text}"
            except Exception:
                return None

    def _tool_calls_followed_by_tools(self, conversation: List[Dict], start_idx: int, tool_calls: List[Dict]) -> bool:
            """判断指定助手消息的工具调用是否拥有后续的工具响应。"""
            if not tool_calls:
                return False
            expected_ids = [tc.get("id") for tc in tool_calls if tc.get("id")]
            if not expected_ids:
                return False
            matched: Set[str] = set()
            idx = start_idx + 1
            total = len(conversation)
            while idx < total and len(matched) < len(expected_ids):
                next_conv = conversation[idx]
                role = next_conv.get("role")
                if role == "tool":
                    call_id = next_conv.get("tool_call_id")
                    if call_id in expected_ids:
                        matched.add(call_id)
                    else:
                        break
                elif role in ("assistant", "user"):
                    break
                idx += 1
            return len(matched) == len(expected_ids)
