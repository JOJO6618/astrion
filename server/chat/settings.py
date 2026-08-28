from __future__ import annotations
from server.chat import chat_bp
import json, logging, time
from datetime import datetime
from typing import Dict, Any, Optional
from pathlib import Path
from io import BytesIO
import zipfile
import os

logger = logging.getLogger(__name__)

from flask import Blueprint, jsonify, request, session, send_file
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge
import secrets

from config import MAX_UPLOAD_SIZE, OUTPUT_FORMATS
from modules.personalization_manager import (
    load_personalization_config,
    resolve_context_compression_settings,
    save_personalization_config,
    RECENT_CONVERSATIONS_PROMPT_LIMIT_MIN,
    RECENT_CONVERSATIONS_PROMPT_LIMIT_MAX,
    PROJECT_MEMORY_INJECT_LIMIT_MIN,
)
from modules.skills_manager import (
    get_skills_catalog,
    infer_private_skills_dir,
    merge_enabled_skills,
    sync_workspace_skills,
)
from modules.upload_security import UploadSecurityError
from modules.host_sandbox_policy import load_policy, save_policy
from modules.user_manager import UserWorkspace
from core.web_terminal import WebTerminal
from config.model_profiles import get_model_context_window

from server.auth_helpers import api_login_required, resolve_admin_policy, get_current_user_record, get_current_username
from server.context import with_terminal, get_gui_manager, get_upload_guard, build_upload_error_response, ensure_conversation_loaded, get_or_create_usage_tracker
from server.security import rate_limited, prune_socket_tokens
from server.utils_common import debug_log
from server.state import PROJECT_MAX_STORAGE_MB, pending_socket_tokens, SOCKET_TOKEN_TTL_SECONDS
from server.state import tool_approval_manager, user_question_manager
from server.extensions import socketio
from server.monitor import get_cached_monitor_snapshot

from modules.i18n import tr

UPLOAD_FOLDER_NAME = ".astrion/user_upload"


def _save_conversation_meta_via_disk(ctx, conversation_id: str, **meta_kwargs) -> None:
    """设置类端点保存对话元数据的统一入口。

    工作区级服务实例不挂载消息历史（conversation_history 恒空），直接拿内存保存会把
    空/陈旧历史写回；这里读磁盘消息回传（merge 语义下消息保持不变），仅更新元数据。
    """
    if not conversation_id:
        return
    manager = ctx._get_conversation_manager_for_id(conversation_id)
    existing = manager.load_conversation(conversation_id) or {}
    manager.save_conversation(
        conversation_id=conversation_id,
        messages=existing.get("messages") or [],
        **meta_kwargs,
    )

@chat_bp.route('/api/thinking-mode', methods=['POST'])
@api_login_required
@with_terminal
@rate_limited("thinking_mode_toggle", 15, 60, scope="user")
def update_thinking_mode(terminal: WebTerminal, workspace: UserWorkspace, username: str):
    """切换运行模式（fast / thinking；旧值 deep 自动映射为 thinking）"""
    try:
        data = request.get_json() or {}
        requested_mode = data.get('mode')
        if isinstance(requested_mode, str) and requested_mode.lower() == 'deep':
            requested_mode = 'thinking'
        if requested_mode in {"fast", "thinking"}:
            target_mode = requested_mode
        elif 'thinking_mode' in data:
            target_mode = "thinking" if bool(data.get('thinking_mode')) else "fast"
        else:
            target_mode = terminal.run_mode
        terminal.set_run_mode(target_mode)
        session['thinking_mode'] = terminal.thinking_mode
        session['run_mode'] = terminal.run_mode
        # 更新当前对话的元数据（服务实例不挂载历史：读磁盘回传，仅更新元数据）
        ctx = terminal.context_manager
        if ctx.current_conversation_id:
            try:
                _save_conversation_meta_via_disk(
                    ctx,
                    ctx.current_conversation_id,
                    project_path=str(ctx.project_path),
                    thinking_mode=terminal.thinking_mode,
                    run_mode=terminal.run_mode,
                    model_key=getattr(terminal, "model_key", None),
                    has_images=getattr(ctx, "has_images", False),
                    has_videos=getattr(ctx, "has_videos", False),
                )
            except Exception as exc:
                logger.error(f"[API] 保存思考模式到对话失败: {exc}")
        
        status = terminal.get_status()
        socketio.emit('status_update', status, room=f"user_{username}")
        
        return jsonify({
            "success": True,
            "data": {
                "thinking_mode": terminal.thinking_mode,
                "mode": terminal.run_mode
            }
        })
    except Exception as exc:
        logger.error(f"[API] 切换思考模式失败: {exc}")
        code = 400 if isinstance(exc, ValueError) else 500
        return jsonify({
            "success": False,
            "error": str(exc),
            "message": tr("chat_settings.thinking_mode_exception")
        }), code

