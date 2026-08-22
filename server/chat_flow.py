from __future__ import annotations
import sys
import os

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import asyncio
import json
import time
import re
import zipfile
from collections import defaultdict, Counter, deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

from flask import Blueprint, request, jsonify, session
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
from modules.sub_agent.state import TERMINAL_STATUSES as SUB_AGENT_TERMINAL_STATUSES
from modules.upload_security import UploadSecurityError
from modules.user_manager import UserWorkspace
from modules.usage_tracker import QUOTA_DEFAULTS
from core.web_terminal import WebTerminal
from utils.tool_result_formatter import format_tool_result_for_context
from utils.conversation_manager import ConversationManager
from config.model_profiles import get_model_context_window, get_model_profile

from .auth_helpers import api_login_required, resolve_admin_policy, get_current_user_record, get_current_username
from .context import with_terminal, get_gui_manager, get_upload_guard, build_upload_error_response, ensure_conversation_loaded, reset_system_state, get_user_resources, get_or_create_usage_tracker
from .work_timer import finalize_conversation_work_timer
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
from .main_task_gate import acquire_adopted_main_task_gate, release_main_task_gate
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
from .chat_flow_runner import handle_task_with_sender


conversation_bp = Blueprint('conversation', __name__)


def generate_conversation_title_background(web_terminal: WebTerminal, conversation_id: str, user_message: str, username: str):
    """在后台生成对话标题并更新索引、推送给前端。"""
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


def process_message_task(terminal: WebTerminal, message: str, images, sender, client_sid, workspace: UserWorkspace, username: str, videos=None, files=None, main_task_gate_token: Optional[str] = None):
    """在后台处理消息任务"""
    videos = videos or []
    files = files or []
    auto_user_message_event = bool(getattr(terminal, "_auto_user_message_event", False))

    # 对话级主任务门闸（平行时空防护）：同一对话同一时刻只允许一个主任务写对话历史。
    # 通知链任务由轮询器预占门闸并移交 token（见 chat_flow_task_main 的
    # poll_completion_notifications）；其余入口竞争获取。所有路径统一 finally 释放。
    gate_token = acquire_adopted_main_task_gate(terminal, main_task_gate_token)
    if gate_token is None:
        conversation_id = getattr(getattr(terminal, "context_manager", None), "current_conversation_id", None)
        debug_log(f"[MainTaskGate] 拒绝并发主任务: conv={conversation_id} client_sid={client_sid}")
        sender('error', {
            'message': '当前对话已有任务在运行，请稍后再试。',
            'conversation_id': conversation_id,
            'task_id': getattr(terminal, "task_id", None) or client_sid,
            'client_sid': client_sid,
        })
        return

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # 创建可取消的任务
        task = loop.create_task(
            handle_task_with_sender(
                terminal,
                workspace,
                message,
                images,
                sender,
                client_sid,
                username,
                videos,
                auto_user_message_event=auto_user_message_event,
                files=files,
            )
        )
        
        # 每个任务使用独立的停止 entry，不复用用户级索引中的旧 entry。
        # 这样同一用户的多个对话并行运行时，取消/停止只精确作用于本任务，
        # 不会通过共享 entry 互相覆盖 task/loop 引用或广播 stop 标志。
        entry = {'stop': False, 'task': task, 'terminal': terminal, 'loop': loop}
        set_stop_flag(client_sid, username, entry)
        
        try:
            loop.run_until_complete(task)
        except asyncio.CancelledError:
            debug_log(f"[ChatFlow] 任务被成功取消: client_sid={client_sid}")
            # 检测是否仍有后台任务在跑，通知前端保持停止按钮
            has_running_sub_agents = False
            has_running_background_commands = False
            conversation_id = getattr(getattr(terminal, 'context_manager', None), 'current_conversation_id', None)
            if conversation_id:
                sub_agent_manager = getattr(terminal, 'sub_agent_manager', None)
                if sub_agent_manager:
                    try:
                        sub_agent_manager.reconcile_task_states(conversation_id=conversation_id)
                        for task_info in sub_agent_manager.tasks.values():
                            if task_info.get('conversation_id') != conversation_id:
                                continue
                            status = task_info.get('status')
                            if status not in SUB_AGENT_TERMINAL_STATUSES.union({"terminated"}):
                                has_running_sub_agents = True
                                break
                    except Exception as exc:
                        debug_log(f"[Task] 取消时检查后台子智能体失败: {exc}")
                bg_manager = getattr(terminal, 'background_command_manager', None)
                if bg_manager:
                    try:
                        bg_manager.reconcile_stale_records(conversation_id=conversation_id)
                        waiting_items = bg_manager.list_waiting_items(conversation_id)
                        if waiting_items:
                            has_running_background_commands = True
                    except Exception as exc:
                        debug_log(f"[Task] 取消时检查后台命令失败: {exc}")
            debug_log(
                f"[ChatFlow] 任务取消，最终停止事件由 _run_chat_task 发送: client_sid={client_sid}, "
                f"has_running_sub_agents={has_running_sub_agents}, "
                f"has_running_background_commands={has_running_background_commands}"
            )
            # 取消时：仅当对话真正空闲（没有后台子智能体/后台命令还在跑）
            # 才把 work_timer 标记为完成，否则后台工作继续期间计时器应保持运行。
            try:
                if terminal and conversation_id:
                    finalized = finalize_conversation_work_timer(
                        terminal,
                        conversation_id,
                        finished_at=datetime.now().isoformat(),
                    )
            except Exception as exc:
                debug_log(f"[ChatFlow] 取消时标记 work_timer 完成失败: {exc}")
            # task_stopped 事件统一在 _run_chat_task finally 中发送，避免重复
            reset_system_state(terminal)

        loop.close()
    except Exception as e:
        # 【新增】错误时确保对话状态不丢失
        try:
            if terminal and terminal.context_manager:
                # 尝试保存当前对话状态
                terminal.context_manager.auto_save_conversation()
                debug_log("错误恢复：对话状态已保存")
        except Exception as save_error:
            debug_log(f"错误恢复：保存对话状态失败: {save_error}")
        
        # 原有的错误处理逻辑
        debug_log(f"任务处理错误: {e}")
        import traceback
        traceback.print_exc()
        sender('error', {
            'message': str(e),
            'conversation_id': getattr(getattr(terminal, "context_manager", None), "current_conversation_id", None),
            'task_id': getattr(terminal, "task_id", None) or client_sid,
            'client_sid': client_sid
        })
        sender('task_complete', {
            'total_iterations': 0,
            'total_tool_calls': 0,
            'auto_fix_attempts': 0,
            'error': str(e)
        })

    finally:
        # 清理任务引用
        clear_stop_flag(client_sid, username)
        # 释放对话级主任务门闸（仅持有者可释放，重复调用为无操作）
        release_main_task_gate(terminal, gate_token)

# === 统一对外入口 ===
def start_chat_task(terminal, message: str, images: Any, sender, client_sid: str, workspace, username: str, videos: Any = None):
    """在线程模式下启动对话任务，供 Socket 事件调用。"""
    return socketio.start_background_task(
        process_message_task,
        terminal,
        message,
        images,
        sender,
        client_sid,
        workspace,
        username,
        videos
    )


def run_chat_task_sync(terminal, message: str, images: Any, sender, client_sid: str, workspace, username: str, videos: Any = None, files: Any = None, main_task_gate_token: Optional[str] = None):
    """同步执行（测试/CLI 使用）。"""
    return process_message_task(terminal, message, images, sender, client_sid, workspace, username, videos, files, main_task_gate_token=main_task_gate_token)
