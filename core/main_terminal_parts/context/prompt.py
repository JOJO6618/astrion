import asyncio
import json
import time
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

# 扫描一级子目录 AGENTS.md 时跳过的通用非源码目录（版本控制/依赖/构建产物/工具数据等，不针对本项目特化）
_AGENTS_MD_SKIP_DIRS = {
    ".git", ".hg", ".svn",
    "node_modules", "bower_components",
    ".venv", "venv", "__pycache__",
    "dist", "build", ".next", ".nuxt", ".output",
    ".astrion", ".claude", ".cursor", ".idea", ".vscode",
}


class PromptMixin:
    """MainTerminalContextMixin prompt 能力 mixin。"""

    def _load_root_md_content(self, filename: str) -> Optional[str]:
        """加载工作区根目录的指定指令文件内容（仅根目录，子目录的不注入）。"""
        try:
            project_path = Path(self.project_path)
            root_file = project_path / filename
            if not root_file.is_file():
                return None
            content = root_file.read_text(encoding='utf-8')
            return content.strip() if content else None
        except Exception as exc:
            logger.warning(f"[{filename}] 读取失败: {exc}")
            return None

    def _load_root_md_updated_at(self, filename: str) -> str:
        """返回根目录指定指令文件的最后修改时间文案（如「（最后修改：2026-08-12 17:54）」，失败返回空串）。"""
        try:
            root_file = Path(self.project_path) / filename
            if not root_file.is_file():
                return ""
            mtime = root_file.stat().st_mtime
            return f"（最后修改：{datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M')}）"
        except Exception:
            return ""

    def _load_sub_md_paths(self, filename: str, max_notice: int = 20, timeout_seconds: float = 30.0) -> tuple:
        """扫描工作区一级子目录中的指定指令文件，返回 (相对路径列表, 实际总数)。

        - 仅扫描根目录与一级子目录，不递归；跳过通用非源码目录（_AGENTS_MD_SKIP_DIRS）。
        - 列表最多 max_notice 个（超出截断，总数仍在第二项返回）。
        - 扫描超过 timeout_seconds 秒时停止，返回已扫描到的结果。
        """
        found: List[str] = []
        total = 0
        try:
            project_path = Path(self.project_path)
            if not project_path.is_dir():
                return found, total
            start = time.monotonic()
            for sub in project_path.iterdir():
                if time.monotonic() - start >= timeout_seconds:
                    logger.warning(f"[{filename}] 子目录扫描超过 {timeout_seconds}s，返回已扫描到的 {len(found)} 个")
                    break
                if not sub.is_dir() or sub.name in _AGENTS_MD_SKIP_DIRS:
                    continue
                candidate = sub / filename
                if candidate.is_file():
                    total += 1
                    if len(found) < max_notice:
                        found.append(candidate.relative_to(project_path).as_posix())
        except Exception as exc:
            logger.warning(f"[{filename}] 子目录扫描失败: {exc}")
        return found, total

    def _load_agents_md_content(self) -> Optional[str]:
        """加载工作区根目录的 AGENTS.md 文件内容（仅根目录，子目录的不注入）。"""
        return self._load_root_md_content("AGENTS.md")

    def _load_agents_md_updated_at(self) -> str:
        """返回根目录 AGENTS.md 的最后修改时间文案（如「（最后修改：2026-08-12 17:54）」，失败返回空串）。"""
        return self._load_root_md_updated_at("AGENTS.md")

    def _load_sub_agents_md_paths(self, max_notice: int = 20, timeout_seconds: float = 30.0) -> tuple:
        """扫描工作区一级子目录中的 AGENTS.md，返回 (相对路径列表, 实际总数)。"""
        return self._load_sub_md_paths("AGENTS.md", max_notice=max_notice, timeout_seconds=timeout_seconds)

    def load_prompt(self, name: str) -> str:
            """加载提示模板"""
            prompt_file = Path(PROMPTS_DIR) / f"{name}.txt"
            if prompt_file.exists():
                with open(prompt_file, 'r', encoding='utf-8') as f:
                    return f.read()
            return "你是 Astrion，一个智能助手。"
