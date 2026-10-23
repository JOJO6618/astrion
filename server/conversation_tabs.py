"""桌面导航命令与版本化快照接口，状态唯一写者在 modules/desktop_navigation。

存 `{DATA_DIR}/conversation_tabs.json`（按用户名分键），双 WebView 提交幂等命令，
主页面确认 renderer/navigation 后提交显示。非 host 模式不提供标签导航。

下发给标签条的 `running` 是**对话级运行态**（主任务 + 后台子智能体/后台命令/
多智能体），与对话记录侧边栏的运行中图标同一口径；不是"主智能体还在不在工作"。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List

from flask import Blueprint, jsonify, request

from config import DATA_DIR, IS_HOST_MODE
from server.auth_helpers import api_login_required, get_current_username
from modules.desktop_navigation import DesktopNavigation, NavigationConflict
from server.context.data import get_workspace_data
from server.context.workspace import request_identity, resolve_workspace

conversation_tabs_bp = Blueprint("conversation_tabs", __name__)

_TABS_PATH = os.path.join(DATA_DIR, "conversation_tabs.json")
navigation = DesktopNavigation(_TABS_PATH)


def _load_index_titles(workspace_id: str) -> Dict[str, str]:
    """对话索引的最新标题（标签持久化里的标题可能只是创建时快照）。"""
    if not workspace_id:
        return {}
    index_path = os.path.join(DATA_DIR, "conversations", workspace_id, "index.json")
    try:
        with open(index_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    titles: Dict[str, str] = {}
    for conv_id, item in data.items():
        if isinstance(item, dict):
            title = str(item.get("title") or "").strip()
            if title:
                titles[str(conv_id)] = title
    return titles


def _normalized_conversation_ids(raw: Any) -> List[str]:
    """对话 id 的合法写法（带/不带 conv_ 前缀），用于与任务记录、terminal 键对齐。"""
    value = str(raw or "").strip()
    if not value:
        return []
    return [value] if value.startswith("conv_") else [value, f"conv_{value}"]


def _active_run_conversation_ids(username: str, workspace_ids: List[str]) -> set:
    """各工作区当前有活动主任务（pending/running/cancel_requested）的对话 id 集合。

    只覆盖主任务（任务注册表）。子智能体/后台命令/多智能体不是 TaskRecord，
    由 `_conversation_is_running` 的对话级 terminal 聚合补齐。
    """
    active: set = set()
    try:
        from server.runtime import runtime_service
    except Exception:
        return active
    for ws_id in workspace_ids:
        try:
            runs = runtime_service.list_runs(username, ws_id, status="active")
        except Exception:
            continue
        for run in runs:
            active.update((ws_id, conv_id) for conv_id in
                          _normalized_conversation_ids(run.get("conversation_id")))
    return active


def _peek_conversation_terminal(username: str, workspace_id: str, conversation_ids: List[str]):
    """读取**已存在**的对话级 terminal（不创建）。

    标签条轮询不能走 get_user_resources 按需建终端（每个对话级终端各自持有
    对话历史，代价不可接受）；这里只聚合已存在的后台运行实例，
    活动主任务独立从 RuntimeService 查询，不从标签查询触发恢复。
    """
    if not conversation_ids:
        return None
    try:
        from server import state
        from server.context import _make_terminal_key
    except Exception:
        return None
    for conv_id in conversation_ids:
        terminal = state.user_terminals.get(_make_terminal_key(username, workspace_id, conv_id))
        if terminal is not None and not getattr(terminal, "_reaper_closing", False):
            return terminal
    # Workspace identity is part of the key; never borrow another workspace's terminal.
    return None


def _conversation_is_running(
    username: str,
    workspace_id: str,
    conversation_id: str,
    active_run_ids: set,
) -> bool:
    """对话级运行中判定：主任务活动 或 后台仍有运行中的工作。

    后台口径复用 `server/context/reaper.py::_conversation_terminal_has_running_work`
    （= `/api/conversations/<id>/running-status` 的 `is_truly_active` 同源口径），
    保证标签条蓝点与对话记录侧边栏一致：包含后台指令、子智能体、多智能体。
    """
    variants = _normalized_conversation_ids(conversation_id)
    if not variants:
        return False
    if any((workspace_id, conv_id) in active_run_ids for conv_id in variants):
        return True
    terminal = _peek_conversation_terminal(username, workspace_id or "", variants)
    if terminal is None:
        return False
    try:
        from server.context import _conversation_terminal_has_running_work

        return any(
            _conversation_terminal_has_running_work(
                username, workspace_id or "", conv_id, terminal
            )
            for conv_id in variants
        )
    except Exception:
        return False


def _enrich_tabs(username: str, tabs: List[Dict[str, Any]]) -> None:
    """GET 时富化：最新标题（对话索引）+ running 标记（对话级运行态）。"""
    ws_ids = sorted({t["workspace_id"] for t in tabs if t.get("kind") == "conv" and t.get("workspace_id")})
    title_maps = {ws: _load_index_titles(ws) for ws in ws_ids}
    active_runs = _active_run_conversation_ids(username, ws_ids)
    for tab in tabs:
        if tab.get("kind") != "conv":
            tab["running"] = False
            continue
        conv_id = str(tab.get("conversation_id") or "")
        fresh = title_maps.get(tab.get("workspace_id") or "", {}).get(conv_id)
        if fresh:
            tab["title"] = fresh[:200]
        tab["running"] = _conversation_is_running(
            username, str(tab.get("workspace_id") or ""), conv_id, active_runs
        )


def _lookup(username: str, *, with_activity: bool = False):
    identity = request_identity()

    def lookup(workspace_id: str, conversation_id: str):
        workspace = resolve_workspace(username, workspace_id or None, identity=identity)
        info = {"workspace_id": workspace.workspace_id,
                "label": str(getattr(workspace, "label", "") or workspace.workspace_id)}
        if conversation_id:
            manager = get_workspace_data(username, workspace.workspace_id, identity=identity).manager()
            path = manager._get_conversation_file_path(conversation_id)
            try:
                path.stat()
                info["exists"] = True
            except FileNotFoundError:
                info["exists"] = False
        elif with_activity:
            from server.runtime import runtime_service
            runs = runtime_service.list_runs(username, workspace.workspace_id, status="active")
            info["active_conversation_id"] = next(
                (run.get("conversation_id") for run in runs if run.get("conversation_id")), None)
        return info

    return lookup


def _public(username: str, state: dict) -> dict:
    _enrich_tabs(username, state["tabs"])
    return state


@conversation_tabs_bp.route("/api/conversation-tabs", methods=["GET"])
@api_login_required
def get_conversation_tabs():
    if not IS_HOST_MODE:
        return jsonify(success=True, tabs=[], active_key="", revision=0)
    username = get_current_username() or ""
    try:
        return jsonify(success=True, **_public(username, navigation.snapshot(username, _lookup(username))))
    except Exception as exc:
        return jsonify(success=False, error=str(exc)), 503


@conversation_tabs_bp.route("/api/conversation-tabs/commands", methods=["POST"])
@api_login_required
def command_conversation_tabs():
    if not IS_HOST_MODE:
        return jsonify(success=False, error="Desktop navigation is unavailable"), 400
    username = get_current_username() or ""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(success=False, error="Invalid navigation command"), 400
    action = str(body.get("action") or "")
    if action == "deleted":
        return jsonify(success=False, error="Invalid navigation command"), 400
    payload = body.get("payload") or {}
    try:
        if not isinstance(payload, dict):
            raise ValueError("Invalid navigation command")
        result = navigation.command(username, body.get("command_id"), action, payload,
                                    lookup=_lookup(username, with_activity=action == "workspace"))
        result["state"] = _public(username, result["state"])
        if action in {"commit", "attach", "page"}:
            try:
                from server.crosspet import focus_tab
                state = result["state"]
                focus_tab(username, next((tab for tab in state["tabs"]
                                          if tab["key"] == state["active_key"]), None))
            except Exception:
                pass
        return jsonify(success=True, **result)
    except NavigationConflict as exc:
        return jsonify(success=False, error=str(exc), code="navigation_superseded"), 409
    except ValueError as exc:
        return jsonify(success=False, error=str(exc)), 400
    except Exception as exc:
        return jsonify(success=False, error=str(exc)), 503
