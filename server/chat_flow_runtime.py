from __future__ import annotations

import asyncio
import json
import time
import re
import zipfile
from collections import defaultdict, Counter, deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from werkzeug.utils import secure_filename

from config import (
    OUTPUT_FORMATS,
    AUTO_FIX_TOOL_CALL,
    AUTO_FIX_MAX_ATTEMPTS,
    MAX_ITERATIONS_PER_TASK,
    MAX_CONSECUTIVE_SAME_TOOL,
    MAX_TOTAL_TOOL_CALLS,
    TOOL_CALL_COOLDOWN,
    MAX_UPLOAD_SIZE,
    DEFAULT_CONVERSATIONS_LIMIT,
    MAX_CONVERSATIONS_LIMIT,
    CONVERSATIONS_DIR,
    DEFAULT_RESPONSE_MAX_TOKENS,
    DEFAULT_PROJECT_PATH,
    LOGS_DIR,
    AGENT_VERSION,
    PROJECT_MAX_STORAGE_MB,
    PROJECT_MAX_STORAGE_BYTES,
    UPLOAD_SCAN_LOG_SUBDIR,
)
from modules.personalization_manager import (
    load_personalization_config,
    save_personalization_config,
)
from modules.upload_security import UploadSecurityError
from modules.user_manager import UserWorkspace
from modules.usage_tracker import QUOTA_DEFAULTS
from core.web_terminal import WebTerminal
from utils.tool_result_formatter import format_tool_result_for_context
from utils.conversation_manager import ConversationManager
from config.model_profiles import get_model_context_window, get_model_profile

from .auth_helpers import api_login_required, resolve_admin_policy, get_current_user_record, get_current_username
from .context import with_terminal, get_gui_manager, get_upload_guard, build_upload_error_response, ensure_conversation_loaded, reset_system_state, get_user_resources, get_or_create_usage_tracker
from .utils_common import (
    build_review_lines,
    debug_log,
    log_backend_chunk,
    log_frontend_chunk,
    log_streaming_debug_entry,
    brief_log,
    DEBUG_LOG_FILE,
    CHUNK_BACKEND_LOG_FILE,
    CHUNK_FRONTEND_LOG_FILE,
    STREAMING_DEBUG_LOG_FILE,
)
from .security import rate_limited, format_tool_result_notice, compact_web_search_result, consume_socket_token, prune_socket_tokens, validate_csrf_request, requires_csrf_protection, get_csrf_token
from .monitor import cache_monitor_snapshot, get_cached_monitor_snapshot
from .extensions import socketio
from .state import (
    MONITOR_FILE_TOOLS,
    MONITOR_MEMORY_TOOLS,
    MONITOR_SNAPSHOT_CHAR_LIMIT,
    MONITOR_MEMORY_ENTRY_LIMIT,
    RATE_LIMIT_BUCKETS,
    FAILURE_TRACKERS,
    pending_socket_tokens,
    usage_trackers,
    MONITOR_SNAPSHOT_CACHE,
    MONITOR_SNAPSHOT_CACHE_LIMIT,
    PROJECT_STORAGE_CACHE,
    PROJECT_STORAGE_CACHE_TTL_SECONDS,
    RECENT_UPLOAD_EVENT_LIMIT,
    RECENT_UPLOAD_FEED_LIMIT,
    TITLE_PROMPT_PATH,
    get_last_active_ts,
    user_manager,
    container_manager,
    custom_tool_registry,
    user_terminals,
    terminal_rooms,
    connection_users,
    stop_flags,
    get_stop_flag,
    set_stop_flag,
    clear_stop_flag,
)
from .chat_flow_helpers import (
    detect_malformed_tool_call as _detect_malformed_tool_call,
    detect_tool_failure,
    generate_conversation_title_background as _generate_conversation_title_background,
)


from .chat_flow_runner_helpers import (
    extract_intent_from_partial,
    resolve_monitor_path,
    resolve_monitor_memory,
    capture_monitor_snapshot,
)


def generate_conversation_title_background(web_terminal: WebTerminal, conversation_id: str, user_message: str, username: str):
    return _generate_conversation_title_background(
        web_terminal=web_terminal,
        conversation_id=conversation_id,
        user_message=user_message,
        username=username,
        socketio_instance=socketio,
        title_prompt_path=TITLE_PROMPT_PATH,
        debug_logger=debug_log,
    )

def detect_malformed_tool_call(text):
    return _detect_malformed_tool_call(text)
