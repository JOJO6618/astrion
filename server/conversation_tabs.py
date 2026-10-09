"""对话标签条（桌面端浏览器式顶部标签）持久化接口。

存 `{DATA_DIR}/conversation_tabs.json`（按用户名分键），供桌面壳（host 模式）
重开应用后恢复上次打开的对话标签。非 host 模式返回空数据（标签功能仅桌面壳启用）。

下发给标签条的 `running` 是**对话级运行态**（主任务 + 后台子智能体/后台命令/
多智能体），与对话记录侧边栏的运行中图标同一口径；不是"主智能体还在不在工作"。
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any, Dict, List

from flask import Blueprint, jsonify, request

from config import DATA_DIR, IS_HOST_MODE
from server.auth_helpers import api_login_required, get_current_username
from utils.atomic_io import atomic_write_json

conversation_tabs_bp = Blueprint("conversation_tabs", __name__)

_LOCK = threading.RLock()
_TABS_PATH = os.path.join(DATA_DIR, "conversation_tabs.json")

_TAB_KINDS = {"conv", "new"}


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
            active.update(_normalized_conversation_ids(run.get("conversation_id")))
    return active


def _peek_conversation_terminal(username: str, workspace_id: str, conversation_ids: List[str]):
    """读取**已存在**的对话级 terminal（不创建）。

    标签条 600ms 轮询，不能走 get_user_resources 按需建终端（每个对话级终端各自持有
    对话历史，代价不可接受）；而没有对话级 terminal 时该对话不可能有运行中的
    子智能体/后台命令/多智能体，跳过判定语义正确。
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
    # 工作区标识不一致时的兜底：按 用户名 + 对话 id 扫描终端键（三段键 username::ws::conv）
    wanted = set(conversation_ids)
    for term_key, terminal in list(state.user_terminals.items()):
        parts = str(term_key).split("::")
        if len(parts) < 3 or parts[0] != username:
            continue
        if "::".join(parts[2:]) not in wanted:
            continue
        if getattr(terminal, "_reaper_closing", False):
            continue
        return terminal
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
    if any(conv_id in active_run_ids for conv_id in variants):
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


def _load_all() -> Dict[str, Any]:
    with _LOCK:
        try:
            with open(_TABS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}


def _sanitize_tabs(raw: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    tabs: List[Dict[str, Any]] = []
    for item in raw[:100]:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "")
        if kind not in _TAB_KINDS:
            continue
        key = str(item.get("key") or "").strip()
        if not key:
            continue
        tabs.append({
            "key": key,
            "kind": kind,
            "conversation_id": str(item.get("conversation_id") or ""),
            "workspace_id": str(item.get("workspace_id") or ""),
            "workspace_label": str(item.get("workspace_label") or ""),
            "title": str(item.get("title") or "")[:200],
        })
    return tabs


@conversation_tabs_bp.route("/api/conversation-tabs", methods=["GET"])
@api_login_required
def get_conversation_tabs():
    if not IS_HOST_MODE:
        return jsonify({"success": True, "tabs": [], "active_key": ""})
    username = get_current_username() or ""
    data = _load_all().get(username) or {}
    tabs = _sanitize_tabs(data.get("tabs"))
    # chrome 标签条独立 webview 以轮询本端点刷新，标题取索引最新值、
    # running 取对话级运行态（主任务 + 后台子智能体/后台命令/多智能体，
    # 与对话记录侧边栏一致）——标签持久化文件只是主页面写入的快照。
    _enrich_tabs(username, tabs)
    return jsonify({
        "success": True,
        "tabs": tabs,
        "active_key": str(data.get("active_key") or ""),
    })


@conversation_tabs_bp.route("/api/conversation-tabs", methods=["PUT"])
@api_login_required
def save_conversation_tabs():
    if not IS_HOST_MODE:
        return jsonify({"success": True})
    username = get_current_username() or ""
    payload = request.get_json(silent=True) or {}
    tabs = _sanitize_tabs(payload.get("tabs"))
    active_key = str(payload.get("active_key") or "")
    tab_keys = {t["key"] for t in tabs}
    if active_key and active_key not in tab_keys:
        active_key = ""
    with _LOCK:
        data = _load_all()
        data[username] = {"tabs": tabs, "active_key": active_key}
        atomic_write_json(_TABS_PATH, data)
    return jsonify({"success": True})
