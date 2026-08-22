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


SKILL_FRONTMATTER_RE = re.compile(r"^---\s*\n(?P<body>.*?)\n---\s*\n?", re.S)
SKILL_FIELD_RE = re.compile(r"^(?P<key>name|description)\s*:\s*(?P<value>.*)$")



def _media_path(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("path") or "")
    return str(item or "")

def _is_video_item(item: Any) -> bool:
    path = _media_path(item)
    if not path:
        return False
    mime, _ = mimetypes.guess_type(path)
    return bool(mime and mime.startswith("video/"))

def _is_image_item(item: Any) -> bool:
    path = _media_path(item)
    if not path:
        return False
    mime, _ = mimetypes.guess_type(path)
    return bool(mime and mime.startswith("image/"))

def _normalize_files_payload(raw: Any) -> List[str]:
    """归一化附加文件列表：仅接受字符串形式的工作区相对路径，去重，最多 9 个。"""
    if not isinstance(raw, list):
        return []
    files: List[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        path = item.strip()
        if not path or len(path) > 500:
            continue
        if path in files:
            continue
        files.append(path)
        if len(files) >= 9:
            break
    return files


def _normalize_media_payload(images: List[Any], videos: List[Any]) -> tuple[List[Any], List[Any]]:
    """纠偏媒体字段：把误传到 images 的视频项自动归入 videos。"""
    fixed_images: List[Any] = []
    fixed_videos: List[Any] = []

    for item in images or []:
        if _is_video_item(item):
            fixed_videos.append(item)
        else:
            fixed_images.append(item)

    for item in videos or []:
        if _is_image_item(item):
            fixed_images.append(item)
        else:
            fixed_videos.append(item)

    return fixed_images, fixed_videos
