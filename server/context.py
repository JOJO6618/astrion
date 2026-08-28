"""用户终端与工作区相关的共享辅助函数。"""
from __future__ import annotations
import os
import time
from functools import wraps
from typing import Optional, Tuple, Dict, Any
from flask import session, jsonify, has_request_context, request

from core.web_terminal import WebTerminal
from modules.gui_file_manager import GuiFileManager
from modules.upload_security import UploadQuarantineManager, UploadSecurityError
from modules.personalization_manager import load_personalization_config
from modules.skills_manager import infer_private_skills_dir, sync_workspace_skills
from modules.host_workspace_manager import resolve_host_workspace
from utils.host_workspace_debug import write_host_workspace_debug
import json
from pathlib import Path
from modules.usage_tracker import UsageTracker
from config import (
    DATA_DIR,
    LOGS_DIR,
    TERMINAL_SANDBOX_MODE,
    UPLOAD_QUARANTINE_SUBDIR,
)
from config.model_profiles import get_registered_model_keys

from . import state
from .utils_common import debug_log
from .auth_helpers import get_current_username, get_current_user_record, get_current_user_role  # will create helper module
from modules.i18n import tr


def make_terminal_callback(username: str):
    """生成面向指定用户的广播函数"""
    from .extensions import socketio
    def _callback(event_type, data):
        try:
            socketio.emit(event_type, data, room=f"user_{username}")
        except Exception as exc:
            debug_log(f"广播事件失败 ({username}): {event_type} - {exc}")
    return _callback


def attach_user_broadcast(terminal: WebTerminal, username: str):
    """确保终端的广播函数指向当前用户的房间。

    对话级 terminal 的回调会额外包装注入 conversation_id（见 _wrap_callback_with_conversation_id）。
    """
    callback = make_terminal_callback(username)
    callback = _wrap_callback_with_conversation_id(
        callback, getattr(terminal, "_bound_conversation_id", None)
    )
    terminal.message_callback = callback
    if terminal.terminal_manager:
        terminal.terminal_manager.broadcast = callback


def _make_terminal_key(
    username: str,
    workspace_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
) -> str:
    """终端缓存键。

    对话级隔离：传入 conversation_id 时键为 ``username::workspace_id::conversation_id``，
    每个对话拥有独立的 WebTerminal（context/file/terminal manager、子智能体等）。
    不传 conversation_id 时保持旧的两段键（工作区级服务实例，供对话列表等无对话上下文的 API 使用）。
    """
    base = f"{username}::{workspace_id}" if workspace_id else username
    if conversation_id:
        base = f"{base}::{conversation_id}"
    return base


def _wrap_callback_with_conversation_id(callback, conversation_id: Optional[str]):
    """包装广播回调，为 dict 类型的事件数据注入 conversation_id（setdefault，不覆盖已有值）。

    对话级 terminal 的广播（shell 输出、terminal 列表等）仍发到用户房间，
    前端按 conversation_id 过滤，避免同工作区多个对话的终端事件互相串扰。
    """
    if not callback or not conversation_id:
        return callback

    def _wrapped(event_type, data):
        if isinstance(data, dict):
            data = dict(data)
            data.setdefault("conversation_id", conversation_id)
        return callback(event_type, data)

    return _wrapped


def _touch_terminal_activity(terminal: Optional[WebTerminal], conversation_id: Optional[str]) -> None:
    """对话级 terminal：刷新最近活动时间（供 24h TTL 回收器判定）。"""
    if not terminal or not conversation_id:
        return
    try:
        terminal.last_activity_at = time.time()
    except Exception:
        pass


def _set_terminal_workspace_label(terminal: WebTerminal, label: Optional[str]) -> None:
    label_text = str(label or "").strip()
    try:
        terminal.workspace_label = label_text
    except Exception:
        pass
    try:
        if getattr(terminal, "context_manager", None):
            terminal.context_manager.workspace_label = label_text
    except Exception:
        pass


