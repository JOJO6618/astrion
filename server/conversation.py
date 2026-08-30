from __future__ import annotations
import logging
import sys, os
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

logger = logging.getLogger(__name__)

import asyncio, json, time, re, os, shutil
from datetime import datetime, timedelta
def _is_not_found_message(text) -> bool:
    """结果消息是否为「不存在」类。

    消息文本按当前 UI 语言生成（见 modules/i18n），判等必须双语兼容：
    zh 含「不存在」；en 一律为 "not found"（见各 conversation.* 文案）。
    """
    s = str(text or "")
    return "不存在" in s or "not found" in s.lower()


from modules.i18n import tr
from pathlib import Path
from collections import defaultdict, Counter, deque
from io import BytesIO
from typing import Dict, Any, Optional, List, Tuple

from flask import Blueprint, request, jsonify, session, send_file
from flask_socketio import emit
from werkzeug.utils import secure_filename
import zipfile

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
    DATA_DIR,
    DEFAULT_RESPONSE_MAX_TOKENS,
    DEFAULT_PROJECT_PATH,
    LOGS_DIR,
    AGENT_VERSION,
    PROJECT_MAX_STORAGE_MB,
    PROJECT_MAX_STORAGE_BYTES,
    UPLOAD_SCAN_LOG_SUBDIR,
    REASONING_EFFORT_LEVELS,
)
from modules.personalization_manager import (
    load_personalization_config,
    save_personalization_config,
)
from modules.upload_security import UploadSecurityError
from modules.user_manager import UserWorkspace
from modules.usage_tracker import QUOTA_DEFAULTS
from modules.sub_agent import TERMINAL_STATUSES
from modules.versioning_manager import ConversationVersioningManager, VersioningError
from modules.shallow_versioning import ShallowVersioningManager
from modules.host_workspace_manager import resolve_host_workspace
from utils.host_workspace_debug import write_host_workspace_debug
from utils.perf_log import perf_log, PerfTimer
from core.web_terminal import WebTerminal
from utils.tool_result_formatter import format_tool_result_for_context
from utils.conversation_manager import ConversationManager
from utils.api_client import APIClient

from .auth_helpers import api_login_required, resolve_admin_policy, get_current_user_record, get_current_username
from .context import with_terminal, get_terminal_for_sid, get_gui_manager, get_upload_guard, build_upload_error_response, ensure_conversation_loaded, reset_system_state, get_user_resources, get_or_create_usage_tracker
from .utils_common import (
    build_review_lines,
    debug_log,
    _sanitize_filename_component,
    log_backend_chunk,
    log_frontend_chunk,
    log_streaming_debug_entry,
    brief_log,
    DEBUG_LOG_FILE,
    CHUNK_BACKEND_LOG_FILE,
    CHUNK_FRONTEND_LOG_FILE,
    STREAMING_DEBUG_LOG_FILE,
)
from .extensions import socketio
from .state import (
    RECENT_UPLOAD_EVENT_LIMIT,
    RECENT_UPLOAD_FEED_LIMIT,
    user_manager,
    container_manager,
    get_last_active_ts,
)
from .usage import record_user_activity
from .conversation_stats import (
    build_admin_dashboard_snapshot,
    compute_workspace_storage,
    collect_user_token_statistics,
    collect_upload_events,
    summarize_upload_events,
)
from .deep_compression import run_deep_compression, heal_stale_compression_flag

conversation_bp = Blueprint('conversation', __name__)


def _terminate_running_sub_agents(terminal: WebTerminal, reason: str = "") -> int:
    """切换/新建对话时，强制终止当前对话仍在运行的子智能体，并记录系统消息。"""
    manager = getattr(terminal, "sub_agent_manager", None)
    if not manager:
        return 0
    current_conv_id = getattr(getattr(terminal, "context_manager", None), "current_conversation_id", None)
    if not current_conv_id:
        return 0
    try:
        manager.reconcile_task_states(conversation_id=current_conv_id)
    except Exception:
        pass
    running_tasks = [
        task for task in manager.tasks.values()
        if task.get("status") not in TERMINAL_STATUSES.union({"terminated"})
        and task.get("run_in_background")
        and task.get("conversation_id") == current_conv_id
    ]
    if not running_tasks:
        return 0
    stopped_count = 0
    for task in running_tasks:
        task_id = task.get("task_id")
        manager.terminate_sub_agent(task_id=task_id)
        stopped_count += 1
    return stopped_count


def _cancel_running_tasks(username: str, workspace_id: str, timeout_seconds: float = 4.0) -> Tuple[int, bool]:
    """取消当前工作区运行中的主任务，并等待其停止，避免切换对话后串写。"""
    try:
        from .tasks import task_manager
    except Exception as exc:
        debug_log(f"[TaskCancel] 导入 task_manager 失败: {exc}")
        return 0, True

    active_statuses = {"pending", "running", "cancel_requested"}

    def _active_tasks():
        try:
            recs = task_manager.list_tasks(username, workspace_id)
        except Exception:
            recs = task_manager.list_tasks(username)
        return [rec for rec in recs if getattr(rec, "status", None) in active_statuses]

    running = _active_tasks()
    if not running:
        return 0, True

    canceled = 0
    for rec in running:
        task_id = getattr(rec, "task_id", None)
        if task_id and task_manager.cancel_task(username, task_id):
            canceled += 1

    deadline = time.time() + max(timeout_seconds, 0.5)
    while time.time() < deadline:
        if not _active_tasks():
            return canceled, True
        time.sleep(0.1)

    return canceled, False


def _get_active_workspace_task(username: str, workspace_id: str):
    """返回当前工作区仍在运行/停止中的主任务。用于避免视图导航改写运行任务的 terminal 上下文。"""
    try:
        from .tasks import task_manager
        active_statuses = {"pending", "running", "cancel_requested"}
        tasks = [
            rec for rec in task_manager.list_tasks(username, workspace_id)
            if getattr(rec, "status", None) in active_statuses
        ]
        tasks.sort(key=lambda rec: getattr(rec, "created_at", 0), reverse=True)
        return tasks[0] if tasks else None
    except Exception as exc:
        debug_log(f"[ConversationSafeNav] 查询运行任务失败: {exc}")
        return None


def _build_safe_load_result(terminal: WebTerminal, conversation_id: str) -> Dict[str, Any]:
    """只读取对话元数据，不调用 terminal.load_conversation，避免修改运行任务正在使用的上下文。"""
    normalized_id = _normalize_conv_id(conversation_id)
    ctx_manager = getattr(terminal, "context_manager", None)
    # 用 _get_conversation_manager_for_id 自动判断对话属于普通管理器还是多智能体管理器
    cm = ctx_manager._get_conversation_manager_for_id(normalized_id) if ctx_manager else None
    data = cm.load_conversation(normalized_id) if cm else None
    if not data:
        return {
            "success": False,
            "error": tr("conversation.load_failed_not_found"),
            "message": tr("conversation.load_failed_detail", conversation_id=normalized_id),
        }
    meta = data.get("metadata", {}) or {}
    run_mode = meta.get("run_mode") or getattr(terminal, "run_mode", "fast")
    if run_mode == "deep":  # 旧版标识符映射
        run_mode = "thinking"
    thinking_mode = bool(meta.get("thinking_mode", run_mode != "fast"))
    return {
        "success": True,
        "conversation_id": normalized_id,
        "title": data.get("title", tr("conversation.unknown_title")),
        "messages_count": len(data.get("messages", []) or []),
        "run_mode": run_mode,
        "thinking_mode": thinking_mode,
        "reasoning_effort": meta.get("reasoning_effort"),
        "model_key": meta.get("model_key") or getattr(terminal, "model_key", None),
        "message": tr("conversation.loaded_detail", conversation_id=normalized_id),
        "safe_navigation": True,
    }


def _normalize_conv_id(conversation_id: str) -> str:
    conv = (conversation_id or "").strip()
    if not conv:
        return conv
    return conv if conv.startswith("conv_") else f"conv_{conv}"


def _sync_restored_conversation_memory(conversation_id: str) -> None:
    """版本回溯后同步内存实例：把绑定该对话的对话级 terminal 内存替换为磁盘最新。

    回溯用 allow_shrink 覆写裁短磁盘；若持有旧（更长）历史的对话级实例之后保存，
    merge-on-save 会把被裁消息当作「内存独有」追加救回，回溯随即被撤销
    （此前用户只能回溯后立刻重启进程的根因）。工作区级服务实例不挂载历史，
    天然无需处理（见 WebTerminal.load_conversation 的 attach_history 分流）。
    """
    from copy import deepcopy
    from server import state as server_state

    normalized = _normalize_conv_id(conversation_id)
    user_terminals = getattr(server_state, "user_terminals", None) or {}
    for term_key, term in list(user_terminals.items()):
        try:
            bound = getattr(term, "_bound_conversation_id", None)
            if not bound or _normalize_conv_id(str(bound)) != normalized:
                continue
            ctx = getattr(term, "context_manager", None)
            if ctx is None:
                continue
            target_manager = ctx._get_conversation_manager_for_id(normalized)
            data = target_manager.load_conversation(normalized) or {}
            ctx.conversation_history = list(data.get("messages") or [])
            ctx.conversation_metadata = deepcopy(data.get("metadata") or {})
            todo_data = data.get("todo_list")
            ctx.todo_list = deepcopy(todo_data) if todo_data else None
            debug_log(
                f"[Versioning][Restore] synced in-memory history term={term_key} "
                f"messages={len(ctx.conversation_history)}"
            )
        except Exception as exc:
            debug_log(f"[Versioning][Restore] sync memory failed for term={term_key}: {exc}")


def _normalize_versioning_tracking_mode(value: Optional[str]) -> str:
    return ConversationVersioningManager.normalize_tracking_mode(value)


def _can_use_versioning_scope(username: str, tracking_mode: str) -> bool:
    normalized_tracking_mode = _normalize_versioning_tracking_mode(tracking_mode)
    return _is_host_mode_request(username) or (
        normalized_tracking_mode == ConversationVersioningManager.TRACKING_MODE_CONVERSATION_ONLY
    )


def _is_host_mode_request(username: str) -> bool:
    return bool(session.get("host_mode")) or (username == "host")


def _get_conv_versioning_manager(workspace: UserWorkspace, conversation_id: str) -> ConversationVersioningManager:
    normalized = _normalize_conv_id(conversation_id)
    return ConversationVersioningManager(
        project_path=workspace.project_path,
        data_dir=workspace.data_dir,
        conversation_id=normalized,
    )


def _sanitize_scope_component(value: Any, default: str = "default") -> str:
    raw = str(value or "").strip()
    if not raw:
        return default
    sanitized = re.sub(r"[^0-9A-Za-z._-]+", "_", raw).strip("._-")
    if not sanitized:
        sanitized = default
    return sanitized[:120]


def _resolve_input_draft_path(workspace: UserWorkspace, username: str) -> Tuple[Path, str]:
    base_dir = Path(workspace.data_dir).expanduser().resolve() / "composer_drafts"
    if _is_host_mode_request(username):
        workspace_id = session.get("host_workspace_id") or session.get("workspace_id") or "default"
        scope = f"host_workspace:{workspace_id}"
        filename = f"{_sanitize_scope_component(workspace_id)}.json"
        return (base_dir / "host" / filename).resolve(), scope
    safe_user = _sanitize_scope_component(username or "user", default="user")
    workspace_id = getattr(workspace, "workspace_id", None) or session.get("workspace_id") or "default"
    safe_workspace = _sanitize_scope_component(workspace_id)
    scope = f"user:{safe_user}:project:{safe_workspace}"
    return (base_dir / "docker" / f"{safe_workspace}.json").resolve(), scope