@chat_bp.route('/api/reasoning-effort', methods=['POST'])
@api_login_required
@with_terminal
@rate_limited("reasoning_effort_toggle", 20, 60, scope="user")
def update_reasoning_effort(terminal: WebTerminal, workspace: UserWorkspace, username: str):
    """设置会话级推理强度；effort 为 null/空 表示默认（请求中不传该参数）。"""
    try:
        data = request.get_json() or {}
        effort = data.get('effort')
        if isinstance(effort, str) and not effort.strip():
            effort = None
        # 前端防抖序列开始时记录的所属对话：指定后把值写入该对话 meta；
        # terminal 当前状态仅当保存目标就是当前对话时才更新，避免串写
        ctx = terminal.context_manager
        target_conversation_id = str(data.get('conversation_id') or '').strip() or None
        is_current_target = not target_conversation_id or target_conversation_id == ctx.current_conversation_id
        if is_current_target:
            terminal.set_reasoning_effort(effort)
        save_conversation_id = target_conversation_id or ctx.current_conversation_id
        # 更新对话的元数据（服务实例不挂载历史：统一读磁盘回传，仅更新元数据）
        if save_conversation_id:
            try:
                if is_current_target:
                    _save_conversation_meta_via_disk(
                        ctx,
                        save_conversation_id,
                        project_path=str(ctx.project_path),
                        thinking_mode=terminal.thinking_mode,
                        run_mode=terminal.run_mode,
                        reasoning_effort=effort,
                        model_key=getattr(terminal, "model_key", None),
                        has_images=getattr(ctx, "has_images", False),
                        has_videos=getattr(ctx, "has_videos", False),
                    )
                else:
                    # 所属对话已不是当前对话：只更新推理强度 meta。
                    # 重新读出目标对话消息回传（merge 语义下保持不变），
                    # 避免把当前对话的消息串写过去
                    existing = ctx.conversation_manager.load_conversation(save_conversation_id) or {}
                    ctx.conversation_manager.save_conversation(
                        conversation_id=save_conversation_id,
                        messages=existing.get("messages") or [],
                        reasoning_effort=effort,
                    )
            except Exception as exc:
                logger.error(f"[API] 保存推理强度到对话失败: {exc}")

        status = terminal.get_status()
        socketio.emit('status_update', status, room=f"user_{username}")

        return jsonify({
            "success": True,
            "data": {
                "reasoning_effort": terminal.reasoning_effort
            }
        })
    except Exception as exc:
        logger.error(f"[API] 设置推理强度失败: {exc}")
        code = 400 if isinstance(exc, ValueError) else 500
        return jsonify({
            "success": False,
            "error": str(exc),
            "message": tr("chat_settings.reasoning_effort_exception")
        }), code

