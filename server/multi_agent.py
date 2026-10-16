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
from server.context.decorators import with_terminal
from server.context.data_decorators import with_workspace_data
from server.entry_assets import serve_entry_html
from modules.multi_agent.role_store import (
    RoleConfig,
    list_roles,
    load_preset_role,
    save_custom_role,
    delete_custom_role,
    is_preset_role,
    sync_preset_roles,
    validate_role_id,
)
from config.paths import CUSTOM_ROLES_DIR, WEB_PRESET_ROLES_DIR
from modules.i18n import tr

multi_agent_bp = Blueprint("multi_agent", __name__)


@multi_agent_bp.route("/multiagent/new")
@login_required
def multi_agent_new_page():
    """多智能体模式入口，返回与 /new 相同的 SPA index.html。"""
    return serve_entry_html("index.html")


@multi_agent_bp.route("/multiagent/<path:conversation_id>")
@login_required
def multi_agent_conversation_page(conversation_id: str):
    """多智能体模式 指定会话 URL，返回 SPA index.html 让前端路由处理。"""
    return serve_entry_html("index.html")


@multi_agent_bp.route("/api/multiagent/rebuild-index", methods=["POST"])
@api_login_required
@with_workspace_data
def rebuild_conversation_index_api(data_context, workspace, username):
    """显式修复所属工作区的多智能体索引，不装配执行资源。"""
    try:
        manager = data_context.manager(True)
        with manager._io_lock:
            rebuilt = manager._rebuild_index_from_files()
            manager._save_index(rebuilt)
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
        try:
            role_id = validate_role_id(role_id)
        except ValueError as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
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
        role_id = validate_role_id(role_id)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
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
        role_id = validate_role_id(role_id)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
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
        compress_threshold = prefs.get("sub_agent_compress_threshold_tokens", 250000)
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
@with_terminal
def create_multi_agent_conversation(terminal, workspace, username):
    """Use the same /new snapshot contract for multi-agent conversations."""
    from server.context.new_conversation import create_from_settings
    try:
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            raise ValueError("Expected an object")
        result = create_from_settings(
            terminal, workspace, payload.get("settings"), multi_agent=True,
        )
        return jsonify(result), 201
    except PermissionError as exc:
        return jsonify(success=False, error=str(exc)), 403
    except ValueError as exc:
        return jsonify(success=False, error=str(exc)), 400


@multi_agent_bp.route("/api/multiagent/models", methods=["GET"])
@api_login_required
def list_sub_agent_models_api():
    """列出子智能体可用的模型列表（统一来源 = 主注册表，含 codex 动态模型，脱敏后返回）。"""
    try:
        from modules.aux_model_resolver import list_aux_models

        return jsonify({
            "success": True,
            "models": list_aux_models(),
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