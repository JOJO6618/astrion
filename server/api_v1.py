"""API v1：Bearer Token 版轻量接口（后台任务 + 轮询 + 文件）。"""
from __future__ import annotations
import os
import json
from pathlib import Path
from typing import Dict, Any, Optional

from flask import Blueprint, request, jsonify, send_file, session

from .api_auth import api_token_required
from .security import rate_limited
from .tasks import task_manager
from .context import get_user_resources, ensure_conversation_loaded, get_upload_guard, apply_conversation_overrides
from .utils_common import sanitize_filename_preserve_unicode
from .utils_common import debug_log
from config.model_profiles import get_registered_model_profiles
from core.tool_config import TOOL_CATEGORIES
from . import state
from modules.personalization_manager import sanitize_personalization_payload
from modules.i18n import tr

api_v1_bp = Blueprint("api_v1", __name__, url_prefix="/api/v1")

@api_v1_bp.route("/tools", methods=["GET"])
@api_token_required
def list_tools_api():
    """返回工具分类与工具名称列表。"""
    categories = []
    for cid, cat in TOOL_CATEGORIES.items():
        categories.append({
            "id": cid,
            "label": cat.label,
            "default_enabled": bool(cat.default_enabled),
            "silent_when_disabled": bool(cat.silent_when_disabled),
            "tools": list(cat.tools),
        })
    return jsonify({"success": True, "categories": categories})


# -------------------- 工作区管理 --------------------
def _sanitize_workspace_id(ws_id: str) -> str:
    import re
    ws = (ws_id or "").strip()
    if not ws:
        return ""
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,40}", ws):
        return ""
    return ws


def _public_workspace_info(workspace) -> Dict[str, Any]:
    """对外返回的工作区信息，隐藏宿主机绝对路径。"""
    return {
        "success": True,
        "workspace_id": workspace.workspace_id,
        "project_path": "project",  # 相对工作区目录，避免暴露宿主机路径
        "data_dir": "data",
    }


@api_v1_bp.route("/workspaces", methods=["GET"])
@api_token_required
def list_workspaces_api():
    username = session.get("username")
    items = state.api_user_manager.list_workspaces(username)
    return jsonify({"success": True, "items": list(items.values())})


@api_v1_bp.route("/workspaces", methods=["POST"])
@api_token_required
@rate_limited("api_v1_create_ws", 10, 300, scope="user")
def create_workspace_api():
    username = session.get("username")
    payload = request.get_json(silent=True) or {}
    ws_id = _sanitize_workspace_id(payload.get("workspace_id") or payload.get("name") or "")
    if not ws_id:
        return jsonify({"success": False, "error": tr("api_v1.workspace_id_invalid_chars")}), 400
    try:
        ws = state.api_user_manager.ensure_workspace(username, ws_id)
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500
    return jsonify(_public_workspace_info(ws))


@api_v1_bp.route("/workspaces/<workspace_id>", methods=["GET"])
@api_token_required
def get_workspace_api(workspace_id: str):
    username = session.get("username")
    ws_id = _sanitize_workspace_id(workspace_id)
    if not ws_id:
        return jsonify({"success": False, "error": tr("api_v1.workspace_id_invalid")}), 400
    workspaces = state.api_user_manager.list_workspaces(username)
    ws_info = workspaces.get(ws_id)
    if not ws_info:
        return jsonify({"success": False, "error": tr("api_v1.workspace_not_found")}), 404
    return jsonify({"success": True, "workspace": ws_info})


