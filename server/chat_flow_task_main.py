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
    resolve_context_compression_settings,
)
from modules.skill_hint_manager import SkillHintManager
from modules.i18n import tr
from modules.upload_security import UploadSecurityError
from modules.user_manager import UserWorkspace
from modules.usage_tracker import QUOTA_DEFAULTS
from modules.sub_agent import TERMINAL_STATUSES
from modules.multi_agent.debug_logger import ma_debug
from modules.versioning_manager import ConversationVersioningManager, VersioningError
from modules.shallow_versioning import ShallowVersioningManager
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
from .security import rate_limited, compact_web_search_result, consume_socket_token, prune_socket_tokens, validate_csrf_request, requires_csrf_protection, get_csrf_token
from .main_task_gate import try_acquire_main_task_gate, release_main_task_gate
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
    active_polling_tasks,
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


from .chat_flow_runtime import (
    generate_conversation_title_background,
    detect_malformed_tool_call,
)

from .chat_flow_task_support import (
    process_sub_agent_updates,
    process_background_command_updates,
    process_multi_agent_master_messages,
    inject_multi_agent_master_message,
    inject_runtime_user_message,
    _auto_message_type_for_multi_agent_subtype,
)
from .chat_flow_tool_loop import execute_tool_calls
from .chat_flow_stream_loop import run_streaming_attempts
from .deep_compression import run_deep_compression
from .goal_flow import (
    maybe_start_goal,
    inject_goal_prompt,
    handle_goal_after_turn,
    stop_goal_user_cancel,
    goal_is_active,
    emit_goal_progress,
)

_VALID_USER_MESSAGE_SOURCES = {
    "user",
    "guidance",
    "notify",
    "presend",
    "sub_agent",
    "background_command",
    "goal",
    "goal_prompt",
    "goal_review",
    "compression",
    "compression_handoff",
    "permission",
    "sandbox",
    "skill",
    "workflow",
}


def _user_message_ui_defaults(source: str, *, auto_user_message_event: bool = False) -> Dict[str, Any]:
    normalized = str(source or "").strip().lower()
    if normalized in {"skill", "goal_prompt", "permission", "sandbox"}:
        return {"visibility": "hidden", "starts_work": False}
    if normalized in {"guidance", "notify"}:
        return {"visibility": "compact", "starts_work": False}
    # 后台完成通知 / 运行期压缩续接：本质属于当前这轮工作的延续，
    # 不应暂停上一轮计时器、也不应开启新的工作头与计时器。
    if normalized in {"sub_agent", "background_command", "compression", "compression_handoff"}:
        return {"visibility": "compact", "starts_work": False}
    # 目标审核续命：确实开启新一轮工作，保持 starts_work=True。
    if normalized == "goal_review":
        return {"visibility": "compact", "starts_work": True}
    # 工作流激活/柔性通知（任务入口派发）：开启新一轮工作，显示工作头与计时器。
    # 运行期 inline 注入走 _runtime_message_ui_defaults（延续当前工作段，不受影响）。
    if normalized == "workflow":
        return {"visibility": "chat", "starts_work": True}
    if normalized == "presend":
        return {"visibility": "chat", "starts_work": True}
    if normalized == "goal":
        return {"visibility": "compact", "starts_work": True}
    return {"visibility": "chat", "starts_work": normalized == "user" and not auto_user_message_event}


def _extract_media_path(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("path") or "").strip()
    return str(item or "").strip()


def _build_media_paths_message(images: Optional[List[Any]], videos: Optional[List[Any]]) -> Optional[str]:
    """为本次消息附带的图片/视频生成一条 hidden user 消息文本。"""
    paths: List[str] = []
    for item in images or []:
        path = _extract_media_path(item)
        if path and path not in paths:
            paths.append(path)
    for item in videos or []:
        path = _extract_media_path(item)
        if path and path not in paths:
            paths.append(path)
    if not paths:
        return None
    lines = ["[系统通知|media_paths]", "本次消息附带的媒体文件路径："]
    for path in paths:
        lines.append(f"- `{path}`")
    return "\n".join(lines)


def _normalize_attached_files(files: Optional[List[Any]]) -> List[str]:
    """归一化消息附加文件：仅保留字符串路径，去重，最多 9 个。"""
    normalized: List[str] = []
    for item in files or []:
        if not isinstance(item, str):
            continue
        path = item.strip()
        if not path or path in normalized:
            continue
        normalized.append(path)
        if len(normalized) >= 9:
            break
    return normalized


def _build_file_paths_message(files: Optional[List[Any]]) -> Optional[str]:
    """为本次消息附带的文件生成一条 hidden user 消息文本（供模型感知文件位置，界面不渲染）。"""
    paths = _normalize_attached_files(files)
    if not paths:
        return None
    lines = ["[系统通知|file_paths]", "本次消息附带的文件路径："]
    for path in paths:
        lines.append(f"- `{path}`")
    return "\n".join(lines)


def _should_skip_versioning_for_message(
    *,
    message: str,
    auto_user_message_event: bool,
) -> bool:
    if auto_user_message_event:
        return True
    text = (message or "").strip()
    if not text:
        return True
    if text.startswith("这是一句系统自动发送的user消息"):
        return True
    return False


def _prepare_hidden_versioning_baseline_for_first_input(
    *,
    web_terminal,
    workspace,
    conversation_id: str,
    message: str,
    auto_user_message_event: bool,
) -> None:
    """Ensure first-run baseline is committed (hidden, no checkpoint row)."""
    try:
        if _should_skip_versioning_for_message(
            message=message,
            auto_user_message_event=auto_user_message_event,
        ):
            return
        cm = getattr(getattr(web_terminal, "context_manager", None), "conversation_manager", None)
        if not cm:
            return
        conv_data = cm.load_conversation(conversation_id) or {}
        versioning_meta = ((conv_data.get("metadata") or {}).get("versioning") or {})
        if not bool(versioning_meta.get("enabled", False)):
            return
        backup_mode = str(versioning_meta.get("backup_mode") or "shallow").strip().lower()
        if backup_mode == "full":
            tracking_mode = ConversationVersioningManager.normalize_tracking_mode(
                versioning_meta.get("tracking_mode")
            )
            if (
                tracking_mode == ConversationVersioningManager.TRACKING_MODE_WORKSPACE_AND_CONVERSATION
                and not bool(getattr(web_terminal, "username", None) == "host")
            ):
                return
            manager = ConversationVersioningManager(
                project_path=workspace.project_path,
                data_dir=workspace.data_dir,
                conversation_id=conversation_id,
            )
            baseline = manager.ensure_baseline_for_first_input(tracking_mode=tracking_mode)
            debug_log(
                f"[Versioning][Baseline] conv={conversation_id} "
                f"tracking_mode={tracking_mode} "
                f"created={baseline.get('created')} skipped={baseline.get('skipped')} "
                f"reason={baseline.get('reason')} head={baseline.get('head')}"
            )
        # shallow 模式下不需要完整 workspace baseline，由工具调用前的 track_edit 处理
    except VersioningError as exc:
        debug_log(f"[Versioning] 创建首轮基线失败: {exc}")
    except Exception as exc:
        debug_log(f"[Versioning] 创建首轮基线异常: {exc}")


def _record_hidden_versioning_checkpoint_after_run(
    *,
    web_terminal,
    workspace,
    conversation_id: str,
    message: str,
    message_index: int,
    auto_user_message_event: bool,
    run_status: str = "completed",
) -> None:
    """Hidden snapshot after current manual user input run finished."""
    try:
        if _should_skip_versioning_for_message(
            message=message,
            auto_user_message_event=auto_user_message_event,
        ):
            return
        cm = getattr(getattr(web_terminal, "context_manager", None), "conversation_manager", None)
        if not cm:
            return
        conv_data = cm.load_conversation(conversation_id) or {}
        versioning_meta = ((conv_data.get("metadata") or {}).get("versioning") or {})
        if not bool(versioning_meta.get("enabled", False)):
            return
        tracking_mode = ConversationVersioningManager.normalize_tracking_mode(
            versioning_meta.get("tracking_mode")
        )
        if (
            tracking_mode == ConversationVersioningManager.TRACKING_MODE_WORKSPACE_AND_CONVERSATION
            and not bool(getattr(web_terminal, "username", None) == "host")
        ):
            return
        snapshot_messages = conv_data.get("messages") or []
        # 找到当前用户消息的消息ID（用于浅备份快照）
        message_id = None
        try:
            target_msg = snapshot_messages[int(message_index)] if 0 <= int(message_index) < len(snapshot_messages) else None
            if target_msg and target_msg.get("role") == "user":
                message_id = target_msg.get("message_id") or target_msg.get("id")
        except Exception:
            message_id = None

        backup_mode = str(versioning_meta.get("backup_mode") or "shallow").strip().lower()
        if backup_mode == "full":
            snapshot_payload = {
                "conversation_id": conversation_id,
                "title": conv_data.get("title"),
                "metadata": conv_data.get("metadata") or {},
                "messages": snapshot_messages,
                "message_index": int(message_index),
                "run_status": str(run_status or "completed"),
            }
            manager = ConversationVersioningManager(
                project_path=workspace.project_path,
                data_dir=workspace.data_dir,
                conversation_id=conversation_id,
            )
            row = manager.create_checkpoint(
                message=message,
                message_index=message_index,
                workspace_path=str(workspace.project_path),
                conversation_snapshot=snapshot_payload,
                run_status=str(run_status or "completed"),
                tracking_mode=tracking_mode,
            )
            debug_log(
                f"[Versioning][Checkpoint] conv={conversation_id} seq={row.get('seq')} "
                f"tracking_mode={tracking_mode} "
                f"msg_index={message_index} snapshot_messages={len(snapshot_messages)} "
                f"tree_hash={row.get('tree_hash')} changed={row.get('changed')} status={row.get('run_status')}"
            )
            cm.update_conversation_metadata(
                conversation_id,
                {
                    "versioning": {
                        "enabled": True,
                        "mode": "overwrite",
                        "tracking_mode": tracking_mode,
                        "backup_mode": "full",
                        "last_commit": row.get("tree_hash"),
                        "last_input_seq": int(row.get("seq") or 0),
                        "updated_at": datetime.now().isoformat(),
                    }
                },
            )
        else:
            # 浅备份：每个用户消息结束时对 tracked files 做快照
            shallow_files: List[Dict[str, Any]] = []
            shallow_insertions = 0
            shallow_deletions = 0
            if message_id:
                shallow_manager = ShallowVersioningManager(
                    project_path=workspace.project_path,
                    data_dir=workspace.data_dir,
                    conversation_id=conversation_id,
                )
                snapshot = shallow_manager.make_snapshot(str(message_id))
                shallow_files = shallow_manager.get_file_diff_stats(str(message_id))
                stats = shallow_manager.get_diff_stats(str(message_id)) or {}
                shallow_insertions = int(stats.get("insertions") or 0)
                shallow_deletions = int(stats.get("deletions") or 0)
                debug_log(
                    f"[Versioning][ShallowSnapshot] conv={conversation_id} "
                    f"msg_id={message_id} tracked_files={snapshot.get('tracked_files_count')}"
                )

            # 同时写入一个轻量 ConversationVersioningManager 检查点行，
            # 让前端版本管理弹窗能统一列出回溯点。
            manager = ConversationVersioningManager(
                project_path=workspace.project_path,
                data_dir=workspace.data_dir,
                conversation_id=conversation_id,
            )
            snapshot_payload = {
                "conversation_id": conversation_id,
                "title": conv_data.get("title"),
                "metadata": conv_data.get("metadata") or {},
                "messages": snapshot_messages,
                "message_index": int(message_index),
                "run_status": str(run_status or "completed"),
                "shallow_message_id": str(message_id) if message_id else None,
            }
            row = manager.create_checkpoint(
                message=message,
                message_index=int(message_index),
                workspace_path=str(workspace.project_path),
                conversation_snapshot=snapshot_payload,
                run_status=str(run_status or "completed"),
                tracking_mode=tracking_mode,
                diff_summary={
                    "insertions": shallow_insertions,
                    "deletions": shallow_deletions,
                    "files_changed": len(shallow_files),
                    "files": shallow_files,
                },
            )
            debug_log(
                f"[Versioning][ShallowCheckpoint] conv={conversation_id} seq={row.get('seq')} "
                f"msg_index={message_index} files={len(shallow_files)} "
                f"insertions={shallow_insertions} deletions={shallow_deletions}"
            )
            cm.update_conversation_metadata(
                conversation_id,
                {
                    "versioning": {
                        "enabled": True,
                        "mode": "overwrite",
                        "tracking_mode": tracking_mode,
                        "backup_mode": "shallow",
                        "last_commit": row.get("tree_hash"),
                        "last_input_seq": int(row.get("seq") or 0),
                        "updated_at": datetime.now().isoformat(),
                    }
                },
            )
    except VersioningError as exc:
        debug_log(f"[Versioning] 记录快照失败: {exc}")
    except Exception as exc:
        debug_log(f"[Versioning] 记录快照异常: {exc}")
    finally:
        # 当前输入处理结束，清除浅备份消息 ID，避免后续无消息上下文时的错误归属。
        try:
            ctx = getattr(web_terminal, "context_manager", None)
            if ctx is not None and hasattr(ctx, "current_shallow_message_id"):
                ctx.current_shallow_message_id = None
        except Exception:
            pass


