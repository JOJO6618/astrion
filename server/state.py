"""共享状态与常量，供各子模块使用。"""
from __future__ import annotations
import os
import threading
from collections import defaultdict, deque
from pathlib import Path
from typing import Dict, Any, Optional

from config import LOGS_DIR, PROJECT_MAX_STORAGE_BYTES, PROJECT_MAX_STORAGE_MB, WEB_SERVER_PORT
from core.web_terminal import WebTerminal
from modules.custom_tool_registry import CustomToolRegistry
from modules.mcp_server_registry import MCPServerRegistry
from modules.mcp_client_manager import MCPClientManager
from modules.usage_tracker import UsageTracker
from modules.user_container_manager import UserContainerManager
from modules.user_manager import UserManager
from modules.api_user_manager import ApiUserManager
from modules.tool_approval_manager import ToolApprovalManager
from modules.user_question_manager import UserQuestionManager
from modules.plan_approval_manager import PlanApprovalManager

# 全局实例
user_manager = UserManager()
api_user_manager = ApiUserManager()
custom_tool_registry = CustomToolRegistry()
mcp_server_registry = MCPServerRegistry()
mcp_client_manager = MCPClientManager(mcp_server_registry)
container_manager = UserContainerManager()
user_terminals: Dict[str, WebTerminal] = {}
terminal_rooms: Dict[str, set] = {}
connection_users: Dict[str, str] = {}
HOST_ACTIVE_WORKSPACE_LOCK = threading.RLock()
HOST_ACTIVE_WORKSPACE_ID: Optional[str] = None
HOST_ACTIVE_WORKSPACE_PATH: Optional[str] = None
HOST_ACTIVE_WORKSPACE_VERSION: int = 0
RECENT_UPLOAD_EVENT_LIMIT = 150
RECENT_UPLOAD_FEED_LIMIT = 60
stop_flags: Dict[str, Dict[str, Any]] = {}
active_polling_tasks: Dict[str, bool] = {}  # conversation_id -> is_polling
tool_approval_manager = ToolApprovalManager()
user_question_manager = UserQuestionManager()
plan_approval_manager = PlanApprovalManager()

# 监控/限流/用量
MONITOR_FILE_TOOLS = {'write_file', 'edit_file'}
MONITOR_MEMORY_TOOLS = {'update_memory'}
MONITOR_SNAPSHOT_CHAR_LIMIT = 60000
MONITOR_MEMORY_ENTRY_LIMIT = 256
RATE_LIMIT_BUCKETS: Dict[str, deque] = defaultdict(deque)
FAILURE_TRACKERS: Dict[str, Dict[str, float]] = {}
pending_socket_tokens: Dict[str, Dict[str, Any]] = {}
usage_trackers: Dict[str, UsageTracker] = {}
active_login_nonces: Dict[str, set] = defaultdict(set)

MONITOR_SNAPSHOT_CACHE: Dict[str, Dict[str, Any]] = {}
MONITOR_SNAPSHOT_CACHE_LIMIT = 120
RECENT_UPLOAD_EVENT_LIMIT = 150
RECENT_UPLOAD_FEED_LIMIT = 60

# 路径与缓存设置（依赖项目配置）
PROJECT_STORAGE_CACHE: Dict[str, Dict[str, Any]] = {}
PROJECT_STORAGE_CACHE_TTL_SECONDS = float(os.environ.get("PROJECT_STORAGE_CACHE_TTL", "30"))

# 其他配置
DEFAULT_PORT = WEB_SERVER_PORT
CSRF_HEADER_NAME = "X-CSRF-Token"
CSRF_SESSION_KEY = "_csrf_token"
CSRF_SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
CSRF_PROTECTED_PATHS = {"/login", "/register", "/logout", "/host-login"}
CSRF_PROTECTED_PREFIXES = ("/api/",)
CSRF_EXEMPT_PATHS = {"/api/csrf-token"}
FAILED_LOGIN_LIMIT = 5
FAILED_LOGIN_LOCK_SECONDS = 300
SOCKET_TOKEN_TTL_SECONDS = 45
USER_IDLE_TIMEOUT_SECONDS = int(os.environ.get("USER_IDLE_TIMEOUT_SECONDS", "900"))
LAST_ACTIVE_FILE = Path(LOGS_DIR).expanduser().resolve() / "last_active.json"
_last_active_lock = threading.Lock()
_last_active_cache: Dict[str, float] = {}
_idle_reaper_started = False
TITLE_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "title_generation_prompt.txt"

# 项目存储限制常量也会被使用
PROJECT_MAX_STORAGE_BYTES = PROJECT_MAX_STORAGE_BYTES
PROJECT_MAX_STORAGE_MB = PROJECT_MAX_STORAGE_MB

