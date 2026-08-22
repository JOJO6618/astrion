import asyncio
import json
import re
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
        WORKSPACE_MEMORY_DIRNAME,
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
        WORKSPACE_MEMORY_DIRNAME,
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
    resolve_project_memory_inject_limit,
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


def _render_optional_block(text: str, tag: str, keep: bool) -> str:
    """渲染 [tag]...[/tag] 条件块：keep=True 去掉标记保留内容；keep=False 整块（含内容）删除。"""
    pattern = re.compile(r"\[" + re.escape(tag) + r"\](.*?)\[/" + re.escape(tag) + r"\]", re.DOTALL)
    if keep:
        return pattern.sub(lambda m: m.group(1), text)
    return pattern.sub("", text)


class MemoryMixin:
    """MainTerminalContextMixin memory 能力 mixin。"""

    def _scan_project_memories(self):
            """扫描 .astrion/memory/*.md 并解析 frontmatter 中的 name 和 description，按最近修改时间倒序返回"""
            try:
                memory_dir = Path(self.project_path) / WORKSPACE_MEMORY_DIRNAME
            except Exception:
                return []
            if not memory_dir.exists() or not memory_dir.is_dir():
                return []
            results = []
            for md_file in sorted(memory_dir.glob("*.md")):
                try:
                    text = md_file.read_text(encoding="utf-8")
                    name = None
                    description = None
                    if text.startswith("---"):
                        end = text.find("---", 3)
                        if end > 0:
                            frontmatter = text[3:end]
                            for line in frontmatter.strip().split("\n"):
                                line_stripped = line.strip()
                                if line_stripped.startswith("name:"):
                                    name = line_stripped.split(":", 1)[1].strip()
                                elif line_stripped.startswith("description:"):
                                    description = line_stripped.split(":", 1)[1].strip()
                    results.append({
                        "file": md_file.name,
                        "name": name or md_file.stem,
                        "description": description or "",
                        "mtime": md_file.stat().st_mtime,
                    })
                except Exception:
                    pass
            # 最近修改的排前面；mtime 相同时保持文件名顺序（sorted 基数 + 稳定排序）
            results.sort(key=lambda item: item.get("mtime", 0), reverse=True)
            return results

    def _build_memory_system_content(self) -> str:
            """构建记忆系统的 prompt 内容"""
            template = self.load_prompt("memory_system").strip()
            if not template:
                return ""

            try:
                global_memory = self.memory_manager.read_main_memory()
                global_memory_text = global_memory.strip() if global_memory else ""
            except Exception:
                global_memory_text = ""

            try:
                personalization_config = (
                    getattr(self.context_manager, "custom_personalization_config", None)
                    or load_personalization_config(self.data_dir)
                )
            except Exception:
                personalization_config = None
            inject_limit = resolve_project_memory_inject_limit(personalization_config)

            project_memories = self._scan_project_memories()
            total_count = len(project_memories)
            truncated = inject_limit is not None and total_count > inject_limit
            shown_memories = project_memories[:inject_limit] if truncated else project_memories

            if shown_memories:
                lines = []
                for m in shown_memories:
                    desc = m.get("description", "")
                    if desc:
                        lines.append(f".astrion/memory/{m['file']}：{desc}")
                    else:
                        lines.append(f".astrion/memory/{m['file']}")
                project_memory_list = "\n".join(lines)
            else:
                project_memory_list = ""

            result = template
            result = result.replace("{global_memory}", global_memory_text)
            result = _render_optional_block(result, "global_memory_empty", keep=not global_memory_text)

            result = result.replace("{project_memory_list}", project_memory_list)
            result = _render_optional_block(result, "project_memory_empty", keep=not project_memories)

            if truncated:
                result = result.replace("{project_memory_total}", str(total_count))
                result = result.replace("{project_memory_limit}", str(inject_limit))
                result = result.replace("{project_memory_remaining}", str(total_count - inject_limit))
            result = _render_optional_block(result, "project_memory_truncated", keep=truncated)

            return result.strip()