@api_v1_bp.route("/workspaces/<workspace_id>", methods=["DELETE"])
@api_token_required
def delete_workspace_api(workspace_id: str):
    username = session.get("username")
    ws_id = _sanitize_workspace_id(workspace_id)
    if not ws_id:
        return jsonify({"success": False, "error": tr("api_v1.workspace_id_invalid")}), 400
    # 阻止删除有运行中任务的工作区
    running = [t for t in task_manager.list_tasks(username, ws_id) if t.status in {"pending", "running"}]
    if running:
        return jsonify({"success": False, "error": tr("api_v1.workspace_has_running_tasks")}), 409
    removed = state.api_user_manager.delete_workspace(username, ws_id)
    # 清理终端/容器缓存
    term_key = f"{username}::{ws_id}"
    state.user_terminals.pop(term_key, None)
    try:
        state.container_manager.release_container(term_key, reason="workspace_deleted")
    except Exception:
        pass
    if not removed:
        return jsonify({"success": False, "error": tr("api_v1.workspace_not_found_or_delete_failed")}), 404
    return jsonify({"success": True, "workspace_id": ws_id})


def _path_within(base: Path, target: Path) -> bool:
    """严格的路径包含判断（带分隔符边界），避免 startswith 前缀碰撞。"""
    if target == base:
        return True
    return str(target).startswith(str(base) + os.sep)


def _validate_resource_name(name: str) -> str:
    """校验 prompts/personalizations 等资源名，防路径穿越写入。

    仅允许字母数字/下划线/连字符，最长 64；不含点号（防 `..` 与后缀伪装）。
    """
    import re
    candidate = (name or "").strip()
    if not candidate or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", candidate):
        raise ValueError(tr("api_v1.invalid_resource_name"))
    return candidate


def _within_uploads(workspace, rel_path: str) -> Path:
    base = Path(workspace.uploads_dir).resolve()
    rel = rel_path or ""
    # 兼容传入包含 user_upload/ 前缀的路径
    if rel.startswith("user_upload/"):
        rel = rel.split("user_upload/", 1)[1]
    rel = rel.lstrip("/").strip()
    target = (base / rel).resolve()
    if not _path_within(base, target):
        raise ValueError(tr("api_v1_raise.invalid_path"))
    return target


def _within_project(workspace, rel_path: str, default_to_project_root: bool = True) -> Path:
    """
    将相对路径解析到工作区 project 目录内，防止越界。

    Args:
        workspace: ApiUserWorkspace
        rel_path: 相对路径，允许以 user_upload/ 开头（向后兼容）
        default_to_project_root: 当 rel_path 为空时，是否默认指向 project 根
    """
    base = Path(workspace.project_path).resolve()
    rel = (rel_path or "").strip()
    if not rel and default_to_project_root:
        rel = ""
    # 兼容 /workspace/<...>/ 前缀（容器路径）
    if rel.startswith("/workspace/"):
        rel = rel.split("/workspace/", 1)[1]
    rel = rel.lstrip("/")
    target = (base / rel).resolve()
    if not _path_within(base, target):
        raise ValueError(tr("api_v1_raise.invalid_path"))
    return target


def _conversation_path(workspace, conv_id: str) -> Path:
    return Path(workspace.data_dir) / "conversations" / f"{conv_id}.json"


