"""简单任务 API：将聊天任务与 WebSocket 解耦，支持后台运行与轮询。"""
from __future__ import annotations
from server.tasks.blueprint import tasks_bp
import mimetypes
import json
import time
import threading
import uuid
import traceback
from collections import deque
from pathlib import Path
from typing import Dict, Any, Optional, List

from flask import Blueprint, request, jsonify
from flask import current_app, session

from server.auth_helpers import api_login_required, get_current_username
from server.context import get_user_resources, ensure_conversation_loaded
from server.security import rate_limited
from server.state import stop_flags
from server.utils_common import debug_log, log_conn_diag
from utils.host_workspace_debug import write_host_workspace_debug
from config import DATA_DIR, WORKSPACE_SKILLS_DIRNAME
from modules.goal_state_manager import GoalStateManager, REASON_USER_CANCEL
from server.tasks import task_manager
from server.runtime import RuntimeContext, TaskParams, principal_from_session_snapshot, runtime_service
from server.tasks.skills import _build_skill_context_messages
from server.tasks.media import _normalize_media_payload, _normalize_files_payload
from modules.i18n import tr



@tasks_bp.route("/api/tasks", methods=["GET"])
@api_login_required
def list_tasks_api():
    username = get_current_username()
    workspace_id = (request.args.get("workspace_id") or "").strip() or None
    status_filter = (request.args.get("status") or "").strip().lower() or None
    # 筛选/排序/序列化统一由公共服务承担（run.list，协议 §4）
    return jsonify({
        "success": True,
        "data": runtime_service.list_runs(username, workspace_id, status=status_filter)
    })

@tasks_bp.route("/api/conversations/<conversation_id>/running-status", methods=["GET"])
@api_login_required
def get_conversation_running_status_api(conversation_id: str):
    """REST 对账接口：聚合某对话的完整运行状态。

    前端架构：事件流（250ms 任务轮询 + socket）负责即时性，本接口周期对账负责
    正确性，冲突以对账为准。聚合四类状态：主 task / 传统后台子智能体 / 后台命令 /
    多智能体实例与待消费消息。
    """
    username = get_current_username()
    conversation_id = (conversation_id or "").strip()
    if not conversation_id:
        return jsonify({"success": False, "error": tr("tasks.missing_conversation_id")}), 400

    # ① 主 task：内存中该对话是否有活动任务（list_runs 已按会话筛选 + created_at 倒序）
    active_runs = runtime_service.list_runs(username, conversation_id=conversation_id, status="active")
    main_rec = active_runs[0] if active_runs else None

    # ②③④ 需要 terminal（sub_agent_manager / background_command_manager 挂在 terminal 上）
    workspace_id = (
        (request.args.get("workspace_id") or "").strip()
        or (main_rec["workspace_id"] if main_rec else "")
        or (session.get("workspace_id") or "")
    ) or None
    bg_status = {
        "has_running_sub_agents": False,
        "has_running_background_commands": False,
        "has_running_multi_agent": False,
    }
    try:
        terminal, _workspace = get_user_resources(username, workspace_id=workspace_id, conversation_id=conversation_id)
    except Exception as exc:
        debug_log(f"[TaskAPI] running-status 获取终端失败: {exc}")
        terminal = None
    if terminal:
        bg_status = task_manager.get_conversation_running_status(terminal, conversation_id)

    is_main_running = main_rec is not None
    return jsonify({
        "success": True,
        "data": {
            "conversation_id": conversation_id,
            "is_main_running": is_main_running,
            "main_task_id": main_rec["task_id"] if main_rec else None,
            "main_task_type": (main_rec.get("task_type") or "chat") if main_rec else None,
            **bg_status,
            "is_truly_active": is_main_running or any(bg_status.values()),
        }
    })

