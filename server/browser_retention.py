"""Lightweight desktop residency decisions; never create a terminal or read history."""
from __future__ import annotations

import json
import time

from flask import jsonify, request

from modules.native_browser.retention import browser_retention


def retention_response():
    from server.auth_helpers import get_current_username
    from server.conversation_tabs import (
        navigation, _active_run_conversation_ids, _conversation_is_running,
    )
    from server.context.workspace import request_identity
    from server.context.data import get_workspace_data

    username = get_current_username() or ""
    try:
        payload = json.loads(request.headers.get("X-Astrion-Browser-State") or request.args.get("state", "{}"))
        scopes, tabs = payload["owners"], payload["tabs"]
        if not isinstance(scopes, list) or len(scopes) > 200 or not isinstance(tabs, list) or len(tabs) > 10000:
            raise ValueError("invalid_browser_retention_state")
        owners = {}
        for scope in scopes:
            if (not isinstance(scope, list) or len(scope) != 3 or scope[0] not in {"agent", "chat"}
                    or any(not isinstance(v, str) or len(v) > 256 for v in scope)):
                raise ValueError("invalid_browser_scope")
            owners[json.dumps(scope, ensure_ascii=False, separators=(",", ":"))] = scope
        if any(not isinstance(tab, dict) or tab.get("owner") not in owners
               or not isinstance(tab.get("id"), str)
               or any(not isinstance(tab.get(key, 0), (int, float)) for key in ("lastActive", "lastAi"))
               for tab in tabs):
            raise ValueError("invalid_browser_retention_tabs")
        # 已弹出到独立窗口的对话（桌面壳上报）与主窗口当前对话同样算前台；未知 owner 忽略。
        presented = payload.get("presented") or []
        if not isinstance(presented, list) or len(presented) > 200:
            raise ValueError("invalid_browser_presented")
        presented_keys = {json.dumps(scope, ensure_ascii=False, separators=(",", ":"))
                          for scope in presented if isinstance(scope, list)} & set(owners)
    except (ValueError, KeyError, TypeError):
        return jsonify(success=False, error="invalid_browser_retention_state"), 400

    # Reading the navigation snapshot neither selects a workspace nor loads a conversation.
    state = navigation.snapshot(username)
    current = next((tab for tab in state["tabs"] if tab["key"] == state["active_key"]), None)
    visible = None
    if current and not state.get("navigation", {}).get("page"):
        visible = [current.get("conversation_kind", "agent"), current.get("workspace_id", ""),
                   current.get("conversation_id") or current["key"]]
    active = _active_run_conversation_ids(username, sorted({scope[1] for scope in scopes if scope[0] == "agent"}))
    statuses = {}
    chat = chat_identity = None
    for key, (mode, workspace, conversation) in owners.items():
        viewed = payload.get("visits", {}).get(key, 0)
        info = {"exists": True, "front": owners[key] == visible or key in presented_keys,
                "viewed": viewed if isinstance(viewed, (int, float)) else 0}
        try:
            if conversation.startswith("conv_"):
                if mode == "chat":
                    from server.chat_mode.context import resources
                    from server.chat_mode.conversations import records, ACTIVE
                    if chat is None:
                        chat_identity, _, chat = resources()
                    path = chat.manager._get_conversation_file_path(conversation)
                    info["front"] |= any(record.status in ACTIVE for record in records(chat_identity, conversation))
                else:
                    data = get_workspace_data(username, workspace, identity=request_identity())
                    path = data.manager()._get_conversation_file_path(conversation)
                    info["front"] |= _conversation_is_running(username, workspace, conversation, active)
                try:
                    path.stat()
                except FileNotFoundError:
                    info["exists"] = False
        except (OSError, ValueError, RuntimeError, KeyError, TypeError):
            # Inaccessible workspaces / failed activity reads are not deletion or idleness.
            info["unknown"] = True
        statuses[key] = info
    decision = browser_retention.decide(username, statuses, tabs, int(time.time() * 1000))
    return jsonify(success=True, username=username, **decision)
