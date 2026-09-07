"""简单任务 API：将聊天任务与 WebSocket 解耦，支持后台运行与轮询。"""
from __future__ import annotations
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