@tasks_bp.route("/api/tasks", methods=["POST"])
@api_login_required
@rate_limited("chat_task_create", 30, 60, scope="user")
def create_task_api():
    username = get_current_username()
    workspace_id = session.get("workspace_id") or "default"
    payload = request.get_json() or {}
    message = (payload.get("message") or "").strip()
    from config import MAX_MESSAGE_CHARS
    if len(message) > MAX_MESSAGE_CHARS:
        return jsonify({"success": False, "error": tr("tasks.message_too_long")}), 400
    images, videos = _normalize_media_payload(payload.get("images") or [], payload.get("videos") or [])
    files = _normalize_files_payload(payload.get("files"))
    conversation_id = payload.get("conversation_id")
    if not message and not images and not videos:
        return jsonify({"success": False, "error": tr("tasks.message_empty")}), 400
    model_key = payload.get("model_key")
    thinking_mode = payload.get("thinking_mode")
    run_mode = payload.get("run_mode")
    message_source = payload.get("message_source")
    max_iterations = payload.get("max_iterations")
    goal_mode = bool(payload.get("goal_mode"))
    # 用户显式发送非目标模式消息时，若本对话仍有残留的活动目标，先停止它，
    # 避免本对话错误延续旧目标。目标状态是对话级的，不影响其他对话。
    if not goal_mode and conversation_id:
        try:
            _terminal, workspace = get_user_resources(username, workspace_id, conversation_id=conversation_id)
            if workspace:
                gsm = GoalStateManager(workspace.data_dir, conversation_id)
                if gsm.is_active():
                    gsm.mark_stopped("new_message_without_goal")
                    debug_log(f"[Goal] 新消息未开启目标模式，停止本对话残留目标状态")
        except Exception as exc:
            debug_log(f"[Goal] 新任务清理残留目标状态失败: {exc}")
    skill_context_messages: List[Dict[str, str]] = []
    raw_skill_refs = payload.get("skill_refs")
    if raw_skill_refs:
        try:
            debug_log(
                f"[SkillsAPI] create_task skill_refs type={type(raw_skill_refs).__name__} "
                f"count={len(raw_skill_refs) if isinstance(raw_skill_refs, list) else 'N/A'} "
                f"refs={raw_skill_refs!r}"
            )
            _terminal, workspace = get_user_resources(username, workspace_id, conversation_id=conversation_id)
            if not workspace:
                debug_log(f"[SkillsAPI] create_task workspace unavailable user={username} ws={workspace_id}")
                return jsonify({"success": False, "error": tr("tasks.workspace_unavailable")}), 400
            debug_log(
                f"[SkillsAPI] create_task workspace OK project_path={getattr(workspace, 'project_path', None)!r} "
                f"data_dir={getattr(workspace, 'data_dir', None)!r}"
            )
            skill_context_messages = _build_skill_context_messages(workspace, raw_skill_refs)
            debug_log(f"[SkillsAPI] create_task built {len(skill_context_messages)} skill context messages")
        except ValueError as exc:
            debug_log(f"[SkillsAPI] create_task ValueError: {exc}")
            return jsonify({"success": False, "error": str(exc)}), 400
        except Exception as exc:
            debug_log(f"[SkillsAPI] 读取技能上下文失败: {exc}")
            debug_log(f"[SkillsAPI] 读取技能上下文失败 traceback:\n{traceback.format_exc()}")
            return jsonify({"success": False, "error": tr("tasks.read_skill_failed")}), 500
    try:
        debug_log(
            "[TaskAPI] create_task payload "
            f"model_key={model_key!r} run_mode={run_mode!r} thinking_mode={thinking_mode!r} "
            f"conversation_id={conversation_id!r} images={len(images)} videos={len(videos)}"
        )
    except Exception:
        pass

    # 对话级隔离兜底已下沉至 RuntimeService.create_task（_ensure_conversation_for_chat）：
    # chat 任务未携带 conversation_id 时由服务层补建对话文件，适配层不再重复实现。

    # 公共任务入口（契约 docs/runtime_contract.md §4）：适配层在完成认证后
    # 从 Flask session 显式构造可信 principal，服务层不再回退读隐式上下文。
    try:
        ctx = RuntimeContext(
            principal=principal_from_session_snapshot(session, workspace_id, username=username),
            params=TaskParams(
                message=message,
                images=images,
                videos=videos,
                files=files or [],
                conversation_id=conversation_id,
                model_key=model_key,
                thinking_mode=thinking_mode,
                run_mode=run_mode,
                max_iterations=max_iterations,
                message_source=message_source,
                goal_mode=goal_mode,
                skill_context_messages=skill_context_messages,
            ),
        )
        rec = runtime_service.create_task(ctx)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except RuntimeError as exc:
        return jsonify({"success": False, "error": str(exc)}), 409
    return jsonify({
        "success": True,
        "data": {
            "task_id": rec.task_id,
            "workspace_id": rec.workspace_id,
            "status": rec.status,
            "created_at": rec.created_at,
            "conversation_id": rec.conversation_id,
        }
    }), 202

