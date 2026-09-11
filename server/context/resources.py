"""用户终端与工作区资源装配（get_user_resources 及内部 helper）。

显式身份模式：传入 RuntimeIdentity 时完全不读写 Flask session（契约
docs/runtime_contract.md §4.1）；为 None 时保持既有 HTTP 适配层行为。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    import modules.user_manager

from server.context._flask_bridge import has_request_context, session_get, session_set

from core.web_terminal import WebTerminal
from modules.personalization_manager import load_personalization_config
from modules.skills_manager import infer_private_skills_dir, sync_workspace_skills
from modules.host_workspace_manager import resolve_host_workspace
from config import (
    DATA_DIR,
    LOGS_DIR,
    TERMINAL_SANDBOX_MODE,
    UPLOAD_QUARANTINE_SUBDIR,
)
from config.model_profiles import get_registered_model_keys
from modules.i18n import tr
from server import state
from server.utils_common import debug_log
from utils.host_workspace_debug import write_host_workspace_debug

# 兼容模式（未传 RuntimeIdentity）需要的 Web 认证辅助改为使用点延迟导入，
# 保持任务核心层依赖链（server.tasks → server.context.resources）无 flask 包依赖。


def _get_current_username() -> Optional[str]:
    """兼容模式专用：延迟导入 Web 认证辅助。"""
    from server.auth_helpers import get_current_username

    return get_current_username()


def _get_current_user_record():
    """兼容模式专用：延迟导入 Web 认证辅助。"""
    from server.auth_helpers import get_current_user_record

    return get_current_user_record()


def _get_current_user_role(record=None) -> str:
    """兼容模式专用：延迟导入 Web 认证辅助。"""
    from server.auth_helpers import get_current_user_role

    return get_current_user_role(record)

from server.context.identity import NoWorkspaceError, RuntimeIdentity, _resolve_user_role
from server.context.personalization import _apply_workspace_personalization_preferences
from server.context.usage import get_or_create_usage_tracker


def make_terminal_callback(username: str):
    """Socket.IO 移除后的兼容空操作：实时推送已全量走任务事件流/REST 轮询，
    回调恒为 None（WebTerminal 与 emit_workflow_progress 均判空安全）。
    保留本符号仅为维持历史 import 路径不炸，新代码不应依赖它产生用户可见效果。
    """
    return None



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



def get_user_resources(
    username: Optional[str] = None,
    workspace_id: Optional[str] = None,
    update_session: bool = True,
    conversation_id: Optional[str] = None,
    identity: Optional[RuntimeIdentity] = None,
) -> Tuple[Optional[WebTerminal], Optional['modules.user_manager.UserWorkspace']]:
    """获取用户终端与工作区资源。

    conversation_id 非空时返回对话级 terminal（每对话独立的 shell/文件/子智能体状态，
    常驻内存 + 24h 无活动回收）；为空时返回工作区级服务 terminal（对话列表等无对话
    上下文的 API 使用）。容器句柄始终按工作区级共享。

    identity 显式传入时（任务线程等非请求上下文场景）：身份/偏好全部来自该快照，
    完全不读写 Flask session；为 None 时保持既有行为（HTTP 适配层在请求上下文内
    读取 session 并回写）。
    """
    from modules.user_manager import UserWorkspace
    username = (username or _get_current_username())
    if not username:
        return None, None

    explicit = identity is not None
    # session 回写仅在「兼容模式 + 请求上下文 + 允许回写」时启用；
    # 请求上下文在单次调用期间不会变化，开头一次性求值。
    can_write_session = (not explicit) and update_session and has_request_context()

    # 宿主机免登录模式：根据 host_workspaces.json 选择路径，不创建 /users/<user>/project
    if explicit:
        host_mode_session = identity.host_mode
    else:
        host_mode_session = bool(session_get("host_mode"))
    sandbox_is_host = (TERMINAL_SANDBOX_MODE or "host").lower() == "host"
    if host_mode_session and sandbox_is_host:
        # 宿主机多工作区并行：资源选择必须优先由显式 workspace_id / 当前请求 session 决定，
        # 不能被进程级 HOST_ACTIVE_WORKSPACE_ID 覆盖，否则后台任务会在用户切换视图后串到新工作区。
        selected_workspace_id = workspace_id
        if not selected_workspace_id:
            if explicit:
                selected_workspace_id = identity.host_workspace_id
            else:
                selected_workspace_id = (
                    session_get("host_workspace_id")
                    or session_get("workspace_id")
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
            if explicit:
                run_mode = identity.preferred_run_mode
                thinking_mode_flag = identity.preferred_thinking_mode
            else:
                run_mode = session_get('run_mode')
                thinking_mode_flag = session_get('thinking_mode')
            if run_mode not in {"fast", "thinking", "deep"}:
                run_mode = "fast"
                thinking_mode_flag = False
            thinking_mode = bool(thinking_mode_flag) if thinking_mode_flag is not None else (run_mode != "fast")
            terminal = WebTerminal(
                project_path=str(project_path),
                thinking_mode=thinking_mode,
                run_mode=run_mode,
                message_callback=None,
                data_dir=str(data_dir),
                container_session=container_handle,
                usage_tracker=usage_tracker,
                conversation_id=conversation_id,
            )
            state.user_terminals[term_key] = terminal
            terminal.username = "host"
            terminal.user_role = "admin"
            terminal.quota_update_callback = None
            if can_write_session:
                session_set('run_mode', terminal.run_mode)
                session_set('thinking_mode', terminal.thinking_mode)
                session_set('workspace_id', getattr(workspace, "workspace_id", None))
                session_set('host_workspace_id', getattr(workspace, "workspace_id", None))
        else:
            terminal.update_container_session(container_handle)
            terminal.username = "host"
            terminal.user_role = "admin"
            if can_write_session:
                session_set('workspace_id', getattr(workspace, "workspace_id", None))
                session_set('host_workspace_id', getattr(workspace, "workspace_id", None))
        _set_terminal_workspace_label(
            terminal,
            host_workspace.get("label") or host_workspace.get("workspace_id") or getattr(workspace, "workspace_id", None),
        )

        # 宿主机模式同样需要应用管理员策略（否则前端工具菜单会退化成静态基础分类）
        try:
            from core.tool_config import ToolCategory
            from modules import admin_policy_manager

            record = None if explicit else _get_current_user_record()
            role = _resolve_user_role(identity, record, default="admin") if explicit else (_get_current_user_role(record) if record else "admin")
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
                            if can_write_session:
                                session_set("model_key", terminal.model_key)
                            break
                        except Exception:
                            continue
        except Exception as exc:
            debug_log(f"[admin_policy][host_mode] 应用失败: {exc}")

        _apply_workspace_personalization_preferences(
            terminal,
            workspace,
            update_session=update_session,
            session_model=(identity.preferred_model_key if explicit else None),
            allow_session_io=not explicit,
        )
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

    is_api_user = identity.is_api_user if explicit else bool(session_get("is_api_user"))
    # API 用户与网页用户使用不同的 manager
    if is_api_user:
        record = None
        if workspace_id is None:
            raise RuntimeError(tr("context.missing_workspace_id"))
        workspace = state.api_user_manager.ensure_workspace(username, workspace_id)
    else:
        record = (state.user_manager.get_user(username) if explicit else _get_current_user_record())
        if explicit:
            selected_workspace_id = workspace_id or "default"
        else:
            selected_workspace_id = (
                workspace_id
                or session_get("workspace_id")
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
        if explicit:
            run_mode = identity.preferred_run_mode
            thinking_mode_flag = identity.preferred_thinking_mode
        else:
            run_mode = session_get('run_mode')
            thinking_mode_flag = session_get('thinking_mode')
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
        state.user_terminals[term_key] = terminal
        terminal.username = username
        terminal.user_role = "api" if is_api_user else _resolve_user_role(identity, record)
        terminal.quota_update_callback = None
        if can_write_session:
            session_set('run_mode', terminal.run_mode)
            session_set('thinking_mode', terminal.thinking_mode)
            session_set('model_key', getattr(terminal, "model_key", None))
            session_set('workspace_id', getattr(workspace, "workspace_id", None))
    else:
        terminal.update_container_session(container_handle)
        terminal.username = username
        terminal.user_role = "api" if is_api_user else _resolve_user_role(identity, record)
        terminal.quota_update_callback = None
        if can_write_session:
            session_set('workspace_id', getattr(workspace, "workspace_id", None))

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
                _resolve_user_role(identity, record),
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
                            if can_write_session:
                                session_set("model_key", terminal.model_key)
                            break
                        except Exception:
                            continue
        except Exception as exc:
            debug_log(f"[admin_policy] 应用失败: {exc}")

    _apply_workspace_personalization_preferences(
        terminal,
        workspace,
        update_session=update_session,
        session_model=(identity.preferred_model_key if explicit else None),
        allow_session_io=not explicit,
    )
    _ensure_workspace_skills_synced(terminal, workspace)
    _touch_terminal_activity(terminal, conversation_id)
    return terminal, workspace


