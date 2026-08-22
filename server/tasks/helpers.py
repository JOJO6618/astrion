"""简单任务 API：将聊天任务与 WebSocket 解耦，支持后台运行与轮询。"""
from __future__ import annotations
from server.tasks import tasks_bp
import mimetypes
import json
import time
import threading
import uuid
import re
from collections import deque
from pathlib import Path
from typing import Dict, Any, Optional, List

from flask import Blueprint, request, jsonify
from flask import current_app, session

from server.auth_helpers import api_login_required, get_current_username
from server.context import get_user_resources, ensure_conversation_loaded
from server.chat_flow import run_chat_task_sync
from server.state import stop_flags
from server.utils_common import debug_log, log_conn_diag
from utils.host_workspace_debug import write_host_workspace_debug
from config import DATA_DIR, WORKSPACE_SKILLS_DIRNAME
from server.tasks.models import TaskRecord


SKILL_FRONTMATTER_RE = re.compile(r"^---\s*\n(?P<body>.*?)\n---\s*\n?", re.S)
SKILL_FIELD_RE = re.compile(r"^(?P<key>name|description)\s*:\s*(?P<value>.*)$")



def _conversation_title_for_task(rec: TaskRecord) -> Optional[str]:
    conv_id = (getattr(rec, "conversation_id", None) or "").strip()
    if not conv_id:
        return None
    if not conv_id.startswith("conv_"):
        conv_id = f"conv_{conv_id}"
    try:
        if rec.username == "host":
            path = Path(DATA_DIR).expanduser().resolve() / "conversations" / rec.workspace_id / f"{conv_id}.json"
        else:
            import server.state as state
            workspace = state.user_manager.ensure_user_workspace(rec.username, rec.workspace_id or "default")
            path = Path(workspace.data_dir).expanduser().resolve() / "conversations" / f"{conv_id}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        title = str(data.get("title") or "").strip()
        return title or None
    except Exception:
        return None

def _workspace_label_for_task(rec: TaskRecord) -> Optional[str]:
    try:
        if rec.username == "host":
            from modules.host_workspace_manager import load_host_workspace_catalog
            catalog = load_host_workspace_catalog()
            for item in catalog.get("workspaces") or []:
                if item.get("workspace_id") == rec.workspace_id:
                    return str(item.get("label") or rec.workspace_id)
        else:
            import server.state as state
            item = state.user_manager.list_user_workspaces(rec.username).get(rec.workspace_id)
            if item:
                return str(item.get("label") or rec.workspace_id)
    except Exception:
        pass
    return rec.workspace_id

def _task_public_payload(rec: TaskRecord, *, include_title: bool = True) -> Dict[str, Any]:
    payload = {
        "task_id": rec.task_id,
        "username": rec.username,
        "workspace_id": rec.workspace_id,
        "workspace_label": _workspace_label_for_task(rec),
        "status": rec.status,
        "created_at": rec.created_at,
        "updated_at": rec.updated_at,
        "message": rec.message,
        "conversation_id": rec.conversation_id,
        "error": rec.error,
        "message_source": (rec.session_data or {}).get("message_source"),
        "goal_mode": bool((rec.session_data or {}).get("goal_mode")),
        "goal_progress": (rec.session_data or {}).get("goal_progress"),
        "task_type": getattr(rec, "task_type", "chat"),
    }
    if include_title:
        payload["conversation_title"] = _conversation_title_for_task(rec)
    return payload