__all__ = [
    "user_manager",
    "api_user_manager",
    "custom_tool_registry",
    "mcp_server_registry",
    "mcp_client_manager",
    "container_manager",
    "user_terminals",
    "terminal_rooms",
    "connection_users",
    "HOST_ACTIVE_WORKSPACE_LOCK",
    "HOST_ACTIVE_WORKSPACE_ID",
    "HOST_ACTIVE_WORKSPACE_PATH",
    "HOST_ACTIVE_WORKSPACE_VERSION",
    "stop_flags",
    "MONITOR_FILE_TOOLS",
    "MONITOR_MEMORY_TOOLS",
    "MONITOR_SNAPSHOT_CHAR_LIMIT",
    "MONITOR_MEMORY_ENTRY_LIMIT",
    "RATE_LIMIT_BUCKETS",
    "FAILURE_TRACKERS",
    "pending_socket_tokens",
    "usage_trackers",
    "active_login_nonces",
    "tool_approval_manager",
    "user_question_manager",
    "plan_approval_manager",
    "MONITOR_SNAPSHOT_CACHE",
    "MONITOR_SNAPSHOT_CACHE_LIMIT",
    "PROJECT_STORAGE_CACHE",
    "PROJECT_STORAGE_CACHE_TTL_SECONDS",
    "DEFAULT_PORT",
    "CSRF_HEADER_NAME",
    "CSRF_SESSION_KEY",
    "CSRF_SAFE_METHODS",
    "CSRF_PROTECTED_PATHS",
    "CSRF_PROTECTED_PREFIXES",
    "CSRF_EXEMPT_PATHS",
    "FAILED_LOGIN_LIMIT",
    "FAILED_LOGIN_LOCK_SECONDS",
    "SOCKET_TOKEN_TTL_SECONDS",
    "USER_IDLE_TIMEOUT_SECONDS",
    "LAST_ACTIVE_FILE",
    "_last_active_lock",
    "_last_active_cache",
    "_idle_reaper_started",
    "TITLE_PROMPT_PATH",
    "PROJECT_MAX_STORAGE_BYTES",
    "PROJECT_MAX_STORAGE_MB",
    "RECENT_UPLOAD_EVENT_LIMIT",
    "RECENT_UPLOAD_FEED_LIMIT",
    "make_stop_keys",
    "get_stop_flag",
    "set_stop_flag",
    "clear_stop_flag",
    "get_last_active_ts",
]


def get_last_active_ts(username: str, fallback: Optional[float] = None) -> Optional[float]:
    """
    返回最近活跃时间，优先使用缓存；当容器句柄中的时间更新、更晚时，自动刷新缓存。
    这样避免“缓存过旧导致刚触碰的容器被立即回收”的问题。
    """
    fallback_val: Optional[float]
    try:
        fallback_val = float(fallback) if fallback is not None else None
    except (TypeError, ValueError):
        fallback_val = None

    with _last_active_lock:
        cached = _last_active_cache.get(username)
        try:
            cached_val = float(cached) if cached is not None else None
        except (TypeError, ValueError):
            cached_val = None

        # 若没有缓存，或句柄时间更新、更晚，则刷新缓存
        if cached_val is None:
            if fallback_val is not None:
                _last_active_cache[username] = fallback_val
            return fallback_val

        if fallback_val is not None and fallback_val > cached_val:
            _last_active_cache[username] = fallback_val
            return fallback_val
    
        return cached_val


# ====== 停止标志辅助 ======
# 语义说明（2026-08 并行对话修复）：
# - 任务级 key（client_sid，REST 任务里即 task_id）是每个任务停止状态的唯一真相；
#   各任务 entry 相互独立，运行期检查只查任务级 key（include_user=False），
#   保证同一用户多对话并行运行时互不影响。
# - 用户级 key（user:{username}）仅作为 socket 链路（connect 恢复映射 / disconnect /
#   stop_task）定位“该用户最近任务”的索引，不参与任务运行期停止判定。
def make_stop_keys(client_sid: Optional[str] = None, username: Optional[str] = None):
    keys = []
    if client_sid:
        keys.append(client_sid)
    if username:
        keys.append(f"user:{username}")
    return keys


def set_stop_flag(client_sid: Optional[str], username: Optional[str], entry: Dict[str, Any]):
    for k in make_stop_keys(client_sid, username):
        stop_flags[k] = entry


def get_stop_flag(client_sid: Optional[str], username: Optional[str], include_user: bool = True) -> Optional[Dict[str, Any]]:
    keys = [client_sid] if client_sid else []
    if include_user and username:
        keys.append(f"user:{username}")
    for k in keys:
        val = stop_flags.get(k)
        if val:
            return val
    return None


def clear_stop_flag(client_sid: Optional[str], username: Optional[str]):
    # 只清理本任务自己的 key；user 级索引仅在仍指向本任务 entry 时才清理，
    # 避免误清其他并行任务的索引。
    entry = stop_flags.pop(client_sid, None) if client_sid else None
    if username and entry is not None:
        user_key = f"user:{username}"
        if stop_flags.get(user_key) is entry:
            stop_flags.pop(user_key, None)