@chat_bp.route('/api/model', methods=['POST'])
@api_login_required
@with_terminal
@rate_limited("model_switch", 10, 60, scope="user")
def update_model(terminal: WebTerminal, workspace: UserWorkspace, username: str):
    """切换基础模型（快速/思考模型组合）。"""
    try:
        data = request.get_json() or {}
        model_key = data.get("model_key")
        if not model_key:
            return jsonify({"success": False, "error": tr("chat_settings.missing_model_key")}), 400

        # 管理员禁用模型校验
        policy = resolve_admin_policy(get_current_user_record())
        disabled_models = set(policy.get("disabled_models") or [])
        if model_key in disabled_models:
            return jsonify({
                "success": False,
                "error": tr("chat_settings.model_admin_disabled"),
                "message": tr("chat_settings.admin_forced_disabled")
            }), 403

        # /new 页面语义：请求未携带 conversation_id 且为工作区级 terminal 时，
        # 用户处于“待创建干净新对话”状态。此时 terminal 上的 has_images/has_videos
        # 是启动时自动加载最近对话留下的残留焦点状态（attach_history=False，不持历史），
        # 不代表任何用户正在编辑的对话，不应拦截模型切换——与 create_new_conversation
        # “新对话视为干净会话，清除图片限制”的语义一致。清除是安全的：
        # 之后显式打开对话时 load_conversation_by_id 会从 metadata 重新恢复。
        requested_cid = str(data.get("conversation_id") or "").strip()
        if requested_cid and not requested_cid.startswith("conv_"):
            requested_cid = f"conv_{requested_cid}"
        if not requested_cid and not getattr(terminal, "_bound_conversation_id", None):
            _new_ctx = getattr(terminal, "context_manager", None)
            if _new_ctx is not None:
                _new_ctx.has_images = False
                _new_ctx.has_videos = False

        terminal.set_model(model_key)
        # fast-only 时 run_mode 可能被强制为 fast
        session["model_key"] = terminal.model_key
        session["run_mode"] = terminal.run_mode
        session["thinking_mode"] = terminal.thinking_mode

        # 更新对话元数据：仅当请求显式携带 conversation_id 时持久化。
        # 对话级隔离后，未带 conversation_id 时拿到的是工作区级 terminal，
        # 其 current_conversation_id 可能是陈旧对话（安全导航/切换后），
        # 盲目保存会把模型写到错误对话，或用陈旧 history 覆盖新消息。
        # /new 页面（无 conversation_id）不持久化，新对话在首个任务时
        # 由前端随任务传入的 model_key 落盘。
        ctx = terminal.context_manager
        current_cid = getattr(ctx, "current_conversation_id", None)
        if requested_cid and current_cid:
            if current_cid == requested_cid:
                try:
                    _save_conversation_meta_via_disk(
                        ctx,
                        current_cid,
                        project_path=str(ctx.project_path),
                        thinking_mode=terminal.thinking_mode,
                        run_mode=terminal.run_mode,
                        reasoning_effort=terminal.reasoning_effort,
                        model_key=terminal.model_key,
                        has_images=getattr(ctx, "has_images", False),
                        has_videos=getattr(ctx, "has_videos", False),
                    )
                except Exception as exc:
                    logger.error(f"[API] 保存模型到对话失败: {exc}")
            else:
                print(
                    f"[API] 跳过模型保存：请求对话 {requested_cid} "
                    f"与 terminal 当前对话 {current_cid} 不一致"
                )

        status = terminal.get_status()
        socketio.emit('status_update', status, room=f"user_{username}")

        return jsonify({
            "success": True,
            "data": {
                "model_key": terminal.model_key,
                "run_mode": terminal.run_mode,
                "thinking_mode": terminal.thinking_mode
            }
        })
    except Exception as exc:
        logger.error(f"[API] 切换模型失败: {exc}")
        code = 400 if isinstance(exc, ValueError) else 500
        return jsonify({"success": False, "error": str(exc), "message": str(exc)}), code

@chat_bp.route('/api/personalization', methods=['GET'])
@api_login_required
@with_terminal
def get_personalization_settings(terminal: WebTerminal, workspace: UserWorkspace, username: str):
    """获取个性化配置"""
    try:
        policy = resolve_admin_policy(get_current_user_record())
        if policy.get("ui_blocks", {}).get("block_personal_space"):
            return jsonify({"success": False, "error": tr("chat_settings.personal_space_admin_disabled")}), 403
        data = load_personalization_config(workspace.data_dir)
        private_skills_dir = infer_private_skills_dir(workspace.data_dir)
        skills_catalog = get_skills_catalog(private_dir=private_skills_dir)
        enabled_skills = merge_enabled_skills(
            data.get("enabled_skills"),
            skills_catalog,
            data.get("skills_catalog_snapshot"),
        )
        data_out = dict(data)
        data_out["enabled_skills"] = enabled_skills
        compression_settings = resolve_context_compression_settings(data_out)
        try:
            model_window = get_model_context_window(getattr(terminal, "model_key", None))
        except Exception:
            model_window = None
        return jsonify({
            "success": True,
            "data": data_out,
            "tool_categories": terminal.get_tool_settings_snapshot(),
            "skills_catalog": skills_catalog,
            "context_compression_settings": {
                **compression_settings,
                "context_window_tokens": min(
                    int(compression_settings.get("deep_trigger_tokens") or 0),
                    int(model_window or compression_settings.get("deep_trigger_tokens") or 0),
                ),
            },
            "recent_conversations_prompt_limit_range": {
                "min": RECENT_CONVERSATIONS_PROMPT_LIMIT_MIN,
                "max": RECENT_CONVERSATIONS_PROMPT_LIMIT_MAX,
            },
            "project_memory_inject_limit_range": {
                "min": PROJECT_MEMORY_INJECT_LIMIT_MIN,
                "max": None,
            }
        })
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500