def _read_input_draft_payload(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8")) or {}
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _atomic_write_input_draft(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _get_conversation_versioning_meta(terminal: WebTerminal, conversation_id: str) -> Dict[str, Any]:
    normalized = _normalize_conv_id(conversation_id)
    data = terminal.context_manager._get_conversation_manager_for_id(normalized).load_conversation(normalized) or {}
    meta = data.get("metadata") or {}
    versioning = meta.get("versioning") or {}
    if not isinstance(versioning, dict):
        versioning = {}
    enabled = bool(versioning.get("enabled", False))
    tracking_mode = _normalize_versioning_tracking_mode(versioning.get("tracking_mode"))
    mode = "overwrite"
    return {
        "enabled": enabled,
        "mode": mode,
        "tracking_mode": tracking_mode,
        "conversation_id": normalized,
        "metadata": meta,
    }


def _ensure_conversation_versioning_enabled(
    terminal: WebTerminal,
    workspace: UserWorkspace,
    conversation_id: str,
    tracking_mode: Optional[str] = None,
) -> None:
    """为指定对话启用版本控制并创建初始 checkpoint（用于默认开启场景）。"""
    normalized_id = _normalize_conv_id(conversation_id)
    host_mode = _is_host_mode_request(get_current_username())
    try:
        prefs = load_personalization_config(workspace.data_dir)
    except Exception:
        prefs = {}
    backup_mode = str(prefs.get("versioning_backup_mode") or "shallow").strip().lower()
    backup_mode = "full" if backup_mode == "full" else "shallow"
    if backup_mode == "shallow":
        default_mode = ConversationVersioningManager.TRACKING_MODE_CONVERSATION_ONLY
    elif host_mode:
        default_mode = ConversationVersioningManager.TRACKING_MODE_WORKSPACE_AND_CONVERSATION
    else:
        default_mode = ConversationVersioningManager.TRACKING_MODE_CONVERSATION_ONLY
    manager = _get_conv_versioning_manager(workspace, normalized_id)
    normalized_tracking_mode = _normalize_versioning_tracking_mode(tracking_mode or default_mode)
    meta = manager.set_enabled(enabled=True, mode="overwrite", tracking_mode=normalized_tracking_mode)
    conv_data = terminal.context_manager._get_conversation_manager_for_id(normalized_id).load_conversation(normalized_id) or {}
    snapshot_payload = {
        "conversation_id": normalized_id,
        "title": conv_data.get("title"),
        "metadata": conv_data.get("metadata") or {},
        "messages": conv_data.get("messages") or [],
        "message_index": -1,
        "run_status": "initial",
    }
    init_result = manager.ensure_initial_checkpoint(
        workspace_path=str(workspace.project_path),
        conversation_snapshot=snapshot_payload,
        tracking_mode=normalized_tracking_mode,
    )
    init_row = init_result.get("row") or {}
    if init_row.get("tree_hash"):
        meta["last_tree_hash"] = init_row.get("tree_hash")
    _update_conversation_versioning_meta(
        terminal,
        normalized_id,
        enabled=True,
        mode="overwrite",
        tracking_mode=normalized_tracking_mode,
        backup_mode=backup_mode,
        last_commit=meta.get("last_commit"),
        last_input_seq=int(meta.get("last_input_seq") or 0),
    )


def _update_conversation_versioning_meta(
    terminal: WebTerminal,
    conversation_id: str,
    *,
    enabled: bool,
    mode: str,
    tracking_mode: Optional[str] = None,
    backup_mode: Optional[str] = None,
    last_commit: Optional[str] = None,
    last_input_seq: Optional[int] = None,
) -> bool:
    normalized = _normalize_conv_id(conversation_id)
    normalized_tracking_mode = _normalize_versioning_tracking_mode(tracking_mode)
    payload: Dict[str, Any] = {
        "versioning": {
            "enabled": bool(enabled),
            "mode": "overwrite",
            "tracking_mode": normalized_tracking_mode,
            "updated_at": datetime.now().isoformat(),
        }
    }
    if backup_mode is not None:
        payload["versioning"]["backup_mode"] = "full" if backup_mode == "full" else "shallow"
    if last_commit is not None:
        payload["versioning"]["last_commit"] = last_commit
    if last_input_seq is not None:
        payload["versioning"]["last_input_seq"] = int(last_input_seq)
    return terminal.context_manager._get_conversation_manager_for_id(normalized).update_conversation_metadata(normalized, payload)


@conversation_bp.route('/api/input-draft', methods=['GET'])
@api_login_required
@with_terminal
def get_input_draft(terminal: WebTerminal, workspace: UserWorkspace, username: str):
    del terminal
    try:
        path, scope = _resolve_input_draft_path(workspace, username)
        payload = _read_input_draft_payload(path)
        content = payload.get("content")
        updated_at = payload.get("updated_at")
        skill_refs = payload.get("skill_refs")
        editor_json = payload.get("editor_json")
        return jsonify({
            "success": True,
            "data": {
                "content": content if isinstance(content, str) else "",
                "skill_refs": skill_refs if isinstance(skill_refs, list) else [],
                "editor_json": editor_json if isinstance(editor_json, dict) else None,
                "updated_at": str(updated_at or ""),
                "scope": scope,
            }
        })
    except Exception as exc:
        return jsonify({"success": False, "error": tr("conversation.read_input_draft_failed", error=str(exc))}), 500


@conversation_bp.route('/api/input-draft', methods=['POST', 'PUT'])
@api_login_required
@with_terminal
def upsert_input_draft(terminal: WebTerminal, workspace: UserWorkspace, username: str):
    del terminal
    try:
        body = request.get_json(silent=True) or {}
        content = body.get("content") if isinstance(body, dict) else ""
        if content is None:
            content = ""
        if not isinstance(content, str):
            content = str(content)
        if len(content) > 40000:
            return jsonify({"success": False, "error": tr("conversation.input_draft_too_long")}), 400
        raw_skill_refs = body.get("skill_refs") if isinstance(body, dict) else []
        skill_refs = []
        if isinstance(raw_skill_refs, list):
            for item in raw_skill_refs[:100]:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "")[:200]
                description = str(item.get("description") or "")[:1000]
                path_value = str(item.get("path") or "")[:4000]
                if name and path_value:
                    skill_refs.append({
                        "name": name,
                        "description": description,
                        "path": path_value,
                    })
        editor_json = body.get("editor_json") if isinstance(body, dict) else None
        if editor_json is not None and not isinstance(editor_json, dict):
            editor_json = None

        path, scope = _resolve_input_draft_path(workspace, username)
        if content == "":
            if path.exists():
                try:
                    path.unlink()
                except Exception:
                    pass
            return jsonify({
                "success": True,
                "data": {
                    "saved": True,
                    "cleared": True,
                    "scope": scope,
                    "updated_at": datetime.now().isoformat(),
                }
            })

        payload = {
            "content": content,
            "skill_refs": skill_refs,
            "editor_json": editor_json,
            "updated_at": datetime.now().isoformat(),
            "scope": scope,
        }
        _atomic_write_input_draft(path, payload)
        return jsonify({
            "success": True,
            "data": {
                "saved": True,
                "cleared": False,
                "scope": scope,
                "updated_at": payload["updated_at"],
            }
        })
    except Exception as exc:
        return jsonify({"success": False, "error": tr("conversation.save_input_draft_failed", error=str(exc))}), 500


# === 背景生成对话标题（从 app_legacy 拆分） ===
def _resolve_target_terminal_for_workspace(
    username: str,
    workspace_id: str,
    current_terminal: WebTerminal,
    current_workspace: UserWorkspace,
) -> tuple[WebTerminal, UserWorkspace]:
    """当请求指定了非当前 session 工作区时，解析并返回目标工作区的 terminal 与 workspace。"""
    write_host_workspace_debug(
        "sidebar-debug-api",
        api="resolve_target_terminal",
        requested_workspace_id=workspace_id,
        session_workspace_id=session.get("workspace_id"),
        current_terminal_project_path=str(getattr(current_terminal, "project_path", None)),
    )
    if _is_host_mode_request(username):
        catalog, _ = resolve_host_workspace()
        if not any(item.get("workspace_id") == workspace_id for item in (catalog.get("workspaces") or [])):
            raise ValueError(tr("conversation.workspace_not_found"))
    else:
        if workspace_id not in user_manager.list_user_workspaces(username):
            raise ValueError(tr("conversation.project_not_found"))
    try:
        target_terminal, target_workspace = get_user_resources(
            username, workspace_id=workspace_id, update_session=False
        )
    except RuntimeError as exc:
        raise RuntimeError(str(exc)) from exc
    if not target_terminal or not target_workspace:
        raise RuntimeError(tr("conversation.system_not_initialized"))
    target_cm = getattr(getattr(target_terminal, "context_manager", None), "conversation_manager", None)
    write_host_workspace_debug(
        "sidebar-debug-api",
        api="resolve_target_terminal_result",
        requested_workspace_id=workspace_id,
        target_terminal_project_path=str(getattr(target_terminal, "project_path", None)),
        target_cm_workspace_id=getattr(target_cm, "current_workspace_id", None),
        target_cm_conversations_dir=str(getattr(target_cm, "conversations_dir", None)),
    )
    return target_terminal, target_workspace


# === 背景生成对话标题（从 app_legacy 拆分） ===
@conversation_bp.route('/api/conversations', methods=['GET'])
@api_login_required
@with_terminal
def get_conversations(terminal: WebTerminal, workspace: UserWorkspace, username: str):
    """获取对话列表，支持通过 workspace_id 查询指定工作区/项目。"""
    try:
        # 获取查询参数
        limit = request.args.get('limit', 20, type=int)
        offset = request.args.get('offset', 0, type=int)
        non_empty = request.args.get('non_empty', '0') in ('1', 'true', 'True')
        # multi_agent_mode: '1' 仅多智能体模式对话；'0' 仅常规对话；未传 None 不过滤
        ma_param = request.args.get('multi_agent_mode', None)
        if ma_param in ('1', 'true', 'True'):
            multi_agent_filter: Optional[bool] = True
        elif ma_param in ('0', 'false', 'False'):
            multi_agent_filter = False
        else:
            multi_agent_filter = None
        target_workspace_id = request.args.get('workspace_id', '', type=str).strip()

        # 限制参数范围
        limit = max(1, min(limit, 10000))  # 限制在1-10000之间
        offset = max(0, offset)

        if target_workspace_id:
            try:
                terminal, workspace = _resolve_target_terminal_for_workspace(
                    username, target_workspace_id, terminal, workspace
                )
            except ValueError as exc:
                return jsonify({"success": False, "error": str(exc)}), 404
            except RuntimeError as exc:
                return jsonify({"success": False, "error": str(exc)}), 503

        result = terminal.get_conversations_list(limit=limit, offset=offset, non_empty=non_empty, multi_agent_mode=multi_agent_filter)
        cm = getattr(getattr(terminal, "context_manager", None), "conversation_manager", None)
        write_host_workspace_debug(
            "sidebar-debug-api",
            api="GET /api/conversations",
            workspace_id=target_workspace_id or None,
            session_workspace_id=session.get("workspace_id"),
            terminal_project_path=str(getattr(terminal, "project_path", None)),
            cm_workspace_id=getattr(cm, "current_workspace_id", None),
            cm_conversations_dir=str(getattr(cm, "conversations_dir", None)),
            result_success=bool(result.get("success")),
            result_count=len((result.get("data") or {}).get("conversations", [])),
        )
        
        if result["success"]:
            return jsonify({
                "success": True,
                "data": result["data"]
            })
        else:
            return jsonify({
                "success": False,
                "error": result.get("error", "Unknown error"),
                "message": result.get("message", tr("conversation.get_list_failed"))
            }), 500
            
    except Exception as e:
        logger.error(f"[API] 获取对话列表错误: {e}")
        return jsonify({
            "success": False,
            "error": str(e),
            "message": tr("conversation.get_list_exception")
        }), 500

@conversation_bp.route('/api/conversations', methods=['POST'])
@api_login_required
@with_terminal
def create_conversation(terminal: WebTerminal, workspace: UserWorkspace, username: str):
    """创建新对话，支持通过 workspace_id 在指定工作区/项目中创建。"""
    perf_log("create_conversation route enter", extra={"username": username})
    t0 = time.perf_counter()
    try:
        data = request.get_json() or {}
        # 前端现在期望"新建对话"回到用户配置的默认模型/模式，
        # 只有当客户端显式要求保留当前模式时才使用传入值。
        preserve_mode = bool(data.get('preserve_mode'))
        thinking_mode = data.get('thinking_mode') if preserve_mode and 'thinking_mode' in data else None
        run_mode = data.get('mode') if preserve_mode and 'mode' in data else None
        target_workspace_id = (data.get('workspace_id') or '').strip()
        multi_agent_mode = bool(data.get('multi_agent_mode'))
        # /new 页发消息/新建对话时前端随 body 传入当前生效的推理强度，
        # 权威写入新对话 meta；未提供时回落 terminal 当前档位 / 个性化默认值
        body_effort_provided = 'reasoning_effort' in data
        body_effort = None
        if body_effort_provided:
            raw_effort = data.get('reasoning_effort')
            if raw_effort is None:
                body_effort = None  # 显式默认档（不传参）
            elif isinstance(raw_effort, str):
                candidate_effort = raw_effort.strip().lower()
                if candidate_effort in REASONING_EFFORT_LEVELS:
                    body_effort = candidate_effort
                else:
                    body_effort_provided = False  # 非法值视为未提供
            else:
                body_effort_provided = False

        if target_workspace_id:
            try:
                terminal, workspace = _resolve_target_terminal_for_workspace(
                    username, target_workspace_id, terminal, workspace
                )
            except ValueError as exc:
                return jsonify({"success": False, "error": str(exc)}), 404
            except RuntimeError as exc:
                return jsonify({"success": False, "error": str(exc)}), 503

        # 多工作区并行后，新建/切换对话只是前端视图导航，不应停止同工作区正在运行的主任务。
        # 同工作区单任务互斥由 /api/tasks 创建任务时兜底限制；输入框禁用由前端根据任务状态处理。
        effective_workspace_id = target_workspace_id or session.get("workspace_id") or "default"
        active_task = _get_active_workspace_task(username=username, workspace_id=effective_workspace_id)
        if active_task:
            # 运行中时只能创建“视图用”的新对话文件，不能调用 terminal.create_new_conversation。
            # 后者会切换 context_manager.current_conversation_id，导致运行任务后续内容串写到新对话。
            cm = getattr(getattr(terminal, "context_manager", None), "conversation_manager", None)
            if not cm:
                return jsonify({"success": False, "error": tr("conversation.manager_not_initialized")}), 500
            try:
                prefs = load_personalization_config(workspace.data_dir)
            except Exception:
                prefs = {}
            safe_run_mode = str(run_mode or "").strip().lower()
            if safe_run_mode == "deep":  # 旧版标识符映射
                safe_run_mode = "thinking"
            if safe_run_mode not in {"fast", "thinking"}:
                candidate = str((prefs or {}).get("default_run_mode") or "").strip().lower()
                if candidate == "deep":
                    candidate = "thinking"
                safe_run_mode = candidate if candidate in {"fast", "thinking"} else "fast"
            safe_thinking = bool(thinking_mode) if thinking_mode is not None else safe_run_mode != "fast"
            previous_cm_current = getattr(cm, "current_conversation_id", None)
            # 运行模式（work_mode）：沿用 terminal 当前值（与正常创建路径一致）；
            # plan 档不变量 ⇒ 权限必须只读，同时记录进入前权限供离开 plan 恢复
            safe_work_mode = getattr(terminal, "get_work_mode", lambda: "plan")()
            if safe_work_mode not in ("plan", "ask", "execute"):
                safe_work_mode = "plan"
            # 权限模式同样沿用 terminal 当前值（/new 切换已同步到 terminal，见
            # _sync_workspace_terminal_mode）；个性化 default_permission_mode 仅在
            # terminal 首次构造时生效，不能在此覆盖用户切换结果
            safe_permission_mode = getattr(terminal, "get_permission_mode", lambda: "unrestricted")()
            if safe_permission_mode not in ("readonly", "approval", "auto_approval", "unrestricted"):
                safe_permission_mode = "unrestricted"
            safe_pre_plan_permission = None
            if safe_work_mode == "plan":
                if safe_permission_mode != "readonly":
                    safe_pre_plan_permission = safe_permission_mode
                safe_permission_mode = "readonly"
            # 推理强度优先级：body 显式值 > terminal 当前档位 > 个性化默认值
            if body_effort_provided:
                default_effort = body_effort
            else:
                terminal_effort = getattr(terminal, "reasoning_effort", None)
                if isinstance(terminal_effort, str) and terminal_effort.strip().lower() in REASONING_EFFORT_LEVELS:
                    default_effort = terminal_effort.strip().lower()
                else:
                    default_effort = (prefs or {}).get("default_reasoning_effort")
                    if isinstance(default_effort, str):
                        default_effort = default_effort.strip().lower() or None
                        if default_effort not in REASONING_EFFORT_LEVELS:
                            default_effort = None
                    else:
                        default_effort = None
            conversation_id = cm.create_conversation(
                project_path=str(workspace.project_path),
                thinking_mode=safe_thinking,
                run_mode=safe_run_mode,
                initial_messages=[],
                model_key=(prefs or {}).get("default_model") or getattr(terminal, "model_key", None),
                metadata_overrides={
                    "permission_mode": safe_permission_mode,
                    "execution_mode": getattr(terminal, "get_execution_mode", lambda: "sandbox")(),
                    "work_mode": safe_work_mode,
                    "pre_plan_permission_mode": safe_pre_plan_permission,
                    "multi_agent_mode": bool(multi_agent_mode),
                    "reasoning_effort": default_effort,
                },
            )
            try:
                cm.current_conversation_id = previous_cm_current
            except Exception:
                pass
            # 同步 terminal 级别的多智能体开关，避免新对话继承旧状态
            terminal.multi_agent_mode = bool(multi_agent_mode)
            try:
                if hasattr(terminal, "sub_agent_manager"):
                    terminal.sub_agent_manager.multi_agent_mode = bool(multi_agent_mode)
            except Exception:
                pass
            result = {
                "success": True,
                "conversation_id": conversation_id,
                "message": tr("conversation.created_detail", conversation_id=conversation_id),
                "safe_navigation": True,
            }
        else:
            # body 显式携带档位时权威覆盖（prefer_defaults 路径内部会应用 prefs 默认值，
            # 必须以用户实际选择为准）
            create_meta_overrides = {"multi_agent_mode": bool(multi_agent_mode)}
            if body_effort_provided:
                create_meta_overrides["reasoning_effort"] = body_effort
            result = terminal.create_new_conversation(
                thinking_mode=thinking_mode,
                run_mode=run_mode,
                metadata_overrides=create_meta_overrides,
            )
            # 同步 terminal 级别的多智能体开关，避免新对话继承旧状态
            terminal.multi_agent_mode = bool(multi_agent_mode)
            try:
                if hasattr(terminal, "sub_agent_manager"):
                    terminal.sub_agent_manager.multi_agent_mode = bool(multi_agent_mode)
            except Exception:
                pass

        if result["success"]:
            # 仅在当前工作区创建时更新 session 模式；指定其他工作区时由前端切换后自动同步。
            if not target_workspace_id:
                session['run_mode'] = terminal.run_mode
                session['thinking_mode'] = terminal.thinking_mode
            perf_log("create_conversation before default versioning", elapsed_ms=(time.perf_counter() - t0) * 1000, extra={"conv_id": result.get("conversation_id")})
            # 根据个性化设置，为新对话默认开启版本控制（安全导航路径也需要）。
            # create_new_conversation 内部已完成初始化时跳过，避免每个新对话初始化两遍。
            try:
                prefs = load_personalization_config(workspace.data_dir)
                if bool(prefs.get("versioning_enabled_by_default", True)) and not result.get("versioning_initialized"):
                    _ensure_conversation_versioning_enabled(terminal, workspace, result["conversation_id"])
            except Exception as exc:
                debug_log(f"[Versioning] create_conversation apply default failed: {exc}")
            perf_log("create_conversation after default versioning", elapsed_ms=(time.perf_counter() - t0) * 1000, extra={"conv_id": result.get("conversation_id")})
            # 广播对话列表更新事件
            socketio.emit('conversation_list_update', {
                'action': 'created',
                'conversation_id': result["conversation_id"]
            }, room=f"user_{username}")

            if not result.get("safe_navigation"):
                # 安全导航创建不代表后端 terminal 当前对话已切换，因此不广播 conversation_changed。
                socketio.emit('conversation_changed', {
                    'conversation_id': result["conversation_id"],
                    'title': tr("conversation.default_title")
                }, room=f"user_{username}")

            perf_log("create_conversation route done", elapsed_ms=(time.perf_counter() - t0) * 1000, extra={"conv_id": result.get("conversation_id")})
            return jsonify(result), 201
        else:
            perf_log("create_conversation route failed", elapsed_ms=(time.perf_counter() - t0) * 1000)
            return jsonify(result), 500
            
    except Exception as e:
        logger.error(f"[API] 创建对话错误: {e}")
        return jsonify({
            "success": False,
            "error": str(e),
            "message": tr("conversation.create_exception")
        }), 500

@conversation_bp.route('/api/conversations/<conversation_id>', methods=['GET'])
@api_login_required
@with_terminal
def get_conversation_info(terminal: WebTerminal, workspace: UserWorkspace, username: str, conversation_id):
    """获取特定对话信息"""
    try:
        # 通过ConversationManager直接获取对话数据
        conversation_data = terminal.context_manager._get_conversation_manager_for_id(conversation_id).load_conversation(conversation_id)
        
        if conversation_data:
            # 提取关键信息，不返回完整消息内容（避免数据量过大）
            info = {
                "id": conversation_data["id"],
                "title": conversation_data["title"],
                "created_at": conversation_data["created_at"],
                "updated_at": conversation_data["updated_at"],
                "metadata": conversation_data["metadata"],
                "messages_count": len(conversation_data.get("messages", []))
            }
            
            return jsonify({
                "success": True,
                "data": info
            })
        else:
            return jsonify({
                "success": False,
                "error": "Conversation not found",
                "message": tr("conversation.not_found_detail", conversation_id=conversation_id)
            }), 404
            
    except Exception as e:
        logger.error(f"[API] 获取对话信息错误: {e}")
        return jsonify({
            "success": False,
            "error": str(e),
            "message": tr("conversation.get_info_exception")
        }), 500

@conversation_bp.route('/api/conversations/<conversation_id>/load', methods=['PUT'])
@api_login_required
@with_terminal
def load_conversation(conversation_id, terminal: WebTerminal, workspace: UserWorkspace, username: str):
    """加载特定对话，支持通过 workspace_id 指定目标工作区/项目。"""
    try:
        target_workspace_id = request.args.get('workspace_id', '', type=str).strip()
        if target_workspace_id:
            try:
                terminal, workspace = _resolve_target_terminal_for_workspace(
                    username, target_workspace_id, terminal, workspace
                )
            except ValueError as exc:
                return jsonify({"success": False, "error": str(exc)}), 404
            except RuntimeError as exc:
                return jsonify({"success": False, "error": str(exc)}), 503

        cm = getattr(getattr(terminal, "context_manager", None), "conversation_manager", None)
        write_host_workspace_debug(
            "sidebar-debug-api",
            api="PUT /api/conversations/load",
            conversation_id=conversation_id,
            target_workspace_id=target_workspace_id or None,
            session_workspace_id=session.get("workspace_id"),
            terminal_project_path=str(getattr(terminal, "project_path", None)),
            cm_workspace_id=getattr(cm, "current_workspace_id", None),
            cm_conversations_dir=str(getattr(cm, "conversations_dir", None)),
        )
        workspace_id = target_workspace_id or session.get("workspace_id") or "default"
        active_task = _get_active_workspace_task(username=username, workspace_id=workspace_id)
        if active_task:
            # 同工作区有任务运行时，加载/查看其他对话不能改后端 terminal 当前上下文。
            # 否则运行任务后续保存与事件归属会被切到当前查看的对话。
            result = _build_safe_load_result(terminal, conversation_id)
        else:
            result = terminal.load_conversation(conversation_id)

        if result["success"]:
            session['run_mode'] = terminal.run_mode
            session['thinking_mode'] = terminal.thinking_mode
            session['model_key'] = getattr(terminal, "model_key", None)
            normalized_id = _normalize_conv_id(conversation_id)
            try:
                vm = _get_conv_versioning_manager(workspace, normalized_id)
                vmeta = _get_conversation_versioning_meta(terminal, normalized_id)
                tracking_mode = _normalize_versioning_tracking_mode(vmeta.get("tracking_mode"))
                enabled = bool(vmeta.get("enabled")) and _can_use_versioning_scope(username, tracking_mode)
                latest_checkpoint = vm.get_latest_checkpoint() if enabled else None
                latest_commit = (latest_checkpoint or {}).get("tree_hash") if latest_checkpoint else None
                result["versioning"] = {
                    "host_mode": _is_host_mode_request(username),
                    "enabled": enabled,
                    "mode": "overwrite",
                    "tracking_mode": tracking_mode,
                    "latest_seq": int((latest_checkpoint or {}).get("seq") or 0) if latest_checkpoint else None,
                    "latest_commit": latest_commit,
                }
            except Exception as exc:
                debug_log(f"[Versioning] load status 读取失败: {exc}")
                result["versioning"] = {
                    "host_mode": _is_host_mode_request(username),
                    "enabled": False,
                    "mode": "overwrite",
                    "tracking_mode": ConversationVersioningManager.TRACKING_MODE_WORKSPACE_AND_CONVERSATION,
                    "error": str(exc),
                }

            if not result.get("safe_navigation"):
                # 安全导航只改变前端查看对象，不代表后端 terminal 当前上下文已切换。
                socketio.emit('conversation_changed', {
                    'conversation_id': conversation_id,
                    'title': result.get("title", tr("conversation.unknown_title")),
                    'messages_count': result.get("messages_count", 0)
                }, room=f"user_{username}")

                # 广播系统状态更新（因为当前对话改变了）
                status = terminal.get_status()
                socketio.emit('status_update', status, room=f"user_{username}")

                # 清理和重置相关UI状态
                socketio.emit('conversation_loaded', {
                    'conversation_id': conversation_id,
                    'clear_ui': True  # 提示前端清理当前UI状态
                }, room=f"user_{username}")

            return jsonify(result)
        else:
            write_host_workspace_debug(
                "sidebar-debug-api",
                api="PUT /api/conversations/load",
                conversation_id=conversation_id,
                session_workspace_id=session.get("workspace_id"),
                terminal_project_path=str(getattr(terminal, "project_path", None)),
                cm_workspace_id=getattr(cm, "current_workspace_id", None),
                cm_conversations_dir=str(getattr(cm, "conversations_dir", None)),
                result_message=result.get("message", ""),
                not_found=True,
            )
            status_code = 404 if _is_not_found_message(result.get("message", "")) else 500
            return jsonify(result), status_code

    except Exception as e:
        import traceback
        logger.error(f"[API] 加载对话错误: {e}")
        traceback.print_exc()
        return jsonify({
            "success": False,
            "error": str(e),
            "message": tr("conversation.load_exception")
        }), 500

@conversation_bp.route('/api/conversations/<conversation_id>', methods=['DELETE'])
@api_login_required
@with_terminal
def delete_conversation(conversation_id, terminal: WebTerminal, workspace: UserWorkspace, username: str):
    """删除特定对话，支持通过 workspace_id 指定目标工作区/项目。"""
    try:
        target_workspace_id = request.args.get('workspace_id', '', type=str).strip()
        if target_workspace_id:
            try:
                terminal, workspace = _resolve_target_terminal_for_workspace(
                    username, target_workspace_id, terminal, workspace
                )
            except ValueError as exc:
                return jsonify({"success": False, "error": str(exc)}), 404
            except RuntimeError as exc:
                return jsonify({"success": False, "error": str(exc)}), 503

        # 检查是否是当前对话
        is_current = (terminal.context_manager.current_conversation_id == conversation_id)

        result = terminal.delete_conversation(conversation_id)
        
        if result["success"]:
            # 广播对话列表更新事件
            socketio.emit('conversation_list_update', {
                'action': 'deleted',
                'conversation_id': conversation_id
            }, room=f"user_{username}")
            
            # 如果删除的是当前对话，广播对话清空事件
            if is_current:
                socketio.emit('conversation_changed', {
                    'conversation_id': None,
                    'title': None,
                    'cleared': True
                }, room=f"user_{username}")
                
                # 更新系统状态
                status = terminal.get_status()
                socketio.emit('status_update', status, room=f"user_{username}")
            
            return jsonify(result)
        else:
            return jsonify(result), 404 if _is_not_found_message(result.get("message", "")) else 500
            
    except Exception as e:
        logger.error(f"[API] 删除对话错误: {e}")
        return jsonify({
            "success": False,
            "error": str(e),
            "message": tr("conversation.delete_exception")
        }), 500

@conversation_bp.route('/api/conversations/search', methods=['GET'])
@api_login_required
@with_terminal
def search_conversations(terminal: WebTerminal, workspace: UserWorkspace, username: str):
    """搜索对话。

    默认仅搜索当前工作区，返回扁平结果；
    all_workspaces=1 时跨当前用户的全部工作区搜索，按工作区分组返回。
    multi_agent_mode: '1' 仅多智能体对话；'0' 仅常规对话；未传不过滤。
    """
    try:
        query = request.args.get('q', '').strip()
        limit = request.args.get('limit', 20, type=int)
        search_all = request.args.get('all_workspaces', '0') in ('1', 'true', 'True')
        ma_param = request.args.get('multi_agent_mode', None)
        if ma_param in ('1', 'true', 'True'):
            multi_agent_filter: Optional[bool] = True
        elif ma_param in ('0', 'false', 'False'):
            multi_agent_filter = False
        else:
            multi_agent_filter = None

        if not query:
            return jsonify({
                "success": False,
                "error": "Missing query parameter",
                "message": tr("conversation.search_query_required")
            }), 400

        # 限制参数范围
        limit = max(1, min(limit, 50))

        if not search_all:
            result = terminal.search_conversations(query, limit, multi_agent_mode=multi_agent_filter)
            return jsonify({
                "success": True,
                "data": {
                    "results": result["results"],
                    "count": result["count"],
                    "query": query
                }
            })

        # 跨工作区搜索：收集全部工作区后逐区搜索，按工作区分组返回
        if _is_host_mode_request(username):
            catalog, _current_host_ws = resolve_host_workspace()
            workspace_entries = [
                (str(ws.get("workspace_id") or ""), str(ws.get("label") or ws.get("workspace_id") or ""))
                for ws in (catalog.get("workspaces") or [])
            ]
        else:
            user_workspaces = user_manager.list_user_workspaces(username) or {}
            workspace_entries = [
                (str(ws_id), str((info or {}).get("label") or ws_id))
                for ws_id, info in user_workspaces.items()
            ]

        groups = []
        total = 0
        seen_ids = set()
        for ws_id, label in workspace_entries:
            if not ws_id or ws_id in seen_ids:
                continue
            seen_ids.add(ws_id)
            try:
                target_terminal, _target_workspace = _resolve_target_terminal_for_workspace(
                    username, ws_id, terminal, workspace
                )
            except (ValueError, RuntimeError) as resolve_err:
                logger.error(f"[API] 搜索跳过工作区 {ws_id}: {resolve_err}")
                continue
            try:
                result = target_terminal.search_conversations(query, limit, multi_agent_mode=multi_agent_filter)
            except Exception as search_err:
                logger.error(f"[API] 搜索工作区对话失败 {ws_id}: {search_err}")
                continue
            results = result.get("results") or []
            if not results:
                continue
            groups.append({
                "workspace_id": ws_id,
                "label": label or ws_id,
                "count": len(results),
                "results": results,
            })
            total += len(results)

        return jsonify({
            "success": True,
            "data": {
                "query": query,
                "groups": groups,
                "total": total
            }
        })
            
    except Exception as e:
        logger.error(f"[API] 搜索对话错误: {e}")
        return jsonify({
            "success": False,
            "error": str(e),
            "message": tr("conversation.search_exception")
        }), 500

@conversation_bp.route('/api/conversations/<conversation_id>/messages', methods=['GET'])
@api_login_required
@with_terminal
def get_conversation_messages(conversation_id, terminal: WebTerminal, workspace: UserWorkspace, username: str):
    """获取对话的消息历史（可选功能，用于调试或详细查看）"""
    try:
        # 获取完整对话数据
        conversation_data = terminal.context_manager._get_conversation_manager_for_id(conversation_id).load_conversation(conversation_id)
        
        if conversation_data:
            messages = conversation_data.get("messages", [])
            
            # 可选：限制消息数量，避免返回过多数据
            limit = request.args.get('limit', type=int)
            if limit:
                messages = messages[-limit:]  # 获取最后N条消息

            # 快捷窗口：本次对话编辑/创建过的文件记录（过滤不存在的文件）
            edited_files: list = []
            raw_edited = (conversation_data.get("metadata") or {}).get("edited_files")
            if isinstance(raw_edited, list):
                file_manager = getattr(terminal, "file_manager", None)
                for item in raw_edited:
                    if not isinstance(item, dict):
                        continue
                    rel_path = str(item.get("path") or "").strip()
                    if not rel_path:
                        continue
                    try:
                        valid, _err, full_path = file_manager._validate_path(rel_path) if file_manager else (False, None, None)
                    except Exception:
                        valid, full_path = False, None
                    if valid and full_path is not None and full_path.exists() and full_path.is_file():
                        edited_files.append({
                            "path": rel_path,
                            "op": item.get("op") or "edit",
                            "ts": item.get("ts") or "",
                        })

            return jsonify({
                "success": True,
                "data": {
                    "conversation_id": conversation_id,
                    "messages": messages,
                    "total_count": len(conversation_data.get("messages", [])),
                    "edited_files": edited_files
                }
            })
        else:
            return jsonify({
                "success": False,
                "error": "Conversation not found",
                "message": tr("conversation.not_found_detail", conversation_id=conversation_id)
            }), 404
            
    except Exception as e:
        logger.error(f"[API] 获取对话消息错误: {e}")
        return jsonify({
            "success": False,
            "error": str(e),
            "message": tr("conversation.get_messages_exception")
        }), 500


@conversation_bp.route('/api/conversations/media/<path:media_id>', methods=['GET'])
@api_login_required
@with_terminal
def download_conversation_media(media_id, terminal: WebTerminal, workspace: UserWorkspace, username: str):
    """按 media_store 中的 media_id 下载会话媒体。"""
    try:
        ctx = getattr(terminal, "context_manager", None)
        media_store = getattr(ctx, "media_store", None)
        if media_store is None:
            return jsonify({"success": False, "error": tr("conversation.media_store_unavailable")}), 503

        target_id = str(media_id or "").strip()
        if not target_id:
            return jsonify({"success": False, "error": tr("conversation.media_id_required")}), 400

        entry = media_store.get_media_entry(target_id)
        if not isinstance(entry, dict):
            return jsonify({"success": False, "error": tr("conversation.media_not_found")}), 404
        payload = media_store.load_bytes_by_media_id(target_id)
        if payload is None:
            return jsonify({"success": False, "error": tr("conversation.media_file_not_found")}), 404

        mime_type = str(entry.get("mime_type") or "application/octet-stream").strip() or "application/octet-stream"
        blob_name = str(entry.get("blob_rel_path") or "")
        filename = Path(blob_name).name if blob_name else target_id.replace(":", "_")
        return send_file(
            BytesIO(payload),
            mimetype=mime_type,
            as_attachment=False,
            download_name=filename,
            conditional=True,
            etag=True,
            max_age=86400,
        )
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@conversation_bp.route('/api/conversations/<conversation_id>/versioning', methods=['GET'])
@api_login_required
@with_terminal
def get_conversation_versioning_status(conversation_id, terminal: WebTerminal, workspace: UserWorkspace, username: str):
    try:
        normalized_id = _normalize_conv_id(conversation_id)
        host_mode = _is_host_mode_request(username)
        versioning_meta = _get_conversation_versioning_meta(terminal, normalized_id)
        tracking_mode = _normalize_versioning_tracking_mode(versioning_meta.get("tracking_mode"))
        enabled = bool(versioning_meta.get("enabled")) and _can_use_versioning_scope(username, tracking_mode)
        manager = _get_conv_versioning_manager(workspace, normalized_id)
        latest = manager.get_latest_checkpoint() if enabled else None
        return jsonify({
            "success": True,
            "data": {
                "conversation_id": normalized_id,
                "host_mode": host_mode,
                "supports_workspace_tracking": host_mode,
                "supports_conversation_only": True,
                "enabled": enabled,
                "mode": "overwrite",
                "tracking_mode": tracking_mode,
                "latest_seq": int((latest or {}).get("seq") or 0) if latest else None,
                "latest_commit": (latest or {}).get("tree_hash"),
            }
        })
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@conversation_bp.route('/api/conversations/<conversation_id>/versioning', methods=['POST'])
@api_login_required
@with_terminal
def update_conversation_versioning(conversation_id, terminal: WebTerminal, workspace: UserWorkspace, username: str):
    try:
        normalized_id = _normalize_conv_id(conversation_id)
        host_mode = _is_host_mode_request(username)
        current_meta = _get_conversation_versioning_meta(terminal, normalized_id)
        payload = request.get_json(silent=True) or {}
        enabled = bool(payload.get("enabled"))
        mode = "overwrite"
        tracking_mode = _normalize_versioning_tracking_mode(
            payload.get("tracking_mode") if "tracking_mode" in payload else current_meta.get("tracking_mode")
        )
        if enabled and tracking_mode == ConversationVersioningManager.TRACKING_MODE_WORKSPACE_AND_CONVERSATION and not host_mode:
            tracking_mode = ConversationVersioningManager.TRACKING_MODE_CONVERSATION_ONLY

        manager = _get_conv_versioning_manager(workspace, normalized_id)
        meta = manager.set_enabled(enabled=enabled, mode=mode, tracking_mode=tracking_mode)
        if enabled:
            conv_data = terminal.context_manager._get_conversation_manager_for_id(normalized_id).load_conversation(normalized_id) or {}
            snapshot_payload = {
                "conversation_id": normalized_id,
                "title": conv_data.get("title"),
                "metadata": conv_data.get("metadata") or {},
                "messages": conv_data.get("messages") or [],
                "message_index": -1,
                "run_status": "initial",
            }
            init_result = manager.ensure_initial_checkpoint(
                workspace_path=str(workspace.project_path),
                conversation_snapshot=snapshot_payload,
                tracking_mode=tracking_mode,
            )
            init_row = init_result.get("row") or {}
            debug_log(
                f"[Versioning][Init] conv={normalized_id} enabled={enabled} "
                f"tracking_mode={tracking_mode} "
                f"created={init_result.get('created')} reason={init_result.get('reason')} "
                f"seq={init_row.get('seq')} commit={init_row.get('commit')}"
            )
            if init_row.get("tree_hash"):
                meta["last_tree_hash"] = init_row.get("tree_hash")
        ok = _update_conversation_versioning_meta(
            terminal,
            normalized_id,
            enabled=enabled,
            mode=mode,
            tracking_mode=tracking_mode,
            last_commit=meta.get("last_commit"),
            last_input_seq=int(meta.get("last_input_seq") or 0),
        )
        if not ok:
            return jsonify({"success": False, "error": tr("conversation.update_versioning_failed")}), 404
        return jsonify({
            "success": True,
            "data": {
                "conversation_id": normalized_id,
                "enabled": bool(meta.get("enabled")),
                "mode": "overwrite",
                "tracking_mode": tracking_mode,
                "last_commit": meta.get("last_commit"),
                "last_input_seq": int(meta.get("last_input_seq") or 0),
            }
        })
    except VersioningError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@conversation_bp.route('/api/conversations/<conversation_id>/versioning/checkpoints', methods=['GET'])
@api_login_required
@with_terminal
def list_conversation_versioning_checkpoints(conversation_id, terminal: WebTerminal, workspace: UserWorkspace, username: str):
    try:
        normalized_id = _normalize_conv_id(conversation_id)
        host_mode = _is_host_mode_request(username)
        versioning_meta = _get_conversation_versioning_meta(terminal, normalized_id)
        tracking_mode = _normalize_versioning_tracking_mode(versioning_meta.get("tracking_mode"))
        if tracking_mode == ConversationVersioningManager.TRACKING_MODE_WORKSPACE_AND_CONVERSATION and not host_mode:
            return jsonify({"success": False, "error": tr("conversation.versioning_workspace_point_unsupported")}), 400
        if not versioning_meta.get("enabled"):
            return jsonify({
                "success": True,
                "data": {"enabled": False, "mode": "overwrite", "tracking_mode": tracking_mode, "items": []}
            })
        manager = _get_conv_versioning_manager(workspace, normalized_id)
        rows = manager.list_checkpoints()
        backup_mode = str(versioning_meta.get("backup_mode") or "shallow").strip().lower()
        if backup_mode == "shallow":
            shallow_manager = ShallowVersioningManager(
                project_path=workspace.project_path,
                data_dir=workspace.data_dir,
                conversation_id=normalized_id,
            )
            normalized_rows: List[Dict[str, Any]] = []
            for row in rows:
                row = dict(row)
                shallow_message_id = row.get("shallow_message_id")
                if not shallow_message_id:
                    snapshot_file = row.get("snapshot_file")
                    if snapshot_file:
                        try:
                            snapshot_path = (Path(workspace.data_dir) / "save" / normalized_id / str(snapshot_file)).resolve()
                            if snapshot_path.exists():
                                snapshot_data = json.loads(snapshot_path.read_text(encoding="utf-8", errors="ignore"))
                                shallow_message_id = snapshot_data.get("shallow_message_id")
                        except Exception:
                            pass
                if shallow_message_id:
                    stats = shallow_manager.get_diff_stats(str(shallow_message_id)) or {}
                    files = shallow_manager.get_file_diff_stats(str(shallow_message_id)) or []
                    row["insertions"] = int(stats.get("insertions") or 0)
                    row["deletions"] = int(stats.get("deletions") or 0)
                    row["files_changed"] = len(files)
                    row["files"] = files
                normalized_rows.append(row)
            rows = normalized_rows
        return jsonify({
            "success": True,
            "data": {
                "enabled": True,
                "mode": "overwrite",
                "tracking_mode": tracking_mode,
                "items": rows,
            }
        })
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@conversation_bp.route('/api/conversations/<conversation_id>/versioning/checkpoints/<int:seq>', methods=['GET'])
@api_login_required
@with_terminal
def get_conversation_versioning_checkpoint_detail(conversation_id, seq: int, terminal: WebTerminal, workspace: UserWorkspace, username: str):
    try:
        normalized_id = _normalize_conv_id(conversation_id)
        host_mode = _is_host_mode_request(username)
        vmeta = _get_conversation_versioning_meta(terminal, normalized_id)
        tracking_mode = _normalize_versioning_tracking_mode(vmeta.get("tracking_mode"))
        if tracking_mode == ConversationVersioningManager.TRACKING_MODE_WORKSPACE_AND_CONVERSATION and not host_mode:
            return jsonify({"success": False, "error": tr("conversation.versioning_workspace_detail_unsupported")}), 400
        manager = _get_conv_versioning_manager(workspace, normalized_id)
        row = manager.get_checkpoint_detail(seq, include_patch=True)
        if not row:
            return jsonify({"success": False, "error": tr("conversation.checkpoint_not_found")}), 404

        backup_mode = str(vmeta.get("backup_mode") or "shallow").strip().lower()
        debug_log(f"[Versioning][Detail] conv={normalized_id} seq={seq} backup_mode={backup_mode} row_shallow_msg_id={row.get('shallow_message_id')} files_count={len(row.get('files') or [])}")
        if backup_mode == "shallow":
            shallow_message_id = row.get("shallow_message_id")
            # 兼容旧检查点：shallow_message_id 之前只写在 snapshot_file 里
            if not shallow_message_id:
                snapshot_file = row.get("snapshot_file")
                if snapshot_file:
                    try:
                        snapshot_path = (Path(workspace.data_dir) / "save" / normalized_id / str(snapshot_file)).resolve()
                        if snapshot_path.exists():
                            snapshot_data = json.loads(snapshot_path.read_text(encoding="utf-8", errors="ignore"))
                            shallow_message_id = snapshot_data.get("shallow_message_id")
                    except Exception:
                        pass
            debug_log(f"[Versioning][Detail] resolved shallow_message_id={shallow_message_id}")
            if shallow_message_id:
                shallow_manager = ShallowVersioningManager(
                    project_path=workspace.project_path,
                    data_dir=workspace.data_dir,
                    conversation_id=normalized_id,
                )
                # 与列表接口保持一致：从 shallow manager 重新计算文件变更，
                # 而不是只给 inputs.jsonl 中原有的 files 补 patch_lines。
                files = shallow_manager.get_file_diff_stats(str(shallow_message_id)) or []
                stats = shallow_manager.get_diff_stats(str(shallow_message_id)) or {}
                normalized_files: List[Dict[str, Any]] = []
                for file_item in files:
                    if not isinstance(file_item, dict):
                        continue
                    item = dict(file_item)
                    path = str(item.get("path") or "")
                    if path:
                        patch = shallow_manager.get_file_patch_lines(path, str(shallow_message_id))
                        debug_log(f"[Versioning][Detail] path={path} patch_lines={len(patch.get('lines') or [])}")
                        item["patch_lines"] = patch.get("lines") or []
                        item["patch_truncated"] = bool(patch.get("truncated"))
                    normalized_files.append(item)
                row["files"] = normalized_files
                row["insertions"] = int(stats.get("insertions") or 0)
                row["deletions"] = int(stats.get("deletions") or 0)
                row["files_changed"] = len(normalized_files)
        debug_log(f"[Versioning][Detail] response files={row.get('files')}")
        return jsonify({"success": True, "data": row})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


def _copy_versioning_records(source_conversation_id: str, target_conversation_id: str, data_dir: Path) -> bool:
    """把源对话的版本控制记录（checkpoints / snapshots / git / meta）复制到目标对话。"""
    source_dir = (Path(data_dir) / "save" / source_conversation_id).resolve()
    target_dir = (Path(data_dir) / "save" / target_conversation_id).resolve()
    if not source_dir.exists():
        return False
    try:
        if target_dir.exists():
            shutil.rmtree(target_dir)
        shutil.copytree(source_dir, target_dir)
        # 复制后保持目标 meta 与源一致，无需修改 conversation_id（manager 按路径构造）。
        return True
    except Exception as exc:
        debug_log(f"[Versioning] copy records failed {source_conversation_id} -> {target_conversation_id}: {exc}")
        return False


def _restore_checkpoint_to_conversation(
    terminal: WebTerminal,
    workspace: UserWorkspace,
    source_conversation_id: str,
    target_conversation_id: str,
    seq: int,
    tracking_mode: str,
    host_mode: bool,
    backup_mode: Optional[str] = None,
) -> Dict[str, Any]:
    """把源对话的指定 checkpoint 恢复到目标对话，覆盖目标对话内容。"""
    source_manager = _get_conv_versioning_manager(workspace, source_conversation_id)
    target_manager = _get_conv_versioning_manager(workspace, target_conversation_id)
    checkpoint = source_manager.get_checkpoint(seq)
    if not checkpoint:
        raise VersioningError(tr("conversation.checkpoint_not_found"))

    normalized_backup_mode = "full" if backup_mode == "full" else "shallow"
    if normalized_backup_mode == "shallow":
        # 浅备份模式：根据检查点行中的 shallow_message_id 恢复被跟踪文件
        shallow_message_id = checkpoint.get("shallow_message_id")
        if shallow_message_id:
            shallow_manager = ShallowVersioningManager(
                project_path=workspace.project_path,
                data_dir=workspace.data_dir,
                conversation_id=source_conversation_id,
            )
            rewind_result = shallow_manager.rewind(str(shallow_message_id))
            debug_log(
                f"[Versioning][Restore] shallow rewind conv={target_conversation_id} seq={seq} "
                f"msg_id={shallow_message_id} files_changed={len(rewind_result.get('files_changed') or [])}"
            )
        else:
            debug_log(
                f"[Versioning][Restore] shallow checkpoint missing message_id "
                f"conv={target_conversation_id} seq={seq}"
            )
    elif tracking_mode == ConversationVersioningManager.TRACKING_MODE_WORKSPACE_AND_CONVERSATION:
        if not host_mode:
            raise VersioningError(tr("conversation.versioning_workspace_restore_unsupported"))
        target_manager.restore_to_tree(checkpoint.get("tree_hash"))
        debug_log(
            f"[Versioning][Restore] git restored conv={target_conversation_id} seq={seq} "
            f"commit={checkpoint.get('commit')}"
        )
    else:
        debug_log(
            f"[Versioning][Restore] conversation-only mode, skip workspace restore "
            f"conv={target_conversation_id} seq={seq}"
        )

    cm = terminal.context_manager.conversation_manager
    conv_data = cm.load_conversation(source_conversation_id) or {}
    conv_meta = conv_data.get("metadata") or {}

    snapshot = source_manager.get_checkpoint_snapshot(seq)
    snapshot_messages = (snapshot or {}).get("messages")
    if not isinstance(snapshot_messages, list):
        snapshot_messages = None
    snapshot_meta = (snapshot or {}).get("metadata")
    if not isinstance(snapshot_meta, dict):
        snapshot_meta = {}

    if snapshot_messages is None:
        all_messages = conv_data.get("messages") or []
        msg_index = int(checkpoint.get("message_index") or 0)
        snapshot_messages = all_messages[: max(0, msg_index + 1)]
        debug_log(
            f"[Versioning][Restore] fallback truncation conv={target_conversation_id} seq={seq} "
            f"msg_index={msg_index} restored_messages={len(snapshot_messages)}"
        )
    else:
        debug_log(
            f"[Versioning][Restore] snapshot loaded conv={target_conversation_id} seq={seq} "
            f"messages={len(snapshot_messages)} meta_keys={list(snapshot_meta.keys())[:8]}"
        )

    restore_meta = dict(conv_meta or {})
    if snapshot_meta:
        restore_meta.update(snapshot_meta)

    restore_project_path = restore_meta.get("project_path") or str(workspace.project_path)
    restore_thinking_mode = bool(restore_meta.get("thinking_mode", False))
    restore_run_mode = restore_meta.get("run_mode") or ("thinking" if restore_thinking_mode else "fast")
    restore_reasoning_effort = restore_meta.get("reasoning_effort")
    restore_model_key = restore_meta.get("model_key")
    restore_has_images = bool(restore_meta.get("has_images", False))
    restore_has_videos = bool(restore_meta.get("has_videos", False))

    ok = cm.save_conversation(
        conversation_id=target_conversation_id,
        messages=snapshot_messages,
        project_path=restore_project_path,
        thinking_mode=restore_thinking_mode,
        run_mode=restore_run_mode,
        reasoning_effort=restore_reasoning_effort,
        model_key=restore_model_key,
        has_images=restore_has_images,
        has_videos=restore_has_videos,
        # 恢复旧检查点是唯一合法的消息数缩减路径，显式豁免防回退守卫
        allow_shrink=True,
    )
    if not ok:
        raise VersioningError(tr("conversation.restore_save_failed"))

    prune_info = target_manager.prune_checkpoints_after(seq)
    resolved_last_commit = prune_info.get("last_tree_hash") or checkpoint.get("tree_hash")
    if not resolved_last_commit:
        resolved_last_commit = (target_manager.load_meta() or {}).get("last_tree_hash")

    _update_conversation_versioning_meta(
        terminal,
        target_conversation_id,
        enabled=True,
        mode="overwrite",
        tracking_mode=tracking_mode,
        last_commit=resolved_last_commit,
        last_input_seq=int(prune_info.get("max_seq") or checkpoint.get("seq") or 0),
    )
    cm.update_conversation_metadata(
        target_conversation_id,
        {
            **restore_meta,
            "versioning": {
                "enabled": True,
                "mode": "overwrite",
                "tracking_mode": tracking_mode,
                "last_commit": resolved_last_commit,
                "last_input_seq": int(prune_info.get("max_seq") or checkpoint.get("seq") or 0),
                "updated_at": datetime.now().isoformat(),
            },
        },
    )
    debug_log(
        f"[Versioning][Restore] saved conv={target_conversation_id} seq={seq} "
        f"messages={len(snapshot_messages)}"
    )
    return {
        "restored_seq": int(checkpoint.get("seq") or 0),
        "restored_commit": checkpoint.get("tree_hash"),
        "tracking_mode": tracking_mode,
    }


@conversation_bp.route('/api/conversations/<conversation_id>/versioning/restore', methods=['POST'])
@api_login_required
@with_terminal
def restore_conversation_versioning_checkpoint(conversation_id, terminal: WebTerminal, workspace: UserWorkspace, username: str):
    try:
        normalized_id = _normalize_conv_id(conversation_id)
        host_mode = _is_host_mode_request(username)
        payload = request.get_json(silent=True) or {}
        seq = int(payload.get("seq") or 0)
        if seq < 0:
            return jsonify({"success": False, "error": tr("conversation.seq_required")}), 400

        vmeta = _get_conversation_versioning_meta(terminal, normalized_id)
        if not vmeta.get("enabled"):
            return jsonify({"success": False, "error": tr("conversation.versioning_not_enabled")}), 400

        restore_mode = str(payload.get("mode") or "overwrite").lower()
        if restore_mode not in {"overwrite", "copy"}:
            restore_mode = "overwrite"

        requested_tracking_mode = _normalize_versioning_tracking_mode(payload.get("tracking_mode"))

        manager = _get_conv_versioning_manager(workspace, normalized_id)
        checkpoint = manager.get_checkpoint(seq)
        if not checkpoint:
            return jsonify({"success": False, "error": tr("conversation.checkpoint_not_found")}), 404

        # 默认使用 checkpoint 创建时的 tracking_mode；若前端显式指定则优先采用。
        tracking_mode = _normalize_versioning_tracking_mode(
            requested_tracking_mode or checkpoint.get("tracking_mode") or vmeta.get("tracking_mode")
        )
        if tracking_mode == ConversationVersioningManager.TRACKING_MODE_WORKSPACE_AND_CONVERSATION and not host_mode:
            return jsonify({"success": False, "error": tr("conversation.versioning_workspace_restore_unsupported")}), 400

        cm = terminal.context_manager.conversation_manager
        target_conversation_id = normalized_id

        if restore_mode == "copy":
            duplicate_result = terminal.context_manager.duplicate_conversation(normalized_id)
            if not duplicate_result.get("success"):
                return jsonify({"success": False, "error": duplicate_result.get("error", tr("conversation.duplicate_failed"))}), 500
            target_conversation_id = _normalize_conv_id(duplicate_result["duplicate_conversation_id"])
            copied = _copy_versioning_records(normalized_id, target_conversation_id, workspace.data_dir)
            if not copied:
                # 复制记录失败时继续执行，只是版本控制记录无法继承；后续仍可恢复对话内容。
                debug_log(f"[Versioning][Restore] copy records skipped for {target_conversation_id}")

        debug_log(
            f"[Versioning][Restore] start conv={normalized_id} seq={seq} mode={restore_mode} "
            f"tracking_mode={tracking_mode} target={target_conversation_id} "
            f"target_commit={checkpoint.get('commit')} workspace={workspace.project_path}"
        )

        backup_mode = str(vmeta.get("backup_mode") or "shallow").strip().lower()
        restore_info = _restore_checkpoint_to_conversation(
            terminal, workspace, normalized_id, target_conversation_id, seq, tracking_mode, host_mode, backup_mode
        )

        # overwrite 场景：回溯已裁短磁盘，必须同步所有持有该对话的内存实例（对话级
        # terminal 常驻 24h），否则旧内存后续保存经 merge 会把被裁消息「救回」，回溯被撤销。
        # copy 场景目标是新对话，无缓存实例，无需处理。
        if restore_mode == "overwrite":
            _sync_restored_conversation_memory(target_conversation_id)

        # 恢复模式与焦点到当前（工作区级）terminal；服务实例不挂载历史，仅读磁盘元数据
        terminal.load_conversation(target_conversation_id)
        debug_log(
            f"[Versioning][Restore] reload done conv={target_conversation_id} "
            f"messages={len(getattr(terminal.context_manager, 'conversation_history', []) or [])}"
        )
        session['run_mode'] = terminal.run_mode
        session['thinking_mode'] = terminal.thinking_mode

        socketio.emit('conversation_list_update', {
            'action': 'version_restored',
            'conversation_id': target_conversation_id
        }, room=f"user_{username}")
        socketio.emit('conversation_changed', {
            'conversation_id': target_conversation_id,
            'title': (cm.load_conversation(target_conversation_id) or {}).get("title", tr("conversation.version_restored_title")),
        }, room=f"user_{username}")

        return jsonify({
            "success": True,
            "data": {
                "restore_mode": restore_mode,
                "conversation_id": target_conversation_id,
                "restored_seq": restore_info["restored_seq"],
                "restored_commit": restore_info["restored_commit"],
                "tracking_mode": restore_info["tracking_mode"],
                "source_conversation_id": normalized_id,
            }
        })
    except VersioningError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@conversation_bp.route('/api/conversations/<conversation_id>/compress', methods=['POST'])
@api_login_required
@with_terminal
def compress_conversation(conversation_id, terminal: WebTerminal, workspace: UserWorkspace, username: str):
    """深层压缩指定对话（in-place）：生成 compact 文件、标记历史前缀为已压缩，按设置决定是否续接。"""
    try:
        policy = resolve_admin_policy(get_current_user_record())
        if policy.get("ui_blocks", {}).get("block_compress_conversation"):
            return jsonify({"success": False, "error": tr("conversation.compress_blocked_by_admin")}), 403
        normalized_id = conversation_id if conversation_id.startswith('conv_') else f"conv_{conversation_id}"
        # 对话级隔离：压缩必须在该对话的专属 terminal 上执行。
        # with_terminal 只从 query/body 取 conversation_id，本路由的 id 在路径里，
        # 默认拿到的是工作区级 terminal——直接用它会把该对话加载进工作区级
        # terminal，与对话级 terminal 形成双持同一对话（可能互相串写历史）。
        try:
            conv_terminal, _ = get_user_resources(username, conversation_id=normalized_id)
            if conv_terminal is not None:
                terminal = conv_terminal
        except RuntimeError as exc:
            return jsonify({"success": False, "error": str(exc), "code": "resource_busy"}), 503
        result = asyncio.run(
            run_deep_compression(
                web_terminal=terminal,
                workspace=workspace,
                conversation_id=normalized_id,
                mode="manual",
                sender=None,
            )
        )

        if not result.get("success"):
            status_code = 404 if _is_not_found_message(result.get("error", "")) else (409 if result.get("in_progress") else 400)
            return jsonify(result), status_code

        # in-place 压缩：对话 id 不变。通知前端当前对话内容已变化（历史前缀被标记），刷新展示。
        load_result = terminal.load_conversation(normalized_id)
        if load_result.get("success"):
            socketio.emit('conversation_list_update', {
                'action': 'compressed',
                'conversation_id': normalized_id
            }, room=f"user_{username}")
            socketio.emit('conversation_loaded', {
                'conversation_id': normalized_id,
                'clear_ui': True
            }, room=f"user_{username}")

        response_payload = {
            "success": True,
            "in_place": True,
            "compressed_conversation_id": normalized_id,
            "compact_file": result.get("compact_file"),
            "summary_failed": result.get("summary_failed", False),
            "guide_message": result.get("guide_message"),
            "compress_form": result.get("compress_form"),
            # 手动压缩只有一种行为：生成压缩消息（引导语），不自动续接，等待用户继续发送消息才工作。
            "compress_behavior": "wait",
            "load_result": load_result
        }

        guide_message = (result.get("guide_message") or "").strip()
        if guide_message:
            # 只把引导语作为 user 消息追加进历史，不触发请求。
            try:
                terminal.context_manager.add_conversation(
                    role="user",
                    content=guide_message,
                    metadata={"message_source": "compression_handoff"},
                )
                response_payload["auto_task_started"] = False
                response_payload["guide_inserted"] = True
                # 通知前端实时显示这条 compact 消息（覆盖 socket 与 in-place 未刷新场景）
                try:
                    emit(
                        "user_message",
                        {
                            "message": guide_message,
                            "images": [],
                            "videos": [],
                            "media_refs": [],
                            "message_source": "compression_handoff",
                            "visibility": "compact",
                            "starts_work": False,
                            "metadata": {
                                "message_source": "compression_handoff",
                                "visibility": "compact",
                                "starts_work": False,
                            },
                            "conversation_id": normalized_id,
                        },
                        room=f"user_{username}",
                    )
                except Exception as emit_exc:
                    debug_log(f"[Compression] 发送 user_message 事件失败: {emit_exc}")
            except Exception as exc:
                debug_log(f"[Compression] 追加引导语消息失败: {exc}")
                response_payload["auto_task_started"] = False
                response_payload["guide_inserted"] = False
                response_payload["guide_insert_error"] = str(exc)
        else:
            response_payload["auto_task_started"] = False

        return jsonify(response_payload)

    except Exception as e:
        logger.error(f"[API] 压缩对话错误: {e}")
        return jsonify({
            "success": False,
            "error": str(e),
            "message": tr("conversation.compress_exception")
        }), 500


@conversation_bp.route('/api/conversations/<conversation_id>/compression_status', methods=['GET'])
@api_login_required
@with_terminal
def get_conversation_compression_status(conversation_id, terminal: WebTerminal, workspace: UserWorkspace, username: str):
    try:
        normalized_id = conversation_id if conversation_id.startswith('conv_') else f"conv_{conversation_id}"
        target_manager = terminal.context_manager._get_conversation_manager_for_id(normalized_id)
        data = target_manager.load_conversation(normalized_id) or {}
        meta = data.get("metadata", {}) or {}
        # 与 /api/status 同一套懒清理：进程重启后残留的压缩标记按 pid 判活。
        compression_in_progress = heal_stale_compression_flag(
            target_manager, normalized_id, meta, context_manager=terminal.context_manager
        )
        return jsonify({
            "success": True,
            "data": {
                "conversation_id": normalized_id,
                "compression_in_progress": compression_in_progress,
                "compression_mode": meta.get("compression_mode") if compression_in_progress else None,
                "compression_stage": meta.get("compression_stage") if compression_in_progress else None,
                "compression_error": meta.get("compression_error"),
                "compression_count": int(meta.get("compression_count", 0) or 0),
                "compression_job_id": meta.get("compression_job_id") if compression_in_progress else None,
            }
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@conversation_bp.route('/api/conversations/<conversation_id>/compression_cancel', methods=['POST'])
@api_login_required
@with_terminal
def cancel_conversation_compression(conversation_id, terminal: WebTerminal, workspace: UserWorkspace, username: str):
    try:
        normalized_id = conversation_id if conversation_id.startswith('conv_') else f"conv_{conversation_id}"
        ok = terminal.context_manager._get_conversation_manager_for_id(
            normalized_id).update_conversation_metadata(
            normalized_id, {
                "compression_in_progress": False,
                "compression_mode": None,
                "compression_stage": None,
                "compression_resume_payload": None,
                "compression_error": tr("conversation.compression_cancelled_error"),
            }
        )
        if not ok:
            return jsonify({"success": False, "error": tr("conversation.not_found_or_cancel_failed")}), 404
        return jsonify({"success": True, "conversation_id": normalized_id})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@conversation_bp.route('/api/sub_agents', methods=['GET'])
@api_login_required
@with_terminal
def list_sub_agents(terminal: WebTerminal, workspace: UserWorkspace, username: str):
    """返回当前对话的子智能体任务列表。"""
    manager = getattr(terminal, "sub_agent_manager", None)
    if not manager:
        return jsonify({"success": True, "data": []})
    try:
        try:
            # 防止不同进程创建的子智能体未被当前进程感知
            manager._load_state()
        except Exception:
            pass
        conversation_id = terminal.context_manager.current_conversation_id
        data = manager.get_overview(conversation_id=conversation_id)

        # 传统模式子智能体列表必须排除多智能体任务，避免 /new 等无当前对话场景
        # 把最近一次多智能体模式的 running/idle 子智能体串过来。
        def _is_multi_agent_task(item: dict) -> bool:
            task_id = item.get("task_id")
            raw_task = manager.tasks.get(task_id) if task_id else None
            return bool(raw_task.get("multi_agent_mode")) if isinstance(raw_task, dict) else False

        data = [item for item in data if not _is_multi_agent_task(item)]

        # 仅在未绑定具体对话时（如全新会话尚未创建 conversation_id），才回退为全局运行态；
        # 否则只显示当前对话关联的子智能体，避免新对话看到其他对话的任务。
        if not data and not conversation_id:
            all_overview = manager.get_overview(conversation_id=None)
            if all_overview:
                terminal_statuses = TERMINAL_STATUSES.union({"terminated"})
                running_only = [
                    item for item in all_overview
                    if item.get("status") not in terminal_statuses and not _is_multi_agent_task(item)
                ]
                if running_only:
                    data = running_only
        debug_log(
            "[SubAgent] /api/sub_agents overview "
            + json.dumps({
                "conversation_id": conversation_id,
                "count": len(data),
                "tasks": [
                    {
                        "task_id": item.get("task_id"),
                        "status": item.get("status"),
                        "run_in_background": item.get("run_in_background"),
                        "conversation_id": item.get("conversation_id"),
                    } for item in data
                ],
            }, ensure_ascii=False)
        )
        if not hasattr(terminal, "_announced_sub_agent_tasks"):
            terminal._announced_sub_agent_tasks = set()
        announced = terminal._announced_sub_agent_tasks
        notified_from_history = set()
        try:
            # 服务实例（工作区级）不挂载历史：通知去重标记以磁盘对话为准
            history = []
            if conversation_id:
                notify_manager = terminal.context_manager._get_conversation_manager_for_id(conversation_id)
                notify_conv_data = notify_manager.load_conversation(conversation_id) or {}
                history = notify_conv_data.get("messages") or []
            for msg in history:
                meta = msg.get("metadata") or {}
                task_id = meta.get("task_id")
                if meta.get("sub_agent_notice") and task_id:
                    notified_from_history.add(task_id)
        except Exception:
            notified_from_history = set()
        for item in data:
            task_id = item.get("task_id")
            raw_task = manager.tasks.get(task_id) if task_id else None
            run_in_background = bool(raw_task.get("run_in_background")) if isinstance(raw_task, dict) else False
            item["run_in_background"] = run_in_background
            status = item.get("status")
            notified_flag = bool(raw_task.get("notified")) if isinstance(raw_task, dict) else False
            already_notified = (
                (task_id in announced) or
                (task_id in notified_from_history) or
                notified_flag
            )
            notice_pending = (
                run_in_background
                and task_id
                and not already_notified
                and (status in TERMINAL_STATUSES or status == "terminated")
            )
            item["notice_pending"] = notice_pending
        debug_log(
            "[SubAgent] /api/sub_agents notice_pending computed "
            + json.dumps({
                "conversation_id": conversation_id,
                "tasks": [
                    {
                        "task_id": item.get("task_id"),
                        "status": item.get("status"),
                        "run_in_background": item.get("run_in_background"),
                        "notice_pending": item.get("notice_pending"),
                    } for item in data
                ],
            }, ensure_ascii=False)
        )
        return jsonify({"success": True, "data": data})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@conversation_bp.route('/api/sub_agents/<task_id>/activity', methods=['GET'])
@api_login_required
@with_terminal
def get_sub_agent_activity(task_id: str, terminal: WebTerminal, workspace: UserWorkspace, username: str):
    """返回指定子智能体的活动记录（进度）。"""
    manager = getattr(terminal, "sub_agent_manager", None)
    if not manager:
        return jsonify({"success": False, "error": tr("conversation.sub_agent_manager_unavailable")}), 404
    try:
        try:
            manager._load_state()
        except Exception:
            pass

        task = manager.tasks.get(task_id)
        if not task:
            return jsonify({"success": False, "error": tr("conversation.sub_agent_task_not_found")}), 404

        progress_file = task.get("progress_file")
        if not progress_file:
            task_root = task.get("task_root")
            if task_root:
                progress_file = str(Path(task_root) / "progress.jsonl")

        entries: List[Dict[str, Any]] = []
        # 默认返回足够多的记录，确保历史工具调用完整展示；同时保留上限防止异常大文件拖垮响应。
        limit = request.args.get("limit", "100000")
        try:
            limit_num = max(1, min(int(limit), 100000))
        except Exception:
            limit_num = 100000

        if progress_file and Path(progress_file).exists():
            try:
                lines = Path(progress_file).read_text(encoding="utf-8").splitlines()
                if limit_num:
                    lines = lines[-limit_num:]
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entries.append(json.loads(line))
                    except Exception:
                        continue
            except Exception as exc:
                return jsonify({"success": False, "error": tr("conversation.read_progress_failed", error=str(exc))}), 500

        payload = {
            "task_id": task_id,
            "status": task.get("status"),
            "entries": entries,
        }
        return jsonify({"success": True, "data": payload})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@conversation_bp.route('/api/sub_agents/stop_all', methods=['POST'])
@api_login_required
@with_terminal
def stop_all_sub_agents(terminal: WebTerminal, workspace: UserWorkspace, username: str):
    """批量停止当前会话的所有非终态子智能体。

    body: {"mode": "terminate" | "soft_stop"}
      - mode=terminate: 传统模式终结子智能体（重、不保留实例）
      - mode=soft_stop: 多智能体模式软停止（轻、保留实例可被后续唤醒）
    """
    data = request.get_json(silent=True) or {}
    mode = str(data.get('mode') or 'terminate').strip().lower()
    if mode not in {'terminate', 'soft_stop'}:
        return jsonify({"success": False, "error": tr("conversation.unsupported_stop_mode")}), 400
    manager = getattr(terminal, 'sub_agent_manager', None)
    if not manager:
        return jsonify({"success": False, "error": tr("conversation.sub_agent_manager_unavailable")}), 404
    conversation_id = terminal.context_manager.current_conversation_id
    try:
        try:
            manager._load_state()
        except Exception:
            pass
        manager.reconcile_task_states(conversation_id=conversation_id)
        stopped_count = 0
        if mode == 'soft_stop':
            stopped_count = manager.soft_stop_all_agents(conversation_id)
            debug_log(f"[StopAllSubAgents] soft_stop 会话={conversation_id}, 计数={stopped_count}")
        else:
            # terminate: 遍历所有非终态子智能体逐个终结
            from modules.sub_agent.state import TERMINAL_STATUSES
            for task_info in list(manager.tasks.values()):
                if task_info.get('conversation_id') != conversation_id:
                    continue
                status = task_info.get('status')
                if status in TERMINAL_STATUSES.union({"terminated"}):
                    continue
                try:
                    tid = task_info.get('task_id')
                    manager.terminate_sub_agent(task_id=tid)
                    stopped_count += 1
                    debug_log(f"[StopAllSubAgents] terminated task_id={tid}")
                except Exception as exc:
                    debug_log(f"[StopAllSubAgents] 终止失败 tid={task_info.get('task_id')}, err={exc}")
        return jsonify({"success": True, "data": {"stopped_count": stopped_count, "mode": mode}})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@conversation_bp.route('/api/sub_agents/<task_id>/terminate', methods=['POST'])
@api_login_required
@with_terminal
def terminate_sub_agent(task_id: str, terminal: WebTerminal, workspace: UserWorkspace, username: str):
    """手动停止指定子智能体。"""
    manager = getattr(terminal, "sub_agent_manager", None)
    if not manager:
        return jsonify({"success": False, "error": tr("conversation.sub_agent_manager_unavailable")}), 404
    try:
        try:
            manager._load_state()
        except Exception:
            pass

        task = manager.tasks.get(task_id)
        if not task:
            return jsonify({"success": False, "error": tr("conversation.sub_agent_task_not_found")}), 404

        current_conv_id = terminal.context_manager.current_conversation_id
        task_conv_id = task.get("conversation_id")
        if current_conv_id and task_conv_id and task_conv_id != current_conv_id:
            return jsonify({"success": False, "error": tr("conversation.sub_agent_stop_forbidden")}), 403

        result = manager.terminate_sub_agent(task_id=task_id)
        if not result.get("success"):
            return jsonify({"success": False, "error": result.get("error") or tr("conversation.sub_agent_stop_failed")}), 400
        return jsonify({"success": True, "data": result})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@conversation_bp.route('/api/background_commands', methods=['GET'])
@api_login_required
@with_terminal
def list_background_commands(terminal: WebTerminal, workspace: UserWorkspace, username: str):
    """返回当前对话的后台 run_command 列表。"""
    manager = getattr(terminal, "background_command_manager", None)
    if not manager:
        return jsonify({"success": True, "data": []})
    try:
        conversation_id = terminal.context_manager.current_conversation_id
        limit_raw = request.args.get("limit", "200")
        try:
            limit_num = max(1, min(int(limit_raw), 1000))
        except Exception:
            limit_num = 200

        records = manager.list_records(conversation_id=conversation_id, limit=limit_num)
        data = []
        terminal_statuses = {"completed", "failed", "timeout", "cancelled"}
        for rec in records:
            result = rec.get("result") if isinstance(rec.get("result"), dict) else {}
            status = rec.get("status")
            notice_pending = bool(
                status in terminal_statuses
                and not rec.get("notified")
                and not rec.get("claimed_by_sleep")
            )
            data.append({
                "command_id": rec.get("command_id"),
                "status": status,
                "command": rec.get("command"),
                "conversation_id": rec.get("conversation_id"),
                "created_at": rec.get("created_at"),
                "updated_at": rec.get("updated_at"),
                "finished_at": rec.get("finished_at"),
                "timeout": rec.get("timeout"),
                "return_code": result.get("return_code"),
                "run_in_background": True,
                "notice_pending": notice_pending,
            })
        return jsonify({"success": True, "data": data})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@conversation_bp.route('/api/background_commands/<command_id>', methods=['GET'])
@api_login_required
@with_terminal
def get_background_command_detail(command_id: str, terminal: WebTerminal, workspace: UserWorkspace, username: str):
    """返回指定后台 run_command 的详情（含实时输出）。"""
    manager = getattr(terminal, "background_command_manager", None)
    if not manager:
        return jsonify({"success": False, "error": tr("conversation.background_command_manager_unavailable")}), 404
    try:
        rec = manager.get_record_with_output(command_id)
        if not rec:
            return jsonify({"success": False, "error": tr("conversation.background_command_not_found")}), 404

        current_conv_id = terminal.context_manager.current_conversation_id
        rec_conv_id = rec.get("conversation_id")
        if current_conv_id and rec_conv_id and rec_conv_id != current_conv_id:
            return jsonify({"success": False, "error": tr("conversation.background_command_access_forbidden")}), 403

        result = rec.get("result") if isinstance(rec.get("result"), dict) else {}
        payload = {
            "command_id": rec.get("command_id"),
            "status": rec.get("status"),
            "command": rec.get("command"),
            "conversation_id": rec.get("conversation_id"),
            "created_at": rec.get("created_at"),
            "updated_at": rec.get("updated_at"),
            "finished_at": rec.get("finished_at"),
            "timeout": rec.get("timeout"),
            "return_code": result.get("return_code"),
            "message": result.get("message"),
            "output": rec.get("output") or "",
            "run_in_background": True,
        }
        return jsonify({"success": True, "data": payload})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@conversation_bp.route('/api/background_commands/<command_id>/cancel', methods=['POST'])
@api_login_required
@with_terminal
def cancel_background_command(command_id: str, terminal: WebTerminal, workspace: UserWorkspace, username: str):
    """手动停止指定后台 run_command。"""
    manager = getattr(terminal, "background_command_manager", None)
    if not manager:
        return jsonify({"success": False, "error": tr("conversation.background_command_manager_unavailable")}), 404
    try:
        rec = manager.get_record(command_id)
        if not rec:
            return jsonify({"success": False, "error": tr("conversation.background_command_not_found")}), 404
        current_conv_id = terminal.context_manager.current_conversation_id
        rec_conv_id = rec.get("conversation_id")
        if current_conv_id and rec_conv_id and rec_conv_id != current_conv_id:
            return jsonify({"success": False, "error": tr("conversation.background_command_stop_forbidden")}), 403

        result = manager.cancel_command(command_id)
        if not result.get("status"):
            return jsonify({"success": False, "error": result.get("error") or tr("conversation.background_command_stop_failed")}), 400
        return jsonify({"success": True, "data": result})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@conversation_bp.route('/api/conversations/<conversation_id>/duplicate', methods=['POST'])
@api_login_required
@with_terminal
def duplicate_conversation(conversation_id, terminal: WebTerminal, workspace: UserWorkspace, username: str):
    """复制指定对话，生成新的对话副本"""

    try:
        result = terminal.context_manager.duplicate_conversation(conversation_id)

        if not result.get("success"):
            status_code = 404 if _is_not_found_message(result.get("error", "")) else 400
            return jsonify(result), status_code

        new_conversation_id = result["duplicate_conversation_id"]
        load_result = terminal.load_conversation(new_conversation_id)

        if load_result.get("success"):
            socketio.emit('conversation_list_update', {
                'action': 'duplicated',
                'conversation_id': new_conversation_id
            }, room=f"user_{username}")
            socketio.emit('conversation_changed', {
                'conversation_id': new_conversation_id,
                'title': load_result.get('title', tr('conversation.duplicated_title')),
                'messages_count': load_result.get('messages_count', 0)
            }, room=f"user_{username}")
            socketio.emit('conversation_loaded', {
                'conversation_id': new_conversation_id,
                'clear_ui': True
            }, room=f"user_{username}")

        response_payload = {
            "success": True,
            "duplicate_conversation_id": new_conversation_id,
            "load_result": load_result
        }

        return jsonify(response_payload)

    except Exception as e:
        logger.error(f"[API] 复制对话错误: {e}")
        return jsonify({
            "success": False,
            "error": str(e),
            "message": tr("conversation.duplicate_exception")
        }), 500


@conversation_bp.route('/api/conversations/<conversation_id>/review_preview', methods=['GET'])
@api_login_required
@with_terminal
def review_conversation_preview(conversation_id, terminal: WebTerminal, workspace: UserWorkspace, username: str):
    """生成对话回顾预览（不落盘，只返回前若干行文本）"""
    policy = resolve_admin_policy(get_current_user_record())
    if policy.get("ui_blocks", {}).get("block_conversation_review"):
        return jsonify({"success": False, "error": tr("conversation.review_blocked_by_admin")}), 403
    try:
        current_id = terminal.context_manager.current_conversation_id
        if conversation_id == current_id:
            return jsonify({
                "success": False,
                "message": tr("conversation.cannot_review_current_conversation")
            }), 400

        conversation_data = terminal.context_manager._get_conversation_manager_for_id(conversation_id).load_conversation(conversation_id)
        if not conversation_data:
            return jsonify({
                "success": False,
                "error": "Conversation not found",
                "message": tr("conversation.not_found_detail", conversation_id=conversation_id)
            }), 404

        limit = request.args.get('limit', default=20, type=int) or 20
        lines = build_review_lines(conversation_data.get("messages", []), limit=limit)

        return jsonify({
            "success": True,
            "data": {
                "preview": lines,
                "count": len(lines)
            }
        })
    except Exception as e:
        logger.error(f"[API] 对话回顾预览错误: {e}")
        return jsonify({
            "success": False,
            "error": str(e),
            "message": tr("conversation.review_preview_exception")
        }), 500


@conversation_bp.route('/api/conversations/<conversation_id>/review', methods=['POST'])
@api_login_required
@with_terminal
def review_conversation(conversation_id, terminal: WebTerminal, workspace: UserWorkspace, username: str):
    """生成完整对话回顾 Markdown 文件"""
    policy = resolve_admin_policy(get_current_user_record())
    if policy.get("ui_blocks", {}).get("block_conversation_review"):
        return jsonify({"success": False, "error": tr("conversation.review_blocked_by_admin")}), 403
    try:
        current_id = terminal.context_manager.current_conversation_id
        if conversation_id == current_id:
            return jsonify({
                "success": False,
                "message": tr("conversation.cannot_review_current_conversation")
            }), 400

        conversation_data = terminal.context_manager._get_conversation_manager_for_id(conversation_id).load_conversation(conversation_id)
        if not conversation_data:
            return jsonify({
                "success": False,
                "error": "Conversation not found",
                "message": tr("conversation.not_found_detail", conversation_id=conversation_id)
            }), 404

        messages = conversation_data.get("messages", [])
        lines = build_review_lines(messages)
        content = "\n".join(lines) + "\n"
        char_count = len(content)

        review_dir = workspace.project_path / ".astrion" / "review"
        review_dir.mkdir(parents=True, exist_ok=True)

        title = conversation_data.get("title") or "untitled"
        safe_title = _sanitize_filename_component(title)
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        filename = f"review_{safe_title}_{timestamp}.md"
        target = review_dir / filename

        target.write_text(content, encoding='utf-8')

        return jsonify({
            "success": True,
            "data": {
                "path": f".astrion/review/{filename}",
                "char_count": char_count
            }
        })
    except Exception as e:
        logger.error(f"[API] 对话回顾生成错误: {e}")
        return jsonify({
            "success": False,
            "error": str(e),
            "message": tr("conversation.review_generate_exception")
        }), 500

@conversation_bp.route('/api/conversations/statistics', methods=['GET'])
@api_login_required
@with_terminal
def get_conversations_statistics(terminal: WebTerminal, workspace: UserWorkspace, username: str):
    """获取对话统计信息"""
    try:
        stats = terminal.context_manager.get_conversation_statistics()
        
        return jsonify({
            "success": True,
            "data": stats
        })
            
    except Exception as e:
        logger.error(f"[API] 获取对话统计错误: {e}")
        return jsonify({
            "success": False,
            "error": str(e),
            "message": tr("conversation.get_statistics_exception")
        }), 500

@conversation_bp.route('/api/conversations/current', methods=['GET'])
@api_login_required
@with_terminal
def get_current_conversation(terminal: WebTerminal, workspace: UserWorkspace, username: str):
    """获取当前对话信息"""
    current_id = terminal.context_manager.current_conversation_id
    
    # 如果是临时ID，返回空的对话信息
    if not current_id or current_id.startswith('temp_'):
        return jsonify({
            "success": True,
            "data": {
                "id": current_id,
                "title": tr("conversation.default_title"),
                "messages_count": 0,
                "is_temporary": True,
                "title_locked": False
            }
        })
    
    # 如果是真实的对话ID，查找对话数据
    try:
        conversation_data = terminal.context_manager._get_conversation_manager_for_id(current_id).load_conversation(current_id)
        if conversation_data:
            metadata = conversation_data.get("metadata", {}) or {}
            return jsonify({
                "success": True,
                "data": {
                    "id": current_id,
                    "title": conversation_data.get("title", tr("conversation.unknown_title")),
                    "messages_count": len(conversation_data.get("messages", [])),
                    "is_temporary": False,
                    "title_locked": bool(metadata.get("title_locked", False))
                }
            })
        else:
            return jsonify({
                "success": False,
                "error": tr("conversation.not_found")
            }), 404
            
    except Exception as e:
        logger.error(f"[API] 获取当前对话错误: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500
    
@socketio.on('send_command')
def handle_command(data):
    """处理系统命令"""
    command = data.get('command', '')
    
    username, terminal, _ = get_terminal_for_sid(request.sid)
    if not terminal:
        emit('error', {'message': 'System not initialized'})
        return
    record_user_activity(username)
    
    result = _execute_system_command(terminal, command)
    emit('command_result', result)


def _execute_system_command(terminal: WebTerminal, command: str) -> Dict[str, Any]:
    """执行系统命令，供 WebSocket 与 REST API 复用。"""
    command = (command or '').strip()
    if command.startswith('/'):
        command = command[1:]

    parts = command.split(maxsplit=1)
    cmd = parts[0].lower() if parts else ''

    if cmd == "clear":
        terminal.context_manager.conversation_history.clear()
        return {
            'command': cmd,
            'success': True,
            'message': tr('conversation.cleared')
        }

    if cmd == "status":
        status = terminal.get_status()
        if terminal.terminal_manager:
            status['terminals'] = terminal.terminal_manager.list_terminals()
        return {
            'command': cmd,
            'success': True,
            'data': status
        }

    if cmd == "terminals":
        if terminal.terminal_manager:
            return {
                'command': cmd,
                'success': True,
                'data': terminal.terminal_manager.list_terminals()
            }
        return {
            'command': cmd,
            'success': False,
            'message': tr('conversation.terminal_system_not_initialized')
        }

    return {
        'command': cmd or command,
        'success': False,
        'message': tr('conversation.unknown_command', command=cmd or command)
    }


@conversation_bp.route('/api/commands', methods=['POST'])
@api_login_required
@with_terminal
def execute_command_api(terminal: WebTerminal, workspace: UserWorkspace, username: str):
    """通过 REST API 执行系统命令（用于无 WebSocket 前端）。"""
    try:
        payload = request.get_json(silent=True) or {}
        command = (payload.get('command') or '').strip()
        if not command:
            return jsonify({
                "success": False,
                "message": tr("conversation.command_required")
            }), 400

        record_user_activity(username)
        result = _execute_system_command(terminal, command)
        status_code = 200 if result.get('success') else 400
        return jsonify(result), status_code
    except Exception as exc:
        return jsonify({
            "success": False,
            "message": tr("conversation.command_execution_exception", error=str(exc))
        }), 500

@conversation_bp.route('/api/conversations/<conversation_id>/token-statistics', methods=['GET'])
@api_login_required
@with_terminal
def get_conversation_token_statistics(conversation_id, terminal: WebTerminal, workspace: UserWorkspace, username: str):
    """获取特定对话的token统计"""
    try:
        stats = terminal.context_manager.get_conversation_token_statistics(conversation_id)
        
        if stats:
            return jsonify({
                "success": True,
                "data": stats
            })
        else:
            return jsonify({
                "success": False,
                "error": "Conversation not found",
                "message": tr("conversation.not_found_detail", conversation_id=conversation_id)
            }), 404
            
    except Exception as e:
        logger.error(f"[API] 获取token统计错误: {e}")
        return jsonify({
            "success": False,
            "error": str(e),
            "message": tr("conversation.get_token_statistics_exception")
        }), 500


@conversation_bp.route('/api/conversations/<conversation_id>/tokens', methods=['GET'])
@api_login_required
@with_terminal
def get_conversation_tokens(conversation_id, terminal: WebTerminal, workspace: UserWorkspace, username: str):
    """获取对话的当前完整上下文token数（包含所有动态内容）"""
    try:
        current_tokens = terminal.context_manager.get_current_context_tokens(conversation_id)
        return jsonify({
            "success": True,
            "data": {
                "total_tokens": current_tokens
            }
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500
