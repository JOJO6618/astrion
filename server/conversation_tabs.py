"""对话标签条（桌面端浏览器式顶部标签）持久化接口。

存 `{DATA_DIR}/conversation_tabs.json`（按用户名分键），供桌面壳（host 模式）
重开应用后恢复上次打开的对话标签。非 host 模式返回空数据（标签功能仅桌面壳启用）。
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


def _running_conversation_ids(username: str, workspace_ids: List[str]) -> set:
    """各工作区当前有活动任务（pending/running/cancel_requested）的对话 id 集合。"""
    running: set = set()
    try:
        from server.runtime import runtime_service
    except Exception:
        return running
    for ws_id in workspace_ids:
        try:
            runs = runtime_service.list_runs(username, ws_id, status="active")
        except Exception:
            continue
        for run in runs:
            conv_id = str(run.get("conversation_id") or "").strip()
            if conv_id:
                running.add(conv_id)
    return running


def _enrich_tabs(username: str, tabs: List[Dict[str, Any]]) -> None:
    """GET 时富化：最新标题（对话索引）+ running 标记（活动任务）。"""
    ws_ids = sorted({t["workspace_id"] for t in tabs if t.get("kind") == "conv" and t.get("workspace_id")})
    title_maps = {ws: _load_index_titles(ws) for ws in ws_ids}
    running = _running_conversation_ids(username, ws_ids)
    for tab in tabs:
        if tab.get("kind") != "conv":
            tab["running"] = False
            continue
        conv_id = str(tab.get("conversation_id") or "")
        fresh = title_maps.get(tab.get("workspace_id") or "", {}).get(conv_id)
        if fresh:
            tab["title"] = fresh[:200]
        tab["running"] = conv_id in running or (
            bool(conv_id) and not conv_id.startswith("conv_") and f"conv_{conv_id}" in running
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
    # running 取运行时活动任务——标签持久化文件只是主页面写入的快照。
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