@chat_bp.route('/api/personalization', methods=['POST'])
@api_login_required
@with_terminal
@rate_limited("personalization_update", 20, 300, scope="user")
def update_personalization_settings(terminal: WebTerminal, workspace: UserWorkspace, username: str):
    """更新个性化配置"""
    payload = request.get_json() or {}
    try:
        policy = resolve_admin_policy(get_current_user_record())
        if policy.get("ui_blocks", {}).get("block_personal_space"):
            return jsonify({"success": False, "error": tr("chat_settings.personal_space_admin_disabled")}), 403
        config = save_personalization_config(workspace.data_dir, payload)
        private_skills_dir = infer_private_skills_dir(workspace.data_dir)
        skills_catalog = get_skills_catalog(private_dir=private_skills_dir)
        enabled_skills = merge_enabled_skills(
            config.get("enabled_skills"),
            skills_catalog,
            config.get("skills_catalog_snapshot"),
        )
        stored_skills = None if len(enabled_skills) == len(skills_catalog) else enabled_skills
        catalog_snapshot = [item.get("id") for item in skills_catalog if item.get("id")]
        if config.get("enabled_skills") != stored_skills or config.get("skills_catalog_snapshot") != catalog_snapshot:
            config = dict(config)
            config["enabled_skills"] = stored_skills
            config["skills_catalog_snapshot"] = catalog_snapshot
            config = save_personalization_config(workspace.data_dir, config)
        try:
            sync_workspace_skills(workspace.project_path, enabled_skills, private_dir=private_skills_dir)
        except Exception as sync_exc:
            debug_log(f"[Skills] 同步失败: {sync_exc}")
        try:
            # 对话级 terminal 的 模型/模式/推理强度 以对话 meta 为权威，
            # 保存个性化默认值不得回写当前对话；工作区级（/new）则立即生效
            terminal.apply_personalization_preferences(
                config,
                apply_default_modes=not bool(getattr(terminal, '_bound_conversation_id', None)),
            )
            session['run_mode'] = terminal.run_mode
            session['thinking_mode'] = terminal.thinking_mode
            ctx = getattr(terminal, 'context_manager', None)
            if ctx and getattr(ctx, 'current_conversation_id', None):
                try:
                    _save_conversation_meta_via_disk(
                        ctx,
                        ctx.current_conversation_id,
                        project_path=str(ctx.project_path),
                        thinking_mode=terminal.thinking_mode,
                        run_mode=terminal.run_mode
                    )
                except Exception as meta_exc:
                    debug_log(f"应用个性化偏好失败: 同步对话元数据异常 {meta_exc}")
            try:
                status = terminal.get_status()
                socketio.emit('status_update', status, room=f"user_{username}")
            except Exception as status_exc:
                debug_log(f"广播个性化状态失败: {status_exc}")
        except Exception as exc:
            debug_log(f"应用个性化偏好失败: {exc}")
        config_out = dict(config)
        config_out["enabled_skills"] = enabled_skills
        compression_settings = resolve_context_compression_settings(config_out)
        try:
            model_window = get_model_context_window(getattr(terminal, "model_key", None))
        except Exception:
            model_window = None
        return jsonify({
            "success": True,
            "data": config_out,
            "tool_categories": terminal.get_tool_settings_snapshot(),
            "skills_catalog": skills_catalog,
            "context_compression_settings": {
                **compression_settings,
                "context_window_tokens": min(
                    int(compression_settings.get("deep_trigger_tokens") or 0),
                    int(model_window or compression_settings.get("deep_trigger_tokens") or 0),
                ),
            },
            "recent_conversations_prompt_limit_range": {
                "min": RECENT_CONVERSATIONS_PROMPT_LIMIT_MIN,
                "max": RECENT_CONVERSATIONS_PROMPT_LIMIT_MAX,
            },
            "project_memory_inject_limit_range": {
                "min": PROJECT_MEMORY_INJECT_LIMIT_MIN,
                "max": None,
            }
        })
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500