def _apply_workspace_personalization_preferences(terminal: WebTerminal, workspace, update_session: bool = True) -> None:
    """Apply persisted workspace personalization after policy/workspace resolution."""
    try:
        config = load_personalization_config(workspace.data_dir)
        session_model = None
        if has_request_context():
            raw_session_model = session.get("model_key")
            if isinstance(raw_session_model, str) and raw_session_model.strip():
                session_model = raw_session_model.strip()

        # 对话级 terminal（_bound_conversation_id）的模型由绑定加载权威恢复
        # （对话文件 metadata.model_key），session 级模型是全局的最后选择，
        # 不能覆盖到某个具体对话的 terminal 上，否则重启后进入对话会被
        # session 里的其它模型回写（模型回变默认的 bug）。
        is_conversation_bound = bool(getattr(terminal, "_bound_conversation_id", None))

        # default_model 是“新会话初始偏好”，不能在每次 /api/status、任务创建、
        # 加载资源时覆盖用户已经在当前会话里手动切换的模型。
        if (
            session_model
            and not is_conversation_bound
            and session_model != getattr(terminal, "model_key", None)
        ):
            try:
                terminal.set_model(session_model)
            except Exception as exc:
                debug_log(f"[Personalization] 恢复会话模型失败: {session_model} ({exc})")

        apply_default_model = (
            not is_conversation_bound
            and not bool(session_model)
            and not bool(getattr(terminal, "_workspace_default_model_applied", False))
        )
        # 对话级 terminal 的 模型/思考模式/推理强度 以对话 meta 为权威，
        # 此函数在每次 /api/status、任务创建、加载资源时都会触发，
        # 不得在对话加载后反复用 prefs 默认值覆盖 meta 恢复值。
        # 工作区级 terminal（/new 页）同理：三项 modes 默认值仅首次应用一次，
        # 之后用户在 /new 手动调整的 模式/档位 必须稳定存活到创建对话时，
        # 不能被 status 轮询反复重置（prefs 更新走 settings 保存路径显式应用，
        # 新建空对话走 create_new_conversation 的 prefer_defaults 路径重置）。
        apply_default_modes = (
            not is_conversation_bound
            and not bool(getattr(terminal, "_workspace_default_modes_applied", False))
        )
        terminal.apply_personalization_preferences(
            config,
            apply_default_model=apply_default_model,
            apply_default_modes=apply_default_modes,
        )
        if apply_default_model:
            try:
                terminal._workspace_default_model_applied = True
            except Exception:
                pass
        if apply_default_modes:
            try:
                terminal._workspace_default_modes_applied = True
            except Exception:
                pass
        if has_request_context() and update_session:
            session["run_mode"] = getattr(terminal, "run_mode", session.get("run_mode"))
            session["thinking_mode"] = getattr(terminal, "thinking_mode", session.get("thinking_mode"))
            session["model_key"] = getattr(terminal, "model_key", session.get("model_key"))
    except Exception as exc:
        debug_log(f"[Personalization] 应用工作区偏好失败: {exc}")


def _ensure_workspace_skills_synced(terminal: WebTerminal, workspace) -> None:
    """
    确保工作区 skills 已按当前个性化配置完成同步。
    使用终端实例上的路径标记避免每次请求都重复全量拷贝。
    """
    try:
        project_path = str(Path(workspace.project_path).resolve())
    except Exception:
        project_path = str(workspace.project_path)

    if getattr(terminal, "_skills_synced_project_path", None) == project_path:
        return

    try:
        config = load_personalization_config(workspace.data_dir)
        enabled_skills = config.get("enabled_skills") if isinstance(config, dict) else None
        result = sync_workspace_skills(
            workspace.project_path,
            enabled_skills,
            private_dir=infer_private_skills_dir(workspace.data_dir),
        )
        if not result.get("success"):
            debug_log(f"[Skills] 工作区同步失败: {result.get('error')}")
            return
        terminal._skills_synced_project_path = project_path
        debug_log(f"[Skills] 工作区技能已同步: {project_path} ({result.get('copied', 0)} 项)")
    except Exception as exc:
        debug_log(f"[Skills] 工作区同步异常: {exc}")


class NoWorkspaceError(RuntimeError):
    """宿主机模式下尚未创建任何工作区。

    与一般的 resource_busy 区分：前端可据 code=no_workspace 进入
    「引导创建工作区」流程，而不是视为系统繁忙。
    """