def _persist_and_echo_preceding_notice(
    *,
    web_terminal,
    sender,
    conversation_id: str,
    message: str,
    payload: Dict[str, Any],
) -> None:
    """预写一条「前置完成通知」：写入对话历史 + socketio 回显（不触发新一轮工作）。

    用于「通知池」一次取出多条时，前 N-1 条随同一后续任务一起呈现。
    这些通知必须落历史，否则模型看不到、刷新后也会丢失。
    """
    message = (message or "").strip()
    if not message:
        return
    src = str(payload.get("message_source") or "sub_agent").strip().lower()
    if src not in _VALID_USER_MESSAGE_SOURCES:
        src = "sub_agent"
    ui_defaults = _user_message_ui_defaults(src, auto_user_message_event=True)
    # 前置通知渲染为完成态气泡：不开启独立工作计时器
    ui_defaults = {**ui_defaults, "starts_work": False}
    metadata = {
        "message_source": src,
        "is_auto_generated": True,
        "auto_message_type": "completion_notice",
        **ui_defaults,
    }
    try:
        cm = getattr(web_terminal, "context_manager", None)
        if cm is not None:
            cm.add_conversation("user", message, metadata=metadata)
    except Exception as exc:
        debug_log(f"[CompletionNotice] 前置通知写入历史失败: {exc}")
    # socketio 回显（在线客户端即时可见）；轮询客户端由后续任务事件流回放，
    # 前端按消息内容 dedup，两条通道不会双显。
    try:
        echo_payload = {
            "message": message,
            "conversation_id": conversation_id,
            "message_source": src,
            **ui_defaults,
            **{k: v for k, v in (payload or {}).items() if k not in {"message", "conversation_id"}},
        }
        echo_payload["starts_work"] = False
        echo_payload["metadata"] = {**metadata}
        sender("user_message", echo_payload)
    except Exception:
        pass

async def _dispatch_completion_user_notice(
    *,
    web_terminal,
    workspace,
    sender,
    client_sid: str,
    username: str,
    conversation_id: str,
    user_message: str,
    extra_payload: Optional[Dict[str, Any]] = None,
    preceding_notices: Optional[List[Dict[str, Any]]] = None,
    main_task_gate_token: Optional[str] = None,
):
    """复用子智能体完成后的 user 代发机制。

    preceding_notices：本批「通知池」里除最后一条以外的前置通知列表，
    每项为 {"message": str, "payload": dict}。这些会先于触发消息写入历史/事件流，
    但不各自触发新一轮工作；仅最后一条 user_message 作为后续任务的触发消息。
    """
    extra_payload = extra_payload or {}
    preceding_notices = preceding_notices or []
    message_source = str(extra_payload.get("message_source") or "").strip().lower()
    if not message_source:
        if extra_payload.get("runtime_mode_notice"):
            message_source = "notify"
        elif extra_payload.get("runtime_guidance"):
            message_source = "guidance"
        elif extra_payload.get("background_command_notice"):
            message_source = "background_command"
        elif extra_payload.get("sub_agent_notice"):
            message_source = "sub_agent"
    if message_source not in _VALID_USER_MESSAGE_SOURCES:
        message_source = "user"

    # 先把前置通知写入历史并 socketio 回显（在线客户端即时可见）。
    # 轮询客户端则通过后续任务事件流回放（见 _run_chat_task 的 preceding_user_notices 注入）。
    for item in preceding_notices:
        _persist_and_echo_preceding_notice(
            web_terminal=web_terminal,
            sender=sender,
            conversation_id=conversation_id,
            message=str(item.get("message") or ""),
            payload=dict(item.get("payload") or {}),
        )

    try:
        from .tasks import task_manager
        workspace_id = getattr(workspace, "workspace_id", None) or "default"
        host_mode = bool(getattr(workspace, "username", None) == "host")
        session_data = {
            "username": username,
            "role": getattr(web_terminal, "user_role", "user"),
            "is_api_user": getattr(web_terminal, "user_role", "") == "api",
            "host_mode": host_mode,
            "host_workspace_id": workspace_id if host_mode else None,
            "workspace_id": workspace_id,
            "run_mode": getattr(web_terminal, "run_mode", None),
            "thinking_mode": getattr(web_terminal, "thinking_mode", None),
            "model_key": getattr(web_terminal, "model_key", None),
            "message_source": message_source,
        }
        # 派发方预占的主任务门闸 token 随任务移交，由任务线程认领（见 process_message_task）
        if main_task_gate_token:
            session_data["main_task_gate_token"] = main_task_gate_token
        ui_defaults = _user_message_ui_defaults(
            message_source,
            auto_user_message_event=True,
        )
        # 关键：通知类后台任务需要把 user_message 写入任务事件流，
        # 否则前端轮询只会看到 AI/tool 事件，看不到 user_message。
        session_data["auto_user_message_event"] = True
        session_data["auto_user_message_payload"] = {
            **dict(extra_payload or {}),
            **ui_defaults,
            "timestamp": datetime.now().isoformat(),
        }
        # 轮询客户端通过后续任务事件流回放前置通知（在线客户端已由上面的 socketio 回显覆盖）。
        if preceding_notices:
            session_data["preceding_user_notices"] = [
                {
                    "message": str(item.get("message") or ""),
                    "payload": dict(item.get("payload") or {}),
                }
                for item in preceding_notices
                if str(item.get("message") or "").strip()
            ]
        ma_debug(
            "dispatch_completion_create_chat_task",
            conversation_id=conversation_id,
            user_message_preview=user_message[:300],
            preceding_count=len(preceding_notices),
        )
        rec = task_manager.create_chat_task(
            username,
            workspace_id,
            user_message,
            [],
            conversation_id,
            model_key=session_data.get("model_key"),
            thinking_mode=session_data.get("thinking_mode"),
            run_mode=session_data.get("run_mode"),
            session_data=session_data,
        )
        ma_debug(
            "dispatch_completion_task_created",
            conversation_id=conversation_id,
            task_id=getattr(rec, "task_id", None),
        )
        payload = {
            'message': user_message,
            'conversation_id': conversation_id,
            'task_id': rec.task_id,
            'message_source': message_source,
            'timestamp': datetime.now().isoformat(),
            **ui_defaults,
        }
        payload.update(extra_payload)
        payload["metadata"] = {
            "message_source": message_source,
            **ui_defaults,
            **(payload.get("metadata") or {}),
        }
        sender('user_message', payload)
        return
    except Exception as e:
        debug_log(f"[CompletionNotice] 创建后台消息任务失败，回退直接执行: {e}")

    payload = {
        'message': user_message,
        'conversation_id': conversation_id,
        'message_source': message_source,
        'timestamp': datetime.now().isoformat(),
    }
    payload.update(_user_message_ui_defaults(message_source, auto_user_message_event=True))
    payload.update(extra_payload)
    payload["metadata"] = {
        "message_source": message_source,
        "visibility": payload.get("visibility"),
        "starts_work": payload.get("starts_work"),
        **(payload.get("metadata") or {}),
    }
    sender('user_message', payload)
    try:
        task_handle = asyncio.create_task(handle_task_with_sender(
            terminal=web_terminal,
            workspace=workspace,
            message=user_message,
            images=[],
            sender=sender,
            client_sid=client_sid,
            username=username,
            videos=[],
            auto_user_message_event=True,
        ))
        await task_handle
    except Exception as inner_exc:
        debug_log(f"[CompletionNotice] 回退处理 user_message 失败: {inner_exc}")