def _update_conversation_metadata(workspace, conv_id: str, updates: Dict[str, Any]):
    path = _conversation_path(workspace, conv_id)
    if not path.exists():
        return
    try:
        data = {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        meta = data.get("metadata") or {}
        meta.update({k: v for k, v in updates.items() if v is not None})
        data["metadata"] = meta
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        debug_log(f"[meta] 更新对话元数据失败: {exc}")


def _prompt_dir(workspace):
    # 优先使用用户级共享目录（ApiUserWorkspace.prompts_dir），否则退回 data_dir/prompts
    target = getattr(workspace, "prompts_dir", None)
    if target:
        Path(target).mkdir(parents=True, exist_ok=True)
        return Path(target)
    p = Path(workspace.data_dir) / "prompts"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _personalization_dir(workspace):
    target = getattr(workspace, "personalization_dir", None)
    if target:
        Path(target).mkdir(parents=True, exist_ok=True)
        return Path(target)
    p = Path(workspace.data_dir) / "personalization"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _resolve_workspace(username: str, workspace_id: str):
    if not workspace_id:
        raise RuntimeError(tr("api_v1_raise.workspace_id_empty"))
    return state.api_user_manager.ensure_workspace(username, workspace_id)


@api_v1_bp.route("/workspaces/<workspace_id>/conversations", methods=["POST"])
@api_token_required
@rate_limited("api_v1_create_conv", 30, 60, scope="user")
def create_conversation_api(workspace_id: str):
    username = session.get("username")
    ws = _resolve_workspace(username, workspace_id)
    terminal, workspace = get_user_resources(username, workspace_id=ws.workspace_id)
    if not terminal:
        return jsonify({"success": False, "error": tr("api_v1.system_not_initialized")}), 503
    payload = request.get_json(silent=True) or {}
    prompt_name = payload.get("prompt_name")
    personalization_name = payload.get("personalization_name")
    result = terminal.create_new_conversation()
    if not result.get("success"):
        return jsonify({"success": False, "error": result.get("error") or tr("api_v1.create_conversation_failed")}), 500
    conv_id = result.get("conversation_id")
    # 记录元数据，稍后消息时可自动应用
    _update_conversation_metadata(workspace, conv_id, {
        "custom_prompt_name": prompt_name,
        "personalization_name": personalization_name,
        "workspace_id": ws.workspace_id,
    })
    # 立即应用覆盖
    apply_conversation_overrides(terminal, workspace, conv_id)
    return jsonify({"success": True, "conversation_id": conv_id, "workspace_id": ws.workspace_id})


@api_v1_bp.route("/workspaces/<workspace_id>/messages", methods=["POST"])
@api_token_required
@rate_limited("api_v1_send_msg", 20, 60, scope="user")
def send_message_api(workspace_id: str):
    username = session.get("username")
    ws = _resolve_workspace(username, workspace_id)
    payload = request.get_json() or {}
    message = (payload.get("message") or "").strip()
    from config import MAX_MESSAGE_CHARS
    if len(message) > MAX_MESSAGE_CHARS:
        return jsonify({"success": False, "error": tr("api_v1.message_too_long")}), 400
    images = payload.get("images") or []
    conversation_id = payload.get("conversation_id")
    model_key = payload.get("model_key")
    thinking_mode = payload.get("thinking_mode")
    run_mode = payload.get("run_mode")
    max_iterations = payload.get("max_iterations")
    prompt_name = payload.get("prompt_name")
    personalization_name = payload.get("personalization_name")
    if not message and not images:
        return jsonify({"success": False, "error": tr("api_v1.message_empty")}), 400

    terminal, workspace = get_user_resources(username, workspace_id=ws.workspace_id)
    if not terminal or not workspace:
        return jsonify({"success": False, "error": tr("api_v1.system_not_initialized")}), 503
    try:
        conversation_id, _ = ensure_conversation_loaded(terminal, conversation_id, workspace=workspace)
    except Exception as exc:
        return jsonify({"success": False, "error": tr("api_v1.conversation_load_failed", error=exc)}), 400

    # 应用自定义 prompt/personalization（如果提供）
    try:
        if prompt_name:
            prompt_name = _validate_resource_name(prompt_name)
            prompt_path = _prompt_dir(workspace) / f"{prompt_name}.txt"
            if not prompt_path.exists():
                return jsonify({"success": False, "error": tr("api_v1.prompt_not_found")}), 404
            terminal.context_manager.custom_system_prompt = prompt_path.read_text(encoding="utf-8")
        if personalization_name:
            personalization_name = _validate_resource_name(personalization_name)
            pers_path = _personalization_dir(workspace) / f"{personalization_name}.json"
            if not pers_path.exists():
                return jsonify({"success": False, "error": tr("api_v1.personalization_not_found")}), 404
            try:
                terminal.context_manager.custom_personalization_config = json.loads(pers_path.read_text(encoding="utf-8"))
            except Exception:
                return jsonify({"success": False, "error": tr("api_v1.personalization_parse_failed")}), 400
        # 持久化到对话元数据
        _update_conversation_metadata(workspace, conversation_id, {
            "custom_prompt_name": prompt_name,
            "personalization_name": personalization_name,
            "workspace_id": ws.workspace_id,
        })
        # 立即应用个性化偏好（确保禁用工具分类生效）；
        # 对话加载链路不应用默认 模型/模式/推理强度（以对话 meta 为权威）
        try:
            terminal.apply_personalization_preferences(
                terminal.context_manager.custom_personalization_config,
                apply_default_modes=False,
            )
        except Exception as exc:
            debug_log(f"[api_v1] 应用个性化失败: {exc}")
    except Exception as exc:
        return jsonify({"success": False, "error": tr("api_v1.custom_params_error", error=exc)}), 400

    try:
        rec = task_manager.create_chat_task(
            username=username,
            workspace_id=ws.workspace_id,
            message=message,
            images=images,
            conversation_id=conversation_id,
            model_key=model_key,
            thinking_mode=thinking_mode,
            run_mode=run_mode,
            max_iterations=max_iterations,
        )
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except RuntimeError as exc:
        # 并发等业务冲突仍用 409
        return jsonify({"success": False, "error": str(exc)}), 409
    return jsonify({
        "success": True,
        "task_id": rec.task_id,
        "conversation_id": rec.conversation_id,
        "workspace_id": ws.workspace_id,
        "status": rec.status,
        "created_at": rec.created_at,
    }), 202


@api_v1_bp.route("/workspaces/<workspace_id>/conversations", methods=["GET"])
@api_token_required
def list_conversations_api(workspace_id: str):
    username = session.get("username")
    ws = _resolve_workspace(username, workspace_id)
    terminal, workspace = get_user_resources(username, workspace_id=ws.workspace_id)
    if not terminal or not workspace:
        return jsonify({"success": False, "error": tr("api_v1.system_not_initialized")}), 503
    limit = max(1, min(int(request.args.get("limit", 20)), 100))
    offset = max(0, int(request.args.get("offset", 0)))
    conv_dir = Path(workspace.data_dir) / "conversations"
    items = []
    for p in sorted(conv_dir.glob("conv_*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            meta = data.get("metadata") or {}
            items.append({
                "id": data.get("id"),
                "title": data.get("title"),
                "created_at": data.get("created_at"),
                "updated_at": data.get("updated_at"),
                "run_mode": meta.get("run_mode"),
                "model_key": meta.get("model_key"),
                "custom_prompt_name": meta.get("custom_prompt_name"),
                "personalization_name": meta.get("personalization_name"),
                "workspace_id": ws.workspace_id,
                "messages_count": len(data.get("messages", [])),
            })
        except Exception:
            continue
    sliced = items[offset:offset + limit]
    return jsonify({"success": True, "data": sliced, "total": len(items)})


@api_v1_bp.route("/workspaces/<workspace_id>/conversations/<conv_id>", methods=["GET"])
@api_token_required
def get_conversation_api(workspace_id: str, conv_id: str):
    username = session.get("username")
    ws = _resolve_workspace(username, workspace_id)
    _, workspace = get_user_resources(username, workspace_id=ws.workspace_id)
    if not workspace:
        return jsonify({"success": False, "error": tr("api_v1.system_not_initialized")}), 503
    path = _conversation_path(workspace, conv_id)
    if not path.exists():
        return jsonify({"success": False, "error": tr("api_v1.conversation_not_found")}), 404
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        include_messages = request.args.get("full", "0") == "1"
        if not include_messages:
            data["messages"] = None
        return jsonify({"success": True, "data": data, "workspace_id": ws.workspace_id})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@api_v1_bp.route("/workspaces/<workspace_id>/conversations/<conv_id>", methods=["DELETE"])
@api_token_required
def delete_conversation_api(workspace_id: str, conv_id: str):
    username = session.get("username")
    ws = _resolve_workspace(username, workspace_id)
    terminal, workspace = get_user_resources(username, workspace_id=ws.workspace_id)
    if not terminal or not workspace:
        return jsonify({"success": False, "error": tr("api_v1.system_not_initialized")}), 503
    result = terminal.delete_conversation(conv_id)
    status = 200 if result.get("success") else 404
    result["workspace_id"] = ws.workspace_id
    return jsonify(result), status


@api_v1_bp.route("/tasks/<task_id>", methods=["GET"])
@api_token_required
def get_task_events(task_id: str):
    username = session.get("username")
    rec = task_manager.get_task(username, task_id)
    if not rec:
        return jsonify({"success": False, "error": tr("api_v1.task_not_found")}), 404
    try:
        offset = int(request.args.get("from", 0))
    except Exception:
        offset = 0
    # 工作线程会持续追加事件，必须持锁快照，不能直接迭代 rec.events
    events = task_manager.get_events_since(rec, offset)
    next_offset = events[-1]["idx"] + 1 if events else offset
    return jsonify({
        "success": True,
        "data": {
            "task_id": rec.task_id,
            "workspace_id": getattr(rec, "workspace_id", None),
            "status": rec.status,
            "created_at": rec.created_at,
            "updated_at": rec.updated_at,
            "error": rec.error,
            "events": events,
            "next_offset": next_offset,
        }
    })


@api_v1_bp.route("/tasks/<task_id>/cancel", methods=["POST"])
@api_token_required
def cancel_task_api_v1(task_id: str):
    username = session.get("username")
    ok = task_manager.cancel_task(username, task_id)
    if not ok:
        return jsonify({"success": False, "error": tr("api_v1.task_not_found")}), 404
    return jsonify({"success": True})


@api_v1_bp.route("/workspaces/<workspace_id>/files/upload", methods=["POST"])
@api_token_required
@rate_limited("api_v1_upload", 20, 300, scope="user")
def upload_file_api(workspace_id: str):
    username = session.get("username")
    ws = _resolve_workspace(username, workspace_id)
    terminal, workspace = get_user_resources(username, workspace_id=ws.workspace_id)
    if not terminal or not workspace:
        return jsonify({"success": False, "error": tr("api_v1.system_not_initialized")}), 503
    if 'file' not in request.files:
        return jsonify({"success": False, "error": tr("api_v1.no_file_uploaded")}), 400
    file_obj = request.files['file']
    raw_name = request.form.get('filename') or file_obj.filename
    filename = sanitize_filename_preserve_unicode(raw_name)
    if not filename:
        return jsonify({"success": False, "error": tr("api_v1.invalid_filename")}), 400
    subdir = request.form.get("dir") or ""
    try:
        target_dir = _within_uploads(workspace, subdir)
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = (target_dir / filename).resolve()
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 400

    guard = get_upload_guard(workspace)
    rel_path = str(target_path.relative_to(workspace.uploads_dir))
    try:
        result = guard.process_upload(
            file_obj,
            target_path,
            username=username,
            source="api_v1",
            original_name=raw_name,
            relative_path=rel_path,
        )
    except Exception as exc:
        return jsonify({"success": False, "error": tr("api_v1.save_file_failed", error=exc)}), 500

    metadata = result.get("metadata", {})
    return jsonify({
        "success": True,
        "workspace_id": ws.workspace_id,
        "path": rel_path,
        "filename": target_path.name,
        "size": metadata.get("size"),
        "sha256": metadata.get("sha256"),
    })


@api_v1_bp.route("/workspaces/<workspace_id>/files", methods=["GET"])
@api_token_required
def list_files_api(workspace_id: str):
    username = session.get("username")
    ws = _resolve_workspace(username, workspace_id)
    _, workspace = get_user_resources(username, workspace_id=ws.workspace_id)
    if not workspace:
        return jsonify({"success": False, "error": tr("api_v1.system_not_initialized")}), 503
    rel = request.args.get("path") or ""
    try:
        target = _within_project(workspace, rel)
        if not target.exists():
            return jsonify({"success": False, "error": tr("api_v1.path_not_found")}), 404
        if not target.is_dir():
            stat = target.stat()
            rel_entry = target.relative_to(workspace.project_path)
            return jsonify({
                "success": True,
                "workspace_id": ws.workspace_id,
                "items": [{
                    "name": target.name,
                    "is_dir": False,
                    "size": stat.st_size,
                    "modified_at": stat.st_mtime,
                    "path": str(rel_entry),
                }],
                "base": str(rel_entry.parent) if rel_entry.parent != Path(".") else "",
            })
        items = []
        for entry in sorted(target.iterdir(), key=lambda p: p.name):
            stat = entry.stat()
            rel_entry = entry.relative_to(workspace.project_path)
            items.append({
                "name": entry.name,
                "is_dir": entry.is_dir(),
                "size": stat.st_size,
                "modified_at": stat.st_mtime,
                "path": str(rel_entry),
            })
        base_rel = target.relative_to(workspace.project_path)
        base_str = "" if str(base_rel) == "." else str(base_rel)
        return jsonify({"success": True, "workspace_id": ws.workspace_id, "items": items, "base": base_str})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 400


@api_v1_bp.route("/workspaces/<workspace_id>/files/download", methods=["GET"])
@api_token_required
def download_file_api(workspace_id: str):
    username = session.get("username")
    ws = _resolve_workspace(username, workspace_id)
    _, workspace = get_user_resources(username, workspace_id=ws.workspace_id)
    if not workspace:
        return jsonify({"success": False, "error": tr("api_v1.system_not_initialized")}), 503
    rel = request.args.get("path")
    if not rel:
        return jsonify({"success": False, "error": tr("api_v1.path_missing")}), 400
    try:
        target = _within_project(workspace, rel)
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    if not target.exists():
        return jsonify({"success": False, "error": tr("api_v1.file_does_not_exist")}), 404

    if target.is_dir():
        # 文件夹打包下载已下线（2026-09-02 安全审计：os.walk/zipfile 跟随文件型
        # 符号链接，可泄露宿主任意文件）
        return jsonify({"success": False, "error": tr("api_v1.folder_download_removed")}), 410
    return send_file(target, as_attachment=True, download_name=target.name)


@api_v1_bp.route("/prompts", methods=["GET"])
@api_token_required
def list_prompts_api():
    username = session.get("username")
    # prompts 按用户共享，使用默认工作区上下文
    ws = _resolve_workspace(username, "default")
    _, workspace = get_user_resources(username, workspace_id=ws.workspace_id)
    if not workspace:
        return jsonify({"success": False, "error": tr("api_v1.system_not_initialized")}), 503
    base = _prompt_dir(workspace)
    items = []
    for p in sorted(base.glob("*.txt")):
        stat = p.stat()
        items.append({
            "name": p.stem,
            "size": stat.st_size,
            "updated_at": stat.st_mtime,
        })
    return jsonify({"success": True, "items": items})


@api_v1_bp.route("/prompts/<name>", methods=["GET"])
@api_token_required
def get_prompt_api(name: str):
    username = session.get("username")
    ws = _resolve_workspace(username, "default")
    _, workspace = get_user_resources(username, workspace_id=ws.workspace_id)
    if not workspace:
        return jsonify({"success": False, "error": tr("api_v1.system_not_initialized")}), 503
    try:
        name = _validate_resource_name(name)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    p = _prompt_dir(workspace) / f"{name}.txt"
    if not p.exists():
        return jsonify({"success": False, "error": tr("api_v1.prompt_not_found")}), 404
    return jsonify({"success": True, "name": name, "content": p.read_text(encoding="utf-8")})


@api_v1_bp.route("/prompts", methods=["POST"])
@api_token_required
def create_prompt_api():
    username = session.get("username")
    ws = _resolve_workspace(username, "default")
    _, workspace = get_user_resources(username, workspace_id=ws.workspace_id)
    if not workspace:
        return jsonify({"success": False, "error": tr("api_v1.system_not_initialized")}), 503
    payload = request.get_json() or {}
    name = (payload.get("name") or "").strip()
    content = payload.get("content") or ""
    if not name:
        return jsonify({"success": False, "error": tr("api_v1.name_empty")}), 400
    try:
        name = _validate_resource_name(name)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    p = _prompt_dir(workspace) / f"{name}.txt"
    p.write_text(content, encoding="utf-8")
    return jsonify({"success": True, "name": name})


@api_v1_bp.route("/personalizations", methods=["GET"])
@api_token_required
def list_personalizations_api():
    username = session.get("username")
    ws = _resolve_workspace(username, "default")
    _, workspace = get_user_resources(username, workspace_id=ws.workspace_id)
    if not workspace:
        return jsonify({"success": False, "error": tr("api_v1.system_not_initialized")}), 503
    base = _personalization_dir(workspace)
    items = []
    for p in sorted(base.glob("*.json")):
        stat = p.stat()
        items.append({
            "name": p.stem,
            "size": stat.st_size,
            "updated_at": stat.st_mtime,
        })
    return jsonify({"success": True, "items": items})


@api_v1_bp.route("/personalizations/<name>", methods=["GET"])
@api_token_required
def get_personalization_api(name: str):
    username = session.get("username")
    ws = _resolve_workspace(username, "default")
    _, workspace = get_user_resources(username, workspace_id=ws.workspace_id)
    if not workspace:
        return jsonify({"success": False, "error": tr("api_v1.system_not_initialized")}), 503
    try:
        name = _validate_resource_name(name)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    p = _personalization_dir(workspace) / f"{name}.json"
    if not p.exists():
        return jsonify({"success": False, "error": tr("api_v1.personalization_not_found")}), 404
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        # 读取时也做一次规范化，避免历史脏数据一直向外暴露
        content = sanitize_personalization_payload(raw)
        if content != raw:
            p.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        return jsonify({"success": False, "error": tr("api_v1.parse_failed", error=exc)}), 500
    return jsonify({"success": True, "name": name, "content": content})


@api_v1_bp.route("/personalizations", methods=["POST"])
@api_token_required
def create_personalization_api():
    username = session.get("username")
    ws = _resolve_workspace(username, "default")
    _, workspace = get_user_resources(username, workspace_id=ws.workspace_id)
    if not workspace:
        return jsonify({"success": False, "error": tr("api_v1.system_not_initialized")}), 503
    payload = request.get_json() or {}
    name = (payload.get("name") or "").strip()
    content = payload.get("content")
    if not name:
        return jsonify({"success": False, "error": tr("api_v1.name_empty")}), 400
    if content is None:
        return jsonify({"success": False, "error": tr("api_v1.content_empty")}), 400
    try:
        name = _validate_resource_name(name)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    p = _personalization_dir(workspace) / f"{name}.json"
    try:
        if not isinstance(content, dict):
            return jsonify({"success": False, "error": tr("api_v1.content_must_be_object")}), 400
        existing = None
        if p.exists():
            try:
                existing = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                existing = None
        # 规范化/清洗：截断长度、过滤非法值、回落默认
        sanitized = sanitize_personalization_payload(content, fallback=existing)
        p.write_text(json.dumps(sanitized, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        return jsonify({"success": False, "error": tr("api_v1.save_failed", error=exc)}), 500
    return jsonify({"success": True, "name": name, "content": sanitized})


@api_v1_bp.route("/models", methods=["GET"])
def list_models_api():
    items = []
    for key, profile in get_registered_model_profiles().items():
        if profile.get("hidden"):
            continue
        multimodal = str(profile.get("multimodal") or "none")
        description = profile.get("description") or ""
        items.append({
            "model_key": key,
            "name": profile.get("name", key),
            "description": description,
            "visible": not bool(profile.get("hidden")),
            "supports_thinking": profile.get("supports_thinking", False),
            "fast_only": profile.get("fast_only", False),
            "thinking_only": profile.get("thinking_only", False),
            "supports_reasoning_effort": profile.get("supports_reasoning_effort", False),
            "multimodal": multimodal,
            "context_window": profile.get("context_window"),
            "max_output_tokens": (profile.get("fast") or {}).get("max_tokens"),
        })
    return jsonify({"success": True, "items": items})


@api_v1_bp.route("/health", methods=["GET"])
def health_api():
    return jsonify({"success": True, "status": "ok"})


__all__ = ["api_v1_bp"]