def get_user_resources(
    username: Optional[str] = None,
    workspace_id: Optional[str] = None,
    update_session: bool = True,
    conversation_id: Optional[str] = None,
) -> Tuple[Optional[WebTerminal], Optional['modules.user_manager.UserWorkspace']]:
    """获取用户终端与工作区资源。

    conversation_id 非空时返回对话级 terminal（每对话独立的 shell/文件/子智能体状态，
    常驻内存 + 24h 无活动回收）；为空时返回工作区级服务 terminal（对话列表等无对话
    上下文的 API 使用）。容器句柄始终按工作区级共享。
    """
    from modules.user_manager import UserWorkspace
    username = (username or get_current_username())
    if not username:
        return None, None

    # 宿主机免登录模式：根据 host_workspaces.json 选择路径，不创建 /users/<user>/project
    host_mode_session = bool(session.get("host_mode")) if has_request_context() else False
    sandbox_is_host = (TERMINAL_SANDBOX_MODE or "host").lower() == "host"
    if host_mode_session and sandbox_is_host:
        # 宿主机多工作区并行：资源选择必须优先由显式 workspace_id / 当前请求 session 决定，
        # 不能被进程级 HOST_ACTIVE_WORKSPACE_ID 覆盖，否则后台任务会在用户切换视图后串到新工作区。
        selected_workspace_id = (
            workspace_id
            or (session.get("host_workspace_id") if has_request_context() else None)
            or (session.get("workspace_id") if has_request_context() else None)
        )
        with state.HOST_ACTIVE_WORKSPACE_LOCK:
            active_workspace_id = state.HOST_ACTIVE_WORKSPACE_ID
            active_workspace_path = state.HOST_ACTIVE_WORKSPACE_PATH
            active_workspace_version = state.HOST_ACTIVE_WORKSPACE_VERSION
        if not selected_workspace_id and active_workspace_id:
            selected_workspace_id = active_workspace_id
        _, host_workspace = resolve_host_workspace(selected_workspace_id)
        if not host_workspace:
            raise NoWorkspaceError(tr("context.no_workspace"))
        if (
            active_workspace_id
            and active_workspace_path
            and selected_workspace_id == active_workspace_id
        ):
            host_workspace = dict(host_workspace)
            host_workspace["workspace_id"] = active_workspace_id
            host_workspace["path"] = active_workspace_path
        project_path = Path(host_workspace.get("path") or "").expanduser().resolve()
        write_host_workspace_debug(
            "context.get_user_resources.host.selected_workspace",
            selected_workspace_id=selected_workspace_id,
            active_workspace_id=active_workspace_id,
            active_workspace_path=active_workspace_path,
            active_workspace_version=active_workspace_version,
            resolved_workspace_id=host_workspace.get("workspace_id"),
            project_path=str(project_path),
            username=username,
        )
        project_path.mkdir(parents=True, exist_ok=True)
        data_dir = Path(DATA_DIR).expanduser().resolve()
        data_dir.mkdir(parents=True, exist_ok=True)
        logs_dir = Path(LOGS_DIR).expanduser().resolve()
        logs_dir.mkdir(parents=True, exist_ok=True)
        uploads_dir = project_path / ".astrion" / "user_upload"
        uploads_dir.mkdir(parents=True, exist_ok=True)
        skills_dir = project_path / ".astrion" / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        quarantine_root = Path(UPLOAD_QUARANTINE_SUBDIR).expanduser()
        if not quarantine_root.is_absolute():
            quarantine_root = (project_path.parent / UPLOAD_QUARANTINE_SUBDIR).resolve()
        quarantine_root.mkdir(parents=True, exist_ok=True)

        workspace = UserWorkspace(
            username="host",
            root=project_path.parent,
            project_path=project_path,
            data_dir=data_dir,
            logs_dir=logs_dir,
            uploads_dir=uploads_dir,
            quarantine_dir=quarantine_root,
        )
        if not hasattr(workspace, "workspace_id"):
            workspace.workspace_id = host_workspace.get("workspace_id") or "default"

        workspace_id_value = getattr(workspace, "workspace_id", None) or host_workspace.get("workspace_id") or "default"
        term_key = _make_terminal_key("host", workspace_id_value, conversation_id)
        # 容器句柄始终按工作区级共享：对话级 terminal 在同一容器内起独立 shell 进程
        container_key = _make_terminal_key("host", workspace_id_value)
        container_handle = state.container_manager.ensure_container("host", str(project_path), container_key=container_key, preferred_mode="host")
        usage_tracker = None  # 宿主机模式不计配额
        terminal = state.user_terminals.get(term_key)
        if terminal is not None and getattr(terminal, "_reaper_closing", False):
            # 回收器正在关闭该实例，视为不存在并原地重建；
            # 回收器 pop 前会校验实例身份，不会误删这里新建的 terminal。
            terminal = None
        target_project_path = str(project_path)
        if terminal:
            should_recreate_terminal = False
            try:
                current_project_path = str(Path(getattr(terminal, "project_path", "")).expanduser().resolve())
            except Exception:
                current_project_path = str(getattr(terminal, "project_path", ""))
            try:
                current_context_project_path = str(
                    Path(getattr(getattr(terminal, "context_manager", None), "project_path", "")).expanduser().resolve()
                )
            except Exception:
                current_context_project_path = str(
                    getattr(getattr(terminal, "context_manager", None), "project_path", "")
                )
            if current_project_path != target_project_path or current_context_project_path != target_project_path:
                write_host_workspace_debug(
                    "context.get_user_resources.host.path_mismatch",
                    terminal_id=id(terminal),
                    current_project_path=current_project_path,
                    current_context_project_path=current_context_project_path,
                    target_project_path=target_project_path,
                )
                try:
                    if hasattr(terminal, "update_project_path"):
                        terminal.update_project_path(target_project_path)
                    else:
                        should_recreate_terminal = True
                except Exception as exc:
                    debug_log(f"[HostWorkspace] update_project_path 失败，回退重建终端: {exc}")
                    should_recreate_terminal = True

                if not should_recreate_terminal:
                    try:
                        updated_project_path = str(Path(getattr(terminal, "project_path", "")).expanduser().resolve())
                    except Exception:
                        updated_project_path = str(getattr(terminal, "project_path", ""))
                    if updated_project_path != target_project_path:
                        should_recreate_terminal = True

                if should_recreate_terminal:
                    write_host_workspace_debug(
                        "context.get_user_resources.host.recreate_terminal",
                        terminal_id=id(terminal),
                        target_project_path=target_project_path,
                    )
                    try:
                        if getattr(terminal, "terminal_manager", None):
                            terminal.terminal_manager.close_all()
                    except Exception:
                        pass
                    state.user_terminals.pop(term_key, None)
                    terminal = None
        if not terminal:
            run_mode = session.get('run_mode') if has_request_context() else None
            thinking_mode_flag = session.get('thinking_mode') if has_request_context() else None
            if run_mode not in {"fast", "thinking", "deep"}:
                run_mode = "fast"
                thinking_mode_flag = False
            thinking_mode = bool(thinking_mode_flag) if thinking_mode_flag is not None else (run_mode != "fast")
            terminal = WebTerminal(
                project_path=str(project_path),
                thinking_mode=thinking_mode,
                run_mode=run_mode,
                message_callback=make_terminal_callback("host"),
                data_dir=str(data_dir),
                container_session=container_handle,
                usage_tracker=usage_tracker,
                conversation_id=conversation_id,
            )
            if terminal.terminal_manager:
                terminal.terminal_manager.broadcast = terminal.message_callback
            state.user_terminals[term_key] = terminal
            terminal.username = "host"
            terminal.user_role = "admin"
            terminal.quota_update_callback = None
            if has_request_context() and update_session:
                session['run_mode'] = terminal.run_mode
                session['thinking_mode'] = terminal.thinking_mode
                session['workspace_id'] = getattr(workspace, "workspace_id", None)
                session['host_workspace_id'] = getattr(workspace, "workspace_id", None)
        else:
            terminal.update_container_session(container_handle)
            attach_user_broadcast(terminal, "host")
            terminal.username = "host"
            terminal.user_role = "admin"
            if has_request_context() and update_session:
                session['workspace_id'] = getattr(workspace, "workspace_id", None)
                session['host_workspace_id'] = getattr(workspace, "workspace_id", None)
        _set_terminal_workspace_label(
            terminal,
            host_workspace.get("label") or host_workspace.get("workspace_id") or getattr(workspace, "workspace_id", None),
        )

        # 宿主机模式同样需要应用管理员策略（否则前端工具菜单会退化成静态基础分类）
        try:
            from core.tool_config import ToolCategory
            from modules import admin_policy_manager

            record = get_current_user_record()
            role = get_current_user_role(record) if record else "admin"
            invite_code = getattr(record, "invite_code", None) if record else None
            policy = admin_policy_manager.get_effective_policy(
                record.username if record else username,
                role,
                invite_code,
            )
            categories_map = {
                cid: ToolCategory(
                    label=cat.get("label") or cid,
                    tools=list(cat.get("tools") or []),
                    default_enabled=bool(cat.get("default_enabled", True)),
                    silent_when_disabled=bool(cat.get("silent_when_disabled", False)),
                )
                for cid, cat in policy.get("categories", {}).items()
            }
            forced_states = policy.get("forced_category_states") or {}
            disabled_models = policy.get("disabled_models") or []
            terminal.set_admin_policy(categories_map, forced_states, disabled_models)
            terminal.admin_policy_ui_blocks = policy.get("ui_blocks") or {}
            terminal.admin_policy_version = policy.get("updated_at")
            if terminal.model_key in disabled_models:
                for candidate in get_registered_model_keys(visible_only=True):
                    if candidate not in disabled_models:
                        try:
                            terminal.set_model(candidate)
                            if has_request_context() and update_session:
                                session["model_key"] = terminal.model_key
                            break
                        except Exception:
                            continue
        except Exception as exc:
            debug_log(f"[admin_policy][host_mode] 应用失败: {exc}")

        _apply_workspace_personalization_preferences(terminal, workspace)
        _ensure_workspace_skills_synced(terminal, workspace)
        _touch_terminal_activity(terminal, conversation_id)
        write_host_workspace_debug(
            "context.get_user_resources.host.return",
            terminal_id=id(terminal),
            terminal_project_path=str(getattr(terminal, "project_path", "")),
            context_project_path=str(getattr(getattr(terminal, "context_manager", None), "project_path", "")),
            workspace_project_path=str(getattr(workspace, "project_path", "")),
            workspace_id=getattr(workspace, "workspace_id", None),
            current_conversation_id=getattr(getattr(terminal, "context_manager", None), "current_conversation_id", None),
        )
        return terminal, workspace

    is_api_user = bool(session.get("is_api_user")) if has_request_context() else False
    # API 用户与网页用户使用不同的 manager
    if is_api_user:
        record = None
        if workspace_id is None:
            raise RuntimeError(tr("context.missing_workspace_id"))
        workspace = state.api_user_manager.ensure_workspace(username, workspace_id)
    else:
        record = get_current_user_record()
        selected_workspace_id = (
            workspace_id
            or (session.get("workspace_id") if has_request_context() else None)
            or "default"
        )
        workspace = state.user_manager.ensure_user_workspace(username, selected_workspace_id)
        # 为兼容后续逻辑，补充 workspace_id 属性
        if not hasattr(workspace, "workspace_id"):
            try:
                workspace.workspace_id = selected_workspace_id or "default"
            except Exception:
                pass
    workspace_id_value = getattr(workspace, "workspace_id", None) or "default"
    term_key = _make_terminal_key(username, workspace_id_value, conversation_id)
    # 容器句柄始终按工作区级共享（docker 模式：一个工作区/项目一个容器）
    container_key = _make_terminal_key(username, workspace_id_value)
    container_handle = state.container_manager.ensure_container(username, str(workspace.project_path), container_key=container_key, preferred_mode="docker")
    usage_tracker = None if is_api_user else get_or_create_usage_tracker(username, workspace)
    terminal = state.user_terminals.get(term_key)
    if terminal is not None and getattr(terminal, "_reaper_closing", False):
        # 回收器正在关闭该实例，视为不存在并原地重建；
        # 回收器 pop 前会校验实例身份，不会误删这里新建的 terminal。
        terminal = None
    if not terminal:
        run_mode = session.get('run_mode') if has_request_context() else None
        thinking_mode_flag = session.get('thinking_mode') if has_request_context() else None
        if run_mode not in {"fast", "thinking", "deep"}:
            preferred_run_mode = None
            try:
                personal_config = load_personalization_config(workspace.data_dir)
                candidate_mode = (personal_config or {}).get('default_run_mode')
                if isinstance(candidate_mode, str) and candidate_mode.lower() in {"fast", "thinking", "deep"}:
                    preferred_run_mode = candidate_mode.lower()
            except Exception as exc:
                debug_log(f"[UserInit] 加载个性化偏好失败: {exc}")

            if preferred_run_mode:
                run_mode = preferred_run_mode
                thinking_mode_flag = preferred_run_mode != "fast"
            elif thinking_mode_flag:
                run_mode = "deep"
            else:
                run_mode = "fast"
        thinking_mode = run_mode != "fast"
        terminal = WebTerminal(
            project_path=str(workspace.project_path),
            thinking_mode=thinking_mode,
            run_mode=run_mode,
            message_callback=make_terminal_callback(username),
            data_dir=str(workspace.data_dir),
            container_session=container_handle,
            usage_tracker=usage_tracker,
            conversation_id=conversation_id,
        )
        if terminal.terminal_manager:
            terminal.terminal_manager.broadcast = terminal.message_callback
        state.user_terminals[term_key] = terminal
        terminal.username = username
        terminal.user_role = "api" if is_api_user else get_current_user_role(record)
        terminal.quota_update_callback = (lambda metric=None: emit_user_quota_update(username)) if not is_api_user else None
        if has_request_context() and update_session:
            session['run_mode'] = terminal.run_mode
            session['thinking_mode'] = terminal.thinking_mode
            session['model_key'] = getattr(terminal, "model_key", None)
            session['workspace_id'] = getattr(workspace, "workspace_id", None)
    else:
        terminal.update_container_session(container_handle)
        attach_user_broadcast(terminal, username)
        terminal.username = username
        terminal.user_role = "api" if is_api_user else get_current_user_role(record)
        terminal.quota_update_callback = (lambda metric=None: emit_user_quota_update(username)) if not is_api_user else None
        if has_request_context() and update_session:
            session['workspace_id'] = getattr(workspace, "workspace_id", None)

    if is_api_user:
        workspace_label = workspace_id_value
    else:
        try:
            workspace_label = (
                state.user_manager.list_user_workspaces(username)
                .get(workspace_id_value, {})
                .get("label")
            ) or workspace_id_value
        except Exception:
            workspace_label = workspace_id_value
    _set_terminal_workspace_label(terminal, workspace_label)

    # 应用管理员策略
    if not is_api_user:
        try:
            from core.tool_config import ToolCategory
            from modules import admin_policy_manager
            policy = admin_policy_manager.get_effective_policy(
                record.username if record else None,
                get_current_user_role(record),
                getattr(record, "invite_code", None),
            )
            categories_map = {
                cid: ToolCategory(
                    label=cat.get("label") or cid,
                    tools=list(cat.get("tools") or []),
                    default_enabled=bool(cat.get("default_enabled", True)),
                    silent_when_disabled=bool(cat.get("silent_when_disabled", False)),
                )
                for cid, cat in policy.get("categories", {}).items()
            }
            forced_states = policy.get("forced_category_states") or {}
            disabled_models = policy.get("disabled_models") or []
            terminal.set_admin_policy(categories_map, forced_states, disabled_models)
            terminal.admin_policy_ui_blocks = policy.get("ui_blocks") or {}
            terminal.admin_policy_version = policy.get("updated_at")
            if terminal.model_key in disabled_models:
                for candidate in get_registered_model_keys(visible_only=True):
                    if candidate not in disabled_models:
                        try:
                            terminal.set_model(candidate)
                            if update_session:
                                session["model_key"] = terminal.model_key
                            break
                        except Exception:
                            continue
        except Exception as exc:
            debug_log(f"[admin_policy] 应用失败: {exc}")

    _apply_workspace_personalization_preferences(terminal, workspace, update_session=update_session)
    _ensure_workspace_skills_synced(terminal, workspace)
    _touch_terminal_activity(terminal, conversation_id)
    return terminal, workspace