def _build_shared_waiting_payload(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """构建与子智能体一致的 waiting 事件载荷结构。"""
    normalized = []
    for item in items or []:
        summary = item.get('summary') or item.get('command') or '后台任务'
        normalized.append({
            'agent_id': item.get('agent_id') or item.get('command_id') or item.get('task_id'),
            'summary': summary,
        })
    return {
        'count': len(normalized),
        'tasks': normalized,
    }

def _build_sub_agent_notice_text(update: Dict[str, Any], *, summary_override: Optional[str] = None) -> str:
    """根据子智能体完成结果构建一条 user 通知文本（统一 [系统通知|sub_agent] 前缀）。

    update 通常是 task 的 final_result，其中 system_message 已经是一条完整的格式化通知
    （含 ✅/❌ 前缀、统计、运行秒数、summary、交付目录）。这里直接复用它，只补统一前缀，
    不再在外层重复拼 agent/运行秒数/交付目录，避免内容出现两遍。
    """
    system_message = (update.get("system_message") or "").strip()
    if not system_message:
        # 兜底：极少数情况下 final_result 没有 system_message，用最小信息拼一条
        agent_id = update.get("agent_id")
        summary = summary_override or update.get("summary") or update.get("message") or ""
        deliverables_dir = update.get("deliverables_dir", "") or ""
        lines = [tr("task_main.sub_agent_done_line", agent_id=agent_id, summary=summary)]
        msg = update.get("message")
        if msg:
            lines.append(str(msg))
        if deliverables_dir:
            lines.append(tr("task_main.deliverables_line", dir=deliverables_dir))
        system_message = "\n\n".join(lines)
    return f"[系统通知|sub_agent]\n{system_message}"


def _collect_pending_completion_notices(*, web_terminal, conversation_id: str) -> List[Dict[str, Any]]:
    """从子智能体 + 后台 run_command 两路统一取出所有待通知项（已按时间排序）。

    返回的每一项形如：
        {
            "kind": "sub_agent" | "background_command",
            "message": <user 通知文本>,
            "payload": <前端事件 extra payload>,
            "sort_key": <用于跨两路排序的时间戳>,
        }
    取出时即就地标记 notified，确保不会被下一轮重复消费。
    """
    notices: List[Dict[str, Any]] = []

    # 1) 子智能体
    sub_manager = getattr(web_terminal, "sub_agent_manager", None)
    if sub_manager:
        if not hasattr(web_terminal, "_announced_sub_agent_tasks"):
            web_terminal._announced_sub_agent_tasks = set()
        # 关键：先 reconcile 刷新真实状态，然后**直接遍历 tasks** 找终态+未通知的任务。
        # 不依赖 poll_updates() —— 它只返回「本次调用里恰好 running→terminal」的跃迁项，
        # 任务一旦被提前 reconcile 成 completed，跃迁就丢了，poll_updates 永远返回空。
        try:
            sub_manager.reconcile_task_states(conversation_id=conversation_id)
        except Exception:
            pass
        candidates = [
            t for t in sub_manager.tasks.values()
            if isinstance(t, dict)
            and t.get("conversation_id") == conversation_id
            and t.get("status") in TERMINAL_STATUSES  # completed/failed/timeout（不含 terminated）
            and not t.get("notified")
            and t.get("task_id") not in web_terminal._announced_sub_agent_tasks
            and not t.get("multi_agent_mode")  # 多智能体任务走独立注入路径，不在这里发传统通知
        ]
        candidates.sort(key=lambda t: t.get("updated_at") or t.get("created_at") or 0)
        for task_info in candidates:
            task_id = task_info.get("task_id")
            final_result = task_info.get("final_result")
            if not isinstance(final_result, dict):
                # 兜底：状态已是终态但 final_result 缺失，主动取一次
                try:
                    final_result = sub_manager._check_task_status(task_info)
                except Exception:
                    pass
            if not isinstance(final_result, dict):
                final_result = {}
            # 就地标记已通知
            if task_id:
                web_terminal._announced_sub_agent_tasks.add(task_id)
            task_info["notified"] = True
            task_info["updated_at"] = time.time()

            agent_id = task_info.get("agent_id")
            summary = task_info.get("summary") or ""
            notice_text = _build_sub_agent_notice_text(
                final_result,
                summary_override=summary,
            )
            notices.append({
                "kind": "sub_agent",
                "message": notice_text,
                "payload": {
                    "sub_agent_notice": True,
                    "message_source": "sub_agent",
                    "agent_id": agent_id,
                    "task_id": task_id,
                },
                "sort_key": task_info.get("updated_at") or task_info.get("created_at") or time.time(),
            })
        try:
            sub_manager._save_state()
        except Exception:
            pass

    # 2) 后台 run_command
    bg_manager = getattr(web_terminal, "background_command_manager", None)
    if bg_manager:
        try:
            bg_updates = bg_manager.poll_updates(conversation_id=conversation_id)
        except Exception:
            bg_updates = []
        for update in bg_updates or []:
            command_id = update.get("command_id")
            command = update.get("command") or ""
            output = update.get("output") or ""
            return_code = update.get("return_code")
            try:
                bg_manager.mark_notified(str(command_id))
            except Exception:
                pass
            # 统一 [系统通知|background_command] 前缀；正文给出命令、退出码、输出
            header = tr("background_tasks.done_header")
            if command:
                header += "\n\n" + tr("background_tasks.command_line", command=command)
            if return_code is not None:
                header += "\n" + tr("background_tasks.return_code_line", code=return_code)
            body = output if output else "[no_output]"
            message_text = f"[系统通知|background_command]\n{header}\n\n{tr('background_tasks.output_section')}\n{body}"
            notices.append({
                "kind": "background_command",
                "message": message_text,
                "payload": {
                    # 与子智能体完成通知完全复用同一前端通道/处理逻辑
                    "sub_agent_notice": True,
                    "message_source": "background_command",
                    "background_command_notice": True,
                    "command_id": command_id,
                },
                "sort_key": update.get("updated_at") or time.time(),
            })

    # 3) 工作流柔性通知（撞限询问 / 用户 slash 退出等）
    try:
        from modules.workflow_state_manager import WorkflowStateManager

        _wsm = WorkflowStateManager(web_terminal.data_dir, conversation_id)
        wf_updates = _wsm.poll_notices()
    except Exception:
        wf_updates = []
        _wsm = None
    for notice in wf_updates or []:
        notice_text = str((notice or {}).get("message") or "").strip()
        if not notice_text:
            continue
        notices.append({
            "kind": "workflow",
            "message": notice_text,
            "payload": {
                "sub_agent_notice": True,
                "message_source": "workflow",
                "workflow_notice": True,
                "conversation_id": conversation_id,
            },
            "sort_key": notice.get("created_at") or time.time(),
        })

    notices.sort(key=lambda item: item.get("sort_key") or 0)
    return notices


def _rollback_completion_notice_marks(*, web_terminal, notices: List[Dict[str, Any]]) -> None:
    """派发失败时回滚 _collect_pending_completion_notices 的就地标记，
    避免通知被标记为已消费却从未派发（静默丢失）。
    """
    sub_manager = getattr(web_terminal, "sub_agent_manager", None)
    announced = getattr(web_terminal, "_announced_sub_agent_tasks", set())
    bg_manager = getattr(web_terminal, "background_command_manager", None)
    wf_restore: List[Dict[str, Any]] = []
    for item in notices or []:
        payload = item.get("payload") or {}
        if item.get("kind") == "sub_agent":
            task_id = payload.get("task_id")
            if not task_id:
                continue
            announced.discard(task_id)
            task_info = sub_manager.tasks.get(task_id) if sub_manager else None
            if isinstance(task_info, dict):
                task_info["notified"] = False
        elif item.get("kind") == "background_command":
            command_id = payload.get("command_id")
            if not command_id or not bg_manager:
                continue
            try:
                with bg_manager._lock:
                    rec = bg_manager._records.get(str(command_id))
                    if rec:
                        rec["notified"] = False
            except Exception:
                pass
        elif item.get("kind") == "workflow":
            wf_restore.append(item)
    # 工作流通知：按 conversation 分组统一放回池（restore_notices 会保留现有未消费项）
    if wf_restore:
        try:
            from modules.workflow_state_manager import WorkflowStateManager

            _groups: Dict[str, List[Dict[str, Any]]] = {}
            for item in wf_restore:
                cid = str((item.get("payload") or {}).get("conversation_id") or "")
                if not cid:
                    continue
                _groups.setdefault(cid, []).append({
                    "type": "workflow",
                    "message": item.get("message") or "",
                    "created_at": item.get("sort_key") or time.time(),
                })
            for cid, items in _groups.items():
                WorkflowStateManager(web_terminal.data_dir, cid).restore_notices(items)
        except Exception:
            pass
    if sub_manager:
        try:
            sub_manager._save_state()
        except Exception:
            pass


def _has_pending_completion_work(*, web_terminal, conversation_id: str) -> bool:
    """是否还有运行中或待通知的传统后台任务（子智能体/后台 run_command）。

    多智能体模式下的 pending 消息由独立的 poll_multi_agent_notifications 处理，
    不再混入本函数。
    """
    sub_manager = getattr(web_terminal, "sub_agent_manager", None)
    if sub_manager:
        announced = getattr(web_terminal, "_announced_sub_agent_tasks", set())
        has_running_non_ma = False
        has_unnotified_non_ma = False
        for task in sub_manager.tasks.values():
            if not isinstance(task, dict):
                continue
            if task.get("conversation_id") != conversation_id:
                continue
            status = task.get("status")
            multi_agent_flag = task.get("multi_agent_mode") or False
            if status not in TERMINAL_STATUSES.union({"terminated"}):
                if not multi_agent_flag and task.get("run_in_background"):
                    has_running_non_ma = True
                continue
            if not multi_agent_flag and task.get("run_in_background") and (task.get("task_id") not in announced) and not task.get("notified"):
                has_unnotified_non_ma = True
        if has_running_non_ma or has_unnotified_non_ma:
            return True
    bg_manager = getattr(web_terminal, "background_command_manager", None)
    if bg_manager:
        try:
            if bg_manager.has_pending_for_conversation(conversation_id):
                return True
        except Exception:
            pass
    return False


def _has_pending_workflow_notices(*, web_terminal, conversation_id: str) -> bool:
    """是否还有未消费的工作流柔性通知（撞限询问 / 用户退出等）。"""
    try:
        from modules.workflow_state_manager import WorkflowStateManager

        return WorkflowStateManager(web_terminal.data_dir, conversation_id).has_pending_notices()
    except Exception:
        return False


async def poll_completion_notifications(*, web_terminal, workspace, conversation_id, client_sid, username):
    """统一的“通知池”轮询器。

    把子智能体完成通知与后台 run_command 完成通知合并到同一条链路：
    - 每轮把两路所有待通知项一次性取出（池化），按时间排序；
    - 这一批里前 N-1 条作为「预写通知」随同一个后续任务一起注入（写历史 + 事件流 + socketio），
      不各自触发新一轮工作；
    - 仅最后 1 条作为后续 chat task 的触发消息，启动一次「工作 → 停止」循环；
    - 该后续任务结束后回到 handle_task_with_sender 结尾重新 spawn 本轮询器，继续消费剩余通知。

    这样多个后台任务同时完成时，会被合并成一次（而非每条都触发一轮停止-再工作）。
    """
    from .extensions import socketio

    sub_manager = getattr(web_terminal, "sub_agent_manager", None)
    bg_manager = getattr(web_terminal, "background_command_manager", None)
    if not sub_manager and not bg_manager:
        return
    if not hasattr(web_terminal, "_announced_sub_agent_tasks"):
        web_terminal._announced_sub_agent_tasks = set()

    max_wait_time = 3600  # 最多等待 1 小时
    start_wait = time.time()

    def sender(event_type, data):
        try:
            socketio.emit(event_type, data, room=f"user_{username}")
        except Exception:
            pass

    loop_count = 0
    ma_debug("poll_completion_notifications_start", conversation_id=conversation_id)
    while (time.time() - start_wait) < max_wait_time:
        loop_count += 1
        # 检查停止标志
        client_stop_info = get_stop_flag(client_sid, username, include_user=False)
        if client_stop_info:
            stop_requested = client_stop_info.get('stop', False) if isinstance(client_stop_info, dict) else client_stop_info
            if stop_requested:
                ma_debug("poll_completion_notifications_stop_requested", conversation_id=conversation_id)
                break

        # 若主对话仍在工具循环中，暂不消费完成事件，避免抢占 system 消息插入
        if getattr(web_terminal, "_tool_loop_active", False):
            ma_debug("poll_completion_notifications_wait_tool_loop", conversation_id=conversation_id)
            await asyncio.sleep(1)
            continue

        # 多智能体模式：主对话任务仍在运行时，由主循环自己消费 pending 消息
        if getattr(web_terminal, "_multi_agent_main_task_active", False):
            ma_debug("poll_completion_notifications_wait_main_task", conversation_id=conversation_id)
            await asyncio.sleep(1)
            continue

        # 主任务门闸：仅当对话没有正在运行的主任务时才预占门闸。
        # 防止通知任务与主任务并发交叉写入对话历史（2026-08-12 平行时空事故）。
        # 预占成功后排发：门闸 token 随 session_data 移交给新任务线程认领释放；
        # 排发失败则释放门闸并回滚通知标记，下轮重新收集。
        gate_token = try_acquire_main_task_gate(web_terminal)
        if gate_token is None:
            ma_debug("poll_completion_notifications_wait_main_task_gate", conversation_id=conversation_id)
            await asyncio.sleep(1)
            continue

        ma_debug("poll_completion_notifications_collect", conversation_id=conversation_id, loop_count=loop_count)
        notices = _collect_pending_completion_notices(
            web_terminal=web_terminal,
            conversation_id=conversation_id,
        )
        ma_debug("poll_completion_notifications_collected", conversation_id=conversation_id, notice_count=len(notices))

        if not notices:
            # 本轮没有待通知项：释放门闸后按原逻辑判断退出或继续
            release_main_task_gate(web_terminal, gate_token)

        if notices:
            # 取出后剩余是否还有未完成/未通知的后台工作（决定前端是否保持等待态）
            has_remaining = _has_pending_completion_work(
                web_terminal=web_terminal,
                conversation_id=conversation_id,
            )

            preceding = notices[:-1]
            last_notice = notices[-1]

            # 前 N-1 条预写通知：随后续任务注入历史/事件流，不单独触发工作
            preceding_payload: List[Dict[str, Any]] = []
            for item in preceding:
                payload = dict(item.get("payload") or {})
                payload["starts_work"] = False  # 预写通知渲染为完成态气泡，不起独立计时器
                preceding_payload.append({
                    "message": item.get("message") or "",
                    "payload": payload,
                })

            last_payload = dict(last_notice.get("payload") or {})
            last_payload.update({
                "has_running_sub_agents": has_remaining,
                "has_running_background_commands": has_remaining,
                "remaining_count": 1 if has_remaining else 0,
            })

            try:
                await _dispatch_completion_user_notice(
                    web_terminal=web_terminal,
                    workspace=workspace,
                    sender=sender,
                    client_sid=client_sid,
                    username=username,
                    conversation_id=conversation_id,
                    user_message=last_notice.get("message") or "",
                    extra_payload=last_payload,
                    preceding_notices=preceding_payload,
                    main_task_gate_token=gate_token,
                )
            except Exception as exc:
                # 派发失败：释放门闸 + 回滚通知标记，下轮轮询重新收集，杜绝静默丢失
                debug_log(f"[CompletionPoll] 派发完成通知失败，释放门闸并回滚标记: {exc}")
                release_main_task_gate(web_terminal, gate_token)
                _rollback_completion_notice_marks(web_terminal=web_terminal, notices=notices)
                ma_debug("poll_completion_notifications_dispatch_failed", conversation_id=conversation_id, error=str(exc))
                await asyncio.sleep(5)
                continue

            # 本轮已派发一个后续任务，门闸随任务移交（由任务线程 finally 释放），
            # 由该任务结束后重新 spawn 本轮询器继续消费剩余通知
            return

        # 没有待通知项：若也没有运行中的后台工作或工作流待通知，结束轮询
        pending = _has_pending_completion_work(
            web_terminal=web_terminal, conversation_id=conversation_id
        ) or _has_pending_workflow_notices(
            web_terminal=web_terminal, conversation_id=conversation_id
        )
        if not pending:
            break

        await asyncio.sleep(5)


async def poll_multi_agent_notifications(*, web_terminal, workspace, conversation_id, client_sid, username):
    """多智能体模式专用通知池。

    只在主任务结束后启动，负责两件事：
    1. 若主任务已空闲且 MultiAgentState 中有 pending_master_messages，
       一次性取出并触发新一轮主任务工作——即使仍有 running 子智能体也立即派发
       （主对话空闲时消息池有消息就该全部插入，设计中属情况2）。
    2. 若 pending 为空且仍有 running 实例，循环等待下一次 push 新输出。

    重要：旧实现要求所有 running 实例退出再 drain，导致 ask_master 阻塞中的
    子智能体永远等不到主对话回答而超时——现已修复为池优先。

    与 poll_completion_notifications 完全分离，避免多智能体消息和传统后台
    通知竞争 task_manager 的单工作区互斥。
    """
    from .extensions import socketio

    max_wait_time = 3600
    start_wait = time.time()

    def sender(event_type, data):
        try:
            socketio.emit(event_type, data, room=f"user_{username}")
        except Exception:
            pass

    import threading as _threading
    ma_debug(
        "poll_ma_notifications_start",
        conversation_id=conversation_id,
        thread_id=_threading.get_ident(),
        multi_agent_mode=getattr(web_terminal, "multi_agent_mode", False),
        main_task_active_at_start=getattr(web_terminal, "_multi_agent_main_task_active", False),
    )

    tick_count = 0
    while (time.time() - start_wait) < max_wait_time:
        tick_count += 1
        # 检查停止标志
        client_stop_info = get_stop_flag(client_sid, username, include_user=False)
        if client_stop_info:
            stop_requested = client_stop_info.get('stop', False) if isinstance(client_stop_info, dict) else client_stop_info
            if stop_requested:
                ma_debug("poll_ma_notifications_stop_requested", conversation_id=conversation_id, tick=tick_count)
                break

        manager = getattr(web_terminal, "sub_agent_manager", None)
        if not manager:
            ma_debug("poll_ma_no_manager", conversation_id=conversation_id, tick=tick_count)
            break

        state = manager.get_multi_agent_state(conversation_id)
        if not state:
            ma_debug("poll_ma_no_state", conversation_id=conversation_id, tick=tick_count)
            break

        # 主任务又被唤醒了（用户发送新消息等），让主循环自己处理，本通知池退出
        if getattr(web_terminal, "_multi_agent_main_task_active", False):
            ma_debug("poll_ma_notifications_main_task_active", conversation_id=conversation_id, tick=tick_count)
            break

        # 状态快照
        all_insts = state.list_all()
        statuses = [a.status for a in all_insts]
        pending_count_now = len(state.pending_master_messages)
        main_active = getattr(web_terminal, "_multi_agent_main_task_active", False)
        ma_debug(
            "poll_ma_tick",
            conversation_id=conversation_id,
            tick=tick_count,
            instance_count=len(all_insts),
            statuses=statuses,
            pending_count=pending_count_now,
            main_active=main_active,
        )

        # 1) 先检查 pending 池——主对话空闲时只要池里有消息就立即派发（情况2）。
        if state.has_pending_master_messages():
            pending = state.drain_master_messages()
            ma_debug(
                "poll_ma_notifications_dispatch",
                conversation_id=conversation_id,
                count=len(pending),
                running_count=len([a for a in all_insts if a.status == "running"]),
                previews=[str(m)[:200] for m in pending],
                tick=tick_count,
            )
            try:
                await _dispatch_multi_agent_idle_messages(
                    web_terminal=web_terminal,
                    workspace=workspace,
                    sender=sender,
                    client_sid=client_sid,
                    username=username,
                    conversation_id=conversation_id,
                    messages=pending,
                )
                ma_debug("poll_ma_dispatch_success", conversation_id=conversation_id, count=len(pending), tick=tick_count)
            except Exception as exc:
                debug_log(f"[MultiAgent] 分发 idle 消息失败: {exc}")
                ma_debug(
                    "poll_ma_notifications_dispatch_error",
                    conversation_id=conversation_id,
                    error=str(exc),
                    error_type=type(exc).__name__,
                    tick=tick_count,
                )
            # 分发后退出，由新任务结束后再决定是否继续轮询
            return

        # 2) 没有 pending 消息：检查还有哪些子智能体在运行，决定退出还是继续等
        running_agents = [a for a in all_insts if a.status == "running"]
        if not running_agents:
            ma_debug("poll_ma_notifications_no_work_end", conversation_id=conversation_id, tick=tick_count)
            break

        ma_debug(
            "poll_ma_notifications_wait_running",
            conversation_id=conversation_id,
            running_count=len(running_agents),
            tick=tick_count,
        )
        await asyncio.sleep(0.5)

    ma_debug("poll_ma_notifications_end", conversation_id=conversation_id, tick=tick_count)

    # poll 退出时，如果主智能体也空闲且没有 pending 消息，发送 task_complete 通知前端对话变空闲。
    # 否则前端会一直保持 taskInProgress=true，MultiAgentTaskProbe 会持续轮询但找不到任务。
    if not getattr(web_terminal, "_multi_agent_main_task_active", False):
        state = manager.get_multi_agent_state(conversation_id) if manager else None
        still_running = state and any(a.status == "running" for a in state.list_all())
        still_pending = state and state.has_pending_master_messages()
        if not still_running and not still_pending:
            sender('task_complete', {
                'total_iterations': 0,
                'total_tool_calls': 0,
                'auto_fix_attempts': 0,
                'has_running_sub_agents': False,
                'has_running_background_commands': False,
                'has_running_multi_agent': False,
                'pending_runtime_guidance_messages': [],
                'multi_agent_idle_signal': True,
            })
            ma_debug("poll_ma_idle_signal_sent", conversation_id=conversation_id, tick=tick_count)


async def _dispatch_multi_agent_idle_messages(
    *,
    web_terminal,
    workspace,
    sender,
    client_sid,
    username,
    conversation_id,
    messages: List[str],
):
    """主智能体空闲时，把多智能体 pending 消息作为新一轮用户消息分发。

    所有消息先持久化到对话历史；前 N-1 条作为前置通知通过 socketio 推送；
    最后一条创建 task_type="notice" 任务触发 Team Leader 新一轮工作。
    """
    ma_debug(
        "dispatch_ma_idle_enter",
        conversation_id=conversation_id,
        count=len(messages) if messages else 0,
        previews=[str(m)[:150] for m in (messages or [])],
    )
    if not messages:
        ma_debug("dispatch_ma_idle_no_messages", conversation_id=conversation_id)
        return

    from modules.multi_agent.state import parse_multi_agent_message
    from .tasks import task_manager

    parsed_messages = []
    for msg_text in messages:
        parsed = parse_multi_agent_message(msg_text) or {}
        parsed_messages.append({
            "text": msg_text,
            "display_name": parsed.get("display_name"),
            "subtype": parsed.get("subtype"),
        })

    # 1) 前置通知全部写入历史 + emit给在线客户端（未后一条会由后续任务自己持久化，
    #    避免重复产生两条相同 user 消息导致刷新后被渲染两遍）。
    ma_debug(
        "dispatch_ma_idle_preceding",
        conversation_id=conversation_id,
        preceding_count=max(0, len(parsed_messages) - 1),
    )
    for item in parsed_messages[:-1]:
        inject_multi_agent_master_message(
            web_terminal=web_terminal,
            messages=None,
            text=item["text"],
            sender=sender,
            conversation_id=conversation_id,
            inline=False,
        )

    # 1.5) 最后一条只 emit 给在线客户端，不在这里持久化（后续 task handle_task_with_sender
    #      会以这条文本作为 message 创建任务，并写入历史，从而避免重复写入。）
    last = parsed_messages[-1]
    message_source = "sub_agent"
    last_emit_payload = {
        "message": last["text"],
        "content": last["text"],
        "conversation_id": conversation_id,
        "message_source": message_source,
        "sub_agent_notice": True,
        "multi_agent_message": True,
        "multi_agent_display_name": last["display_name"],
        "multi_agent_subtype": last["subtype"],
        "auto_message_type": _auto_message_type_for_multi_agent_subtype(last["subtype"]),
        "visibility": "chat",
        "starts_work": False,
        "metadata": {
            "message_source": message_source,
            "auto_message_type": _auto_message_type_for_multi_agent_subtype(last["subtype"]),
            "multi_agent_message": True,
            "multi_agent_display_name": last["display_name"],
            "multi_agent_subtype": last["subtype"],
            "visibility": "chat",
            "starts_work": False,
        },
        "timestamp": datetime.now().isoformat(),
    }
    try:
        sender("user_message", last_emit_payload)
        ma_debug(
            "dispatch_ma_idle_last_emit",
            conversation_id=conversation_id,
            message_preview=last["text"][:200],
        )
    except Exception as exc:
        ma_debug(
            "dispatch_ma_idle_last_emit_error",
            conversation_id=conversation_id,
            error=str(exc),
        )

    # 2) 前置通知的 socketio emit 已在上述注入 inject_multi_agent_master_message 内
    #    同时完成，避免在多智能体消息场景重复发送相同事件。

    # 3) 最后一条触发新一轮工作
    ui_defaults = dict(_user_message_ui_defaults(message_source, auto_user_message_event=True))
    ui_defaults["starts_work"] = False

    workspace_id = getattr(workspace, "workspace_id", None) or "default"
    host_mode = bool(getattr(workspace, "username", None) == "host")
    session_data = {
        "username": username,
        "role": getattr(web_terminal, "user_role", "user"),
        "is_api_user": getattr(web_terminal, "user_role", "") == "api",
        "host_mode": host_mode,
        "host_workspace_id": workspace_id if host_mode else None,
        "workspace_id": workspace_id,
        "run_mode": getattr(web_terminal, "run_mode", None),
        "thinking_mode": getattr(web_terminal, "thinking_mode", None),
        "model_key": getattr(web_terminal, "model_key", None),
        "message_source": message_source,
    }
    ma_auto_type = _auto_message_type_for_multi_agent_subtype(last["subtype"])
    # 重要：ui_defaults 默认会给出 visibility="compact"/starts_work=False，
    # 多智能体主消息是正常聊天消息，必须在 **ui_defaults 之后覆写为 chat，
    # 否则后端历史 metadata.visibility=compact 会让加载的消息走通知渲染。
    session_data["auto_user_message_event"] = True
    session_data["auto_user_message_payload"] = {
        **ui_defaults,
        "message_source": message_source,
        "sub_agent_notice": True,
        "multi_agent_message": True,
        "multi_agent_display_name": last["display_name"],
        "multi_agent_subtype": last["subtype"],
        "auto_message_type": ma_auto_type,
        "starts_work": False,
        "visibility": "chat",
        "timestamp": datetime.now().isoformat(),
    }
    ma_debug(
        "dispatch_ma_idle_before_create_task",
        conversation_id=conversation_id,
        last_text_preview=last["text"][:300],
        workspace_id=workspace_id,
        username=username,
    )
    session_data["auto_user_message_payload"].setdefault("metadata", {
        **ui_defaults,
        "message_source": message_source,
        "auto_message_type": ma_auto_type,
        "multi_agent_message": True,
        "multi_agent_display_name": last["display_name"],
        "multi_agent_subtype": last["subtype"],
        "starts_work": False,
        "visibility": "chat",
    })
    if len(parsed_messages) > 1:
        session_data["preceding_user_notices"] = [
            {
                "message": item["text"],
                "payload": {
                    "message_source": "sub_agent",
                    "sub_agent_notice": True,
                    "multi_agent_message": True,
                    "multi_agent_display_name": item["display_name"],
                    "multi_agent_subtype": item["subtype"],
                    "auto_message_type": _auto_message_type_for_multi_agent_subtype(item["subtype"]),
                    "visibility": "chat",
                    "starts_work": False,
                },
            }
            for item in parsed_messages[:-1]
        ]

    try:
        rec = task_manager.create_chat_task(
            username,
            workspace_id,
            last["text"],
            [],
            conversation_id,
            model_key=session_data.get("model_key"),
            thinking_mode=session_data.get("thinking_mode"),
            run_mode=session_data.get("run_mode"),
            session_data=session_data,
            task_type="notice",
        )
    except Exception as exc:
        ma_debug(
            "dispatch_ma_idle_create_task_exception",
            conversation_id=conversation_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        raise
    task_id_created = getattr(rec, "task_id", None)
    ma_debug("dispatch_ma_idle_task_created", conversation_id=conversation_id, task_id=task_id_created)

    payload = {
        "message": last["text"],
        "content": last["text"],
        "conversation_id": conversation_id,
        "task_id": getattr(rec, "task_id", None),
        "message_source": message_source,
        "sub_agent_notice": True,
        "multi_agent_message": True,
        "multi_agent_display_name": last["display_name"],
        "multi_agent_subtype": last["subtype"],
        "visibility": "chat",
        "starts_work": False,
        "auto_message_type": _auto_message_type_for_multi_agent_subtype(last["subtype"]),
        "metadata": {
            "message_source": message_source,
            "auto_message_type": _auto_message_type_for_multi_agent_subtype(last["subtype"]),
            "multi_agent_message": True,
            "multi_agent_display_name": last["display_name"],
            "multi_agent_subtype": last["subtype"],
            "visibility": "chat",
            "starts_work": False,
        },
        "timestamp": datetime.now().isoformat(),
    }
    sender("user_message", payload)
    ma_debug(
        "dispatch_ma_idle_sender_user_message",
        conversation_id=conversation_id,
        task_id=getattr(rec, "task_id", None),
        payload_message_preview=last["text"][:200],
    )
    ma_debug(
        "dispatch_ma_idle_exit_ok",
        conversation_id=conversation_id,
        count=len(parsed_messages),
        task_id=getattr(rec, "task_id", None),
    )


async def handle_task_with_sender(
    terminal: WebTerminal,
    workspace: UserWorkspace,
    message,
    images,
    sender,
    client_sid,
    username: str,
    videos=None,
    auto_user_message_event: bool = False,
    files=None,
):
    """处理任务并发送消息 - 集成token统计版本"""
    from .extensions import socketio

    web_terminal = terminal
    conversation_id = getattr(web_terminal.context_manager, "current_conversation_id", None)
    # 多智能体模式：标记主对话任务正在运行，供后台通知池判断是否可以安全消费
    if getattr(web_terminal, "multi_agent_mode", False):
        web_terminal._multi_agent_main_task_active = True
        manager = getattr(web_terminal, "sub_agent_manager", None)
        pending_count = 0
        if manager:
            state = manager.get_multi_agent_state(conversation_id)
            if state:
                pending_count = len(state.pending_master_messages)
        ma_debug(
            "handle_task_with_sender_start",
            conversation_id=conversation_id,
            terminal_id=id(web_terminal),
            manager_id=id(manager) if manager else None,
            message_preview=str(message)[:300] if message else None,
            pending_master_messages_count=pending_count,
        )
    videos = videos or []
    files = _normalize_attached_files(files)
    raw_sender = sender

    def sender(event_type, data):
        """为关键事件补充会话标识，便于前端定位报错归属。"""
        if not isinstance(data, dict):
            raw_sender(event_type, data)
            return
        payload = dict(data)
        current_conv = conversation_id or getattr(web_terminal.context_manager, "current_conversation_id", None)

        # 为所有事件添加 conversation_id，确保前端能正确匹配
        if current_conv and event_type not in {"connect", "disconnect", "system_ready"}:
            payload.setdefault("conversation_id", current_conv)

        # 调试信息：记录关键事件
        if event_type in {"user_message", "ai_message_start", "text_start", "text_chunk", "tool_preparing"}:
            debug_log(f"[SENDER] 发送事件: {event_type}, conversation_id={current_conv}, data_keys={list(payload.keys())}")

        # 为关键事件添加额外的标识信息
        if event_type in {"error", "quota_exceeded", "task_stopped", "task_complete"}:
            task_id = getattr(web_terminal, "task_id", None) or client_sid
            if task_id:
                payload.setdefault("task_id", task_id)
            if client_sid:
                payload.setdefault("client_sid", client_sid)

        raw_sender(event_type, payload)
    
    # 添加到对话历史
    user_work_started_at = datetime.now().isoformat()
    user_message_index = -1
    current_work_user_message_index = -1
    finalized_work_message_indices: set[int] = set()
    history_len_before = len(getattr(web_terminal.context_manager, "conversation_history", []) or [])
    is_first_user_message = history_len_before == 0
    # 构建 user 消息来源与 metadata
    source_from_terminal = str(getattr(web_terminal, "_current_user_message_source", "user") or "user").strip().lower()
    user_message_source = source_from_terminal or "user"
    multi_agent_meta: Dict[str, Any] = {}
    if auto_user_message_event:
        auto_payload = getattr(web_terminal, "_auto_user_message_payload", None)
        if isinstance(auto_payload, dict):
            src = str(auto_payload.get("message_source") or "").strip().lower()
            if src:
                user_message_source = src
            elif auto_payload.get("runtime_mode_notice"):
                user_message_source = "notify"
            elif auto_payload.get("runtime_guidance"):
                user_message_source = "guidance"
            elif auto_payload.get("background_command_notice"):
                user_message_source = "background_command"
            elif auto_payload.get("sub_agent_notice"):
                user_message_source = "sub_agent"
            # 保留多智能体元数据，避免前端把子智能体输出渲染成普通 user 消息
            if auto_payload.get("multi_agent_message"):
                multi_agent_meta["multi_agent_message"] = True
                multi_agent_meta["multi_agent_display_name"] = auto_payload.get("multi_agent_display_name")
                multi_agent_meta["multi_agent_subtype"] = auto_payload.get("multi_agent_subtype")
    if user_message_source not in _VALID_USER_MESSAGE_SOURCES:
        user_message_source = "user"

    # 构建user消息metadata
    user_message_metadata = {
        "message_source": user_message_source,
    }
    user_message_metadata.update(
        _user_message_ui_defaults(
            user_message_source,
            auto_user_message_event=bool(auto_user_message_event),
        )
    )
    if user_message_metadata.get("starts_work") is True:
        user_message_metadata["work_timer"] = {
            "status": "working",
            "started_at": user_work_started_at
        }
    # 如果是自动发送的user消息（子智能体/后台命令完成通知），添加标记
    if auto_user_message_event:
        user_message_metadata["is_auto_generated"] = True
        if multi_agent_meta.get("multi_agent_message"):
            # 多智能体消息：恢复专用 auto_message_type 与显示字段
            user_message_metadata["auto_message_type"] = _auto_message_type_for_multi_agent_subtype(
                multi_agent_meta.get("multi_agent_subtype")
            )
            user_message_metadata.update(multi_agent_meta)
            # 多智能体消息是正常聊天消息，走多智能体动态专用渲染，
            # 不应走 _user_message_ui_defaults 默认给出的 visibility="compact"（通知渲染）。
            user_message_metadata["visibility"] = "chat"
            # 多智能体消息触发的是主智能体对子智能体输出的续答，不应显示新的
            # assistant 回复头部（Astrion/工作时间），因此 starts_work=false。
            # 前端通过单独的 multi_agent_message 标记恢复轮询。
            user_message_metadata["starts_work"] = False
        else:
            user_message_metadata["auto_message_type"] = "completion_notice"
    # 附加文件路径记录在主消息 metadata 中：前端据此在气泡内渲染文件块，历史恢复同理
    if files:
        user_message_metadata["files"] = list(files)
    saved_user_message = web_terminal.context_manager.add_conversation(
        "user",
        message,
        images=images,
        videos=videos,
        metadata=user_message_metadata
    )
    # 为浅备份 track_edit 提供当前用户消息 ID，避免文件修改被归到上一个输入。
    try:
        web_terminal.context_manager.current_shallow_message_id = (
            saved_user_message.get("message_id") if isinstance(saved_user_message, dict) else None
        )
    except Exception:
        web_terminal.context_manager.current_shallow_message_id = None
    try:
        user_message_index = len(getattr(web_terminal.context_manager, "conversation_history", []) or []) - 1
    except Exception:
        user_message_index = -1
    current_work_user_message_index = user_message_index

    skill_context_messages = getattr(web_terminal, "_skill_context_messages", None)
    if not auto_user_message_event and isinstance(skill_context_messages, list):
        for skill_item in skill_context_messages:
            if not isinstance(skill_item, dict):
                continue
            skill_content = str(skill_item.get("content") or "")
            if not skill_content:
                continue
            skill_name = str(skill_item.get("name") or "").strip()
            skill_path = str(skill_item.get("path") or "").strip()
            web_terminal.context_manager.add_conversation(
                "user",
                f"[系统通知|skill]\n{skill_content}",
                metadata={
                    "source": "skill",
                    "message_source": "skill",
                    "visibility": "hidden",
                    "starts_work": False,
                    "hidden": True,
                    "skill_name": skill_name,
                    "skill_path": skill_path,
                }
            )
    if not auto_user_message_event:
        media_paths_message = _build_media_paths_message(images, videos)
        if media_paths_message:
            web_terminal.context_manager.add_conversation(
                "user",
                media_paths_message,
                metadata={
                    "source": "media_paths",
                    "message_source": "media_paths",
                    "visibility": "hidden",
                    "starts_work": False,
                    "hidden": True,
                }
            )
        file_paths_message = _build_file_paths_message(files)
        if file_paths_message:
            web_terminal.context_manager.add_conversation(
                "user",
                file_paths_message,
                metadata={
                    "source": "file_paths",
                    "message_source": "file_paths",
                    "visibility": "hidden",
                    "starts_work": False,
                    "hidden": True,
                }
            )
        try:
            sender(
                'user_message',
                {
                    "message": message,
                    "images": (saved_user_message or {}).get("images") or images or [],
                    "videos": (saved_user_message or {}).get("videos") or videos or [],
                    "media_refs": (saved_user_message or {}).get("media_refs") or [],
                    "message_source": user_message_source,
                    "visibility": user_message_metadata.get("visibility"),
                    "starts_work": user_message_metadata.get("starts_work"),
                    "metadata": user_message_metadata,
                    "conversation_id": conversation_id,
                    "timestamp": (saved_user_message or {}).get("timestamp") or user_work_started_at,
                },
            )
        except Exception as exc:
            debug_log(f"[TaskFlow] 发送 user_message 回显失败: {exc}")
        # 空闲期运行期模式（权限/执行环境/网络权限）切换的补发通知。
        # 仅真实用户消息（直接发送 / 提前输入队列 / 引导消息转换）触发，
        # 自动消息（子智能体/后台命令完成等触发的续答）不触发。
        # drift 检测比较「当前实际模式 vs 本对话已通知基线」，天然去重：
        # 空闲期反复切换只在最终值与基线不同时补发一遍；
        # 基线缺失（新对话/历史对话）时静默初始化，不补发。
        if user_message_source in {"user", "presend", "guidance"}:
            try:
                drift = (
                    web_terminal.collect_runtime_mode_drift()
                    if hasattr(web_terminal, "collect_runtime_mode_drift")
                    else []
                )
            except Exception as exc:
                drift = []
                debug_log(f"[RuntimeMode] 空闲期模式漂移检测失败: {exc}")
            for drift_item in drift:
                try:
                    notice_text = web_terminal.build_runtime_mode_switch_notice(
                        drift_item.get("kind"), drift_item.get("mode")
                    )
                    if not str(notice_text or "").strip():
                        continue
                    inject_runtime_user_message(
                        web_terminal=web_terminal,
                        messages=None,
                        text=notice_text,
                        source=str(drift_item.get("source") or "notify"),
                        sender=sender,
                        conversation_id=conversation_id,
                        inline=False,
                    )
                    web_terminal.update_runtime_mode_baseline(
                        {drift_item.get("kind"): drift_item.get("mode")}
                    )
                except Exception as exc:
                    debug_log(f"[RuntimeMode] 空闲期切换通知注入失败: {exc}")
    _prepare_hidden_versioning_baseline_for_first_input(
        web_terminal=web_terminal,
        workspace=workspace,
        conversation_id=conversation_id,
        message=message,
        auto_user_message_event=bool(auto_user_message_event),
    )

    def finalize_user_work_timer(index: Optional[int] = None):
        target_index = current_work_user_message_index if index is None else index
        if target_index in finalized_work_message_indices:
            return
        history = getattr(web_terminal.context_manager, "conversation_history", None) or []
        if target_index < 0 or target_index >= len(history):
            return
        target_msg = history[target_index] or {}
        if target_msg.get("role") != "user":
            return
        metadata = target_msg.get("metadata") or {}
        timer = metadata.get("work_timer")
        if not isinstance(timer, dict):
            return
        started_at = timer.get("started_at") or target_msg.get("timestamp") or user_work_started_at
        start_ts = None
        try:
            start_ts = datetime.fromisoformat(str(started_at).replace("Z", "+00:00")).timestamp()
        except Exception:
            start_ts = None
        now_ts = time.time()
        duration_ms = int(max(0.0, (now_ts - start_ts) * 1000.0)) if start_ts is not None else 0
        timer.update({
            "status": "completed",
            "started_at": started_at,
            "finished_at": datetime.now().isoformat(),
            "duration_ms": duration_ms
        })
        metadata["work_timer"] = timer
        target_msg["metadata"] = metadata
        history[target_index] = target_msg
        web_terminal.context_manager.auto_save_conversation(force=True)
        # 修改留痕收尾：写入完成时间；被 rm/移动的文件转为可恢复 diff（实时内容已在编辑时落盘）
        try:
            from modules.modify_history import finalize_modify_history_for_task
            finalize_modify_history_for_task(
                project_path=getattr(web_terminal, "project_path", None),
                data_dir=getattr(web_terminal, "data_dir", None),
                conversation_id=conversation_id,
                msg=target_msg,
                finished_at=timer.get("finished_at"),
            )
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(f"[modify_history] 收尾钩子执行失败: {exc}")
        finalized_work_message_indices.add(target_index)

    def switch_current_work_timer_to_latest_user():
        nonlocal current_work_user_message_index
        history = getattr(web_terminal.context_manager, "conversation_history", None) or []
        for idx in range(len(history) - 1, -1, -1):
            msg = history[idx] or {}
            if msg.get("role") == "user":
                metadata = msg.get("metadata") or {}
                if isinstance(metadata.get("work_timer"), dict):
                    current_work_user_message_index = idx
                    return

    versioning_checkpoint_recorded = False

    def finalize_run_versioning_checkpoint(run_status: str = "completed"):
        nonlocal versioning_checkpoint_recorded
        if versioning_checkpoint_recorded:
            return
        _record_hidden_versioning_checkpoint_after_run(
            web_terminal=web_terminal,
            workspace=workspace,
            conversation_id=conversation_id,
            message=message,
            message_index=user_message_index,
            auto_user_message_event=bool(auto_user_message_event),
            run_status=run_status,
        )
        versioning_checkpoint_recorded = True

    # Skill 提示系统：检测关键词并在用户消息之后插入 system 消息
    try:
        personal_config = load_personalization_config(workspace.data_dir)
        skill_hints_enabled = personal_config.get("skill_hints_enabled", False)

        if skill_hints_enabled and message:
            hint_manager = SkillHintManager()
            hint_manager.set_enabled(True)
            hint_messages = hint_manager.build_hint_messages(message)

            # 将提示消息插入到对话历史中（在用户消息之后）
            for hint_msg in hint_messages:
                debug_log(f"[Skill Hints] 插入提示消息: {hint_msg['content'][:100]}")
                web_terminal.context_manager.add_conversation(
                    "system",
                    hint_msg["content"]
                )
                # 验证插入后的消息
                last_msg = web_terminal.context_manager.conversation_history[-1]
                debug_log(f"[Skill Hints] 插入后验证 - role: {last_msg.get('role')}, content: {last_msg.get('content')[:100]}")
    except Exception as exc:
        debug_log(f"Skill hints 处理失败: {exc}")

    if is_first_user_message and getattr(web_terminal, "context_manager", None):
        try:
            personal_config = load_personalization_config(workspace.data_dir)
        except Exception:
            personal_config = {}
        auto_title_enabled = personal_config.get("auto_generate_title", True)
        skip_auto_title_generation = bool(
            (web_terminal.context_manager.conversation_metadata or {}).get("skip_auto_title_generation", False)
        )
        if auto_title_enabled and not skip_auto_title_generation:
            conv_id = getattr(web_terminal.context_manager, "current_conversation_id", None)
            socketio.start_background_task(
                generate_conversation_title_background,
                web_terminal,
                conv_id,
                message,
                username,
                personal_config.get("title_model", "")
            )

    # 自动深层压缩（用户输入后触发）
    try:
        personal_config = load_personalization_config(workspace.data_dir)
    except Exception:
        personal_config = {}
    compression_settings = resolve_context_compression_settings(personal_config)
    auto_deep_enabled = bool(personal_config.get("auto_deep_compress_enabled", False))
    current_tokens_for_deep = web_terminal.context_manager.get_current_context_tokens(conversation_id)
    if (
        auto_deep_enabled
        and current_tokens_for_deep > compression_settings["deep_trigger_tokens"]
        and not web_terminal.context_manager.is_compression_in_progress()
    ):
        web_terminal.context_manager._set_meta_flag("is_ultra_long_conversation", True)
        sender('compression_state', {
            "conversation_id": conversation_id,
            "in_progress": True,
            "mode": "auto",
            "stage": "queued"
        })
        deep_result = await run_deep_compression(
            web_terminal=web_terminal,
            workspace=workspace,
            conversation_id=conversation_id,
            mode="auto",
            sender=sender,
        )
        if not deep_result.get("success"):
            sender('error', {
                "message": deep_result.get("error") or tr("tool_loop.deep_compression_failed"),
                "conversation_id": conversation_id,
            })
        else:
            guide_message = (deep_result.get("guide_message") or "").strip()
            if guide_message:
                finalize_user_work_timer()
                finalize_run_versioning_checkpoint("auto_deep_compress_handoff")
                # 压缩后的续接引导语单独标记来源，使其以 compact 形式展示，
                # 而不是混入普通用户消息流。
                setattr(web_terminal, "_current_user_message_source", "compression_handoff")
                try:
                    await handle_task_with_sender(
                        web_terminal,
                        workspace,
                        guide_message,
                        [],
                        sender,
                        client_sid,
                        username,
                        []
                    )
                finally:
                    setattr(web_terminal, "_current_user_message_source", "user")
                return
        finalize_user_work_timer()
        finalize_run_versioning_checkpoint("auto_deep_compress_end")
        return
    
    # === 移除：不在这里计算输入token，改为在每次API调用前计算 ===
    
    # 构建上下文和消息（用于API调用）
    context = web_terminal.build_context()
    messages = web_terminal.build_messages(context, message)
    tools = web_terminal.define_tools()

    # === 工作流：真实用户消息到达时清零单步轮数（知情交互，撞限询问后可重新计数）===
    if user_message_source == "user":
        try:
            from modules.workflow_state_manager import WorkflowStateManager as _WorkflowStateManager
            _wf_mgr = _WorkflowStateManager(web_terminal.data_dir, conversation_id)
            if _wf_mgr.is_active():
                _wf_mgr.reset_stage_rounds()
        except Exception:
            pass

    # === 工作流：激活中的对话在任务开头广播一次进度快照进事件流。
    # REST activate 的 socketio 广播在轮询架构下到不了前端；事件流才是可靠通道。
    try:
        from server.workflow_flow import get_active_manager as _get_active_wfm, emit_workflow_progress as _emit_wf_progress
        _wsm_boot = _get_active_wfm(web_terminal.data_dir, conversation_id)
        if _wsm_boot is not None:
            _emit_wf_progress(wsm=_wsm_boot, sender=sender, conversation_id=conversation_id)
    except Exception as _wf_boot_exc:
        debug_log(f"[Workflow] 任务开头进度广播失败: {_wf_boot_exc}")

    # === 目标模式（Goal Mode）启动 / 续注入 ===
    # 1) 用户请求开启目标模式 → 无条件覆盖工作区旧目标状态并启动新目标。
    # 2) 工作区已存在活动目标（含压缩后重入、子智能体通知重入）→ 重新注入提示词，
    #    确保主模型在长运行/压缩后仍“知道自己处于目标模式”。
    try:
        if bool(getattr(web_terminal, "_goal_mode_requested", False)):
            maybe_start_goal(
                web_terminal=web_terminal,
                workspace=workspace,
                conversation_id=conversation_id,
                goal_text=message,
                current_tool_calls=0,
            )
            # 本标记只表示“本次用户显式开启目标模式”，写入对话级目标状态后
            # 必须立即消费掉：自动深层压缩会递归重入 handle_task_with_sender，
            # 若不消费，压缩引导语会被误当作新目标再次启动目标模式。
            setattr(web_terminal, "_goal_mode_requested", False)
        if goal_is_active(workspace, conversation_id):
            inject_goal_prompt(
                web_terminal=web_terminal,
                messages=messages,
                sender=sender,
                conversation_id=conversation_id,
            )
            emit_goal_progress(
                web_terminal=web_terminal,
                workspace=workspace,
                sender=sender,
                conversation_id=conversation_id,
                total_tool_calls=0,
            )
    except Exception as exc:
        debug_log(f"[Goal] 启动/注入失败: {exc}")

    try:
        profile = get_model_profile(getattr(web_terminal, "model_key", None))
        web_terminal.apply_model_profile(profile)
    except Exception as exc:
        debug_log(f"更新模型配置失败: {exc}")

    # === 上下文预算与安全校验（避免超出模型上下文） ===
    max_context_tokens = get_model_context_window(getattr(web_terminal, "model_key", None))
    current_tokens = web_terminal.context_manager.get_current_context_tokens(conversation_id)
    # 提前同步给底层客户端，动态收缩 max_tokens
    web_terminal.api_client.update_context_budget(current_tokens, max_context_tokens)
    if max_context_tokens:
        if current_tokens >= max_context_tokens:
            err_msg = tr(
                "task_main.context_overflow",
                current=current_tokens,
                max=max_context_tokens,
            )
            debug_log(err_msg)
            web_terminal.context_manager.add_conversation("system", err_msg)
            sender('error', {
                'message': err_msg,
                'status_code': 400,
                'error_type': 'context_overflow'
            })
            finalize_user_work_timer()
            finalize_run_versioning_checkpoint("context_overflow")
            return
        usage_percent = (current_tokens / max_context_tokens) * 100
        warned = web_terminal.context_manager.conversation_metadata.get("context_warning_sent", False)
        if usage_percent >= 70 and not warned:
            warn_msg = tr(
                "task_main.context_usage_hint",
                percent=f"{usage_percent:.1f}",
                current=current_tokens,
                max=max_context_tokens,
            )
            web_terminal.context_manager.conversation_metadata["context_warning_sent"] = True
            web_terminal.context_manager.auto_save_conversation(force=True)
            sender('context_warning', {
                'title': tr("task_main.context_overflow_title"),
                'message': warn_msg,
                'type': 'warning',
                'conversation_id': conversation_id
            })
    
    # 开始新的AI消息
    sender('ai_message_start', {})
    
    # 增量保存相关变量
    accumulated_response = ""   # 累积的响应内容
    is_first_iteration = True   # 是否是第一次迭代
    
    # 统计和限制变量
    total_iterations = 0
    total_tool_calls = 0
    # 行内引用：任务级已用来源累积（task_complete 时带给前端实时渲染），按 id 去重
    task_citations: Dict[str, dict] = {}
    consecutive_same_tool = defaultdict(int)
    last_tool_name = ""
    auto_fix_attempts = 0
    last_tool_call_time = 0
    detected_tool_intent: Dict[str, str] = {}
    # 目标模式：记录本“目标轮段”内主模型是否调用过工具（空转保护用）。
    # 每次续命注入后重置为 False，直到下一次 no-tool-call break。
    goal_segment_made_tool_call = False
    
    # 设置最大迭代次数（API 可覆盖）；None 表示不限制
    max_iterations_override = getattr(web_terminal, "max_iterations_override", None)
    max_iterations = max_iterations_override if max_iterations_override is not None else MAX_ITERATIONS_PER_TASK
    max_api_retries = 4
    retry_delay_seconds = 10
    

    iteration = 0
    while max_iterations is None or iteration < max_iterations:
        # 检查停止标志
        stop_entry = get_stop_flag(client_sid, username, include_user=False)
        if stop_entry and stop_entry.get('stop'):
            debug_log(f"[Task] 检测到停止标志，退出循环")
            try:
                stop_goal_user_cancel(
                    web_terminal=web_terminal,
                    workspace=workspace,
                    sender=sender,
                    conversation_id=conversation_id,
                    total_tool_calls=total_tool_calls,
                )
            except Exception as exc:
                debug_log(f"[Goal] 用户停止时停止目标失败: {exc}")
            # 计算后台任务状态，前端用这个字段决定是否保持对话运行态、输入区是否释放
            _stopped_has_bg = False
            _stopped_has_bg_cmd = False
            _stopped_has_ma = False
            _manager = getattr(web_terminal, "sub_agent_manager", None)
            if _manager:
                try:
                    _manager.reconcile_task_states(conversation_id=conversation_id)
                except Exception:
                    pass
                if getattr(web_terminal, "multi_agent_mode", False):
                    _state = _manager.get_multi_agent_state(conversation_id)
                    if _state:
                        _stopped_has_ma = any(a.status == "running" for a in _state.list_all())
                for _t in _manager.tasks.values():
                    if _t.get("conversation_id") != conversation_id:
                        continue
                    if _t.get("status") in TERMINAL_STATUSES.union({"terminated"}):
                        continue
                    if _t.get("multi_agent_mode"):
                        continue
                    _stopped_has_bg = True
                    break
            _bg_mgr = getattr(web_terminal, "background_command_manager", None)
            if _bg_mgr and conversation_id:
                try:
                    _bg_mgr.reconcile_stale_records(conversation_id=conversation_id)
                except Exception:
                    pass
                if _bg_mgr.list_waiting_items(conversation_id):
                    _stopped_has_bg_cmd = True
            sender('task_stopped', {
                'message': tr("task_main.task_stopped"),
                'reason': 'user_requested',
                'has_running_sub_agents': _stopped_has_bg,
                'has_running_background_commands': _stopped_has_bg_cmd,
                'has_running_multi_agent': _stopped_has_ma,
            })
            break

        current_iteration = iteration + 1
        iteration += 1
        total_iterations += 1
        iteration_limit_label = max_iterations if max_iterations is not None else "∞"
        debug_log(f"\n--- 迭代 {current_iteration}/{iteration_limit_label} 开始 ---")

        # === 工作流：单步轮数计数与撞限柔性通知 ===
        try:
            from modules.workflow_state_manager import WorkflowStateManager as _WorkflowStateManager2
            from server.workflow_flow import build_round_limit_notice as _build_round_limit_notice
            _wf_mgr2 = _WorkflowStateManager2(web_terminal.data_dir, conversation_id)
            if _wf_mgr2.is_active():
                _wf_mgr2.increment_stage_rounds()
                _wf_notice = _build_round_limit_notice(
                    data_dir=web_terminal.data_dir,
                    conversation_id=conversation_id,
                )
                if _wf_notice:
                    web_terminal.inject_runtime_user_message(
                        _wf_notice,
                        messages=messages,
                        source="workflow",
                        inline=True,
                    )
        except Exception as _wf_exc:
            debug_log(f"[Workflow] 轮数计数/撞限通知失败: {_wf_exc}")
        
        # 检查是否超过总工具调用限制
        if MAX_TOTAL_TOOL_CALLS is not None and total_tool_calls >= MAX_TOTAL_TOOL_CALLS:
            debug_log(f"已达到最大工具调用次数限制 ({MAX_TOTAL_TOOL_CALLS})")
            sender('system_message', {
                'content': tr("task_main.max_tool_calls_reached", limit=MAX_TOTAL_TOOL_CALLS)
            })
            break
        
        full_response = ""
        tool_calls = []
        current_thinking = ""
        detected_tools = {}
        last_usage_payload = None
        
        # 状态标志
        in_thinking = False
        thinking_started = False
        thinking_ended = False
        text_started = False
        text_has_content = False
        text_streaming = False
        text_chunk_index = 0
        last_text_chunk_time: Optional[float] = None
        
        # 计数器
        chunk_count = 0
        reasoning_chunks = 0
        content_chunks = 0
        tool_chunks = 0
        last_finish_reason = None

        thinking_expected = web_terminal.api_client.get_current_thinking_mode()
        debug_log(f"思考模式: {thinking_expected}")
        quota_allowed = True
        quota_info = {}
        if hasattr(web_terminal, "record_model_call"):
            quota_allowed, quota_info = web_terminal.record_model_call(bool(thinking_expected))
        if not quota_allowed:
            quota_type = 'thinking' if thinking_expected else 'fast'
            socketio.emit('quota_notice', {
                'type': quota_type,
                'reset_at': quota_info.get('reset_at'),
                'limit': quota_info.get('limit'),
                'count': quota_info.get('count')
            }, room=f"user_{getattr(web_terminal, 'username', '')}")
            sender('quota_exceeded', {
                'type': quota_type,
                'reset_at': quota_info.get('reset_at')
            })
            sender('error', {
                'message': tr("task_main.quota_exceeded"),
                'quota': quota_info
            })
            finalize_user_work_timer()
            finalize_run_versioning_checkpoint("quota_exceeded")
            return
        
        tool_call_limit_label = MAX_TOTAL_TOOL_CALLS if MAX_TOTAL_TOOL_CALLS is not None else "∞"
        debug_log(f"[API] 第{current_iteration}次调用 (总工具调用: {total_tool_calls}/{tool_call_limit_label})")
        
        stream_result = await run_streaming_attempts(
            web_terminal=web_terminal,
            messages=messages,
            tools=tools,
            sender=sender,
            client_sid=client_sid,
            username=username,
            conversation_id=conversation_id,
            current_iteration=current_iteration,
            max_api_retries=max_api_retries,
            retry_delay_seconds=retry_delay_seconds,
            detected_tool_intent=detected_tool_intent,
            full_response=full_response,
            tool_calls=tool_calls,
            current_thinking=current_thinking,
            detected_tools=detected_tools,
            last_usage_payload=last_usage_payload,
            in_thinking=in_thinking,
            thinking_started=thinking_started,
            thinking_ended=thinking_ended,
            text_started=text_started,
            text_has_content=text_has_content,
            text_streaming=text_streaming,
            text_chunk_index=text_chunk_index,
            last_text_chunk_time=last_text_chunk_time,
            chunk_count=chunk_count,
            reasoning_chunks=reasoning_chunks,
            content_chunks=content_chunks,
            tool_chunks=tool_chunks,
            last_finish_reason=last_finish_reason,
            accumulated_response=accumulated_response,
        )
        if stream_result.get("stopped"):
            finalize_user_work_timer()
            finalize_run_versioning_checkpoint("stopped")
            return

        full_response = stream_result["full_response"]
        tool_calls = stream_result["tool_calls"]
        current_thinking = stream_result["current_thinking"]
        detected_tools = stream_result["detected_tools"]
        last_usage_payload = stream_result["last_usage_payload"]
        in_thinking = stream_result["in_thinking"]
        thinking_started = stream_result["thinking_started"]
        thinking_ended = stream_result["thinking_ended"]
        text_started = stream_result["text_started"]
        text_has_content = stream_result["text_has_content"]
        text_streaming = stream_result["text_streaming"]
        text_chunk_index = stream_result["text_chunk_index"]
        last_text_chunk_time = stream_result["last_text_chunk_time"]
        chunk_count = stream_result["chunk_count"]
        reasoning_chunks = stream_result["reasoning_chunks"]
        content_chunks = stream_result["content_chunks"]
        tool_chunks = stream_result["tool_chunks"]
        last_finish_reason = stream_result["last_finish_reason"]
        accumulated_response = stream_result["accumulated_response"]

        # 流结束后的处理
        debug_log(f"\n流结束统计:")
        debug_log(f"  总chunks: {chunk_count}")
        debug_log(f"  思考chunks: {reasoning_chunks}")
        debug_log(f"  内容chunks: {content_chunks}")
        debug_log(f"  工具chunks: {tool_chunks}")
        debug_log(f"  收集到的思考: {len(current_thinking)} 字符")
        debug_log(f"  收集到的正文: {len(full_response)} 字符")
        debug_log(f"  收集到的工具: {len(tool_calls)} 个")
        
        # 结束未完成的流
        if in_thinking and not thinking_ended:
            sender('thinking_end', {'full_content': current_thinking})
            await asyncio.sleep(0.1)
            
        
        # 确保text_end事件被发送
        if text_started and text_has_content:
            debug_log(f"发送text_end事件，完整内容长度: {len(full_response)}")
            sender('text_end', {'full_content': full_response})
            await asyncio.sleep(0.1)
            text_streaming = False
            
            if full_response.strip():
                debug_log(f"流式文本内容长度: {len(full_response)} 字符")
        
        # 检测是否有格式错误的工具调用
        if not tool_calls and full_response and AUTO_FIX_TOOL_CALL:
            if detect_malformed_tool_call(full_response):
                auto_fix_attempts += 1
                
                if auto_fix_attempts <= AUTO_FIX_MAX_ATTEMPTS:
                    debug_log(f"检测到格式错误的工具调用，尝试自动修复 (尝试 {auto_fix_attempts}/{AUTO_FIX_MAX_ATTEMPTS})")
                    
                    fix_message = tr("task_main.auto_fix_instruction")

                    sender('system_message', {
                        'content': tr("task_main.auto_fix_notice", message=fix_message)
                    })
                    
                    messages.append({
                        "role": "user",
                        "content": fix_message
                    })
                    
                    await asyncio.sleep(1)
                    continue
                else:
                    debug_log(f"自动修复尝试已达上限 ({AUTO_FIX_MAX_ATTEMPTS})")
                    sender('system_message', {
                        'content': tr("task_main.auto_fix_failed")
                    })
                    break
        
        # 构建助手消息（用于API继续对话）
        assistant_content_parts = []
        
        if full_response:
            assistant_content_parts.append(full_response)
        
        assistant_content = "\n".join(assistant_content_parts) if assistant_content_parts else ""
        
        # 行内引用：校验 marker、剥离无效引用，已用来源挂到消息 metadata 一并落库
        round_citations = []
        if assistant_content and ("【cite:" in assistant_content or "【file:" in assistant_content):
            try:
                from modules.citations import finalize_message_citations, get_registry
                assistant_content, round_citations = finalize_message_citations(
                    assistant_content,
                    get_registry(web_terminal),
                    str(getattr(web_terminal, "project_path", "") or ""),
                )
                for ann in round_citations:
                    if ann.get("id"):
                        task_citations[ann["id"]] = ann
            except Exception as _cite_exc:
                debug_log(f"[Citations] 行内引用处理失败，保留原文: {_cite_exc}")
                round_citations = []
        
        # 添加到消息历史（用于API继续对话，不保存到文件）
        assistant_message = {
            "role": "assistant",
            "content": assistant_content,
            # thinking 模式下，reasoning_content 需要原样回传；即使该轮为空字符串也保留字段
            "reasoning_content": current_thinking or "",
        }
        if tool_calls:
            assistant_message["tool_calls"] = tool_calls
        
        messages.append(assistant_message)
        if assistant_content or current_thinking or tool_calls:
            web_terminal.context_manager.add_conversation(
                "assistant",
                assistant_content,
                tool_calls=tool_calls if tool_calls else None,
                reasoning_content=current_thinking or "",
                metadata={"citations": round_citations} if round_citations else None
            )
        
        # 为下一轮迭代重置流状态标志，但保留 full_response 供上面保存使用
        text_streaming = False
        text_started = False
        text_has_content = False
        full_response = ""

        if not tool_calls:
            debug_log("没有工具调用，结束迭代")
            # === 目标模式：turn 结束拦截 ===
            try:
                if goal_is_active(workspace, conversation_id):
                    goal_result = await handle_goal_after_turn(
                        web_terminal=web_terminal,
                        workspace=workspace,
                        messages=messages,
                        sender=sender,
                        conversation_id=conversation_id,
                        assistant_content=assistant_content,
                        made_tool_call=goal_segment_made_tool_call,
                        total_tool_calls=total_tool_calls,
                    )
                    action = goal_result.get("action")
                    if action == "continue":
                        # 已注入续命 user 消息：上一段 assistant work 到此结束，
                        # 后续计时切换到刚注入的 goal_review 消息。
                        finalize_user_work_timer()
                        switch_current_work_timer_to_latest_user()
                        goal_segment_made_tool_call = False
                        is_first_iteration = False
                        debug_log(f"[Goal] 续命：{goal_result.get('message', '')[:80]}")
                        continue
                    if action == "done":
                        debug_log("[Goal] 目标已达成，结束任务")
                    elif action == "stop":
                        debug_log(f"[Goal] 目标停止：{goal_result.get('reason')}")
            except Exception as exc:
                debug_log(f"[Goal] turn 结束处理失败: {exc}")

            # 多智能体模式：没有工具调用时，先消费子智能体待转发到主对话的消息；
            # 如果有新消息注入，继续迭代让 Team Leader 响应，而不是直接结束任务。
            if getattr(web_terminal, "multi_agent_mode", False):
                injected_count = await process_multi_agent_master_messages(
                    messages=messages,
                    inline=False,
                    web_terminal=web_terminal,
                    sender=sender,
                    debug_log=debug_log,
                )
                ma_debug(
                    "no_tool_call_turn_process_ma_messages",
                    conversation_id=conversation_id,
                    injected_count=injected_count,
                )
                if injected_count:
                    debug_log(f"[MultiAgent] no-tool-call turn 注入 {injected_count} 条子智能体消息，继续迭代")
                    is_first_iteration = False
                    continue

            break

        # 目标模式：本轮段确实产生了工具调用
        goal_segment_made_tool_call = True

        # 检查连续相同工具调用
        for tc in tool_calls:
            tool_name = tc["function"]["name"]
            
            if tool_name == last_tool_name:
                consecutive_same_tool[tool_name] += 1
                
                if (
                    MAX_CONSECUTIVE_SAME_TOOL is not None
                    and consecutive_same_tool[tool_name] >= MAX_CONSECUTIVE_SAME_TOOL
                ):
                    debug_log(f"警告: 连续调用相同工具 {tool_name} 已达 {MAX_CONSECUTIVE_SAME_TOOL} 次")
                    sender('system_message', {
                        'content': tr("task_main.repeat_tool_warning", tool=tool_name, limit=MAX_CONSECUTIVE_SAME_TOOL)
                    })
                    
                    if consecutive_same_tool[tool_name] >= MAX_CONSECUTIVE_SAME_TOOL + 2:
                        debug_log(f"终止: 工具 {tool_name} 调用次数过多")
                        sender('system_message', {
                            'content': tr("task_main.repeat_tool_terminated", tool=tool_name)
                        })
                        break
            else:
                consecutive_same_tool.clear()
                consecutive_same_tool[tool_name] = 1
            
            last_tool_name = tool_name

        # 更新统计
        total_tool_calls += len(tool_calls)
        try:
            if goal_is_active(workspace, conversation_id):
                emit_goal_progress(
                    web_terminal=web_terminal,
                    workspace=workspace,
                    sender=sender,
                    conversation_id=conversation_id,
                    total_tool_calls=total_tool_calls,
                )
        except Exception as exc:
            debug_log(f"[Goal] 进度广播失败: {exc}")

        # 执行每个工具
        tool_loop_result = await execute_tool_calls(
            web_terminal=web_terminal,
            tool_calls=tool_calls,
            sender=sender,
            messages=messages,
            client_sid=client_sid,
            username=username,
            iteration=iteration,
            conversation_id=conversation_id,
            last_tool_call_time=last_tool_call_time,
            process_sub_agent_updates=process_sub_agent_updates,
            process_background_command_updates=process_background_command_updates,
            process_multi_agent_master_messages=process_multi_agent_master_messages,
            get_stop_flag=get_stop_flag,
            clear_stop_flag=clear_stop_flag,
            workspace=workspace,
        )
        last_tool_call_time = tool_loop_result.get("last_tool_call_time", last_tool_call_time)
        if tool_loop_result.get("stopped"):
            finalize_user_work_timer()
            finalize_run_versioning_checkpoint("stopped")
            return
        if tool_loop_result.get("approval_rejected"):
            sender('task_stopped', {
                'message': tool_loop_result.get("approval_message") or tr("tool_loop.rejected_by_user"),
                'reason': 'approval_rejected',
                'conversation_id': conversation_id,
                'has_running_sub_agents': False,
                'has_running_background_commands': False,
                'has_running_multi_agent': False
            })
            finalize_user_work_timer()
            finalize_run_versioning_checkpoint("approval_rejected")
            return
        if tool_loop_result.get("deep_compressed"):
            deep_result = tool_loop_result.get("deep_result") or {}
            guide_message = (deep_result.get("guide_message") or "").strip()
            if deep_result.get("success") and guide_message:
                finalize_user_work_timer()
                finalize_run_versioning_checkpoint("deep_compress_handoff")
                # 压缩后的续接引导语单独标记来源，使其以 compact 形式展示，
                # 而不是混入普通用户消息流。
                setattr(web_terminal, "_current_user_message_source", "compression_handoff")
                try:
                    await handle_task_with_sender(
                        web_terminal,
                        workspace,
                        guide_message,
                        [],
                        sender,
                        client_sid,
                        username,
                        []
                    )
                finally:
                    setattr(web_terminal, "_current_user_message_source", "user")
                return
            finalize_user_work_timer()
            finalize_run_versioning_checkpoint("deep_compress_end")
            return
        
        # 标记不再是第一次迭代
        is_first_iteration = False

        # 多智能体模式：在进入下一轮模型调用前，消费子智能体最新输出到主对话
        if getattr(web_terminal, "multi_agent_mode", False):
            await process_multi_agent_master_messages(
                messages=messages,
                inline=False,
                web_terminal=web_terminal,
                sender=sender,
                debug_log=debug_log,
            )

    
    # 最终统计
    # 多智能体模式：主对话任务结束，解除活跃标记
    if getattr(web_terminal, "multi_agent_mode", False):
        web_terminal._multi_agent_main_task_active = False

    debug_log(f"\n{'='*40}")
    debug_log(f"任务完成统计:")
    debug_log(f"  总迭代次数: {total_iterations}")
    debug_log(f"  总工具调用: {total_tool_calls}")
    debug_log(f"  自动修复尝试: {auto_fix_attempts}")
    debug_log(f"  累积响应: {len(accumulated_response)} 字符")
    debug_log(f"{'='*40}\n")
    
    # 检查是否有后台运行的子智能体或待通知的完成任务
    manager = getattr(web_terminal, "sub_agent_manager", None)
    has_running_sub_agents = False
    has_running_multi_agent = False
    bg_manager = getattr(web_terminal, "background_command_manager", None)
    has_running_background_commands = False
    if manager:
        try:
            manager.reconcile_task_states(conversation_id=conversation_id)
        except Exception as exc:
            debug_log(f"[SubAgent] reconcile_task_states failed: {exc}")
        if not hasattr(web_terminal, "_announced_sub_agent_tasks"):
            web_terminal._announced_sub_agent_tasks = set()

        # 多智能体模式：检测是否还有真正运行中的实例，以及是否有尚未消费的 pending 消息。
        # idle 实例已完成当前输出、保留上下文等待后续指令；pending 消息会在主任务
        # 结束后由 poll_multi_agent_notifications 继续消费并触发新一轮工作。
        has_pending_ma_messages = False
        if getattr(web_terminal, "multi_agent_mode", False):
            state = manager.get_multi_agent_state(conversation_id)
            if state:
                has_running_multi_agent = any(a.status == "running" for a in state.list_all())
                has_pending_ma_messages = state.has_pending_master_messages()

        running_tasks = [
            task for task in manager.tasks.values()
            if task.get("status") not in TERMINAL_STATUSES.union({"terminated"})
            and task.get("run_in_background")
            and not task.get("multi_agent_mode")
            and task.get("conversation_id") == conversation_id
        ]
        pending_notice_tasks = [
            task for task in manager.tasks.values()
            if task.get("status") in TERMINAL_STATUSES.union({"terminated"})
            and task.get("run_in_background")
            and not task.get("multi_agent_mode")
            and task.get("conversation_id") == conversation_id
            and task.get("task_id") not in web_terminal._announced_sub_agent_tasks
        ]

        if running_tasks or pending_notice_tasks:
            has_running_sub_agents = True
            notify_tasks = running_tasks + pending_notice_tasks
            debug_log(f"[SubAgent] 后台子智能体等待: running={len(running_tasks)} pending_notice={len(pending_notice_tasks)}")
            # 先通知前端：有子智能体在运行/待通知，保持等待状态
            sender('sub_agent_waiting', {
                'count': len(notify_tasks),
                'tasks': [{'agent_id': t.get('agent_id'), 'summary': t.get('summary')} for t in notify_tasks]
            })

    # 检查是否有后台 run_command 或待通知任务
    if bg_manager and conversation_id:
        try:
            bg_manager.reconcile_stale_records(conversation_id=conversation_id)
        except Exception as exc:
            debug_log(f"[BgCmdDebug] reconcile_stale_records failed: {exc}")
        waiting_items = bg_manager.list_waiting_items(conversation_id)
        if waiting_items:
            has_running_background_commands = True
            # 与子智能体完全复用同一 waiting 事件（前端已有稳定处理链路）
            sender('sub_agent_waiting', _build_shared_waiting_payload(waiting_items))

    # 传统后台任务通知池：只处理 run_in_background=True 的子智能体 / 后台 run_command。
    needs_completion_poll = (
        has_running_sub_agents
        or has_running_background_commands
        or _has_pending_completion_work(
            web_terminal=web_terminal, conversation_id=conversation_id
        )
        or _has_pending_workflow_notices(
            web_terminal=web_terminal, conversation_id=conversation_id
        )
    )
    if needs_completion_poll:
        ma_debug(
            "completion_poll_start",
            conversation_id=conversation_id,
            has_running_sub_agents=has_running_sub_agents,
            has_running_background_commands=has_running_background_commands,
        )
        def run_completion_poll():
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(poll_completion_notifications(
                    web_terminal=web_terminal,
                    workspace=workspace,
                    conversation_id=conversation_id,
                    client_sid=client_sid,
                    username=username
                ))
            finally:
                loop.close()

        socketio.start_background_task(run_completion_poll)

    # 多智能体模式独立通知池：处理 running 实例 / idle 实例待消费的 pending 消息。
    # 与传统后台任务完全分离，避免两者竞争 create_chat_task 的单工作区互斥。
    ma_mode_flag = bool(getattr(web_terminal, "multi_agent_mode", False))
    needs_ma_poll = ma_mode_flag and (has_running_multi_agent or has_pending_ma_messages)
    ma_debug(
        "ma_poll_decision",
        conversation_id=conversation_id,
        multi_agent_mode=ma_mode_flag,
        has_running_multi_agent=has_running_multi_agent,
        has_pending_ma_messages=has_pending_ma_messages,
        needs_ma_poll=needs_ma_poll,
    )
    if needs_ma_poll:
        ma_debug(
            "ma_poll_start",
            conversation_id=conversation_id,
            has_running_multi_agent=has_running_multi_agent,
            has_pending_ma_messages=has_pending_ma_messages,
        )
        def run_ma_poll():
            import asyncio
            import threading as _t
            ma_debug(
                "ma_poll_thread_enter",
                conversation_id=conversation_id,
                thread_id=_t.get_ident(),
            )
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(poll_multi_agent_notifications(
                    web_terminal=web_terminal,
                    workspace=workspace,
                    conversation_id=conversation_id,
                    client_sid=client_sid,
                    username=username
                ))
            except Exception as exc:
                ma_debug(
                    "ma_poll_thread_exception",
                    conversation_id=conversation_id,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
            else:
                ma_debug("ma_poll_thread_exit_ok", conversation_id=conversation_id)
            finally:
                loop.close()
                ma_debug("ma_poll_thread_finally", conversation_id=conversation_id)

        socketio.start_background_task(run_ma_poll)

    if not needs_completion_poll and not needs_ma_poll:
        ma_debug(
            "completion_poll_skip",
            conversation_id=conversation_id,
            reason="no_running_background_or_multi_agent",
        )

    has_running_completion_jobs = has_running_sub_agents or has_running_background_commands

    # 任务结束时清理残留的 pending runtime mode（权限/执行环境/网络权限）。
    # 场景：用户在模型最后一次输出（无工具调用）后、task 仍为 running 状态时切换了
    # 权限/执行环境，此时会进入 queue_*_change 路径只设置 pending，但 task 随即自然
    # 结束，不会进入 execute_tool_calls → apply_pending_runtime_mode_changes。
    # 若不在此清理，pending 会残留到下一次工作首次工具调用时才被应用，
    # 并把"变更通知"作为 user 消息注入到新一轮对话中（bug）。
    # 正确行为：task 结束时立即应用 pending（等同空闲期切换），此处不生成通知；
    # 由于通知基线（runtime_mode_baseline）未更新，下一条真实 user 消息时会通过
    # drift 检测一次性补发切换通知（见 user_message 回显后的注入点）。
    try:
        if hasattr(web_terminal, "apply_pending_runtime_mode_changes"):
            residual_notices = web_terminal.apply_pending_runtime_mode_changes() or []
            if residual_notices:
                debug_log(
                    "[RuntimeMode] task 结束时应用残留 pending runtime mode，丢弃通知: "
                    f"{[n.get('source') if isinstance(n, dict) else n for n in residual_notices]}"
                )
    except Exception as exc:
        debug_log(f"[RuntimeMode] task 结束时清理 pending runtime mode 失败: {exc}")

    pending_runtime_guidance_messages: List[str] = []
    try:
        from .tasks import task_manager

        raw_items = task_manager.consume_runtime_guidance_for_injection(
            username=username,
            task_id=client_sid,
        )
        # 权限/执行环境/网络权限变更通知在任务完成时不应该触发新一轮工作，
        # 只保留真正的引导消息（压缩续接等），丢弃变更通知。
        runtime_skip_sources = {"权限变更", "执行环境变更", "网络权限变更", "notify", "permission_change", "execution_change", "network_change"}
        for item in (raw_items or []):
            if isinstance(item, dict):
                src = str(item.get("source") or "").strip().lower()
                text = str(item.get("text") or "").strip()
                if src in runtime_skip_sources:
                    continue
                if text:
                    pending_runtime_guidance_messages.append(text)
            else:
                text = str(item or "").strip()
                if text:
                    pending_runtime_guidance_messages.append(text)
    except Exception as exc:
        debug_log(f"[RuntimeGuidance] 读取剩余引导消息失败: {exc}")
        pending_runtime_guidance_messages = []

    # 发送完成事件（如果有后台完成任务在运行，前端会保持等待状态）
    if not has_running_completion_jobs:
        # 对话真正空闲：把 work_timer 持久化到后端，避免刷新后恢复成「工作中」
        try:
            if web_terminal and conversation_id:
                finalize_conversation_work_timer(
                    web_terminal,
                    conversation_id,
                    finished_at=datetime.now().isoformat(),
                )
        except Exception:
            pass
        finalize_user_work_timer()
        finalize_run_versioning_checkpoint("completed")
    else:
        finalize_run_versioning_checkpoint("waiting_background")
    sender('task_complete', {
        'total_iterations': total_iterations,
        'total_tool_calls': total_tool_calls,
        'auto_fix_attempts': auto_fix_attempts,
        # 沿用子智能体字段，确保前端直接走已验证通路
        'has_running_sub_agents': has_running_completion_jobs,
        'has_running_background_commands': has_running_background_commands,
        # 多智能体模式：只要还有 running 实例或尚未消费的 pending 消息，
        # 前端就应保持运行态并继续轮询。
        'has_running_multi_agent': has_running_multi_agent or has_pending_ma_messages,
        'pending_runtime_guidance_messages': pending_runtime_guidance_messages,
        # 行内引用：本任务全部已用来源，前端挂到当前 assistant 消息渲染 chip
        'citations': list(task_citations.values()),
    })