@tasks_bp.route("/api/tasks/<task_id>", methods=["GET"])
@api_login_required
def get_task_api(task_id: str):
    started_at = time.time()
    username = get_current_username()
    poll_req_id = request.headers.get("X-Task-Poll", "-")
    rec = runtime_service.get_task(username, task_id)
    if not rec:
        log_conn_diag(
            f"task-poll-missing req={poll_req_id} user={username} task_id={task_id}"
        )
        return jsonify({"success": False, "error": tr("tasks.task_not_found")}), 404
    try:
        offset = int(request.args.get("from", 0))
    except Exception:
        offset = 0
    # 经公共服务读取事件流（含 window_start 缺口检测水位，协议 §5.2）
    events, next_offset, _ev_err, ev_meta = runtime_service.get_task_events(username, task_id, offset)
    events = events or []
    next_offset = next_offset if next_offset is not None else offset
    window_start = (ev_meta or {}).get("window_start", 0)
    elapsed_ms = (time.time() - started_at) * 1000.0
    should_log = (
        offset == 0
        or len(events) > 0
        or rec.status != "running"
        or elapsed_ms >= 800
    )
    if should_log:
        log_conn_diag(
            "task-poll "
            f"req={poll_req_id} user={username} task_id={task_id} status={rec.status} "
            f"from={offset} events={len(events)} next_offset={next_offset} "
            f"elapsed_ms={elapsed_ms:.1f} slow={elapsed_ms >= 800}"
        )
    return jsonify({
        "success": True,
        "data": {
            "task_id": rec.task_id,
            "workspace_id": rec.workspace_id,
            "status": rec.status,
            "created_at": rec.created_at,
            "updated_at": rec.updated_at,
            "message": rec.message,
            "conversation_id": rec.conversation_id,
            "error": rec.error,
            "message_source": (rec.session_data or {}).get("message_source"),
            "goal_mode": bool((rec.session_data or {}).get("goal_mode")),
            "goal_progress": (rec.session_data or {}).get("goal_progress"),
            "events": events,
            "next_offset": next_offset,
            "window_start": window_start,
            "runtime_queued_messages": runtime_service.get_runtime_pending_messages(
                username, task_id
            ),
        }
    })

@tasks_bp.route("/api/tasks/<task_id>/cancel", methods=["POST"])
@api_login_required
def cancel_task_api(task_id: str):
    username = get_current_username()
    rec = runtime_service.get_task(username, task_id)
    if not rec:
        return jsonify({"success": False, "error": tr("tasks.task_not_found")}), 404
    ok = runtime_service.cancel_task(username, task_id)
    # 用户取消任务时，一并停止该工作区的目标模式，避免后续新对话继承旧目标。
    try:
        if ok and rec.workspace_id:
            _, workspace = get_user_resources(username, rec.workspace_id, conversation_id=rec.conversation_id)
            if workspace and rec.conversation_id:
                gsm = GoalStateManager(workspace.data_dir, rec.conversation_id)
                if gsm.is_active():
                    gsm.mark_stopped(REASON_USER_CANCEL)
                    debug_log(f"[Goal] 用户取消任务 {task_id}，同步停止本对话目标模式")
    except Exception as exc:
        debug_log(f"[Goal] 取消任务时停止目标模式失败: {exc}")
    return jsonify({"success": True})