def get_or_create_usage_tracker(username: Optional[str], workspace: Optional['modules.user_manager.UserWorkspace'] = None) -> Optional[UsageTracker]:
    if not username:
        return None
    tracker = state.usage_trackers.get(username)
    if tracker:
        return tracker
    from modules.user_manager import UserWorkspace
    if workspace is None:
        workspace = state.user_manager.ensure_user_workspace(username)
    record = state.user_manager.get_user(username)
    role = getattr(record, "role", "user") if record else "user"
    tracker = UsageTracker(str(workspace.data_dir), role=role or "user")
    state.usage_trackers[username] = tracker
    return tracker


def emit_user_quota_update(username: Optional[str]):
    from .extensions import socketio
    if not username:
        return
    tracker = get_or_create_usage_tracker(username)
    if not tracker:
        return
    try:
        snapshot = tracker.get_quota_snapshot()
        socketio.emit('quota_update', {'quotas': snapshot}, room=f"user_{username}")
    except Exception:
        pass


def apply_conversation_overrides(terminal: WebTerminal, workspace, conversation_id: Optional[str]):
    """根据对话元数据应用自定义 prompt / personalization（仅 API 用途）。"""
    if not conversation_id:
        return
    conv_path = Path(workspace.data_dir) / "conversations" / f"{conversation_id}.json"
    if not conv_path.exists():
        return
    try:
        data = json.loads(conv_path.read_text(encoding="utf-8"))
        meta = data.get("metadata") or {}
        prompt_name = meta.get("custom_prompt_name")
        personalization_name = meta.get("personalization_name")
        # prompt override
        if prompt_name:
            prompt_path = Path(workspace.data_dir) / "prompts" / f"{prompt_name}.txt"
            if prompt_path.exists():
                terminal.context_manager.custom_system_prompt = prompt_path.read_text(encoding="utf-8")
            else:
                terminal.context_manager.custom_system_prompt = None
        else:
            terminal.context_manager.custom_system_prompt = None
        # personalization override
        if personalization_name:
            pers_path = Path(workspace.data_dir) / "personalization" / f"{personalization_name}.json"
            if pers_path.exists():
                try:
                    terminal.context_manager.custom_personalization_config = json.loads(pers_path.read_text(encoding="utf-8"))
                except Exception:
                    terminal.context_manager.custom_personalization_config = None
            else:
                terminal.context_manager.custom_personalization_config = None
        else:
            terminal.context_manager.custom_personalization_config = None

        # 应用个性化偏好（含禁用工具分类）到当前终端；
        # 对话加载链路不应用默认 模型/模式/推理强度（以对话 meta 为权威）
        try:
            terminal.apply_personalization_preferences(
                terminal.context_manager.custom_personalization_config,
                apply_default_modes=False,
            )
        except Exception as exc:
            debug_log(f"[apply_overrides] 应用个性化失败: {exc}")
    except Exception as exc:
        debug_log(f"[apply_overrides] 读取对话元数据失败: {exc}")


