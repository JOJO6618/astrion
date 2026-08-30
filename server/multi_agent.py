"""多智能体模式 server 路由。

- `/multiagent/new` 返回主 SPA 入口（与 `/new` 一样返回 static/index.html）
  前端通过路径识别多智能体模式后，在创建对话时写入 metadata.multi_agent_mode=true
- `/api/multiagent/conversations` POST 创建多智能体对话（写入 metadata）
- `/api/multiagent/roles` GET 列出可用角色
- `/api/multiagent/roles` POST 创建自定义角色
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from flask import Blueprint, current_app, jsonify, request, session

from server.auth_helpers import api_login_required, login_required, get_current_username
from server.context import get_user_resources
from modules.multi_agent.role_store import (
    RoleConfig,
    list_roles,
    load_preset_role,
    save_custom_role,
    delete_custom_role,
    is_preset_role,
    sync_preset_roles,
)
from config.paths import CUSTOM_ROLES_DIR, WEB_PRESET_ROLES_DIR
from config.limits import REASONING_EFFORT_LEVELS
from modules.i18n import tr

multi_agent_bp = Blueprint("multi_agent", __name__)


@multi_agent_bp.route("/multiagent/new")
@login_required
def multi_agent_new_page():
    """多智能体模式入口，返回与 /new 相同的 SPA index.html。"""
    return current_app.send_static_file("index.html")


@multi_agent_bp.route("/multiagent/<path:conversation_id>")
@login_required
def multi_agent_conversation_page(conversation_id: str):
    """多智能体模式 指定会话 URL，返回 SPA index.html 让前端路由处理。"""
    return current_app.send_static_file("index.html")


@multi_agent_bp.route("/api/multiagent/rebuild-index", methods=["POST"])
@api_login_required
def rebuild_conversation_index_api():
    """强制从磁盘重建对话索引，补全 multi_agent_mode 等新字段。"""
    try:
        username = get_current_username()
        if not username:
            return jsonify({"success": False, "error": tr("multi_agent_api.not_logged_in")}), 401
        terminal, _ = get_user_resources(username)
        if not terminal:
            return jsonify({"success": False, "error": tr("multi_agent_api.workspace_not_ready")}), 503
        ctx_manager = getattr(terminal, "context_manager", None)
        if not ctx_manager:
            return jsonify({"success": False, "error": tr("multi_agent_api.context_manager_not_initialized")}), 503
        ma_manager = getattr(ctx_manager, "multi_agent_conversation_manager", None)
        if not ma_manager:
            return jsonify({"success": False, "error": tr("multi_agent_api.ma_conversation_manager_not_initialized")}), 503
        rebuilt = ma_manager._rebuild_index_from_files()
        ma_manager._save_index(rebuilt)
        return jsonify({"success": True, "index_size": len(rebuilt)})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


def _get_role_dirs() -> tuple:
    """根据 session 判断当前登录模式，返回 (runtime_dir, custom_dir)。

    - host 模式：runtime_dir = custom_dir = host/mutiagents/agents
    - web/docker 模式：runtime_dir = web/mutiagents/agents, custom_dir = users/<user>/personal/mutiagents/agents
    """
    from flask import session as _session
    is_host_session = bool(_session.get('host_mode'))
    if is_host_session:
        return CUSTOM_ROLES_DIR, CUSTOM_ROLES_DIR
    # web 模式
    username = get_current_username()
    if not username:
        return WEB_PRESET_ROLES_DIR, None
    _, workspace = get_user_resources(username)
    if not workspace or not workspace.data_dir:
        return WEB_PRESET_ROLES_DIR, None
    from modules.multi_agent.role_store import infer_custom_roles_dir
    custom = infer_custom_roles_dir(workspace.data_dir)
    return WEB_PRESET_ROLES_DIR, str(custom) if custom else None


@multi_agent_bp.route("/api/multiagent/roles", methods=["GET"])
@api_login_required
def list_roles_api():
    """列出全部可用角色（预置+用户自定义）。"""
    try:
        runtime_dir, custom_dir = _get_role_dirs()
        roles = list_roles(runtime_dir=runtime_dir, custom_dir=custom_dir)
        return jsonify({
            "success": True,
            "roles": [r.to_dict() for r in roles],
        })
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@multi_agent_bp.route("/api/multiagent/roles", methods=["POST"])
@api_login_required
def create_role_api():
    """创建自定义角色。body: { role_id, name, description?, body_prompt, thinking_mode? }"""
    try:
        data = request.get_json() or {}
        role_id = str(data.get("role_id") or "").strip()
        name = str(data.get("name") or "").strip()
        body_prompt = str(data.get("body_prompt") or "").strip()
        description = str(data.get("description") or "").strip()
        thinking_mode = str(data.get("thinking_mode") or "fast").strip()
        model_key = str(data.get("model_key") or "").strip() or None
        if not role_id or not name or not body_prompt:
            return jsonify({"success": False, "error": tr("multi_agent_api.role_fields_required")}), 400
        if thinking_mode not in {"fast", "thinking"}:
            thinking_mode = "fast"
        # 不允许覆盖预设角色
        if is_preset_role(role_id):
            return jsonify({"success": False, "error": tr("multi_agent_api.cannot_override_preset_role", role_id=role_id)}), 409
        # 不允许覆盖已存在的用户自定义角色
        runtime_dir, custom_dir = _get_role_dirs()
        existing = {r.role_id for r in list_roles(runtime_dir=runtime_dir, custom_dir=custom_dir)}
        if role_id in existing:
            return jsonify({"success": False, "error": tr("multi_agent_api.role_already_exists", role_id=role_id)}), 409
        role = RoleConfig(
            role_id=role_id,
            name=name,
            description=description,
            body_prompt=body_prompt,
            thinking_mode=thinking_mode,
            model_key=model_key,
            is_custom=True,
        )
        saved = save_custom_role(role, custom_dir=custom_dir)
        return jsonify({"success": True, "role_id": role_id, "file": str(saved)})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@multi_agent_bp.route("/api/multiagent/roles/<role_id>", methods=["PUT"])
@api_login_required
def update_role_api(role_id: str):
    """更新用户自定义角色。不能更新预设角色。"""
    try:
        data = request.get_json() or {}
        runtime_dir, custom_dir = _get_role_dirs()
        # 加载现有角色（先查自定义覆盖，再查预设）
        role = load_preset_role(role_id, runtime_dir=runtime_dir, custom_dir=custom_dir)
        if not role:
            return jsonify({"success": False, "error": tr("multi_agent_api.role_not_found", role_id=role_id)}), 404
        # 更新字段
        if "name" in data:
            role.name = str(data["name"]).strip() or role.name
        if "description" in data:
            role.description = str(data["description"] or "").strip()
        if "body_prompt" in data:
            role.body_prompt = str(data["body_prompt"] or "").strip()
        if "thinking_mode" in data:
            tm = str(data["thinking_mode"] or "fast").strip()
            if tm in {"fast", "thinking"}:
                role.thinking_mode = tm
        if "model_key" in data:
            role.model_key = str(data["model_key"] or "").strip() or None
        if "skills" in data:
            role.skills = list(data["skills"] or [])
        role.is_custom = True
        saved = save_custom_role(role, custom_dir=custom_dir)
        return jsonify({"success": True, "role_id": role_id, "file": str(saved)})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@multi_agent_bp.route("/api/multiagent/roles/<role_id>", methods=["DELETE"])
@api_login_required
def delete_role_api(role_id: str):
    """删除用户自定义角色。不能删除预设角色。"""
    try:
        if is_preset_role(role_id):
            return jsonify({"success": False, "error": tr("multi_agent_api.cannot_delete_preset_role", role_id=role_id)}), 403
        _, custom_dir = _get_role_dirs()
        deleted = delete_custom_role(role_id, custom_dir=custom_dir)
        if not deleted:
            return jsonify({"success": False, "error": tr("multi_agent_api.role_not_found_or_delete_failed", role_id=role_id)}), 404
        return jsonify({"success": True, "role_id": role_id})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@multi_agent_bp.route("/api/multiagent/settings", methods=["GET"])
@api_login_required
def get_multi_agent_settings_api():
    """获取多智能体模式设置（子智能体压缩阈值等）。"""
    try:
        username = get_current_username()
        if not username:
            return jsonify({"success": False, "error": tr("multi_agent_api.not_logged_in")}), 401
        _, workspace = get_user_resources(username)
        if not workspace:
            return jsonify({"success": False, "error": tr("multi_agent_api.workspace_not_ready")}), 503
        # 从个人化配置中读取子智能体设置
        from modules.personalization_manager import load_personalization_config
        prefs = load_personalization_config(workspace.data_dir) or {}
        compress_threshold = prefs.get("sub_agent_compress_threshold_tokens", 150000)
        # 最大执行轮次：None（未设置）表示默认 50；0 表示无上限；正整数为该值
        max_turns = prefs.get("sub_agent_max_turns")
        return jsonify({
            "success": True,
            "settings": {
                "sub_agent_compress_threshold_tokens": compress_threshold,
                "sub_agent_max_turns": max_turns,
            },
        })
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@multi_agent_bp.route("/api/multiagent/settings", methods=["PUT"])
@api_login_required
def update_multi_agent_settings_api():
    """更新多智能体模式设置。"""
    try:
        username = get_current_username()
        if not username:
            return jsonify({"success": False, "error": tr("multi_agent_api.not_logged_in")}), 401
        _, workspace = get_user_resources(username)
        if not workspace:
            return jsonify({"success": False, "error": tr("multi_agent_api.workspace_not_ready")}), 503
        data = request.get_json() or {}
        settings = data.get("settings") or {}
        from modules.personalization_manager import load_personalization_config, save_personalization_config
        prefs = load_personalization_config(workspace.data_dir) or {}
        dirty = False
        # 更新子智能体压缩阈值
        threshold = settings.get("sub_agent_compress_threshold_tokens")
        if threshold is not None:
            threshold = int(threshold)
            if threshold < 10000:
                return jsonify({"success": False, "error": tr("multi_agent_api.compress_threshold_too_small")}), 400
            prefs["sub_agent_compress_threshold_tokens"] = threshold
            dirty = True
        # 更新子智能体最大轮次（键存在才处理：None → 删除键恢复默认 50；0 → 无上限；正整数 → 该值）
        if "sub_agent_max_turns" in settings:
            max_turns_raw = settings.get("sub_agent_max_turns")
            if max_turns_raw is None:
                # 置空恢复默认 50：写入显式 null（sanitize 会保留 None），不能 pop——
                # save_personalization_config 的 fallback=existing 会把已存在的旧值带回来
                if prefs.get("sub_agent_max_turns") is not None:
                    dirty = True
                prefs["sub_agent_max_turns"] = None
            else:
                try:
                    max_turns = int(max_turns_raw)
                except (TypeError, ValueError):
                    return jsonify({"success": False, "error": tr("multi_agent_api.max_turns_must_be_integer")}), 400
                if max_turns < 0:
                    return jsonify({"success": False, "error": tr("multi_agent_api.max_turns_cannot_be_negative")}), 400
                prefs["sub_agent_max_turns"] = max_turns
                dirty = True
        if dirty:
            save_personalization_config(workspace.data_dir, prefs)
        return jsonify({"success": True})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@multi_agent_bp.route("/api/multiagent/conversations", methods=["POST"])
@api_login_required
def create_multi_agent_conversation():
    """创建多智能体模式对话。在 metadata 中写入 multi_agent_mode=true。

    body: { workspace_id?, thinking_mode?, run_mode?, preserve_mode? }
    """
    import time as _time
    from server.conversation import _get_active_workspace_task, _resolve_target_terminal_for_workspace
    from modules.personalization_manager import load_personalization_config
    try:
        from server.user_workspace import UserWorkspace  # noqa
    except Exception:
        UserWorkspace = None  # type: ignore

    username = get_current_username()
    if not username:
        return jsonify({"success": False, "error": tr("multi_agent_api.not_logged_in")}), 401

    data = request.get_json() or {}
    target_workspace_id = (data.get("workspace_id") or "").strip()

    terminal, workspace = get_user_resources(username)
    if not terminal or not workspace:
        return jsonify({"success": False, "error": tr("multi_agent_api.workspace_not_ready")}), 503

    if target_workspace_id:
        try:
            terminal, workspace = _resolve_target_terminal_for_workspace(
                username, target_workspace_id, terminal, workspace
            )
        except ValueError as exc:
            return jsonify({"success": False, "error": str(exc)}), 404
        except RuntimeError as exc:
            return jsonify({"success": False, "error": str(exc)}), 503

    preserve_mode = bool(data.get("preserve_mode"))
    thinking_mode = data.get("thinking_mode") if preserve_mode and "thinking_mode" in data else None
    run_mode = data.get("mode") if preserve_mode and "mode" in data else None
    # 前端随 body 传入当前生效的推理强度，权威写入新对话 meta
    body_effort_provided = "reasoning_effort" in data
    body_effort = None
    if body_effort_provided:
        raw_effort = data.get("reasoning_effort")
        if raw_effort is None:
            body_effort = None
        elif isinstance(raw_effort, str):
            candidate_effort = raw_effort.strip().lower()
            if candidate_effort in REASONING_EFFORT_LEVELS:
                body_effort = candidate_effort
            else:
                body_effort_provided = False
        else:
            body_effort_provided = False

    effective_workspace_id = target_workspace_id or session.get("workspace_id") or "default"
    active_task = _get_active_workspace_task(username=username, workspace_id=effective_workspace_id)

    try:
        prefs = load_personalization_config(workspace.data_dir)
    except Exception:
        prefs = {}

    ctx_manager = getattr(terminal, "context_manager", None)
    if not ctx_manager:
        return jsonify({"success": False, "error": tr("multi_agent_api.context_manager_not_initialized")}), 500
    cm = getattr(ctx_manager, "multi_agent_conversation_manager", None) or getattr(ctx_manager, "conversation_manager", None)
    if not cm:
        return jsonify({"success": False, "error": tr("multi_agent_api.conversation_manager_not_initialized")}), 500

    safe_run_mode = str(run_mode or "").strip().lower()
    if safe_run_mode == "deep":  # 旧版标识符映射
        safe_run_mode = "thinking"
    if safe_run_mode not in {"fast", "thinking"}:
        candidate = str((prefs or {}).get("default_run_mode") or "").strip().lower()
        if candidate == "deep":
            candidate = "thinking"
        safe_run_mode = candidate if candidate in {"fast", "thinking"} else "fast"
    safe_thinking = bool(thinking_mode) if thinking_mode is not None else safe_run_mode != "fast"
    # 运行模式（work_mode）：沿用 terminal 当前值（与正常创建路径一致，
    # /new 页面切换后新对话继承）；plan 档不变量 ⇒ 权限必须只读，
    # 同时记录进入前权限供离开 plan 恢复
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

    previous_cm_current = getattr(ctx_manager, "current_conversation_id", None)

    # 推理强度优先级：body 显式值 > terminal 当前档位 > 个性化默认值
    if body_effort_provided:
        ma_effort = body_effort
    else:
        terminal_effort = getattr(terminal, "reasoning_effort", None)
        if isinstance(terminal_effort, str) and terminal_effort.strip().lower() in REASONING_EFFORT_LEVELS:
            ma_effort = terminal_effort.strip().lower()
        else:
            ma_effort = (prefs or {}).get("default_reasoning_effort")
            if isinstance(ma_effort, str):
                ma_effort = ma_effort.strip().lower() or None
                if ma_effort not in REASONING_EFFORT_LEVELS:
                    ma_effort = None
            else:
                ma_effort = None

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
            "multi_agent_mode": True,
            "reasoning_effort": ma_effort,
        },
    )
    # 恢复 context_manager 的当前对话（不要影响普通对话的 current_conversation_id）
    try:
        ctx_manager.current_conversation_id = previous_cm_current
    except Exception:
        pass
    # 同步 terminal 级别的多智能体开关
    terminal.multi_agent_mode = True
    try:
        if hasattr(terminal, "sub_agent_manager"):
            terminal.sub_agent_manager.multi_agent_mode = True
    except Exception:
        pass

    # 触发对话列表更新事件
    try:
        from server.app_legacy import socketio
        socketio.emit('conversation_list_update', {
            'action': 'created',
            'conversation_id': conversation_id,
        }, room=f"user_{username}")
    except Exception:
        pass

    return jsonify({
        "success": True,
        "conversation_id": conversation_id,
        "multi_agent_mode": True,
    }), 201


@multi_agent_bp.route("/api/multiagent/models", methods=["GET"])
@api_login_required
def list_sub_agent_models_api():
    """列出子智能体可用的模型列表（从 sub_agent_models.json 读取，脱敏后返回）。"""
    try:
        from config.sub_agent import SUB_AGENT_MODELS_CONFIG_FILE
        from pathlib import Path as _Path
        import json as _json
        config_path = SUB_AGENT_MODELS_CONFIG_FILE
        if not _Path(config_path).exists():
            return jsonify({"success": True, "models": [], "default_model": ""})
        raw = _json.loads(_Path(config_path).read_text(encoding="utf-8"))
        models_raw = raw.get("models", []) if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])
        default_model = str(raw.get("default_model", "")) if isinstance(raw, dict) else ""
        # 脱敏：只返回 name / modes / multimodal / max_output / max_context
        safe_models = []
        for m in models_raw:
            if not isinstance(m, dict):
                continue
            safe_models.append({
                "name": m.get("name") or m.get("model_name") or "",
                "modes": m.get("modes") or m.get("mode") or "",
                "multimodal": m.get("multimodal") or "none",
                "max_output": m.get("max_output") or m.get("max_tokens") or 0,
                "max_context": m.get("max_context") or m.get("context_window") or 0,
            })
        return jsonify({
            "success": True,
            "models": safe_models,
            "default_model": default_model,
        })
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@multi_agent_bp.route("/api/multiagent/active_sub_agents", methods=["GET"])
@api_login_required
def list_active_sub_agents_api():
    """查询当前会话所有子智能体实例（多智能体模式专用）。"""
    username = get_current_username()
    if not username:
        return jsonify({"success": False, "error": tr("multi_agent_api.not_logged_in")}), 401
    conversation_id = (request.args.get("conversation_id") or "").strip()
    if not conversation_id:
        return jsonify({"success": False, "error": tr("multi_agent_api.missing_conversation_id_param")}), 400
    terminal, _ = get_user_resources(username, conversation_id=conversation_id)
    if not terminal:
        return jsonify({"success": False, "error": tr("multi_agent_api.workspace_not_ready")}), 503
    sub_agent_manager = getattr(terminal, "sub_agent_manager", None)
    if not sub_agent_manager:
        return jsonify({"success": False, "error": tr("multi_agent_api.sub_agent_manager_not_ready")}), 503
    state = sub_agent_manager.get_multi_agent_state(conversation_id)
    agents: List[Dict[str, Any]] = []
    if state:
        for a in state.list_all():
            d = a.to_dict()
            task = sub_agent_manager.tasks.get(a.task_id)
            if isinstance(task, dict):
                d["last_tool"] = task.get("last_tool")
                stats_file = Path(task.get("stats_file", ""))
                if stats_file.exists():
                    try:
                        stats = json.loads(stats_file.read_text(encoding="utf-8"))
                        d["current_context_tokens"] = stats.get("current_context_tokens", 0)
                    except Exception:
                        d["current_context_tokens"] = 0
                else:
                    d["current_context_tokens"] = 0
            agents.append(d)
    if not state:
        return jsonify({"success": True, "agents": []})
    return jsonify({
        "success": True,
        "agents": agents,
    })


__all__ = ["multi_agent_bp"]