@tasks_bp.route("/api/tasks/<task_id>/runtime_guidance", methods=["POST"])
@api_login_required
def enqueue_runtime_guidance_api(task_id: str):
    username = get_current_username()
    payload = request.get_json() or {}
    message = (payload.get("message") or "").strip()
    if not message:
        return jsonify({"success": False, "error": tr("tasks.guidance_content_empty")}), 400

    result = runtime_service.enqueue_runtime_guidance(username, task_id, message)
    if not result.get("success"):
        code = result.get("code") or "runtime_guidance_failed"
        if code == "task_not_found":
            return jsonify({"success": False, "error": result.get("error") or tr("tasks.task_not_found")}), 404
        if code in {"queue_full", "task_not_running"}:
            return jsonify({"success": False, "error": result.get("error") or tr("tasks.task_status_not_allowed")}), 409
        return jsonify({"success": False, "error": result.get("error") or tr("tasks.guidance_enqueue_failed")}), 400

    return jsonify(
        {
            "success": True,
            "data": {
                "task_id": result.get("task_id"),
                "queued_count": result.get("queued_count", 0),
            },
        }
    )

@tasks_bp.route("/api/tasks/<task_id>/runtime_queue", methods=["POST"])
@api_login_required
def enqueue_runtime_queue_message_api(task_id: str):
    username = get_current_username()
    payload = request.get_json() or {}
    message = (payload.get("message") or "").strip()
    if not message:
        return jsonify({"success": False, "error": tr("tasks.message_empty")}), 400
    files = _normalize_files_payload(payload.get("files"))
    result = runtime_service.enqueue_runtime_pending_message(username, task_id, message, files=files)
    if not result.get("success"):
        code = result.get("code") or "runtime_queue_enqueue_failed"
        if code == "task_not_found":
            return jsonify({"success": False, "error": result.get("error") or tr("tasks.task_not_found")}), 404
        if code in {"queue_full", "task_not_running"}:
            return jsonify({"success": False, "error": result.get("error") or tr("tasks.task_status_not_allowed")}), 409
        return jsonify({"success": False, "error": result.get("error") or tr("tasks.message_enqueue_failed")}), 400
    return jsonify(
        {
            "success": True,
            "data": {
                "task_id": result.get("task_id"),
                "item": result.get("item"),
                "messages": result.get("messages") or [],
            },
        }
    )

@tasks_bp.route("/api/tasks/<task_id>/runtime_queue/<message_id>", methods=["DELETE"])
@api_login_required
def delete_runtime_queue_message_api(task_id: str, message_id: str):
    username = get_current_username()
    result = runtime_service.remove_runtime_pending_message(username, task_id, message_id)
    if not result.get("success"):
        code = result.get("code") or "runtime_queue_delete_failed"
        if code == "task_not_found":
            return jsonify({"success": False, "error": result.get("error") or tr("tasks.task_not_found")}), 404
        if code == "message_not_found":
            return jsonify({"success": False, "error": result.get("error") or tr("tasks.message_not_found")}), 404
        return jsonify({"success": False, "error": result.get("error") or tr("tasks.delete_failed")}), 400
    return jsonify(
        {
            "success": True,
            "data": {
                "task_id": result.get("task_id"),
                "messages": result.get("messages") or [],
            },
        }
    )

@tasks_bp.route("/api/tasks/<task_id>/runtime_queue/<message_id>/guide", methods=["POST"])
@api_login_required
def guide_runtime_queue_message_api(task_id: str, message_id: str):
    username = get_current_username()
    result = runtime_service.promote_runtime_pending_to_guidance(username, task_id, message_id)
    if not result.get("success"):
        code = result.get("code") or "runtime_queue_guide_failed"
        if code == "task_not_found":
            return jsonify({"success": False, "error": result.get("error") or tr("tasks.task_not_found")}), 404
        if code == "message_not_found":
            return jsonify({"success": False, "error": result.get("error") or tr("tasks.message_not_found")}), 404
        if code in {"guidance_queue_full", "task_not_running"}:
            return jsonify({"success": False, "error": result.get("error") or tr("tasks.task_status_not_allowed")}), 409
        return jsonify({"success": False, "error": result.get("error") or tr("tasks.guidance_failed")}), 400
    return jsonify(
        {
            "success": True,
            "data": {
                "task_id": result.get("task_id"),
                "queued_count": result.get("queued_count", 0),
                "messages": result.get("messages") or [],
            },
        }
    )