def with_terminal(func):
    """注入用户专属终端和工作区。

    请求携带 conversation_id（query 参数或 JSON body）时返回该对话的对话级 terminal，
    否则返回工作区级服务 terminal。
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        username = get_current_username()
        conversation_id = None
        try:
            conversation_id = (request.args.get("conversation_id") or "").strip() or None
            if not conversation_id and request.is_json:
                body = request.get_json(silent=True) or {}
                if isinstance(body, dict):
                    conversation_id = (body.get("conversation_id") or "").strip() or None
        except Exception:
            conversation_id = None
        try:
            terminal, workspace = get_user_resources(username, conversation_id=conversation_id)
        except NoWorkspaceError as exc:
            return jsonify({"error": str(exc), "code": "no_workspace"}), 503
        except RuntimeError as exc:
            return jsonify({"error": str(exc), "code": "resource_busy"}), 503
        if not terminal or not workspace:
            return jsonify({"error": "System not initialized"}), 503
        kwargs.update({
            'terminal': terminal,
            'workspace': workspace,
            'username': username
        })
        return func(*args, **kwargs)
    return wrapper


def get_terminal_for_sid(sid: str, conversation_id: Optional[str] = None):
    username = state.connection_users.get(sid)
    if not username:
        return None, None, None
    try:
        terminal, workspace = get_user_resources(username, conversation_id=conversation_id)
    except RuntimeError:
        return username, None, None
    return username, terminal, workspace


def get_gui_manager(workspace):
    return GuiFileManager(str(workspace.project_path))


def get_upload_guard(workspace):
    return UploadQuarantineManager(workspace)


def build_upload_error_response(exc: UploadSecurityError):
    status = 400
    if exc.code in {"scanner_missing", "scanner_unavailable"}:
        status = 500
    return jsonify({
        "success": False,
        "error": str(exc),
        "code": exc.code,
    }), status


def ensure_conversation_loaded(
    terminal: WebTerminal,
    conversation_id: Optional[str],
    workspace=None,
):
    created_new = False
    if not conversation_id:
        result = terminal.create_new_conversation()
        if not result.get("success"):
            raise RuntimeError(result.get("message", tr("context.create_conversation_failed")))
        conversation_id = result["conversation_id"]
        if has_request_context():
            session['run_mode'] = terminal.run_mode
            session['thinking_mode'] = terminal.thinking_mode
        created_new = True
    else:
        conversation_id = conversation_id if conversation_id.startswith('conv_') else f"conv_{conversation_id}"
        current_id = terminal.context_manager.current_conversation_id
        if current_id != conversation_id:
            load_result = terminal.load_conversation(conversation_id)
            if not load_result.get("success"):
                raise RuntimeError(load_result.get("message", tr("context.load_conversation_failed")))
            write_host_workspace_debug(
                "context.ensure_conversation_loaded.after_load",
                terminal_id=id(terminal),
                conversation_id=conversation_id,
                terminal_project_path=str(getattr(terminal, "project_path", "")),
                context_project_path=str(getattr(getattr(terminal, "context_manager", None), "project_path", "")),
                metadata_project_path=(
                    getattr(getattr(terminal, "context_manager", None), "conversation_metadata", {}) or {}
                ).get("project_path"),
            )
            try:
                conv_data = terminal.context_manager._get_conversation_manager_for_id(conversation_id).load_conversation(conversation_id) or {}
                meta = conv_data.get("metadata", {}) or {}
                run_mode_meta = meta.get("run_mode")
                if run_mode_meta:
                    terminal.set_run_mode(run_mode_meta)
                elif meta.get("thinking_mode"):
                    terminal.set_run_mode("thinking")
                else:
                    terminal.set_run_mode("fast")
                try:
                    terminal.set_reasoning_effort(meta.get("reasoning_effort"))
                except (ValueError, AttributeError):
                    pass
                if has_request_context():
                    session['run_mode'] = terminal.run_mode
                    session['thinking_mode'] = terminal.thinking_mode
                    session['model_key'] = getattr(terminal, "model_key", None)
            except Exception:
                pass
    if workspace is not None:
        try:
            workspace_project_path = str(Path(workspace.project_path).expanduser().resolve())
            terminal.update_project_path(workspace_project_path)
            write_host_workspace_debug(
                "context.ensure_conversation_loaded.reapply_workspace_path",
                terminal_id=id(terminal),
                conversation_id=conversation_id,
                workspace_project_path=workspace_project_path,
                terminal_project_path=str(getattr(terminal, "project_path", "")),
                context_project_path=str(getattr(getattr(terminal, "context_manager", None), "project_path", "")),
            )
        except Exception as exc:
            write_host_workspace_debug(
                "context.ensure_conversation_loaded.reapply_workspace_path_failed",
                terminal_id=id(terminal),
                conversation_id=conversation_id,
                error=str(exc),
            )
    # 应用对话级自定义 prompt / personalization（仅 API）。
    # 注意：ensure_conversation_loaded 在 WebSocket/后台任务等多处复用，有些调用点拿不到 workspace；
    #       因此这里允许 workspace 为空（仅跳过 override，不影响正常对话加载）。
    if workspace is not None:
        try:
            apply_conversation_overrides(terminal, workspace, conversation_id)
        except Exception as exc:
            debug_log(f"[apply_overrides] 失败: {exc}")
    return conversation_id, created_new


def reset_system_state(terminal: Optional[WebTerminal]):
    """完整重置系统状态"""
    if not terminal:
        return
    try:
        if hasattr(terminal, 'current_session_id'):
            terminal.current_session_id += 1
            debug_log(f"重置会话ID为: {terminal.current_session_id}")
        web_attrs = ['streamingMessage', 'currentMessageIndex', 'preparingTools', 'activeTools']
        for attr in web_attrs:
            if hasattr(terminal, attr):
                if attr in ['streamingMessage']:
                    setattr(terminal, attr, False)
                elif attr in ['currentMessageIndex']:
                    setattr(terminal, attr, -1)
                elif attr in ['preparingTools', 'activeTools'] and hasattr(getattr(terminal, attr), 'clear'):
                    getattr(terminal, attr).clear()
        debug_log("系统状态重置完成")
    except Exception as e:
        debug_log(f"状态重置过程中出现错误: {e}")
        import traceback
        debug_log(f"错误详情: {traceback.format_exc()}")


# ====== 对话级 terminal 24h TTL 回收器 ======
# 对话级 terminal（key 为 username::workspace_id::conversation_id 三段）常驻内存，
# 仅当「超过 TTL 无活动 且 该对话无运行中工作」时回收；工作区级服务 terminal 不回收。
CONVERSATION_TERMINAL_TTL_SECONDS = float(os.environ.get("CONVERSATION_TERMINAL_TTL_SECONDS", str(24 * 3600)))
CONVERSATION_TERMINAL_REAP_INTERVAL_SECONDS = float(os.environ.get("CONVERSATION_TERMINAL_REAP_INTERVAL_SECONDS", "600"))
_conversation_terminal_reaper_started = False


def _conversation_terminal_has_running_work(
    username: str,
    workspace_id: str,
    conversation_id: str,
    terminal: WebTerminal,
) -> bool:
    """判定对话是否仍有运行中的工作（主任务/子智能体/后台命令/多智能体）。

    判定失败时保守返回 True（不回收）。
    """
    try:
        from .tasks import task_manager
        active_statuses = {"pending", "running", "cancel_requested"}
        for rec in task_manager.list_tasks(username, workspace_id):
            if rec.conversation_id == conversation_id and rec.status in active_statuses:
                return True
        status = task_manager.get_conversation_running_status(terminal, conversation_id)
        if any(bool(v) for v in (status or {}).values()):
            return True
    except Exception as exc:
        debug_log(f"[ConvTerminalReaper] 运行状态判定失败 {conversation_id}: {exc}")
        return True
    return False


def reap_idle_conversation_terminals(now: Optional[float] = None) -> int:
    """回收超过 TTL 且无运行任务的对话级 terminal，返回回收数量（可测试）。"""
    now = now or time.time()
    reaped = 0
    for term_key, terminal in list(state.user_terminals.items()):
        parts = term_key.split("::")
        if len(parts) < 3:
            continue  # 工作区级服务 terminal 不回收
        username, workspace_id = parts[0], parts[1]
        conversation_id = "::".join(parts[2:])
        try:
            last_active = float(getattr(terminal, "last_activity_at", None))
        except (TypeError, ValueError):
            last_active = None
        if last_active is None:
            # 无时间戳实例（旧版本创建）：补上当前时间，下轮再判定
            try:
                terminal.last_activity_at = now
            except Exception:
                pass
            continue
        if now - last_active < CONVERSATION_TERMINAL_TTL_SECONDS:
            continue
        if _conversation_terminal_has_running_work(username, workspace_id, conversation_id, terminal):
            continue
        # 竞态防护：判定到关闭之间存在窗口，期间新请求可能拿到该实例并建任务。
        # 先打关闭标记（get_user_resources 见到标记会原地重建新实例），
        # 并二次确认活动时间/运行工作未变化，最后 pop 时校验实例身份。
        try:
            terminal._reaper_closing = True
        except Exception:
            pass
        aborted = False
        latest_active = float(getattr(terminal, "last_activity_at", 0) or 0)
        if latest_active > last_active:
            debug_log(f"[ConvTerminalReaper] 关闭前检测到新活动，取消回收: {term_key}")
            aborted = True
        elif _conversation_terminal_has_running_work(username, workspace_id, conversation_id, terminal):
            debug_log(f"[ConvTerminalReaper] 关闭前检测到运行任务，取消回收: {term_key}")
            aborted = True
        if aborted:
            try:
                terminal._reaper_closing = False
            except Exception:
                pass
            continue
        try:
            cm = getattr(terminal, "context_manager", None)
            # 与 __del__ 同理：空 history 保存会把磁盘上非空对话覆盖为空
            if cm and getattr(cm, "current_conversation_id", None) and getattr(cm, "conversation_history", None):
                cm.save_current_conversation()
        except Exception as exc:
            debug_log(f"[ConvTerminalReaper] 保存对话失败 {conversation_id}: {exc}")
        try:
            tm = getattr(terminal, "terminal_manager", None)
            if tm:
                tm.close_all()
        except Exception as exc:
            debug_log(f"[ConvTerminalReaper] 关闭 shell 失败 {conversation_id}: {exc}")
        try:
            mcp = getattr(terminal, "mcp_client_manager", None)
            if mcp:
                mcp.close_all_clients()
        except Exception as exc:
            debug_log(f"[ConvTerminalReaper] 关闭 MCP 失败 {conversation_id}: {exc}")
        # 仅当缓存里仍是本实例时才移除（可能已被请求侧原地重建）
        if state.user_terminals.get(term_key) is terminal:
            state.user_terminals.pop(term_key, None)
            reaped += 1
            debug_log(f"[ConvTerminalReaper] 已回收对话级 terminal: {term_key} (idle {int(now - last_active)}s)")
    return reaped


def _conversation_terminal_reaper_loop():
    """后台循环：定期扫描回收空闲对话级 terminal。"""
    while True:
        try:
            reap_idle_conversation_terminals()
            time.sleep(CONVERSATION_TERMINAL_REAP_INTERVAL_SECONDS)
        except Exception as exc:
            debug_log(f"[ConvTerminalReaper] 后台循环异常: {exc}")
            time.sleep(CONVERSATION_TERMINAL_REAP_INTERVAL_SECONDS)


def start_conversation_terminal_reaper():
    """幂等启动对话级 terminal TTL 回收后台线程。"""
    global _conversation_terminal_reaper_started
    if _conversation_terminal_reaper_started:
        return
    _conversation_terminal_reaper_started = True
    from .extensions import socketio
    socketio.start_background_task(_conversation_terminal_reaper_loop)


__all__ = [
    "get_user_resources",
    "with_terminal",
    "get_terminal_for_sid",
    "get_gui_manager",
    "get_upload_guard",
    "build_upload_error_response",
    "ensure_conversation_loaded",
    "reset_system_state",
    "get_or_create_usage_tracker",
    "emit_user_quota_update",
    "attach_user_broadcast",
    "reap_idle_conversation_terminals",
    "start_conversation_terminal_reaper",
]
